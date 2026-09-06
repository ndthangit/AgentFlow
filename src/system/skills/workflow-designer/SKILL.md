# Workflow Designer

Khi thiết kế workflow:

1. Bắt đầu bằng đúng một trigger phù hợp với yêu cầu.
2. Mỗi node chỉ thực hiện một trách nhiệm và phải mô tả input/output rõ ràng.
3. Chèn approval trước hành động có tác động ra hệ thống bên ngoài khi yêu cầu cần người duyệt.
4. Không tạo cycle hoặc fan-out không giới hạn.
5. Kết thúc mọi nhánh hoạt động bằng một node `end`.
