# GLP

Giao thức tầng ứng dựa trên HTTP với rule chặt hơn, cơ bản giao thức có 2 phần phân tách nhau bằng CRLF `\r\n` gồm:

- **Head**
  - ROUTE: Định hướng
  - ACTION: Hành động sẽ làm
  - SESSION: user token
- **Body**
  - ARGC: số lượng argument
  - Size của Arg x
  - Arg x

Dùng chung cho cả Request và Response

Route chia rõ vài phần quan trọng:

- **Request**
  - SYS
  - AUTH
  - DASH
  - MSG
  - FILE (đang thử)
- **Response**
  - OK
  - REDIRECT
  - DENY
  - ERROR
  - EVT

Có thể sửa lại để tối ưu hơn và rõ nghĩa hơn, nhưng chung quy vẫn dùng format request/response này, sẽ tối ưu sau.

# Server

Mục tiêu của server là cung cấp:

- authentication cho user (login/logout/register/phân quyền/quản lý tài nguyên)
- Lưu trữ chat message và cho phép chatting 2 chiều (cả private và global)
- FILE (đang thử)

Tất cả đều được handle qua backend nhờ request từ user, trong đó những phần quan trọng:

- Database service
- Auth service
- Chat service
- File service (chưa chốt)
- System service (check alive liên tục cho user)

# Client

Viết sẵn code để tương ứng các gửi request và handle response cùng gửi lại response nếu alive, kiểm tra connection, chuyển đôi thành giao diện TUI (tạm tập trung backend Client, chưa xử lý xong)
