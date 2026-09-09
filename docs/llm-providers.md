# Quản lý LLM provider

AgentFlow lưu cấu hình provider theo từng người dùng trong bảng `system.llm_providers`. Provider đầu tiên là `openrouter`.

## Thiết kế mở rộng

`LlmProviderAdapter` là interface ổn định gồm `verify()`, `list_models()` và `complete()`. `OpenRouterAdapter` triển khai interface này, còn `create_provider_adapter()` đóng vai trò registry/factory. Khi thêm provider mới, tạo adapter và settings riêng rồi đăng ký vào factory; code orchestration chỉ phụ thuộc interface chung.

OpenRouter dùng `GET /api/v1/models` để lấy catalog và `POST /api/v1/chat/completions` để gọi model. Adapter gửi API key bằng Bearer token và hỗ trợ các header attribution `HTTP-Referer`, `X-OpenRouter-Title`.

## Lưu cấu hình

Bảng `system.llm_providers` lưu `name`, `kind`, `settings`, API key đã mã hóa, trạng thái bật/tắt và `revision` để cập nhật optimistic concurrency. `settings` của OpenRouter gồm:

- `default_model`: model mặc định, ví dụ `openai/gpt-5.2`.
- `site_url`: URL tùy chọn dùng cho attribution.
- `app_title`: tên ứng dụng gửi cho OpenRouter.

API key là trường write-only trong request tạo/cập nhật provider. System kiểm tra key qua `GET /api/v1/key` của OpenRouter trước khi commit, sau đó mã hóa bằng Fernet và lưu ciphertext vào PostgreSQL. Response chỉ có cờ `has_api_key`, không trả plaintext hoặc ciphertext.

Khóa Fernet được tự tạo tại đường dẫn `PROVIDER_SECRETS_KEY_FILE`. Docker Compose đặt file này trong named volume `agentflow_system_secrets`; cần backup volume cùng database vì mất file khóa sẽ khiến các API key đã lưu không thể giải mã. Khi chạy local, đường dẫn mặc định là `.cache/provider-secrets.key`.

## API quản lý

- `GET /v1/llm-providers`
- `POST /v1/llm-providers`
- `GET /v1/llm-providers/{id}`
- `PUT /v1/llm-providers/{id}`
- `GET /v1/llm-providers/{id}/models`: kiểm tra kết nối và lấy catalog model.

Ví dụ tạo cấu hình:

```json
{
  "name": "OpenRouter chính",
  "kind": "openrouter",
  "api_key": "sk-or-v1-...",
  "settings": {
    "default_model": "openai/gpt-5.2",
    "site_url": "http://localhost:3000",
    "app_title": "AgentFlow"
  }
}
```

Chạy migration `uv run alembic upgrade head` hoặc khởi động lại Docker Compose. Provider cũ được migrate với `has_api_key = false`; nhập key bằng thao tác “Thay API key” trước khi bật hoặc sử dụng lại.
