# Quản lý skill cho agent

System quản lý một catalog skill dùng chung. Mọi skill đang enabled tự động xuất hiện trong bộ chọn của từng Agent node; người dùng không cần gắn hoặc tích chọn skill ở cấp workflow. Agent integration không tự đọc toàn bộ folder và chỉ nhận các skill có ID được chọn trong `config.skillIds` của node.

## Nguồn skill

| Nguồn | Nơi định nghĩa | Quyền sửa |
| --- | --- | --- |
| Built-in | `src/system/skills/<slug>/SKILL.md` và `metadata.json` | Sửa qua repository, deploy lại System |
| User | PostgreSQL `system.skills` | Chỉ chủ sở hữu JWT `sub` được cập nhật |

Khi System khởi động, các built-in skill được đọc từ filesystem và upsert vào PostgreSQL. Nếu nội dung `SKILL.md` thay đổi, System tăng version và cập nhật SHA-256. Built-in skill không sửa được qua API.

## API

| Endpoint | Chức năng |
| --- | --- |
| `GET /v1/skills` | Liệt kê built-in skill và skill của người đang đăng nhập |
| `POST /v1/skills` | Tạo user skill |
| `GET /v1/skills/{id}` | Đọc nội dung một skill có quyền truy cập |
| `PUT /v1/skills/{id}` | Cập nhật user skill bằng `expected_version` |
Các endpoint workflow-skill cũ vẫn được giữ để tương thích với client cũ nhưng giao diện và quá trình publish không còn dùng selection cấp workflow. System kiểm tra từng `config.skillIds` khi validate/publish và chỉ chấp nhận skill đang enabled, là built-in hoặc thuộc chính người dùng.

## Snapshot lúc publish

Khi publish workflow version, System thu thập các ID được chọn trong mọi Agent node, loại trùng theo thứ tự xuất hiện và chép các trường sau vào `workflow_versions.graph.skills`:

```json
{
  "id": "skill-uuid",
  "slug": "workflow-designer",
  "name": "Workflow Designer",
  "version": 1,
  "content_hash": "sha256",
  "instructions": "Nội dung SKILL.md tại thời điểm publish"
}
```

Worker lấy skill từ snapshot của workflow version và chỉ truyền đúng danh sách `skillIds` của Agent đang chạy. Việc sửa hay tắt skill sau đó không làm thay đổi version/run cũ.

## Thêm built-in skill

Tạo hai file:

```text
src/system/skills/my-skill/
├── metadata.json
└── SKILL.md
```

`metadata.json`:

```json
{
  "name": "My Skill",
  "description": "Mô tả ngắn để người dùng quyết định có chọn skill hay không."
}
```

Tên folder là slug và phải dùng chữ thường, số, dấu gạch ngang. `SKILL.md` chứa chỉ dẫn đầy đủ sẽ được đưa cho agent. Không đặt API key, password hoặc credential trong skill.
