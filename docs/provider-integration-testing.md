# Tích hợp OpenAI, Anthropic và NVIDIA không cần API key thật khi test

> Trạng thái: đã triển khai adapter, runtime contract, Models UI, provider mock và
> automated test. Smoke test bằng key thật vẫn là tùy chọn.

## Mục tiêu

Mở rộng AgentFlow từ provider `openrouter` hiện tại sang `openai`, `anthropic` và
`nvidia`, đồng thời cho phép phát triển và chạy CI mà:

- không cần API key thật;
- không gửi request ra Internet;
- vẫn kiểm tra được header xác thực, đường dẫn, request/response và lỗi provider;
- không làm yếu cơ chế bảo vệ secret trong production.

Chỉ thay key thật bằng chuỗi như `test-key` là **chưa đủ**. Backend hiện gọi API
provider khi tải catalog, kích hoạt provider và test model. Nếu base URL vẫn là URL
thật thì request vẫn đi ra Internet và nhận `401`. Giải pháp cần thay cả credential
lẫn endpoint bằng test double.

## Trạng thái triển khai

- Control plane, API và Models UI chấp nhận `openrouter`, `openai`, `anthropic` và
  `nvidia`.
- OpenAI và NVIDIA dùng `OpenAICompatibleAdapter`; Anthropic dùng
  `AnthropicAdapter`; OpenRouter giữ adapter và attribution headers hiện có.
- API key được mã hóa bằng Fernet, response chỉ trả `has_api_key`.
- Worker inject contract trung lập `AGENT_PROVIDER_*`; runtime `default` hỗ trợ cả
  bốn provider. OpenRouter vẫn nhận alias cũ để tương thích image trước đây.
- `docker/compose.provider-mock.yaml` chuyển ba provider mới tới service local bằng
  endpoint override chỉ được phép ngoài production.
- Unit test dùng `httpx.MockTransport`; integration test nạp chính ứng dụng mock qua
  ASGI transport nên không cần Docker, Internet hoặc key thật.
- Cột `kind` là `varchar` và `settings` là JSONB, vì vậy thay đổi không cần database
  migration.

## Kiến trúc đề xuất

```text
Models UI / API
      |
      | lưu key thật hoặc key giả dưới dạng ciphertext
      v
Provider registry -----> endpoint resolver
      |                     | production: URL cố định của provider
      |                     | test: URL nội bộ của provider-mock
      v                     v
OpenAI adapter      Anthropic adapter      NVIDIA adapter
      \                    |                    /
       +-------------------+-------------------+
                           |
                    LlmProviderAdapter
                           |
        +------------------+------------------+
        |                                     |
 unit test: httpx.MockTransport    integration: provider-mock
```

Nên có ba lớp kiểm thử riêng:

1. **Unit test mặc định:** `httpx.MockTransport`, không mở socket và chạy nhanh.
2. **Integration test local/CI:** container `provider-mock`, chỉ chạy trong Docker
   network của AgentFlow.
3. **Smoke test với API thật:** tùy chọn, chạy thủ công hoặc scheduled CI khi có
   secret thật; không nằm trong test suite bắt buộc của pull request.

## Registry và contract chung

Thay các nhánh `if kind == ...` rải rác bằng một registry tập trung. Registry chỉ
chứa metadata, tuyệt đối không chứa API key.

```python
@dataclass(frozen=True)
class ProviderSpec:
    kind: str
    api_style: Literal["openai_chat", "anthropic_messages"]
    default_base_url: str
    models_path: str
    completion_path: str
    runtime_provider: str
```

Ma trận MVP:

| `kind` | Base URL production | Xác thực | Catalog | Completion | Adapter |
| --- | --- | --- | --- | --- | --- |
| `openai` | `https://api.openai.com/v1` | `Authorization: Bearer ...` | `GET /models` | `POST /chat/completions` | OpenAI-compatible |
| `anthropic` | `https://api.anthropic.com` | `x-api-key` và `anthropic-version` | `GET /v1/models` | `POST /v1/messages` | Anthropic riêng |
| `nvidia` | `https://integrate.api.nvidia.com/v1` | `Authorization: Bearer ...` | `GET /models` | `POST /chat/completions` | OpenAI-compatible |
| `openrouter` | URL hiện tại | Bearer và attribution headers | endpoint hiện tại | `POST /chat/completions` | Giữ tương thích |

OpenAI và NVIDIA có thể dùng chung lớp nền `OpenAICompatibleAdapter`. Anthropic cần
adapter riêng vì Messages API dùng envelope khác, nội dung trả về là content blocks,
và system prompt nằm ở field riêng thay vì một message có role `system`.

Contract bên trong vẫn nên trả `ProviderModel` và `ChatCompletionResult` để phần
workflow không phụ thuộc provider. Adapter chịu trách nhiệm:

- chuyển request chung sang wire format của provider;
- chuẩn hóa model catalog và usage;
- ánh xạ `401/403`, `429`, `5xx` sang các exception hiện có;
- không đưa response body, header hoặc API key vào exception public/log.

Ở MVP có thể tiếp tục dùng Chat Completions cho OpenAI để khớp contract hiện tại.
Responses API nên là một capability/adapter riêng ở giai đoạn sau thay vì trộn hai
wire format vào cùng một phương thức `complete()`.

## Quản lý key và endpoint

### API key

Giữ nguyên mô hình hiện tại:

- người dùng nhập key qua Models UI/API;
- backend mã hóa trước khi lưu;
- chỉ worker giải mã ngay trước khi gọi provider hoặc tạo disposable container;
- không đặt key provider trong `docker/.env`, Compose image, workflow JSON, argv,
  telemetry hoặc log;
- UI/API không bao giờ đọc lại plaintext.

Trong test dùng các giá trị cố ý không giống credential production:

```text
test-openai-key
test-anthropic-key
test-nvidia-key
```

Không dùng key đã bị revoke hoặc key có prefix thật làm fixture. Secret scanner có
thể chặn nhầm và người đọc khó phân biệt fixture với secret bị lộ.

### Base URL test

Không nên cho người dùng nhập `base_url` tùy ý trong Models UI. System API và worker
có quyền truy cập các service nội bộ; URL tùy ý sẽ tạo rủi ro SSRF.

Resolver chỉ cho override từ môi trường tin cậy:

```text
ALLOW_PROVIDER_ENDPOINT_OVERRIDES=true
OPENAI_BASE_URL_OVERRIDE=http://provider-mock:8089/openai/v1
ANTHROPIC_BASE_URL_OVERRIDE=http://provider-mock:8089/anthropic
NVIDIA_BASE_URL_OVERRIDE=http://provider-mock:8089/nvidia/v1
```

Quy tắc bắt buộc:

- mặc định `ALLOW_PROVIDER_ENDPOINT_OVERRIDES=false`;
- `compose.prod.yaml` phải từ chối khởi động nếu flag này bật;
- khi flag tắt, bỏ qua toàn bộ biến `*_BASE_URL_OVERRIDE`;
- production URL nằm trong registry, không lấy từ request của người dùng;
- system và workflow-worker phải dùng cùng một resolver;
- worker truyền URL đã resolve vào container qua `AGENT_PROVIDER_BASE_URL`.

## Fake provider dùng cho integration test

Service `src/provider-mock` được bật bằng Compose override
`docker/compose.provider-mock.yaml`. Service tham gia cả network `backend` và
`runtime` để System API lẫn disposable Agent container đều gọi được bằng hostname
`provider-mock`.

Fake server triển khai đúng tập contract AgentFlow sử dụng:

```text
GET  /openai/v1/models
POST /openai/v1/chat/completions

GET  /anthropic/v1/models
POST /anthropic/v1/messages

GET  /nvidia/v1/models
POST /nvidia/v1/chat/completions
```

Response phải deterministic. Ví dụ OpenAI/NVIDIA:

```json
{
  "id": "chatcmpl-test-001",
  "model": "test-chat-model",
  "choices": [
    {
      "index": 0,
      "message": { "role": "assistant", "content": "mock response" },
      "finish_reason": "stop"
    }
  ],
  "usage": { "prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5 }
}
```

Ví dụ Anthropic:

```json
{
  "id": "msg_test_001",
  "type": "message",
  "role": "assistant",
  "model": "test-claude-model",
  "content": [{ "type": "text", "text": "mock response" }],
  "stop_reason": "end_turn",
  "usage": { "input_tokens": 3, "output_tokens": 2 }
}
```

Fake server nên kiểm tra đúng header bằng các key giả cố định:

- OpenAI/NVIDIA: `Authorization: Bearer test-...-key`;
- Anthropic: `x-api-key: test-anthropic-key` và có `anthropic-version`;
- sai hoặc thiếu header trả `401` với envelope giống provider tương ứng.

Nên hỗ trợ các model/fixture đặc biệt để ép lỗi mà không cần thay code mock:

| Model fixture | Kết quả |
| --- | --- |
| `test-chat-model` | `200`, response hợp lệ |
| `test-rate-limit` | `429` |
| `test-unavailable` | `503` |
| `test-invalid-envelope` | `200`, body sai schema |
| `test-slow` | delay lớn hơn timeout test |

Không publish port fake provider ra `0.0.0.0`. Nếu cần debug từ host, chỉ bind
`127.0.0.1` và đặt port trong Compose override, không đặt ở Compose production.

## Unit test không cần Docker và không có network

Tiếp tục pattern đang có trong `test_llm_providers.py`:

```python
async def handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/v1/models"
    assert request.headers["Authorization"] == "Bearer test-openai-key"
    return httpx.Response(
        200,
        json={"object": "list", "data": [{"id": "test-chat-model"}]},
    )

client = httpx.AsyncClient(
    base_url="https://unit.test/v1",
    transport=httpx.MockTransport(handler),
)
adapter = create_provider_adapter(
    "openai",
    {"default_model": "test-chat-model"},
    "test-openai-key",
    client=client,
)
```

Mỗi adapter cần test tối thiểu:

- đúng method, URL, auth header và content type;
- không đưa key vào body, exception hoặc object view;
- normalize model list;
- serialize system/user/assistant/tool messages đúng wire format;
- normalize completion và usage;
- ánh xạ `401/403`, `429`, `5xx`, timeout và JSON lỗi;
- retry chỉ với lỗi transient, không retry auth hoặc validation;
- đóng HTTP client do adapter tự tạo nhưng không đóng client được inject.

Các unit test bắt buộc inject `MockTransport`. Không patch DNS và không gọi URL
provider thật. Có thể thêm network guard cho test suite để fail ngay khi code tạo
kết nối ngoài mà không qua transport được inject.

## Các thành phần đã triển khai

### Backend

`src/system/providers/adapters.py`

- có `OpenAICompatibleAdapter` và `AnthropicAdapter`;
- có provider registry/endpoint resolver;
- `LlmProviderCreate.kind` nhận `openai`, `anthropic`, `nvidia` và `openrouter`;
- settings model dùng chung cho model selection;
- factory nhận resolved base URL hoặc HTTP client đã có base URL.

`src/system/api/providers.py`

- giữ API CRUD hiện tại;
- dùng adapter tương ứng khi list/test model;
- dùng message lỗi trung lập;
- không verify bằng một endpoint `/key` chung: OpenRouter dùng `/key`, các
  provider còn lại có thể verify qua model catalog.

`src/system/runtime/agent_executor.py`

- đã bỏ guard chỉ chấp nhận OpenRouter;
- map mọi provider sang contract runtime trung lập:
  `AGENT_PROVIDER`, `AGENT_MODEL`, `AGENT_PROVIDER_BASE_URL`,
  `AGENT_PROVIDER_API_KEY`;
- chỉ thêm alias cũ của OpenRouter trong giai đoạn chuyển tiếp.

`src/agents/default/src/agent/graph.py`

- ưu tiên contract `AGENT_PROVIDER_*`;
- `openai` và `nvidia` dùng `ChatOpenAI` với base URL được inject;
- `anthropic` dùng `ChatAnthropic` với base URL và key được inject, không phụ thuộc
  key tồn tại sẵn trong Compose.

### Web

`src/web/src/shared/types.ts`

- mở union `kind`;
- dùng settings chung cho model selection và settings riêng nếu provider cần.

`src/web/src/features/models/ModelsPage.tsx`

- có ba lựa chọn provider mới;
- lấy label/logo/mô tả từ metadata thay vì hard-code OpenRouter trong card;
- vẫn yêu cầu key không rỗng kể cả local test, vì fake server dùng key giả để kiểm
  tra luồng secret end-to-end.

### Docker và test

- có `src/provider-mock` và `docker/compose.provider-mock.yaml`;
- unit test adapter nằm trong `src/system/tests/test_llm_providers.py`;
- integration test không network nằm trong
  `src/system/tests/test_provider_mock_integration.py`.

## Luồng chạy local

Để chạy stack local với fake provider:

```powershell
cd docker
docker compose --env-file .env `
  -f compose.yaml `
  -f compose.provider-mock.yaml up --build
```

Trong Models UI:

1. chọn OpenAI, Anthropic hoặc NVIDIA;
2. nhập một trong các key giả đã quy ước;
3. mở model catalog và chọn model `test-chat-model` hoặc model test tương ứng;
4. bấm test model.

Toàn bộ request của luồng quản lý provider phải kết thúc tại `provider-mock`. Có thể
xác minh bằng cách tắt egress của test job; suite vẫn phải pass. Mock hiện tập trung
vào Models API, Chat Completions và Messages API; nó không mô phỏng đầy đủ mọi vòng
tool-calling của một Agent phức tạp.

## CI đề xuất

Pull request pipeline:

```text
unit-provider-adapters     # MockTransport, luôn chạy
provider-mock-integration  # ASGI test + Compose config/image build
real-provider-smoke        # không chạy trong pull request
```

`real-provider-smoke` chỉ chạy khi người vận hành chủ động cấu hình secret trong CI,
giới hạn budget và chọn model rẻ. Job này không được là điều kiện bắt buộc để merge.

Không dùng fallback “nếu thiếu key thật thì skip” cho unit/integration test. Hai lớp
này phải luôn chạy với key giả; nhờ vậy CI mới phát hiện regression thay vì báo xanh
do toàn bộ test bị skip.

## Tiêu chí nghiệm thu

- Tạo được bốn loại provider: OpenRouter, OpenAI, Anthropic và NVIDIA.
- API key vẫn write-only, được mã hóa và không xuất hiện trong log/telemetry/argv.
- Unit test của mọi adapter pass khi máy không có Internet.
- Integration test pass chỉ với các key `test-*`.
- Models UI tải được fake catalog, kích hoạt provider và test completion.
- Agent container nhận đúng generic provider environment cho từng provider.
- `401`, `429`, `503`, timeout và invalid response được ánh xạ sang lỗi public ổn
  định, không lộ response body hay key.
- Production không cho phép endpoint override và không chứa service mock.
- Không cần migration cho dữ liệu hiện tại và provider OpenRouter cũ vẫn hoạt động.

## Thứ tự đã triển khai

1. Tách registry, endpoint resolver và settings chung nhưng giữ OpenRouter chạy.
2. Thêm OpenAI-compatible adapter; bật `openai` và `nvidia`.
3. Thêm Anthropic adapter và chuyển đổi Messages API.
4. Chuyển worker/runtime sang các biến `AGENT_PROVIDER_*` trung lập.
5. Thêm provider-mock, ASGI integration test và Compose image.
6. Mở UI cho ba provider mới.
7. Giữ smoke test bằng key thật là bước tùy chọn sau khi toàn bộ test giả pass.

## Tài liệu giao thức chính thức

- [OpenAI API authentication và API overview](https://developers.openai.com/api/reference/overview)
- [Anthropic API overview, authentication, Models và Messages](https://platform.claude.com/docs/en/api/overview)
- [NVIDIA hosted Chat Completions API](https://docs.api.nvidia.com/nim/reference/stepfun-ai-step-5-preview-infer)
- [NVIDIA NIM OpenAI-compatible API và local endpoint](https://docs.nvidia.com/nim/large-language-models/latest/reference/api-reference.html)
