# Kế hoạch tích hợp Agent Workspace và tải file đầu ra

## Mục tiêu

Cho phép mỗi Agent trong một workflow tạo file trong workspace riêng. Khi workflow
kết thúc, hệ thống giữ lại workspace, đóng gói thành file ZIP và cho phép chủ sở hữu
run tải về từ lịch sử chạy.

Agent container vẫn là container dùng một lần. Hệ thống chỉ giữ dữ liệu workspace,
không giữ container sau khi node hoàn thành.

## Phạm vi phiên bản đầu tiên

- Mỗi workflow run có một workspace riêng.
- Mỗi Agent node ghi file vào thư mục riêng trong workspace của run.
- Các node trong cùng run có thể đọc file của những node đã chạy trước.
- Workspace được đóng gói sau khi run chuyển sang trạng thái kết thúc.
- Người dùng tải một file ZIP từ màn hình Run history.
- Chỉ chủ sở hữu run được phép xem metadata và tải workspace.
- Workspace có giới hạn dung lượng, số file và thời gian lưu giữ.

Không đưa file nhị phân vào JSON output của node. Agent vẫn phải trả structured
JSON theo output schema hiện tại.

## Luồng tổng thể

```text
Tạo workflow run
       |
       v
Tạo workspace theo run-id
       |
       v
Mount workspace vào từng Agent container
       |
       v
Agent ghi file vào thư mục node của mình
       |
       v
Container bị xóa sau khi node hoàn thành
       |
       v
Workflow kết thúc -> kiểm tra file -> tạo manifest và ZIP
       |
       v
Lưu metadata vào FlowRun
       |
       v
Người dùng tải ZIP từ Run history
```

## Cấu trúc workspace

```text
runs/<run-id>/
|-- nodes/
|   |-- agent_writer/
|   `-- agent_reviewer/
|-- shared/
`-- manifest.json
```

- `/workspace/nodes/<node-id>` là thư mục làm việc mặc định của Agent node.
- `/workspace/shared` dành cho file cần dùng chung giữa nhiều Agent.
- Các Agent chạy song song ghi vào thư mục node khác nhau để tránh xung đột.
- `node-id` đã được workflow validation giới hạn, nhưng supervisor vẫn phải kiểm
  tra lại trước khi dùng trong đường dẫn.

## Thiết kế lưu trữ

Phiên bản đầu sử dụng Docker named volume cố định, ví dụ
`agentflow-workspaces`.

- `workflow-worker` mount volume read-write tại
  `/var/lib/agentflow/workspaces`.
- `system` mount cùng volume read-only để phục vụ download.
- Agent container chỉ mount subpath của run hiện tại vào `/workspace`.
- Root filesystem của Agent container tiếp tục ở chế độ read-only.
- Không mount Docker socket, provider secrets hoặc thư mục host khác vào Agent
  workspace.

Đường dẫn lưu trữ không chứa `owner_subject`; run UUID là khóa không đoán được và
API luôn kiểm tra quyền sở hữu trong PostgreSQL.

## Các giai đoạn triển khai

### 1. Workspace lifecycle

Tạo module quản lý workspace trong `src/system/runtime/` với các trách nhiệm:

- Tạo `runs/<run-id>/nodes/<node-id>` và `shared` trước khi chạy Agent.
- Đặt owner thành UID/GID `10001:10001` để Agent container ghi được file.
- Xóa riêng thư mục node trước một lần retry để không dùng nhầm output dở.
- Không xóa dữ liệu của các node khác trong cùng run.
- Tạo archive qua file tạm rồi atomic rename để tránh file ZIP nửa chừng.

`RuntimeExecutionContext` hiện đã cung cấp `run_id` và `node_id`; nên tái sử dụng
thay vì truyền thêm các chuỗi đường dẫn qua nhiều tầng.

### 2. Mount workspace vào Agent container

Cập nhật `build_agent_docker_command()` để thêm mount dạng volume subpath:

```text
type=volume,
src=agentflow-workspaces,
dst=/workspace,
volume-subpath=runs/<run-id>
```

Container nhận thêm:

```text
AGENTFLOW_WORKSPACE=/workspace
AGENTFLOW_NODE_WORKSPACE=/workspace/nodes/<node-id>
```

Working directory của runtime được đặt thành thư mục node hiện tại. Supervisor
không nhận đường dẫn tùy ý từ request của người dùng.

### 3. Cấp quyền file cho từng runtime

#### Default Agent

- Thay virtual `StateBackend` bằng filesystem backend có root tại workspace.
- Chỉ cung cấp công cụ đọc, ghi, sửa và liệt kê file bên trong workspace.
- Cập nhật system prompt để Agent lưu file vào
  `AGENTFLOW_NODE_WORKSPACE` và vẫn trả structured JSON.

#### Codex

- Đổi Codex sandbox từ `read-only` sang `workspace-write`.
- Giữ `approval_policy="never"` và root filesystem Docker read-only.
- Đặt `cwd` thành thư mục workspace của node.

#### OpenCode

- Cho phép đúng các file tools cần thiết trong `/workspace`.
- Tiếp tục deny các công cụ shell hoặc quyền ngoài workspace nếu không cần cho
  yêu cầu tạo file.
- Đặt working directory/session directory theo node workspace.

#### OpenHands

- Không dùng `TemporaryDirectory` nội bộ cho output cần giữ lại.
- Dùng node workspace đã được mount làm workspace của `Conversation`.
- Chỉ bật file editor tools cần thiết cùng `FinishTool` hiện có.

### 4. Đóng gói và manifest

Khi run kết thúc, worker quét workspace và tạo `manifest.json` gồm:

```json
{
  "run_id": "uuid",
  "file_count": 2,
  "total_bytes": 12345,
  "files": [
    {
      "path": "nodes/agent_writer/report.md",
      "size": 12000,
      "sha256": "..."
    }
  ]
}
```

Sau đó worker tạo archive:

```text
archives/<run-id>/workspace.zip
```

Nên đóng gói cả workspace một phần cho run `failed` hoặc `cancelled` nếu đã có
file. Trạng thái workflow và trạng thái workspace được lưu độc lập; lỗi đóng gói
không được âm thầm đổi một workflow thành công thành thất bại.

### 5. Database migration

Thêm các trường sau vào `system.runs`:

```text
workspace_status        none | active | ready | failed | expired
workspace_size_bytes    bigint nullable
workspace_file_count    integer nullable
workspace_sha256        varchar(64) nullable
workspace_archive_key   varchar(512) nullable
workspace_expires_at    timestamptz nullable
```

`workspace_archive_key` là dữ liệu nội bộ và không được trả thẳng về browser.
`FlowRunView` chỉ trả metadata an toàn và cờ cho biết có thể download hay không.

### 6. API download

Thêm endpoint:

```http
GET /v1/runs/{run_id}/workspace
```

Endpoint phải:

1. Tìm `FlowRun` theo cả `run_id` và `owner_subject`.
2. Chỉ cho tải khi `workspace_status == ready`.
3. Không nhận tên file hoặc đường dẫn từ query string.
4. Resolve archive từ khóa nội bộ đã được kiểm tra.
5. Stream bằng `FileResponse` với content type `application/zip`.
6. Dùng tên download cố định, ví dụ
   `agentflow-run-8f24a910-workspace.zip`.

Nếu run không tồn tại, không thuộc người dùng hoặc workspace đã hết hạn, trả `404`
để không làm lộ sự tồn tại của tài nguyên.

### 7. Giao diện người dùng

Trong Run history:

- Hiển thị trạng thái workspace: preparing, ready, empty, failed hoặc expired.
- Hiển thị số file và dung lượng tổng.
- Thêm nút **Download workspace** khi archive sẵn sàng.
- Disable nút trong lúc workflow còn chạy hoặc workspace đang đóng gói.
- Hiển thị thông báo rõ ràng nếu đóng gói thất bại hoặc workspace đã hết hạn.

API client cần một hàm download riêng vì helper JSON hiện tại luôn đặt
`Content-Type: application/json` và parse response thành JSON.

### 8. Retention và dọn dẹp

Thêm cấu hình:

```text
AGENT_WORKSPACE_VOLUME=agentflow-workspaces
WORKSPACE_ROOT=/var/lib/agentflow/workspaces
WORKSPACE_MAX_TOTAL_BYTES=104857600
WORKSPACE_MAX_FILE_BYTES=26214400
WORKSPACE_MAX_FILES=1000
WORKSPACE_RETENTION_DAYS=7
```

Recovery/cleanup định kỳ sẽ:

- Xóa archive và workspace đã hết hạn.
- Xóa file `.tmp` còn lại sau worker crash.
- Xóa workspace không còn `FlowRun` tương ứng.
- Đánh dấu `workspace_status=expired` sau khi dọn.

## Yêu cầu bảo mật

- Không follow symlink khi quét hoặc đóng gói.
- Từ chối symlink, hardlink bất thường, socket, FIFO và device file.
- Chỉ archive regular file nằm dưới workspace root sau khi resolve đường dẫn.
- Dùng `lstat` và mở file với `O_NOFOLLOW` khi nền tảng hỗ trợ.
- Giới hạn số file, kích thước mỗi file, tổng dung lượng và độ sâu đường dẫn.
- Không ghi provider key, MCP header hoặc environment vào workspace/manifest.
- Không cho Agent mount toàn bộ volume workspace của hệ thống.
- Không phục vụ trực tiếp workspace đang chạy; chỉ phục vụ archive đã hoàn tất.
- Tên file trong `Content-Disposition` không lấy từ input người dùng.

## Kiểm thử bắt buộc

### Unit test

- Docker command mount đúng run subpath và working directory.
- Không cho node ID hoặc path thoát khỏi workspace.
- Retry chỉ xóa thư mục của node hiện tại.
- Hai node song song ghi vào hai thư mục khác nhau.
- Symlink và special file bị từ chối.
- Vượt giới hạn file/dung lượng tạo `workspace_status=failed`.
- Manifest chứa đúng path, size và SHA-256.
- Archive được atomic rename và không để file tạm sau thành công.

### API test

- Chủ sở hữu tải được ZIP.
- Người dùng khác nhận `404`.
- Run đang chạy, archive lỗi hoặc hết hạn không tải được.
- Response có đúng content type và tên file an toàn.

### Runtime test

- Default, Codex, OpenCode và OpenHands đều tạo được file trong node workspace.
- Runtime không ghi được vào root filesystem hoặc workspace của run khác.
- Structured JSON output vẫn được validation như trước.

### End-to-end test

1. Workflow có hai Agent node tạo hai file khác nhau.
2. Chạy workflow qua worker thật.
3. Xác nhận Agent containers đã bị xóa.
4. Tải workspace ZIP qua API.
5. Xác nhận ZIP có đúng hai thư mục node, manifest và checksum.

## Thứ tự thực hiện đề xuất

1. Thêm volume, workspace lifecycle và giới hạn an toàn.
2. Mount workspace vào Agent container và viết unit test isolation.
3. Bật file tools cho từng runtime.
4. Thêm finalizer, manifest và database migration.
5. Thêm API download và authorization test.
6. Thêm UI Run history.
7. Thêm retention cleanup và E2E Compose test.

## Tiêu chí hoàn thành

- Agent tạo được file thật trong workspace của run.
- Agent container bị xóa nhưng file vẫn còn.
- Workflow run hiển thị metadata workspace.
- Chủ sở hữu tải được một ZIP hợp lệ từ Run history.
- Không runtime nào truy cập được workspace của run khác.
- Workspace quá hạn được dọn tự động.
- Backend tests, runtime tests, web build và Compose validation đều đạt.

## Giới hạn đã biết

Docker named volume phù hợp với deployment một Docker host. Khi workflow workers
chạy trên nhiều host, cần chuyển archive sang object storage như S3/MinIO và dùng
presigned download URL hoặc API proxy. Việc này nằm ngoài phạm vi phiên bản đầu.
