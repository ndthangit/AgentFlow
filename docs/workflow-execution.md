# Lưu trữ và xử lý một workflow

Tài liệu này mô tả đường đi của dữ liệu từ lúc người dùng tạo draft đến lúc một workflow run kết thúc. Phần **hiện có** phản ánh code trong `src/system`; phần **runtime mục tiêu** mô tả worker sẽ được bổ sung tiếp theo.

## 1. Ranh giới service và nơi lưu dữ liệu

Môi trường phát triển dùng một PostgreSQL database `agentflow`, nhưng mỗi service sở hữu schema riêng:

| Thành phần | Nơi lưu | Trách nhiệm |
| --- | --- | --- |
| FastAPI `system` | schema `system` | Workflow draft, version đã publish và trạng thái run |
| Keycloak | schema `keycloak` | Realm, user, client, role, session đăng nhập |
| Agent integration | Không có database riêng | Nhận yêu cầu thực thi agent và trả kết quả có schema |
| Artifact storage, worker history | Chưa triển khai | Log lớn, file, diff, checkpoint và lịch sử thực thi bền vững |

Hai schema dùng chung PostgreSQL server/database để vận hành development đơn giản. Không tạo foreign key giữa `system` và `keycloak`; System chỉ tin danh tính `sub` sau khi xác minh JWT. Việc dùng chung database không cho phép System đọc hoặc sửa bảng nội bộ của Keycloak.

`db-init` trong `compose.yaml` tạo idempotent schema `system` và `keycloak`. Sau đó Keycloak quản lý bảng của schema `keycloak`, còn Alembic của System quản lý bảng và `alembic_version` trong schema `system`.

```mermaid
flowchart LR
    UI[Client / UI] -->|Bearer JWT| API[FastAPI system :8000]
    API -->|SQLAlchemy async| PG[(PostgreSQL agentflow)]
    KC[Keycloak :8080] -->|tables| KCS[keycloak schema]
    API --> SYS[system schema]
    PG --- KCS
    PG --- SYS
    API -. runtime mục tiêu .-> WORKER[Workflow worker]
    WORKER -. HTTP/internal contract .-> AGENT[Agent integration :8001]
```

## 2. Các bảng hiện có

### `system.workflows`

Đây là bản chỉnh sửa hiện tại của workflow.

| Cột | Ý nghĩa |
| --- | --- |
| `id` | UUID ổn định của workflow |
| `owner_subject` | Claim `sub` lấy từ access token Keycloak; dùng để giới hạn truy vấn theo người dùng |
| `name` | Tên workflow |
| `draft` | JSONB chứa node, edge và cấu hình đang chỉnh sửa |
| `revision` | Số revision dùng cho optimistic locking |
| `created_at`, `updated_at` | Thời điểm tạo và cập nhật projection |

Khi lưu draft, client gửi `expected_revision`. Câu lệnh `UPDATE` chỉ thành công nếu revision trong database vẫn bằng giá trị client đã đọc. Hai editor cùng sửa một revision sẽ không âm thầm ghi đè nhau; request đến sau nhận HTTP `409` và phải tải draft mới.

### `system.workflow_versions`

Mỗi hàng là một snapshot đã publish và không được sửa tại chỗ.

| Cột | Ý nghĩa |
| --- | --- |
| `workflow_id`, `version` | Khóa duy nhất của số phiên bản trong một workflow |
| `graph` | Snapshot JSONB dùng cho thực thi |
| `content_hash` | SHA-256 của JSON canonical để nhận diện chính xác nội dung |
| `created_at` | Thời điểm publish |

Run luôn tham chiếu `workflow_version_id`, không tham chiếu draft. Vì vậy việc sửa canvas sau khi start không thay đổi graph của run đang chạy.

### `system.runs`

Đây là projection trạng thái cấp run dành cho API/UI.

| Cột | Ý nghĩa |
| --- | --- |
| `workflow_version_id` | Snapshot workflow được chọn để chạy |
| `owner_subject` | Chủ thể Keycloak tạo run |
| `status` | Hiện tại khởi tạo với `pending` |
| `input` | JSONB input của lần chạy |
| `output` | JSONB kết quả nhỏ; hiện để trống khi mới tạo |
| `created_at`, `updated_at` | Thời gian tạo và cập nhật trạng thái |

`runs` là dữ liệu truy vấn cho UI, không nên trở thành nơi duy nhất quyết định node tiếp theo khi có worker bền vững. Runtime mục tiêu cần thêm node execution, attempt, command và event/outbox.

## 3. Luồng hiện đã chạy được

```mermaid
sequenceDiagram
    participant C as Client
    participant K as Keycloak
    participant A as FastAPI System
    participant P as PostgreSQL/system

    C->>K: Đăng nhập
    K-->>C: Access token
    C->>A: POST /v1/workflows
    A->>A: Xác minh JWT, lấy claim sub
    A->>P: INSERT workflows (draft, revision=1)
    C->>A: PUT /v1/workflows/{id}/draft
    A->>P: UPDATE ... WHERE revision=expected_revision
    C->>A: POST /validate
    A->>A: Kiểm tra node ID, edge và cycle
    C->>A: POST /versions
    A->>P: INSERT immutable graph + SHA-256
    C->>A: POST /runs với version_id + input
    A->>P: INSERT run status=pending
    A-->>C: HTTP 202 + run projection
```

Các bước cụ thể:

1. `POST /v1/workflows` tạo draft thuộc `sub` của token. Client không được tự truyền `owner_subject`.
2. `PUT /v1/workflows/{id}/draft` tăng revision trong cùng câu lệnh cập nhật có điều kiện.
3. `POST /v1/workflows/{id}/validate` hiện kiểm tra cấu trúc `nodes`/`edges`, ID rỗng hoặc trùng, edge tham chiếu node không tồn tại và cycle.
4. `POST /v1/workflows/{id}/versions` chỉ publish graph hợp lệ, đánh số version tiếp theo và lưu hash.
5. `POST /v1/workflows/{id}/runs` xác minh version thuộc đúng workflow, sau đó ghi run `pending` và trả HTTP `202`.
6. `GET /v1/runs/{run_id}` đọc projection theo `run_id` và `owner_subject`.

Ở trạng thái code hiện tại, bước 5 chỉ ghi nhận run bền vững. Chưa có dispatcher/worker lấy run `pending`, nên run chưa tự chuyển sang `running` hoặc `succeeded`.

## 4. Runtime xử lý workflow mục tiêu

Worker tiếp theo nên xử lý theo snapshot, không đọc draft trong lúc chạy:

```mermaid
stateDiagram-v2
    [*] --> PENDING: API commit run
    PENDING --> QUEUED: command/outbox được ghi
    QUEUED --> RUNNING: worker nhận lease
    RUNNING --> WAITING_APPROVAL: gặp approval node
    WAITING_APPROVAL --> RUNNING: nhận decision hợp lệ
    RUNNING --> SUCCEEDED: mọi nhánh hoạt động hoàn tất
    RUNNING --> FAILED: node lỗi không được xử lý
    RUNNING --> TIMED_OUT: vượt deadline
    PENDING --> CANCELLED: hủy trước khi chạy
    RUNNING --> CANCELLED: worker xác nhận đã dừng
```

Một chu kỳ xử lý đề xuất:

1. API tạo `run`, `StartRun command` và outbox event trong **một transaction**. Chỉ trả `202` sau commit.
2. Dispatcher claim outbox bằng lease và gửi run tới workflow worker. Gửi lại phải dùng cùng operation key.
3. Worker tải `workflow_versions.graph`, xác minh `content_hash`, tạo trạng thái execution từ snapshot và input.
4. Graph interpreter tìm node đủ dependency, đánh dấu `READY`, sau đó cấp lease để chạy với giới hạn song song.
5. Executor resolve input mapping từ `$input` và output node trước; secret chỉ được resolve ngay trước lúc gọi connector/agent.
6. Node thuần như transform/if chạy trong worker. Node agent gọi service `agent:8001`; HTTP request, test, approval và connector dùng adapter riêng.
7. Kết quả được kiểm tra output schema trước khi node chuyển `SUCCEEDED`. Output lớn được lưu thành artifact và database chỉ giữ reference/checksum.
8. Worker chọn edge tiếp theo, đánh dấu nhánh không được chọn là `SKIPPED`, rồi tiếp tục cho đến khi không còn node khả dụng.
9. Mỗi lần đổi trạng thái ghi projection và domain event/outbox trong cùng transaction. UI đọc projection và có thể nhận event qua SSE.
10. Khi mọi nhánh kết thúc, worker ghi trạng thái terminal và output tổng hợp của run.

## 5. Dữ liệu cần bổ sung cho worker

Các bảng sau chưa có trong migration hiện tại, nhưng cần cho runtime bền vững:

| Bảng | Nội dung chính |
| --- | --- |
| `node_executions` | Một lần kích hoạt node trong run, input/output reference và trạng thái |
| `node_attempts` | Số attempt, lease generation, thời gian và lỗi chuẩn hóa |
| `commands` | Start/cancel/approval command cùng idempotency key và payload hash |
| `outbox` | Event/command cần dispatch sau khi transaction domain commit |
| `run_events` | Event audit có sequence để SSE replay |
| `agent_sessions` | Provider, session ID, runner/job reference và workspace snapshot |
| `approval_requests` | Action hash, reviewer scope, deadline và decision |
| `artifacts` | Object key, content type, size, checksum và retention |

Các bảng trên vẫn thuộc schema `system` trong môi trường hiện tại. Nếu sau này một service trở thành chủ sở hữu dữ liệu độc lập, có thể chuyển schema/database bằng migration mà không để agent integration truy cập trực tiếp bảng System.

## 6. Transaction, retry và khôi phục

- Tạo run và tạo command phải atomic; không để có run mà worker không bao giờ biết tới.
- Delivery được xem là at-least-once. Mọi node có side effect cần `operation_key` ổn định qua retry.
- Worker chỉ claim một execution bằng lease có hạn. Generation/fencing ngăn worker cũ ghi kết quả sau khi mất lease.
- Lỗi mạng tạm thời có thể retry với backoff. Lỗi auth, policy hoặc schema không retry tự động.
- Sau timeout không rõ kết quả của API bên ngoài, node chuyển `RECONCILIATION_REQUIRED` thay vì gọi lại mù quáng.
- Agent trả text “done” chưa đủ để thành công; System phải nhận response hợp lệ, kiểm tra schema và commit kết quả.
- Khi process restart, worker dựng lại tiến độ từ run/node projection và durable orchestration history; không dựa vào task chỉ nằm trong memory FastAPI.

Thiết kế event/outbox và durable orchestration chi tiết nằm trong [event-driven.md](event-driven.md). Ngữ nghĩa node, branch và join nằm trong [workflow-spec.md](workflow-spec.md). Ranh giới gọi agent nằm trong [agent-integration.md](agent-integration.md).

## 7. Ví dụ dữ liệu qua từng giai đoạn

Draft đang sửa:

```json
{
  "name": "Xử lý yêu cầu hỗ trợ",
  "draft": {
    "nodes": [
      { "id": "start", "type": "trigger.manual" },
      { "id": "classify", "type": "agent" },
      { "id": "done", "type": "end" }
    ],
    "edges": [
      { "from": "start", "to": "classify" },
      { "from": "classify", "to": "done" }
    ]
  }
}
```

Yêu cầu tạo run từ version đã publish:

```json
{
  "version_id": "6f902d28-979b-4c5f-b9dc-94bc7f29dc77",
  "input": {
    "ticket_id": "SUP-1024",
    "message": "Không đăng nhập được"
  }
}
```

Projection hiện tại ngay sau khi API commit:

```json
{
  "id": "0d905356-02e0-48f1-9618-6fc6c53f9685",
  "workflow_version_id": "6f902d28-979b-4c5f-b9dc-94bc7f29dc77",
  "status": "pending",
  "input": {
    "ticket_id": "SUP-1024",
    "message": "Không đăng nhập được"
  },
  "output": null
}
```

Khi worker được triển khai, chính hàng run này sẽ phản ánh trạng thái tổng hợp; chi tiết từng bước nằm trong `node_executions` và `node_attempts`, còn file/log lớn nằm trong artifact storage.
