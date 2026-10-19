# AgentFlow provider mock

Local-only test double for OpenAI, Anthropic and NVIDIA provider contracts. It
accepts only these intentionally fake keys:

- `test-openai-key`
- `test-anthropic-key`
- `test-nvidia-key`

Start it through `docker/compose.provider-mock.yaml`; do not deploy it with the
production Compose stack.
