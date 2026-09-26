# Filesystem MCP demo

MCP server tối giản cho demo AgentFlow. Server cung cấp ba tool:

- `write_file`: ghi file UTF-8 trong `/data`.
- `read_file`: đọc file UTF-8 trong `/data`.
- `list_files`: liệt kê file trong `/data`.

Đường dẫn tuyệt đối, `..`, symlink thoát khỏi `/data`, file quá 1 MiB và kết quả liệt kê quá 200 mục đều bị chặn.

Khi chạy bằng Docker Compose, đăng ký trong trang MCP với:

- Slug: `filesystem-demo`
- Transport: `Streamable HTTP`
- URL: `http://filesystem-mcp:8002/mcp`
- Header xác thực: để trống

Sau đó chọn MCP này trong cấu hình node Agent. Dữ liệu được giữ trong volume `agentflow_mcp_files`.

