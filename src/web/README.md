# AgentFlow Web

Giao diện React/Vite tối giản cho FastAPI System. Web đăng nhập Keycloak bằng Authorization Code + PKCE, sau đó cho phép tạo, sửa JSON draft, validate, publish workflow và tạo run.

```powershell
npm.cmd install
npm.cmd run dev
```

Mặc định web chạy ở `http://localhost:5173`. Bản Docker được public tại `http://localhost:3000`.

## Cấu trúc mã nguồn

Mã trong `src` được chia theo trách nhiệm để thay đổi một tính năng không kéo theo
các phần không liên quan:

```text
src/
├── app/                 # App shell, điều hướng và phối hợp trạng thái trang
├── features/
│   ├── mcp/             # Quản lý MCP server
│   ├── models/          # Quản lý provider và model
│   └── workflows/       # Template, tóm tắt và workflow editor
│       └── editor/      # Canvas, graph utilities, schema builder và types
├── shared/              # API client, xác thực và contract dùng chung
└── styles/              # CSS entry point của ứng dụng
```

Logic biến đổi graph thuần nằm trong `features/workflows/editor/graph.ts`; component
React chỉ giữ việc quản lý state và render. Các workflow mẫu nằm riêng trong
`features/workflows/templates.ts` để tránh trộn dữ liệu demo với app shell.

Workflow editor tự lưu draft sau mỗi thao tác graph hợp lệ. Nút lưu trong drawer
node vừa cập nhật graph vừa xếp thao tác ghi database; các lần ghi được tuần tự hóa
theo revision để tránh ghi đè. Publish luôn chờ draft mới nhất lưu xong rồi mới tạo
version, vì vậy không có nút “Lưu draft” riêng.

Kiểm tra production build sau khi thay đổi:

```powershell
npm.cmd run build
```
