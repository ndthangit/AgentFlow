# Quản lý LLM provider

Phần quản lý hỗ trợ OpenRouter, OpenAI, Anthropic, NVIDIA và provider bất kỳ theo
giao thức OpenAI-compatible hoặc Anthropic-compatible. Chiến lược kiểm thử
hoàn toàn bằng key giả được mô tả tại
[Tích hợp provider không cần API key thật](provider-integration-testing.md).

AgentFlow lưu cấu hình provider theo từng người dùng trong bảng `system.llm_providers`. Mỗi bản ghi là một cấu hình kết nối độc lập, có tên do người dùng đặt, API key và danh sách model riêng. Vì vậy một người dùng có thể tạo nhiều cấu hình cùng loại nhưng dùng các key khác nhau.

## Luồng cấu hình

1. Người dùng thêm provider bằng tên cấu hình, loại provider và API key. Với loại protocol-compatible, người dùng nhập thêm base URL và có thể khai báo model ID thủ công. Tên cấu hình phải là duy nhất trong phạm vi tài khoản hiện tại.
2. API key được mã hóa và provider luôn được tạo ở trạng thái `Inactive`. Bước này không gọi provider.
3. Khi người dùng chọn kích hoạt, AgentFlow gọi model catalog bằng key đã lưu và hiển thị kết quả để chọn.
4. Người dùng phải chọn ít nhất một model và có thể chọn rõ model mặc định. Nếu model mặc định cũ không còn trong lựa chọn, giao diện tự chuyển sang model đầu tiên còn lại.
5. Backend tải lại catalog để xác thực các model trước khi chuyển provider sang `Active`.
6. Mỗi model đã chọn được hiển thị trên một dòng và có nút kiểm tra riêng. Phép kiểm tra gửi một chat completion ngắn tới đúng model đó.

## Thiết kế adapter

`LlmProviderAdapter` là interface ổn định gồm `verify()`, `list_models()` và `complete()`. `OpenRouterAdapter`, `OpenAICompatibleAdapter` và `AnthropicAdapter` triển khai interface này, còn `create_provider_adapter()` là registry/factory. Code orchestration chỉ phụ thuộc interface chung.

OpenRouter dùng `GET /api/v1/models` để lấy catalog và `POST /api/v1/chat/completions` để gọi hoặc kiểm tra model. Adapter gửi API key bằng Bearer token và hỗ trợ các header attribution `HTTP-Referer`, `X-OpenRouter-Title`.

OpenAI, NVIDIA và `openai-compatible` dùng Bearer token cùng giao thức Chat Completions tương thích OpenAI. Anthropic và `anthropic-compatible` dùng `x-api-key`, `anthropic-version`, Models API và Messages API; adapter chuyển system message sang field `system` và chuẩn hóa content blocks về `ChatCompletionResult`.

## Provider theo giao thức

Dùng `openai-compatible` khi hãng cung cấp API có `POST /chat/completions` và thường có `GET /models`. Base URL nên chứa prefix phiên bản, ví dụ `https://api.vendor.example/v1`. Dùng `anthropic-compatible` khi hãng cung cấp `POST /v1/messages`; base URL là phần đứng trước `/v1`, ví dụ `https://api.vendor.example`.

Nếu hãng không triển khai endpoint liệt kê model, nhập model ID thủ công lúc tạo provider. AgentFlow dùng danh sách đó làm catalog cục bộ; nút **Kiểm tra** vẫn gửi completion thật sau khi provider được kích hoạt. Nếu để trống, AgentFlow tải catalog từ API của hãng.

Base URL tùy chỉnh được bảo vệ như sau:

- mặc định chỉ chấp nhận HTTPS, không chứa user/password, query hoặc fragment;
- từ chối localhost, IP không public và hostname nội bộ thông dụng;
- trước request thật, backend resolve DNS và từ chối nếu bất kỳ địa chỉ nào không public;
- HTTP/private network chỉ được phép trong môi trường local khi đặt `ALLOW_CUSTOM_PROVIDER_PRIVATE_NETWORKS=true`; cấu hình này bị từ chối ở production;
- HTTP client không tự theo redirect, giúp tránh redirect sang tài nguyên nội bộ.

## Dữ liệu được lưu

Bảng `system.llm_providers` lưu tên cấu hình, loại provider, settings, API key đã mã hóa, trạng thái và `revision` cho optimistic concurrency. Ràng buộc duy nhất áp dụng trên cặp `owner_subject + name`, không áp dụng trên `kind`, nên nhiều cấu hình cùng nhà cung cấp được hỗ trợ. Settings chung gồm:

- `selected_models`: tối đa 100 model ID người dùng cho phép sử dụng.
- `custom_models`: catalog model khai báo thủ công cho provider theo giao thức.
- `base_url`: endpoint đã kiểm tra của provider theo giao thức.
- `default_model`: model mặc định; backend tự dùng model đầu tiên đã chọn nếu giá trị cũ không còn hợp lệ.
- `site_url`: URL attribution tùy chọn.
- `app_title`: tên ứng dụng gửi tới OpenRouter.

API key là write-only. Response chỉ trả `has_api_key`, không trả plaintext hoặc ciphertext. Khóa Fernet nằm tại `PROVIDER_SECRETS_KEY_FILE`; Docker Compose lưu khóa trong volume `agentflow_system_secrets`. Cần backup volume này cùng database.

## API quản lý

- `GET /v1/llm-providers`
- `POST /v1/llm-providers` với body gồm `name`, `kind`, `api_key`; hai loại protocol-compatible nhận thêm `base_url` và `custom_models`
- `GET /v1/llm-providers/{id}`
- `DELETE /v1/llm-providers/{id}` để xóa provider thuộc người dùng hiện tại
- `PUT /v1/llm-providers/{id}` để thay key, chọn model hoặc bật/tắt
- `GET /v1/llm-providers/{id}/models` để xác thực key và tải catalog, kể cả khi provider Inactive
- `POST /v1/llm-providers/{id}/models/test` với body `{ "model": "..." }` để kiểm tra một model đã chọn của provider Active

Ví dụ tạo provider:

```json
{
  "name": "OpenRouter công ty",
  "kind": "openrouter",
  "api_key": "sk-or-v1-..."
}
```

Ví dụ tạo một provider nhỏ theo giao thức OpenAI:

```json
{
  "name": "Vendor nội bộ của đội",
  "kind": "openai-compatible",
  "api_key": "vendor-key-...",
  "base_url": "https://api.vendor.example/v1",
  "custom_models": ["vendor-chat-small", "vendor-chat-large"]
}
```

Các model đã chọn được lưu trong JSONB `settings`, vì vậy thay đổi này không cần migration database mới. Provider cũ phải mở “Quản lý models” và lưu ít nhất một model để sử dụng luồng kiểm tra mới.
