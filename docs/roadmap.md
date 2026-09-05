# Lộ trình MVP và vận hành

Trạng thái: kế hoạch đề xuất ngày **05/09/2026**. Repo hiện có tài liệu thiết kế; các thành phần dưới đây **chưa được triển khai**.

## 1. Kết quả MVP cần đạt

Người dùng tạo flow bằng canvas, publish một version, chạy Codex hoặc OpenCode trong workspace riêng, xem tiến độ, nhận artifact, chờ duyệt và thực hiện một connector action. Run phải có thể tiếp tục sau khi API/worker restart mà không âm thầm nhân đôi tác động bên ngoài.

Chưa chốt số tuần vì chưa có nhân lực và yêu cầu hạ tầng. Thứ tự dưới đây theo dependency và rủi ro cần kiểm chứng, không phải cam kết thời gian.

## 2. Mốc triển khai

| Mốc | Công việc | Tiêu chí nghiệm thu |
| --- | --- | --- |
| M0 — PoC engine và runtime | Temporal flow ngắn; Codex/OpenCode headless; artifact; khóa phiên bản | Cả hai runtime trả output chuẩn; xác minh cancel, session persistence và lỗi mất kết nối |
| M1 — Lõi workflow | DSL validator/interpreter; versioning; Postgres; start command/outbox; HTTP/Transform/If | Webhook trùng tạo một logical run; branch inactive không làm join treo; restart tiếp tục đúng |
| M2 — Agent runner | Adapter contract, workspace, process supervisor, result manifest, quota | Agent sửa repo mẫu; test từ snapshot; worker chết có thể lấy kết quả đã lưu hoặc báo unknown đúng |
| M3 — Editor và quan sát | React Flow, node form, input mapping, publish, run timeline và SSE | Reload trình duyệt xem lại run; node lỗi hiển thị nguyên nhân và artifact liên quan |
| M4 — Approval và connector | Durable approval, GitHub connector, cancellation và đối soát | Duyệt đúng snapshot; deny/expiry không tạo PR; lỗi mất ACK không tự tạo PR thứ hai |
| M5 — Bản thử nội bộ | OIDC/RBAC, backup, telemetry, failure drills, tài liệu chạy local | Cài từ repo sạch; chạy flow mẫu; restore backup; kiểm tra isolation và runbook |

Nếu interactive permission của agent chưa phục hồi được sau server chết, M4 chỉ hỗ trợ Approval node ở ranh giới tác vụ. Giới hạn này phải thể hiện rõ trong node capabilities và UI.

## 3. Cấu trúc mã nguồn dự kiến

```text
apps/
  web/                      # Canvas, forms, run viewer
  api/                      # Auth, workflow, run, approval, artifact API
  dispatcher/               # Durable command outbox -> Temporal
  workflow-worker/          # Graph interpreter và projection activities
  connector-worker/         # HTTP/GitHub và các connector
  agent-worker/             # Temporal activity giám sát runner job
  runner/                   # Adapter host, process supervisor, artifact collection
packages/
  workflow-schema/          # DSL, node schema và validator
  node-registry/            # Node metadata và versions
  agent-contracts/          # Capabilities, result/error/event contracts
  adapter-codex/
  adapter-opencode/
  database/                 # Schema, migrations, outbox/inbox
  observability/
  policy/
infra/
  compose/                  # Local development
  deployment/               # Thêm cấu hình production sau PoC
docs/
```

Đây là cây dự kiến, chưa có các thư mục ứng dụng. Có thể gộp dispatcher/projection vào một worker process ban đầu; giữ ranh giới module để tách sau. Không tạo microservice chỉ vì sơ đồ có nhiều khối.

## 4. Các thử nghiệm bắt buộc trước bản thử nội bộ

| Kịch bản | Kết quả cần chứng minh |
| --- | --- |
| Gửi cùng webhook nhiều lần | Một run theo delivery ID, response nhất quán |
| Crash sau DB commit, trước start Temporal | Dispatcher gửi lại; run không mất |
| Crash sau Temporal accept, trước outbox ACK | Cùng workflow ID; không tạo execution thứ hai |
| Worker chết sau agent xong, trước Activity completion | Lấy result manifest cũ; không gọi agent lại mặc định |
| Worker chết khi agent còn chạy | Worker mới attach/reconcile; không có hai writer |
| Runner mất volume/session | Hiển thị lỗi hoặc quy trình recovery rõ, không giả vờ resume thành công |
| Event lặp, đảo thứ tự, cursor hết hạn | Không đổi terminal status về running; UI phục hồi bằng snapshot |
| Approval đồng thời, hết hạn, API restart | Một decision được áp dụng; approval sống qua restart |
| Artifact thay đổi sau approval | Connector từ chối action hash cũ |
| API tạo PR thành công nhưng client timeout | Đối soát kết quả trước retry |
| Cancel khi shell sinh process con | Dừng cả cây process/container; cleanup artifact tạm theo policy |
| Nhánh true/false, skipped join | Không chạy nhánh bị bỏ qua và không deadlock |
| Provider 429, output sai JSON, budget cạn | Retry/repair có giới hạn; thông báo đúng loại lỗi |
| Tenant A đoán ID của tenant B | Không đọc được run, stream, credential, snapshot hoặc artifact |
| Repo chứa prompt yêu cầu lấy secret | Runner không có đường quyền truy cập secret/control plane tương ứng |
| Deploy interpreter mới cho run cũ | Replay test không phát sinh nondeterminism |

Unit test tập trung vào graph validation, branch/join, idempotency và policy. Integration test dùng Temporal/PostgreSQL thực trong môi trường test và fake provider để chủ động gây lỗi. End-to-end với runtime thật dùng repo mẫu nhỏ và budget riêng; không gọi model thật trong mọi unit test.

## 5. Triển khai local và production

Local development dự kiến dùng Docker Compose: PostgreSQL, Temporal development service, API, worker và web; artifact local volume. Development server và cấu hình local không phải mặc định production. Trên máy Windows có thể phát triển editor/API và chạy runner Linux qua môi trường container phù hợp; phải kiểm tra path/permission bằng chính nền tảng mục tiêu.

Production nội bộ ban đầu:

- TLS reverse proxy trước API/UI; không public Temporal, database hoặc agent server.
- Persistence Temporal được triển khai theo hướng dẫn production hoặc dịch vụ managed; lưu application database riêng.
- Runner pool tách host hoặc security boundary khỏi API; controller tin cậy cấp sandbox, agent không có quyền quản lý container host.
- Object storage cho artifact; backup database và artifact metadata, kiểm tra restore.
- Secret manager/KMS của hạ tầng; OIDC và RBAC project; token ngắn hạn cho runner.
- Khóa image/package versions và có migration/rollback plan; tiếp tục hỗ trợ worker version của run đang mở.

Chuyển sang Kubernetes khi cần scheduling runner nhiều máy, node pool riêng hoặc HA; cần benchmark trước khi chọn resource requests. Việc thêm Kubernetes không tự bảo đảm sandbox an toàn cho mã không tin cậy.

## 6. Chỉ số và mục tiêu thử nghiệm

Các số dưới đây là **mục tiêu PoC**, chưa được benchmark và không phải SLA:

| Chỉ số | Mục tiêu ban đầu |
| --- | --- |
| Tải nghiệm thu | 10 agent jobs đồng thời trên runner pool được ghi rõ cấu hình |
| Start API latency | p95 dưới 500 ms cho payload nhỏ, chỉ tính durable acceptance |
| Tiến độ tới UI | p95 dưới 2 giây trong tải nghiệm thu, không gồm độ trễ provider |
| Phát hiện worker mất heartbeat | Dưới 60 giây với cấu hình PoC; recovery còn phụ thuộc runtime |
| Input/control payload | Giới hạn mặc định 64 KiB; file lớn chuyển artifact |
| Giới hạn flow | Tối đa 100 nodes/run trong MVP; cấu hình concurrency riêng |

Theo dõi queue wait, active runners, heartbeat age, workflow/node duration, retries, errors theo provider, outbox lag, projection lag, approval age, artifact bytes và cost estimate so với usage. Tách thời gian người dùng chờ duyệt khỏi thời gian máy thực thi.

Không dùng run ID/session ID làm metric label có cardinality cao; giữ chúng trong trace/log. Chỉ ghi prompt/tool output khi có policy retention phù hợp và đã che dữ liệu nhạy cảm. Sử dụng OpenTelemetry để liên kết trace từ trigger qua Activity và adapter: [OpenTelemetry](https://opentelemetry.io/docs/what-is-opentelemetry/).

## 7. Runbook tối thiểu

| Sự cố | Hướng xử lý |
| --- | --- |
| Temporal ngừng | API lưu command nếu DB còn khỏe; giới hạn backlog; dispatcher retry khi phục hồi |
| Database ứng dụng ngừng | Không ACK trigger mới; không xác nhận approval chưa lưu; tránh ghi side effect khi thiếu ledger |
| Provider lỗi/rate limit | Giảm concurrency, backoff theo provider; giữ deadline và budget |
| Runner mồ côi | Reconciler kiểm tra lease/job; fence hoặc dừng trước khi cho attempt mới |
| Outbox bị kẹt | Kiểm tra lỗi, schema và quyền; redrive với cùng command/event ID |
| Projection lệch | Nạp trạng thái Temporal và result manifests; sửa read model qua reconciler |
| Artifact mất | Đánh dấu không thể dùng output; phục hồi backup hoặc tạo rerun rõ provenance |
| Bản deploy lỗi history | Giữ worker phiên bản cũ, rollback worker mới; không sửa history trực tiếp |

Retention cần thống nhất giữa history, result manifest, event log và artifact: không xóa snapshot/session mà run đang mở hoặc approval còn cần. Dedup ledger phải tồn tại ít nhất bằng khoảng retry/redelivery được hỗ trợ. Xóa dữ liệu theo tenant phải bao phủ cả database, object storage, session store và log theo chính sách.

## 8. Sau MVP

1. Subflow, bounded loops, Map với concurrency limit và version pinning.
2. Interactive agent approval/steering sau khi chứng minh recovery theo runtime.
3. Tool gateway/MCP cho agent dùng connector đã quản lý quyền. MCP mô tả host/client/server và trao đổi tool/context, không thay engine; nguồn: [MCP architecture](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture).
4. NATS JetStream khi có consumer độc lập cần replay/fan-out; không thêm để thay Temporal task queue.
5. Multi-tenant SaaS, sandbox tăng cường, quota/billing và audit mở rộng.
6. Plugin SDK, connector marketplace, template gallery và workflow import có phạm vi tương thích công bố rõ.
7. RAG/vector search hoặc LangGraph node khi có use case cụ thể cần memory và agent loop tự xây.

## 9. Giới hạn của đợt nghiên cứu

Đã đọc tài liệu chính thức của Codex, OpenCode, Temporal, React Flow, NATS, BullMQ, LangGraph, PostgreSQL, OpenTelemetry, MCP, gVisor và AWS outbox. Các link đặt cạnh thông tin liên quan trong bộ tài liệu.

Trang n8n queue mode không lấy được nội dung bằng công cụ nghiên cứu ở phiên này, nên không dùng để kết luận chi tiết engine nội bộ n8n. Đối chiếu tính năng AI/human review sử dụng trang Gmail node của n8n truy cập được. Kiến trúc AgentFlow là đề xuất độc lập từ yêu cầu sản phẩm.

Chưa chạy thử CLI/SDK, chưa chốt runtime versions, chưa đo hiệu năng hay chi phí model. M0 cần hoàn thành các kiểm chứng này trước khi biến các đề xuất thành hợp đồng triển khai.
