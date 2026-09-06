# Quản lý skill cho agent

System quản lý một catalog skill để người dùng chọn năng lực/hướng dẫn cho workflow trước khi gọi agent. Agent integration không tự đọc toàn bộ folder và không tự chọn skill ngoài danh sách System cung cấp.

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
| `GET /v1/workflows/{id}/skills` | Liệt kê skill đang gắn với workflow |
| `PUT /v1/workflows/{id}/skills` | Thay toàn bộ selection, giữ thứ tự từ `skill_ids` |

Một workflow chỉ chọn tối đa 20 skill đang enabled. System kiểm tra quyền sở hữu workflow và chỉ cho chọn built-in skill hoặc skill thuộc chính người dùng.

## Snapshot lúc publish

Selection trên draft là mutable. Khi publish workflow version, System chép các trường sau vào `workflow_versions.graph.skills`:

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

Worker sau này phải lấy skill từ snapshot của workflow version và truyền đúng danh sách này cho agent. Việc sửa hay tắt skill sau đó không làm thay đổi version/run cũ.

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
