# Workflow Designer

Khi thiết kế workflow:

1. Bắt đầu bằng đúng một trigger phù hợp với yêu cầu.
2. Mỗi node chỉ thực hiện một trách nhiệm và phải mô tả input/output rõ ràng.
3. Chèn approval trước hành động có tác động ra hệ thống bên ngoài khi yêu cầu cần người duyệt.
4. Không tạo cycle hoặc fan-out không giới hạn.
5. Kết thúc mọi nhánh hoạt động bằng một node `end`.
6. Dùng node `if` với edge `true`/`false` cho nhánh điều kiện; nhánh không được chọn sẽ bị skip.
7. Dùng node `parallel` với ít nhất hai edge cổng `parallel` khi các nhánh độc lập có thể chạy đồng thời.
8. Các node con có thể nhận output của node rẽ trực tiếp hoặc tham chiếu rõ bằng `$nodes.<id>.output.<field>`.
9. Dùng `llm.call` cho tác vụ chỉ cần đúng một lần gọi model; chỉ dùng `agent` khi cần skill hoặc hành vi agent nhiều bước.
10. Trong prompt/instructions, tham chiếu dữ liệu đã resolve của node bằng `{{input.field}}`; dùng `{{input}}` khi cần toàn bộ JSON input.
