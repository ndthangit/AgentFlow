# Testing OpenAI, Anthropic, and NVIDIA integrations without real API keys

> Status: deployed adapter, runtime contract, Models UI, provider mock and
> automated testing. Smoke testing with a real key is still optional.

## Target

Extend AgentFlow from current `openrouter` provider to `openai`, `anthropic` and
`nvidia`, and allows developing and running CI that:

- no need for real API key;
- Do not send requests to the Internet;
- still checks authentication headers, paths, request/response and provider errors;
- Do not weaken the secret protection mechanism in production.

Just replacing the real key with a string like `test-key` is **not enough**. The backend now calls the API
provider when loading the catalog, activating the provider and testing the model. If base URL is still URL
Actually, the request still goes out to the Internet and receives `401`. The solution requires replacing the credential
both endpoints with test double.

## Deployment status

- Control plane, API and Models UI accept `openrouter`, `openai`, `anthropic`,
  `nvidia`, `openai-compatible` and `anthropic-compatible`.
- OpenAI and NVIDIA use `OpenAICompatibleAdapter`; Anthropic uses
  `AnthropicAdapter`; OpenRouter keeps the existing adapter and attribution headers.
- API key is encrypted with Fernet, response only returns `has_api_key`.
- Worker inject neutral contract `AGENT_PROVIDER_*`; runtime `default` supports both
  four providers. OpenRouter still accepts the old alias for previous image compatibility.
- `docker/compose.provider-mock.yaml` transfers the three new providers to the local service with
  Endpoint override is only allowed outside of production.
- Unit test uses `httpx.MockTransport`; integration test loads the mock application itself through
  ASGI transport so no need for Docker, Internet or real key.
- Column `kind` is `varchar` and `settings` is JSONB, so changes do not require a database
  migration.

## Proposed architecture

```text
Models UI / API
      |
      | Save real or fake key as ciphertext
      v
Provider registry -----> endpoint resolver
      |                     | production: Fixed URL of the provider
      |                     | test: internal URL of provider-mock
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

There should be three separate layers of testing:

1. **Default unit test:** `httpx.MockTransport`, does not open socket and runs quickly.
2. **Integration test local/CI:** container `provider-mock`, only runs in Docker
   AgentFlow's network.
3. **Smoke test with real API:** optional, run manually or schedule CI when available
   real secret; Not included in the pull request's required test suite.

## Registry and general contract

Replace scattered `if kind == ...` branches with one centralized registry. Registry only
contains metadata, absolutely does not contain API keys.

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

MVP Matrix:

| `kind` | Base URL production | Authentication | Catalog | Completion | Adapters |
| --- | --- | --- | --- | --- | --- |
| `openai` | `https://api.openai.com/v1` | `Authorization: Bearer ...` | `GET /models` | `POST /chat/completions` | OpenAI-compatible |
| `anthropic` | `https://api.anthropic.com` | `x-api-key` and `anthropic-version` | `GET /v1/models` | `POST /v1/messages` | Private Anthropic |
| `nvidia` | `https://integrate.api.nvidia.com/v1` | `Authorization: Bearer ...` | `GET /models` | `POST /chat/completions` | OpenAI-compatible |
| `openrouter` | Current URL | Bearer and attribution headers | current endpoint | `POST /chat/completions` | Stay Compatible |

OpenAI and NVIDIA can share the same `OpenAICompatibleAdapter` substrate. Anthropic needs
separate adapter because the Messages API uses a different envelope, the returned content is content blocks,
and the system prompt is in a separate field instead of a message with role `system`.

The inner contract should still pay `ProviderModel` and `ChatCompletionResult` to share
workflow is provider independent. Adapter is responsible for:

- convert general request to provider's wire format;
- standardize model catalog and usage;
- map `401/403`, `429`, `5xx` to existing exceptions;
- Do not include response body, header or API key in exception public/log.

In MVP, you can continue to use Chat Completions for OpenAI to match the current contract.
Responses API should be a separate capability/adapter at a later stage instead of mixing the two
wire format into the same `complete()` method.

## Manage keys and endpoints

### API key

Maintain the current model:

- user enters key via Models UI/API;
- backend encryption before saving;
- only worker decodes immediately before calling provider or creating disposable container;
- do not set key provider in `docker/.env`, Compose image, workflow JSON, argv,
  telemetry or log;
- UI/API never reads back plaintext.

In tests, use values that are intentionally different from production credentials:

```text
test-openai-key
test-anthropic-key
test-nvidia-key
```

Do not use revoke keys or keys with real prefixes as fixtures. Secret scanner has
can be mistakenly blocked and it will be difficult for the reader to distinguish the fixture from the leaked secret.

### Base test URL for built-in provider

Built-in providers do not receive `base_url` from users. System API and workers
uses a fixed URL, while test double is configured using a trusted environment variable.

Resolver for override only from trusted environment:

```text
ALLOW_PROVIDER_ENDPOINT_OVERRIDES=true
OPENAI_BASE_URL_OVERRIDE=http://provider-mock:8089/openai/v1
ANTHROPIC_BASE_URL_OVERRIDE=http://provider-mock:8089/anthropic
NVIDIA_BASE_URL_OVERRIDE=http://provider-mock:8089/nvidia/v1
```

Required rules:

- default `ALLOW_PROVIDER_ENDPOINT_OVERRIDES=false`;
- `compose.prod.yaml` must refuse to boot if this flag is enabled;
- when flag is off, ignore all `*_BASE_URL_OVERRIDE` variables;
- production URL is in the registry, not taken from the user's request;
- system and workflow-worker must use the same resolver;
- worker transmits resolved URL to container via `AGENT_PROVIDER_BASE_URL`.

The two types according to the protocol are intentional exceptions: the user enters `base_url` to conclude
connect small firms. They only accept public HTTPS by default, block internal hosts/IPs, and check
Check DNS results before calling. Local integration testing can be explicitly enabled
`ALLOW_CUSTOM_PROVIDER_PRIVATE_NETWORKS=true`; production always rejects this flag.

## Fake provider used for integration testing

Service `src/provider-mock` is enabled with Compose override
`docker/compose.provider-mock.yaml`. Service participates in both the `backend` and
`runtime` so that System API and disposable Agent containers can both be called by hostname
`provider-mock`.

The fake server implements the correct set of AgentFlow contracts using:

```text
GET  /openai/v1/models
POST /openai/v1/chat/completions

GET  /anthropic/v1/models
POST /anthropic/v1/messages

GET  /nvidia/v1/models
POST /nvidia/v1/chat/completions
```

Response must be deterministic. OpenAI/NVIDIA example:

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

Anthropic example:

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

The fake server should check the correct header with fixed fake keys:

- OpenAI/NVIDIA: `Authorization: Bearer test-...-key`;
- Anthropic: `x-api-key: test-anthropic-key` and has `anthropic-version`;
- wrong or missing return header `401` with the same envelope as the corresponding provider.

Should support special models/fixtures to suppress errors without changing mock code:

| Model fixture | Results |
| --- | --- |
| `test-chat-model` | `200`, valid response |
| `test-rate-limit` | `429` |
| `test-unavailable` | `503` |
| `test-invalid-envelope` | `200`, body sai schema |
| `test-slow` | delay is larger than timeout test |

Do not publish fake port provider to `0.0.0.0`. If you need to debug from the host, just bind
`127.0.0.1` and set the port in Compose override, not in Compose production.

## Unit testing does not require Docker and does not have a network

Continuing the existing pattern in `test_llm_providers.py`:

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

Each adapter needs minimum testing:

- correct method, URL, auth header and content type;
- do not include keys in body, exception or object view;
- normalize model list;
- serialize system/user/assistant/tool messages in the correct wire format;
- normalize completion and usage;
- mapping `401/403`, `429`, `5xx`, timeout and JSON errors;
- retry only for transient errors, not auth or validation retry;
- closes the HTTP client created by the adapter but does not close the injected client.

Unit tests must inject `MockTransport`. Do not patch DNS and do not call URLs
real provider. You can add network guard to the test suite to fail as soon as the code is created
External connections that do not go through transport are injected.

## Deployed components

### Backend

`src/system/providers/adapters.py`

- has `OpenAICompatibleAdapter` and `AnthropicAdapter`;
- has provider registry/endpoint resolver;
- `LlmProviderCreate.kind` receives `openai`, `anthropic`, `nvidia` and `openrouter`;
- shared model settings for model selection;
- factory receives resolved base URL or HTTP client already has base URL.

`src/system/api/providers.py`

- keep current CRUD API;
- use the corresponding adapter when listing/testing models;
- use neutral error messages;
- do not verify with a common `/key` endpoint: OpenRouter uses `/key`,
  The remaining provider can verify via model catalog.

`src/system/runtime/agent_executor.py`

- removed guard to only accept OpenRouter;
- map all providers to a neutral runtime contract:
  `AGENT_PROVIDER`, `AGENT_MODEL`, `AGENT_PROVIDER_BASE_URL`,
  `AGENT_PROVIDER_API_KEY`;
- only add the old OpenRouter alias during the transition period.

`src/agents/default/src/agent/graph.py`

- Priority contract `AGENT_PROVIDER_*`;
- `openai` and `nvidia` use `ChatOpenAI` with base URL injected;
- `anthropic` uses `ChatAnthropic` with base URL and key injected, no dependencies
  key exists in Compose.

### Web

`src/web/src/shared/types.ts`

- open union `kind`;
- Use general settings for model selection and separate settings if needed by provider.

`src/web/src/features/models/ModelsPage.tsx`

- there are three new provider options;
- get label/logo/description from metadata instead of OpenRouter hard-code in card;
- still requires a non-empty key even for local testing, because the fake server uses a fake key to test
  Look up the end-to-end secret flow.

### Docker and testing

- has `src/provider-mock` and `docker/compose.provider-mock.yaml`;
- unit test adapter is located in `src/system/tests/test_llm_providers.py`;
- integration test without network inside
  `src/system/tests/test_provider_mock_integration.py`.

## Local execution flow

To run stack locally with fake provider:

```powershell
cd docker
docker compose --env-file .env `
  -f compose.yaml `
  -f compose.provider-mock.yaml up --build
```

In the Models UI:

1. choose OpenAI, Anthropic or NVIDIA;
2. Enter one of the conventional fake keys;
3. Open the model catalog and select the corresponding model `test-chat-model` or test model;
4. Click test model.

All requests of the provider management thread must end at `provider-mock`. Maybe
Verify by turning off test job's egress; suite still requires a pass. Mock is now focused
go to Models API, Chat Completions and Messages API; it does not fully simulate every round
tool-calling of a complex Agent.

## CI proposed

Pull request pipeline:

```text
unit-provider-adapters # MockTransport, always running
provider-mock-integration  # ASGI test + Compose config/image build
real-provider-smoke # does not run in pull requests
```

`real-provider-smoke` only runs when the operator actively configures the secret in CI,
Limit your budget and choose a cheap model. This job cannot be a mandatory condition for merging.

Do not use the fallback "if the real key is missing, skip" for unit/integration tests. Two layers
This must always be run with fake keys; Thanks to that, CI detects regression instead of giving a green report
because the entire test is skipped.

## Acceptance criteria

- Create four types of providers: OpenRouter, OpenAI, Anthropic and NVIDIA.
- API key remains write-only, encrypted and does not appear in log/telemetry/argv.
- Unit test of all adapter passes when the device does not have Internet.
- Integration test pass with only `test-*` keys.
- UI models can download fake catalog, activate provider and test completed.
- Agent container receives the correct generic provider environment for each provider.
- `401`, `429`, `503`, timeout and invalid response are mapped to public errors properly
  specified, do not expose the response body or key.
- Production does not allow endpoint override and does not contain service mocks.
- No migration required for existing data and the old OpenRouter provider still works.

## Deployed order

1. Separate the registry, endpoint resolver and general settings but keep OpenRouter running.
2. Add OpenAI-compatible adapter; enable `openai` and `nvidia`.
3. Add Anthropic adapter and Messages API conversion.
4. Switch worker/runtime to neutral `AGENT_PROVIDER_*` variables.
5. Add provider-mock, ASGI integration test and Compose image.
6. Open UI for three new providers.
7. Holding a smoke test with the real key is an optional step after all fake tests pass.

## Official protocol document

- [OpenAI API authentication and API overview](https://developers.openai.com/api/reference/overview)
- [Anthropic API overview, authentication, Models and Messages](https://platform.claude.com/docs/en/api/overview)
- [NVIDIA hosted Chat Completions API](https://docs.api.nvidia.com/nim/reference/stepfun-ai-step-5-preview-infer)
- [NVIDIA NIM OpenAI-compatible API and local endpoint](https://docs.nvidia.com/nim/large-language-models/latest/reference/api-reference.html)
