# Quản lý LLM provider

AgentFlow lưu cấu hình provider theo từng người dùng trong bảng `system.llm_providers`. Provider đầu tiên được hỗ trợ là OpenRouter.

## Luồng cấu hình

1. Người dùng thêm OpenRouter chỉ bằng API key.
2. API key được mã hóa và provider luôn được tạo ở trạng thái `Inactive`. Bước này không gọi OpenRouter.
3. Khi người dùng chọn kích hoạt, AgentFlow gọi `GET /api/v1/models` bằng key đã lưu và hiển thị catalog để chọn.
4. Người dùng phải chọn ít nhất một model. Model đầu tiên được dùng làm `default_model` nếu model mặc định cũ không còn trong lựa chọn.
5. Backend tải lại catalog để xác thực các model trước khi chuyển provider sang `Active`.
6. Mỗi model đã chọn được hiển thị trên một dòng và có nút kiểm tra riêng. Phép kiểm tra gửi một chat completion ngắn tới đúng model đó.

## Thiết kế adapter

`LlmProviderAdapter` là interface ổn định gồm `verify()`, `list_models()` và `complete()`. `OpenRouterAdapter` triển khai interface này, còn `create_provider_adapter()` là registry/factory. Code orchestration chỉ phụ thuộc interface chung.

OpenRouter dùng `GET /api/v1/models` để lấy catalog và `POST /api/v1/chat/completions` để gọi hoặc kiểm tra model. Adapter gửi API key bằng Bearer token và hỗ trợ các header attribution `HTTP-Referer`, `X-OpenRouter-Title`.

## Dữ liệu được lưu

Bảng `system.llm_providers` lưu tên, loại provider, settings, API key đã mã hóa, trạng thái và `revision` cho optimistic concurrency. Settings OpenRouter gồm:

- `selected_models`: tối đa 100 model ID người dùng cho phép sử dụng.
- `default_model`: model mặc định; backend tự dùng model đầu tiên đã chọn nếu giá trị cũ không còn hợp lệ.
- `site_url`: URL attribution tùy chọn.
- `app_title`: tên ứng dụng gửi tới OpenRouter.

API key là write-only. Response chỉ trả `has_api_key`, không trả plaintext hoặc ciphertext. Khóa Fernet nằm tại `PROVIDER_SECRETS_KEY_FILE`; Docker Compose lưu khóa trong volume `agentflow_system_secrets`. Cần backup volume này cùng database.

## API quản lý

- `GET /v1/llm-providers`
- `POST /v1/llm-providers` với body chỉ gồm `api_key`
- `GET /v1/llm-providers/{id}`
- `DELETE /v1/llm-providers/{id}` để xóa provider thuộc người dùng hiện tại
- `PUT /v1/llm-providers/{id}` để thay key, chọn model hoặc bật/tắt
- `GET /v1/llm-providers/{id}/models` để xác thực key và tải catalog, kể cả khi provider Inactive
- `POST /v1/llm-providers/{id}/models/test` với body `{ "model": "..." }` để kiểm tra một model đã chọn của provider Active

Ví dụ tạo provider:

```json
{
  "api_key": "sk-or-v1-..."
}
```

Các model đã chọn được lưu trong JSONB `settings`, vì vậy thay đổi này không cần migration database mới. Provider cũ phải mở “Quản lý models” và lưu ít nhất một model để sử dụng luồng kiểm tra mới.
