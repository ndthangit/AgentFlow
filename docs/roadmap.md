# MVP roadmap and operations

Status: proposed plan date **September 5, 2026**, adjusted to start from [agent Deep Agents under `src/agents/default`](../src/agents/default/README.md), support for [add runtime under times](ephemeral-agent-runtime.md). CLI/HTTP, Docker local supervisor path and offline testing are available; Agent job is sustainable, runs multiple hosts, and the rest below are not fully implemented.

Technical backlog by dependency, expected schema/API and acceptance criteria are tracked at [core workflow and Agent Runtime implementation plan](core-runtime-implementation-plan.md).

## 1. MVP results to achieve

Users create a flow using canvas, publish a version, run Codex or OpenCode in their own workspace, view progress, receive artifacts, wait for approval, and perform a connector action. Run must be able to continue after an API/worker restart without silently duplicating external impacts.

The number of weeks has not yet been finalized because there are no human resources and infrastructure requirements. The order below is based on dependencies and risks that need to be verified, not time commitment.

## 2. Implementation milestone

| Mold | Work | Acceptance criteria |
| --- | --- | --- |
| M0a — Sample Agent | Deep Agents/LangGraph, small tool, output schema, CLI/HTTP, real model | Sample code is available; Needs valid provider/model and credential configuration to run |
| M0b — PoC ephemeral agent | The short flow creates a container from the locked image for each attempt and calls the agent via a JSON file | Get correct output/error; Containers are always cleaned; Verify deadlines, redelivery and policy retry before adding another runtime |
| M1 — Workflow core | DSL validator/interpreter; versioning; Postgres; start command/outbox; HTTP/Transform/If | Webhooks create a logical run; branch inactive does not cause the join to hang; restart continue true |
| M2 — Agent runner | Private job queue, lease/fencing, workspace, container supervisor, result manifest, quota and reconciler | Agent edits sample repo; test from snapshot; A dead worker can retrieve saved results or report unknown correctly; Orphaned containers are cleaned up |
| M3 — Editor and Viewer | React Flow, node form, input mapping, publish, run timeline and SSE | Reload browser to see run again; Error node displays cause and related artifact |
| M4 — Approval and connector | Durable approval, GitHub connector, cancellation and reconciliation | Browse the correct snapshot; deny/expiry does not generate PR; ACK loss error does not automatically create a second PR |
| M5 — Internal Demo | OIDC/RBAC, backup, telemetry, failure drills, local running documentation | Install from clean repo; run sample flow; restore backup; check isolation and runbook |

If the agent's interactive permission has not been restored after the server dies, M4 only supports Approval nodes at the task boundary. This limit must be clearly shown in node capabilities and UI.

## 3. Expected source code structure

```text
apps/
  web/                      # Canvas, forms, run viewer
  api/                      # Auth, workflow, run, approval, artifact API
  dispatcher/               # Durable command outbox -> Temporal
  workflow-worker/ # Graph interpreter and projection activities
  connector-worker/ # HTTP/GitHub and connectors
  agent-worker/ # Temporal activity monitors runner jobs
  runner/                   # Adapter host, process supervisor, artifact collection
packages/
  workflow-schema/ # DSL, node schema and validator
  node-registry/ # Node metadata and versions
  agent-contracts/          # Capabilities, result/error/event contracts
  adapter-codex/
  adapter-opencode/
  database/                 # Schema, migrations, outbox/inbox
  observability/
  policy/
infra/
  compose/                  # Local development
  deployment/ # Add production configuration after PoC
docs/
```

This is a tentative tree, without application folders. Dispatcher/projection can be merged into an original worker process; keep module boundaries for later separation. Don't create microservices just because the diagram has many blocks.

## 4. Mandatory tests before internal demo

| Script | Results need to be proven |
| --- | --- |
| Send the same webhook multiple times | One run by delivery ID, consistent response |
| Crash after DB commit, before start Temporal | Dispatcher sends again; trembling without loss |
| Crash after Temporal accept, before outbox ACK | Same workflow ID; do not create a second execution |
| The worker dies after the agent finishes, before the Activity completes | Get the old result manifest; do not call agent again by default |
| Worker dies while agent is running | New worker attach/reconcile; There are no two writers |
| Runner loses volume/session | Show errors or recovery process clearly, do not pretend to resume successfully |
| Event repeats, reverse order, cursor expires | Do not change terminal status to running; UI restored by snapshot |
| Concurrent approval, expiration, API restart | A decision is applied; approval live via restart |
| Artifact changes after approval | Connector rejects old action hash |
| API created PR successfully but client timeout | Check the results before retry |
| Cancel when the shell spawns a child process | Stop the entire process/container tree; cleanup artifact temporarily according to policy |
| Branch true/false, skipped join | Do not run skipped branches and do not deadlock |
| Provider 429, output wrong JSON, budget exhausted | Retry/repair is limited; Report the correct type of error |
| Tenant A guesses tenant B's ID | Cannot read run, stream, credential, snapshot or artifact |
| The repo contains a prompt asking to get the secret | Runner does not have a corresponding secret/control plane access path
| Deploy new interpreter for old run | Replay test does not generate nondeterminism |

Unit testing focuses on graph validation, branch/join, idempotency and policy. Integration testing uses real Temporal/PostgreSQL in the test environment and a fake provider to proactively cause errors. End-to-end with real runtime using small sample repo and separate budget; Do not call the real model in every unit test.

## 5. Deploy locally and to production

Local development plans to use Docker Compose: PostgreSQL, Temporal development service, API, worker and web; artifact local volume. Development server and local configuration are not production defaults. On Windows machines, it is possible to develop editor/API and run Linux runner through a suitable container environment; must check path/permission using the target platform itself.

Initial internal production:

- TLS reverse proxy before API/UI; No public Temporal, database or agent server.
- Persistence Temporal is deployed according to production or managed service guidelines; Save separate application database.
- Runner pool separates the host or security boundary from the API; sandbox-level trusted controller, the agent does not have authority to manage the container host.
- Object storage for artifacts; backup database and artifact metadata, check restore.
- Secret manager/KMS of the infrastructure; OIDC and RBAC project; short-term token for runner.
- Lock image/package versions and have a migration/rollback plan; continues to support the worker version of the open run.

Switch to Kubernetes when you need to schedule multiple machines, private node pools, or HA; Need benchmark before choosing resource requests. Adding Kubernetes does not by itself ensure a secure sandbox for untrusted code.

## 6. Testing metrics and goals

The numbers below are **PoC targets**, have not been benchmarked and are not SLAs:

| Index | Initial goal |
| --- | --- |
| Download for acceptance | 10 simultaneous agent jobs on the runner pool are clearly configured |
| Start API latency | p95 under 500 ms for small payload, only counts durable acceptance |
| Progress towards UI | p95 under 2 seconds in acceptance load, not including provider latency |
| Detect worker losing heartbeat | Under 60 seconds with PoC configuration; Recovery also depends on runtime |
| Input/control payload | Default limit 64 KiB; large file transfer artifact |
| Flow limit | Maximum 100 nodes/run in MVP; private concurrency configuration |

Track queue wait, active runners, heartbeat age, workflow/node duration, retries, errors by provider, outbox lag, projection lag, approval age, artifact bytes and cost estimate vs usage. Separate the time the user waits for approval from the time the machine takes to execute.

Do not use run IDs or session IDs as high-cardinality metric labels; keep them in traces and logs. Record prompt/tool output only when an appropriate retention policy exists and sensitive data is masked. Use OpenTelemetry to link traces from triggers through Activities and adapters: [OpenTelemetry](https://opentelemetry.io/docs/what-is-opentelemetry/).

## 7. Minimum runbook

| Incident | Processing directions |
| --- | --- |
| Temporal stop | The API saves the command if the database is healthy; backlog limit; dispatcher retry on recovery |
| Application database stopped | Do not ACK new trigger; no unsaved approval confirmation; Avoid recording side effects when ledger | is missing
| Provider error/rate limit | Reduce concurrency and backoff by provider; keep deadlines and budget |
| Orphan Runner | Reconciler checks lease/job; fence or stop before giving a new attempt |
| Outbox stuck | Check for errors, schema and permissions; redrive with the same command/event ID |
| Deviation Projection | Load Temporal state and result manifests; Edit read model via reconciler |
| Artifact loss | Mark cannot use output; restore backup or create rerun with clear provenance |
| Error deployment history | Keep old worker version, rollback new worker; Do not edit history directly |

Retention needs to be consistent between history, result manifest, event log and artifact: do not delete snapshots/sessions where the run is open or approval is still needed. The dedup ledger must exist at least as long as the supported retry/redelivery interval. Deleting data by tenant must cover the database, object storage, session store, and log according to the policy.

## 8. After the MVP

Codex/OpenCode adapters are implemented after the sample agent integration path is stable; not a condition to try the first flow.

1. Subflow, bounded loops, Map with concurrency limit and version pinning.
2. Interactive agent approval/steering after demonstrating runtime recovery.
3. Tool gateway/MCP for agents using a permission-managed connector. MCP describes host/client/server and tool/context exchange, not engine; source: [MCP architecture](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture).
4. NATS JetStream when there are independent consumers that need replay/fan-out; Do not add to replace Temporal task queue.
5. Multi-tenant SaaS, enhanced sandbox, quota/billing and expanded audit.
6. Plugin SDK, connector marketplace, template gallery and import workflow have clearly stated compatibility scope.
7. RAG/vector search or LangGraph node when there is a specific use case that needs memory and a self-built agent loop.

## 9. Limitations of the study

Read the official documentation of Codex, OpenCode, Temporal, React Flow, NATS, BullMQ, LangGraph, PostgreSQL, OpenTelemetry, MCP, gVisor and AWS outbox. Links are placed next to relevant information in the document set.

The n8n queue mode page cannot retrieve content using the research tool in this session, so it cannot be used to conclude details of the n8n internal engine. Compare the AI/human review feature using the accessible n8n Gmail node page. The AgentFlow architecture is proposed independently of the product requirements.

Haven't tested CLI/SDK, haven't finalized runtime versions, haven't measured performance or model cost. M0 needs to complete these validations before turning proposals into implementation contracts.
