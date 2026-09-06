# Đặc tả workflow và API dự kiến

Trạng thái: **DSL/API v0.1 đang triển khai từng phần**. FastAPI hiện đã lưu draft/version/run và kiểm tra node ID, edge reference, cycle; worker thực thi node và validator schema đầy đủ chưa được triển khai. Xem [lưu trữ và xử lý workflow](workflow-execution.md).

## 1. Workflow được xuất bản như thế nào?

Editor lưu draft với optimistic revision. Khi publish, server validate graph, schema và quyền truy cập credential, sau đó tạo version bất biến. Một run luôn gắn vào đúng version; việc sửa canvas không đổi run đang chạy.

Định nghĩa thực thi tách khỏi `ui`: tọa độ node, màu và viewport có thể thay đổi mà không đổi kết quả thực thi. Node type và implementation version phải nằm trong execution snapshot.

## 2. Các node trong MVP

| Loại | Input/output | Hành vi |
| --- | --- | --- |
| Manual/Webhook/Schedule Trigger | Payload đã chuẩn hóa | Khởi tạo run sau xác thực/dedup |
| HTTP Request | Request config → status, headers, body | Timeout, credentialRef, response size limit, policy network |
| Transform | JSON → JSON | Mapping giới hạn, không chạy JavaScript tùy ý |
| If/Switch | JSON → port được chọn | Điều kiện deterministic, port `true`/`false` hoặc route cụ thể |
| Agent | Prompt + input refs → JSON + artifact refs | Adapter Codex/OpenCode, timeout/budget/policy |
| Test | Snapshot → passed, exitCode, reportRef | Lệnh từ cấu hình được quản lý, chạy trong sandbox |
| Approval | Action/artifact hash → decision | Chờ bền vững, có reviewer scope và deadline |
| Connector Action | Payload → external resource reference | Operation key, quyền và tác động được khai báo |
| End | JSON kết quả | Kết thúc nhánh; run kết thúc khi không còn công việc khả dụng |

MVP dùng một object JSON cho mỗi invocation, không mặc định tự lặp qua mảng như mọi hệ workflow khác. `Map`, bounded loop và subflow bổ sung sau với iteration ID và giới hạn fan-out rõ ràng.

## 3. Ngữ nghĩa edge, branch và join

Control edge xác định điều kiện kích hoạt. Input mapping xác định dữ liệu truyền vào node; không tự truyền toàn bộ dữ liệu/secret của run.

- DAG trong MVP; phát hiện cycle lúc publish, không cho nối vòng tự do trên canvas.
- Node bình thường chỉ chạy một lần khi các dependency cần thiết thành công và control edge đã kích hoạt.
- Switch chỉ mở port được chọn. Các đường không được chọn được đánh dấu inactive và lan truyền để node không mắc kẹt ở pending.
- Join `all` đợi tất cả nhánh đã kích hoạt thành công; nhánh inactive không phải nhánh lỗi. Tất cả nhánh inactive thì join bị skipped.
- MVP chưa hỗ trợ join `any`/race. Khi thêm cần quy tắc chọn kết quả và hủy nhánh còn lại.
- Input từ nhánh có thể skipped phải được khai báo optional/default; đọc output bắt buộc của nhánh skipped là lỗi validation.
- Node fail làm fail run theo policy mặc định và hủy phần đang chạy. Nếu bật error edge, chỉ error port được kích hoạt, success port inactive; downstream nhận error object có schema.
- Mảng trong JSON là dữ liệu. Fan-out phải thể hiện bằng node riêng để tránh vô tình tạo hàng nghìn agent.

Validator hiện đã kiểm tra duplicate node ID, node không tồn tại trong edge và cycle. Các bước tiếp theo cần kiểm tra port, node unreachable, type/schema version, reference chưa có dependency, thiếu default và budget không hợp lệ. Chỉ chấp nhận node implementation nằm trong registry được quản lý.

## 4. Ví dụ flow sửa lỗi rồi tạo PR

Ví dụ JSON hợp lệ về cú pháp, nhưng là **DSL đề xuất**, chưa thực thi được trong repository. Credential, repo, model và policy references đều là placeholder do project quản lý. Số liệu timeout/budget chỉ minh họa.

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
        "prompt": "Sửa lỗi theo task. Tạo bản thay đổi và tóm tắt kết quả.",
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
        "prompt": "Review snapshot và test report, liệt kê vấn đề còn tồn tại.",
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

`revisionRef` được resolve thành commit SHA cụ thể khi tạo run và lưu vào snapshot thực thi. `artifacts.snapshotRef` do supervisor sinh sau khi thu workspace, không phải model tự đặt. Reviewer và connector phải dùng chính snapshot này; test chạy từ snapshot và không được âm thầm thay input cho review.

Mapping `from` là grammar đường dẫn giới hạn do AgentFlow parse; chưa hỗ trợ gọi hàm hoặc JavaScript expression. Text issue là dữ liệu cho prompt, không có quyền sửa `policyRef` hoặc credential.

`End.outcome` là kết quả nghiệp vụ: `failed` làm run FAILED; `expired` làm run TIMED_OUT; `declined` kết thúc điều phối thành công với outcome declined và không tạo PR. Nếu nhiều End hoạt động, ưu tiên lỗi/timeout; run chỉ terminal sau khi các nhánh còn lại đã kết thúc hoặc được hủy. Node Test trả `passed: false` vẫn có thể thành công về mặt thực thi; sự cố không khởi được test runner là lỗi node.

## 5. Node registry

Mỗi node definition đăng ký `type`, `typeVersion`, config schema, input/output schemas, control ports, UI fields, credential requirements và side-effect class (`pure`, `read`, `workspace-write`, `external-write`).

Executor thuộc worker; UI chỉ nhận metadata phục vụ cấu hình. MVP cho node first-party được đóng gói cùng release. Marketplace/plugin bên thứ ba cần signing, capability scope và runner isolation riêng ở giai đoạn sau.

## 6. API công khai dự kiến

| Endpoint | Ý nghĩa |
| --- | --- |
| `POST /v1/workflows` | Tạo draft |
| `PUT /v1/workflows/{id}/draft` | Lưu draft kèm expected revision; xung đột trả 409 |
| `POST /v1/workflows/{id}/validate` | Trả lỗi graph/schema/quyền trước publish |
| `POST /v1/workflows/{id}/versions` | Publish snapshot bất biến |
| `POST /v1/workflows/{id}/runs` | Bắt đầu từ version ID + input; yêu cầu Idempotency-Key |
| `GET /v1/runs/{runId}` | Snapshot trạng thái, outcome và projection freshness |
| `GET /v1/runs/{runId}/events` | SSE đã phân quyền, có cursor replay |
| `POST /v1/runs/{runId}/cancel` | Ghi lệnh hủy, trả 202 khi đã nhận |
| `POST /v1/approvals/{requestId}/decisions` | Approve/deny kèm action hash và idempotency key |
| `GET /v1/artifacts/{artifactId}/download` | Kiểm tra ACL rồi cấp URL/download |
| `POST /v1/runs/{runId}/reruns` | Tạo run mới có provenance; không sửa run cũ |

Start/cancel/decision trả `commandId` để theo dõi tiếp nhận và áp dụng. Key trùng + cùng request trả kết quả cũ; key trùng + payload khác trả 409. Quyền thực thi và quyền sử dụng credential được kiểm tra ở server lúc publish, start và tại connector, không tin tenantId do client tự gửi.

Webhook route riêng kiểm tra chữ ký/timestamp theo provider, dedup delivery ID, giới hạn payload và trả ACK sau durable acceptance. Schedule có timezone IANA, chính sách chạy bù và chống overlap; MVP có thể dùng scheduler ghi command với scheduled time để dedup, không chỉ cron trong memory API.

Chạy lại từ một node ở giai đoạn sau phải tạo run mới, lấy input từ artifact bất biến và duyệt lại tác động khi cần. Không thể lấy một node bất kỳ trong history rồi chạy lại an toàn nếu không xác minh dependency và side effect.
