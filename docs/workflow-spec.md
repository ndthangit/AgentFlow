# Proposed workflow and API specification

Status: **DSL/API v0.1 is rolling out partially**. FastAPI now saves draft/version/run; The worker executes the DAG including schema, math, LLM Call, Agent, limited Python, `if`/`else` and fan-out in parallel. Durable node-by-node retry and full JSON Schema validator have not been implemented yet. See [workflow storage and execution](workflow-execution.md).

## 1. How workflows are published

The editor saves drafts with optimistic revisions. When publishing, the server validates the graph, schema, and credential access, then creates an immutable version. A run is always attached to one specific version; editing the canvas does not change a run that is already in progress.

Execution definition separate from `ui`: node coordinates, color and viewport can be changed without changing the execution result. Node type and implementation version must be in the execution snapshot.

## 2. Nodes in MVP

| Type | Input/output | Behavior |
| --- | --- | --- |
| Manual/Webhook/Schedule Trigger | Standardized Payload | Initialize run after authentication/dedup |
| HTTP Request | Request config → status, headers, body | Timeout, credentialRef, response size limit, policy network |
| Transform | JSON → JSON | Limited Mapping, does not run arbitrary JavaScript |
| If/Switch | JSON → selected port | Deterministic condition, port `true`/`false` or specific route |
| Parallel | JSON → every child | Pass the same parent output and activate all edges `parallel` | simultaneously
| LLM Call | Prompt + input refs → JSON | Call the model with exactly one completion, without loading skills or running an Agent | loop
| Agent | Prompt + input refs → JSON + artifact refs | Runtime is selected in `src/agents/<runtime-name>`, disposable container, timeout/budget/policy |
| Test | Snapshot → passed, exitCode, reportRef | Commands from managed configuration, run in sandbox |
| Approval | Action/artifact hash → decision | Wait sustainably, have reviewer scope and deadline |
| Connector Action | Payload → external resource reference | Operation key, permissions and effects are declared |
| End | Result JSON | End of branch; run ends when there are no more jobs available |

In the current M0b slice, the Agent node chooses a route using `config.runtime`: Runtime container resolved from allow-list `AGENT_RUNTIME_IMAGES`; graphs cannot pass arbitrary images. Selected tracks `providerId` and `model` have been registered. The Worker decodes the credential at runtime and passes the base URL/API key from the Models into the environment of the correct container attempt; runtime does not get API key from `.env`. Sample workflow and production workflow both refer to the real model. See [Agent runtime integration contract](../src/agents/README.md) and [ephemeral runtime design, limitations](ephemeral-agent-runtime.md).

`config.mcpServerIds` selects enabled remote MCP servers for the Agent node alone. Runtime `agent` supports Streamable HTTP and SSE; runtime `codex`, `opencode` and `openhands` support Streamable HTTP. OpenCode and OpenHands only accept selected MCPs for invocation. Version workflow snapshot slug, transport and URL; The authentication header remains encrypted in the registry and is only decrypted into the container's environment at runtime.

MVP uses a JSON object for each invocation, not by default iterating through the array like every other workflow system. `Map`, bounded loop and subflow added later with iteration ID and explicit fan-out limit.

## 3. Edge, branch and join semantics

The control edge determines the trigger condition. Input mapping determines the data transmitted to the node; Do not manually transmit all run data/secrets.

- DAG in MVP; Detect cycles when publishing, do not allow free loops to be connected on the canvas.
- Node `if` receives `inputs.value`, uses operator deterministic and only opens port `true` or `false`.
- Node `parallel` must have at least two `parallel` port edges; Enough dependent nodes are run concurrently by the scheduler, limited by `settings.maxParallelNodes` (1–32, default 4).
- Node normally runs only once when the necessary dependencies are successful and the control edge is enabled.
- The switch only opens the selected port. Unselected paths are marked inactive and propagated so that the node is not stuck pending.
- Join `all` waits for all branches to be successfully activated; Inactive branch is not an error branch. All inactive branches will be joined and skipped.
- MVP does not support joining `any`/race yet. When adding, a rule is needed to select the result and cancel the remaining branch.
- Input from branches that can be skipped must be declared optional/default; reading the required output of a skipped branch is a validation error.
- Node failure makes the run fail according to the default policy and cancels the running part. If error edge is enabled, only error port is active, success port inactive; downstream receives error object with schema.
- Arrays in JSON are data. Fan-out must be represented by a separate node to avoid accidentally creating thousands of agents.

Validator has now checked for duplicate node ID, node does not exist in edge and cycle. The next steps need to check port, node unreachable, type/schema version, reference without dependency, missing default and invalid budget. Only accept node implementations located in the managed registry.

## 4. Example flow to fix errors and then create PR

The JSON example is syntactically valid, but is a **recommended DSL**, not yet implemented in the repository. Credential, repo, model and policy references are all placeholders managed by the project. Timeout/budget figures are for illustration only.

```json
{
  "schemaVersion": "0.1",
  "id": "fix-issue",
  "version": 1,
  "inputSchema": {
    "type": "object",
    "properties": { "issueText": { "type": "string", "maxLength": 20000 } },
    "required": ["issueText"],
    "additionalProperties": false
  },
  "settings": {
    "maxParallelNodes": 2,
    "runTimeoutSeconds": 172800,
    "budget": { "maxEstimatedCostUsd": 5 },
    "workspace": { "repositoryRef": "repo_backend", "revisionRef": "approved_base" }
  },
  "nodes": [
    { "id": "start", "type": "trigger.manual", "typeVersion": 1 },
    {
      "id": "fix", "type": "agent", "typeVersion": 1,
      "inputs": { "task": { "from": "$input.issueText" } },
      "config": {
        "provider": "codex",
        "modelRef": "model_coding",
        "credentialRef": "credential_codex",
        "policyRef": "policy_workspace_edit",
        "prompt": "Fix errors by task. Create changes and summarize results.",
        "timeoutSeconds": 1800,
        "maxTurns": 8,
        "outputSchema": {
          "type": "object",
          "properties": { "summary": { "type": "string" } },
          "required": ["summary"],
          "additionalProperties": false
        }
      }
    },
    {
      "id": "test", "type": "test", "typeVersion": 1,
      "inputs": { "snapshot": { "from": "$nodes.fix.artifacts.snapshotRef" } },
      "config": { "commandRef": "command_unit_tests", "timeoutSeconds": 600 }
    },
    {
      "id": "test_ok", "type": "if", "typeVersion": 1,
      "inputs": { "value": { "from": "$nodes.test.output.passed" } },
      "config": { "operator": "equals", "expected": true }
    },
    {
      "id": "review", "type": "agent", "typeVersion": 1,
      "inputs": {
        "snapshot": { "from": "$nodes.fix.artifacts.snapshotRef" },
        "testReport": { "from": "$nodes.test.output.reportRef" }
      },
      "config": {
        "provider": "opencode",
        "modelRef": "model_review",
        "credentialRef": "credential_review",
        "policyRef": "policy_review_readonly",
        "prompt": "Review snapshot and test report, listing remaining issues.",
        "timeoutSeconds": 900,
        "maxTurns": 4,
        "outputSchema": {
          "type": "object",
          "properties": { "findings": { "type": "array", "items": { "type": "string" } } },
          "required": ["findings"],
          "additionalProperties": false
        }
      }
    },
    {
      "id": "approval", "type": "approval", "typeVersion": 1,
      "inputs": {
        "snapshot": { "from": "$nodes.fix.artifacts.snapshotRef" },
        "review": { "from": "$nodes.review.output.findings" },
        "testReport": { "from": "$nodes.test.output.reportRef" }
      },
      "config": {
        "reviewerRole": "maintainer",
        "timeoutSeconds": 86400,
        "action": "create_pull_request"
      }
    },
    {
      "id": "pr", "type": "github.createPullRequest", "typeVersion": 1,
      "inputs": {
        "snapshot": { "from": "$nodes.fix.artifacts.snapshotRef" },
        "approvalRef": { "from": "$nodes.approval.output.approvalRef" }
      },
      "config": { "credentialRef": "credential_github", "draft": true }
    },
    { "id": "done", "type": "end", "typeVersion": 1 },
    { "id": "test_failed", "type": "end", "typeVersion": 1, "config": { "outcome": "failed" } },
    { "id": "declined", "type": "end", "typeVersion": 1, "config": { "outcome": "declined" } },
    { "id": "expired", "type": "end", "typeVersion": 1, "config": { "outcome": "expired" } }
  ],
  "edges": [
    { "from": "start", "port": "success", "to": "fix" },
    { "from": "fix", "port": "success", "to": "test" },
    { "from": "test", "port": "success", "to": "test_ok" },
    { "from": "test_ok", "port": "true", "to": "review" },
    { "from": "test_ok", "port": "false", "to": "test_failed" },
    { "from": "review", "port": "success", "to": "approval" },
    { "from": "approval", "port": "approved", "to": "pr" },
    { "from": "approval", "port": "denied", "to": "declined" },
    { "from": "approval", "port": "expired", "to": "expired" },
    { "from": "pr", "port": "success", "to": "done" }
  ]
}
```

`revisionRef` is resolved to the specific commit SHA when creating the run and saved to the execution snapshot. `artifacts.snapshotRef` was generated by the supervisor after collecting the workspace, not a self-set model. Reviewer and connector must use this snapshot; Tests run from snapshots and cannot silently change input for review.

Mapping `from` is the bounded path grammar parsed by AgentFlow; Function calls or JavaScript expressions are not supported yet. Text issue is data for prompt, does not have permission to edit `policyRef` or credential.

`End.outcome` is the business result: `failed` causes run FAILED; `expired` run TIMED_OUT; `declined` successfully completed coordination with outcome declined and did not generate a PR. If multiple Ends are active, error/timeout priority; run terminal only after the remaining branches have finished or been aborted. Node Test returning `passed: false` can still be successful in terms of execution; The problem of not being able to start the test runner is a node error.

## 5. Node registry

Each node definition registers `type`, `typeVersion`, config schema, input/output schemas, control ports, UI fields, credential requirements and side-effect classes (`pure`, `read`, `workspace-write`,

Executor belongs to worker; The UI only receives metadata for configuration purposes. The MVP for the first-party node is packaged with the release. Third-party marketplaces/plugins need their own signing, capability scope, and runner isolation at a later stage.

## 6. Expected public API

| Endpoints | Meaning |
| --- | --- |
| `GET /v1/node-types` | Catalog node/version/port/capability that the current System accepts |
| `POST /v1/workflows` | Create draft |
| `DELETE /v1/workflows/{id}` | Delete workflows belonging to users with the same version, run and related skill links |
| `PUT /v1/workflows/{id}/draft` | Save draft with expected revision; return conflict 409 |
| `POST /v1/workflows/{id}/validate` | Return graph/schema/permission error before publish |
| `POST /v1/workflows/{id}/versions` | Publish immutable snapshots |
| `POST /v1/workflows/{id}/runs` | Start from version ID + input; requires Idempotency-Key |
| `GET /v1/workflows/{id}/runs` | Get the history of workflow runs, newest first |
| `GET /v1/runs/{runId}` | Snapshot of status, outcome and projection freshness |
| `GET /v1/runs/{runId}/steps` | Input, output, error and status of each node in order of execution |
| `GET /v1/runs/{runId}/events` | SSE is decentralized, has cursor replay |
| `POST /v1/runs/{runId}/cancel` | Record cancellation order, pay 202 upon receipt |
| `POST /v1/approvals/{requestId}/decisions` | Approve/deny with action hash and idempotency key |
| `GET /v1/artifacts/{artifactId}/download` | Check the ACL then grant URL/download |
| `POST /v1/runs/{runId}/reruns` | Create a new run with provenance; Don't fix old run |

Start/cancel/decision returns `commandId` to track reception and adoption. Duplicate key + same request returns old results; Duplicate key + different payload returns 409. Execution rights and credential usage rights are checked at the server at publish, start and at the connector, not trusting the tenantId sent by the client itself.

Private webhook route checks signature/timestamp by provider, dedup delivery ID, limits payload and returns ACK after durable acceptance. Schedule has IANA timezone, compensation and anti-overlap policy; MVP can use scheduler to record commands with scheduled time for dedup, not just cron in memory API.

Rerunning from a node at a later stage requires creating a new run, taking input from the immutable artifact, and revising the action as needed. It is impossible to take any node in the history and run it again safely without verifying dependencies and side effects.
