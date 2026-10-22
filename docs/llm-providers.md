# Managing LLM providers

Provider management supports OpenRouter, OpenAI, Anthropic, NVIDIA, and arbitrary
OpenAI-compatible or Anthropic-compatible providers. The fake-key testing strategy
is documented in
[Provider integration without real API keys](provider-integration-testing.md).

AgentFlow stores per-user provider configurations in `system.llm_providers`. Each
record is an independent connection with a user-defined name, API key, and model
list. A user can therefore create multiple configurations of the same provider type
with different keys.

## Configuration flow

1. The user adds a provider with a configuration name, provider type, and API key. Protocol-compatible providers also require a base URL and may declare model IDs manually. The configuration name must be unique within the current account.
2. The API key is encrypted and the provider is always created as `Inactive`. This step does not call the provider.
3. When the user activates it, AgentFlow requests the model catalog with the stored key and presents the results for selection.
4. The user must select at least one model and may choose a default. If the previous default is no longer selected, the UI uses the first remaining model.
5. The backend reloads the catalog to verify the models before setting the provider to `Active`.
6. Each selected model appears on its own row with a test button. A test sends a short chat completion to that exact model.

## Adapter design

`LlmProviderAdapter` is the stable interface containing `verify()`, `list_models()`,
and `complete()`. `OpenRouterAdapter`, `OpenAICompatibleAdapter`, and
`AnthropicAdapter` implement it, while `create_provider_adapter()` is the
registry/factory. Orchestration code depends only on the shared interface.

OpenRouter uses `GET /api/v1/models` for the catalog and
`POST /api/v1/chat/completions` to invoke or test a model. The adapter sends the API
key as a bearer token and supports the `HTTP-Referer` and `X-OpenRouter-Title`
attribution headers.

OpenAI, NVIDIA, and `openai-compatible` use bearer tokens and the OpenAI-compatible
Chat Completions protocol. Anthropic and `anthropic-compatible` use `x-api-key`,
`anthropic-version`, the Models API, and the Messages API; the adapter moves system
messages into the `system` field and normalizes content blocks into
`ChatCompletionResult`.

## Protocol-compatible providers

Use `openai-compatible` when a vendor provides `POST /chat/completions` and usually
`GET /models`. The base URL should include the version prefix, for example
`https://api.vendor.example/v1`. Use `anthropic-compatible` when a vendor provides
`POST /v1/messages`; the base URL is the part before `/v1`, such as
`https://api.vendor.example`.

If the vendor does not implement a model-list endpoint, enter model IDs manually
when creating the provider. AgentFlow uses that list as a local catalog; the
**Test** button still sends a real completion after activation. If the list is
blank, AgentFlow loads the catalog from the vendor API.

Custom base URLs are protected as follows:

- HTTPS is required by default, and user/password, query, and fragment components are forbidden.
- Localhost, non-public IP addresses, and common internal hostnames are rejected.
- Before a real request, the backend resolves DNS and rejects the request if any address is non-public.
- HTTP/private networks are allowed only in local environments with `ALLOW_CUSTOM_PROVIDER_PRIVATE_NETWORKS=true`; production rejects this setting.
- The HTTP client does not follow redirects automatically, preventing redirects to internal resources.

## Stored data

The `system.llm_providers` table stores the configuration name, provider type,
settings, encrypted API key, status, and a `revision` for optimistic concurrency.
Uniqueness applies to `owner_subject + name`, not `kind`, so multiple configurations
for the same provider are supported. Common settings include:

- `selected_models`: up to 100 model IDs allowed by the user.
- `custom_models`: manually declared catalog for a protocol-compatible provider.
- `base_url`: validated endpoint for a protocol-compatible provider.
- `default_model`: default model; the backend uses the first selected model if the old value is invalid.
- `site_url`: optional attribution URL.
- `app_title`: application name sent to OpenRouter.

API keys are write-only. Responses return only `has_api_key`, never plaintext or
ciphertext. The Fernet key lives at `PROVIDER_SECRETS_KEY_FILE`; Docker Compose
stores it in the `agentflow_system_secrets` volume. Back up this volume together
with the database.

## Management API

- `GET /v1/llm-providers`
- `POST /v1/llm-providers` with `name`, `kind`, and `api_key`; protocol-compatible types also accept `base_url` and `custom_models`
- `GET /v1/llm-providers/{id}`
- `DELETE /v1/llm-providers/{id}` to delete a provider owned by the current user
- `PUT /v1/llm-providers/{id}` to replace the key, select models, or enable/disable it
- `GET /v1/llm-providers/{id}/models` to verify the key and load the catalog, even while inactive
- `POST /v1/llm-providers/{id}/models/test` with `{ "model": "..." }` to test a selected model from an active provider

Example provider creation:

```json
{
  "name": "Company OpenRouter",
  "kind": "openrouter",
  "api_key": "sk-or-v1-..."
}
```

Example of a small OpenAI-compatible provider:

```json
{
  "name": "Team internal vendor",
  "kind": "openai-compatible",
  "api_key": "vendor-key-...",
  "base_url": "https://api.vendor.example/v1",
  "custom_models": ["vendor-chat-small", "vendor-chat-large"]
}
```

Selected models are stored in JSONB `settings`, so this change requires no new
database migration. Existing providers must open “Manage models” and save at least
one model to use the new test flow.
