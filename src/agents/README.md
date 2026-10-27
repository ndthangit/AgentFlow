# Agent runtimes

Each direct child directory under `src/agents` is an independent Agent runtime
implementation. The project includes the [`default`](default/README.md),
[`codex`](codex/README.md), [`opencode`](opencode/README.md), and
[`openhands`](openhands/README.md) runtimes.

```text
src/agents/
├── default/          # Bundled Deep Agents/LangGraph runtime
├── codex/            # OpenAI Codex CLI one-shot runtime
├── opencode/         # OpenCode SDK/server one-shot runtime
├── openhands/        # OpenHands Software Agent SDK one-shot runtime
├── my-runtime/       # User-defined custom runtime
└── README.md         # Integration contract
```

## Build the runtime catalog for Docker Compose

Compose builds the `default`, `codex`, `opencode`, and `openhands` runtimes and
registers the four images in the workflow worker's `AGENT_RUNTIME_IMAGES` setting:

```powershell
docker compose --env-file docker/.env -f docker/compose.yaml build agent-runtime-default-image agent-runtime-codex-image agent-runtime-opencode-image agent-runtime-openhands-image
docker compose --env-file docker/.env -f docker/compose.yaml up -d workflow-worker
```

Workflows select a runtime with `config.runtime`; `agent` is an alias for
`AGENT_RUNTIME_DEFAULT`. To add `my-runtime`, add a service that builds or pulls the
image and a corresponding `AGENT_RUNTIME_IMAGES` entry. The worker creates a fresh,
single-use container for every Agent node attempt.

## Minimum contract for a new runtime

A new runtime directory must:

1. Have a `Dockerfile` that builds from that directory.
2. Provide an `agent` executable on the image's `PATH`.
3. Read exactly one UTF-8 JSON request from `stdin` when `agent` runs without arguments.
4. Write exactly one JSON result to `stdout`; diagnostic logs must go to `stderr`.
5. Never write credentials, raw provider responses, or secrets to output or logs.
6. Run with a read-only filesystem, an unprivileged user, and supervisor-enforced CPU, RAM, PID, and network limits.

The current request has `task`, `context`, `skills`, and `output_schema` fields. On
success, the runtime returns:

```json
{
  "run_id": "runtime-generated-id",
  "status": "succeeded",
  "mode": "live",
  "output": {}
}
```

On failure, the runtime should return
`{ "error": { "code": "...", "message": "...", "detail": "..." } }` and a nonzero
exit code. `message` is a stable description; `detail` is the root cause, limited to
500 characters, with API keys, tokens, passwords, and authentication data removed.
You can copy [`default`](default/README.md) as a starting point, but do not commit
`.env`, `.venv`, cache, or credential files in a new runtime.
