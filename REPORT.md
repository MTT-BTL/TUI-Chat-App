# GLP

Định nghĩa giao thức tầng ứng dụng, trong đó gói tin GLP bao gồm các thành phần:

- CRLF: Ngay cách giữa các thành phần!
- Magic Bytes: `GLP` + CRLF
- Action.Route: Định tuyến cho giao thức
- Session Token: Định dang gồm 2 default token và 32 ký tự hex + CRLF
- CRLF chia cắt Body và Head
- Body gồm số lượng arg, hay argc + CRLF
- Size của Arg1 + CRLF + Arg1 + CRLF
- Size của Arg... + CRLF + Arg... + CRLF

Cả Request và Response đều tuân thủ format này!

Parse hoạt động tương đối đơn giản:

- Cắt làm 2 qua CRLF+CRFL
- Cắt Head qua CRLF, kiểm tra Magic GLP, Action.Route và Session
- Cắt Body qua CRLF, kiểm tra số lượng Arg qua Argc, đọc từng size của Arg và đọc chính xác Arg, kiểm tra có được end chuẩn với CRLF không

Ý tưởng chính lấy từ giao thức của Redis kết hợp với HTTP

# GLP Connection

Quản lý các connection khi giao tiếp qua giao thức GLP, cụ thể Server sẽ đóng vai nhận Request, Gửi Response, Client thì ngược lại! Và GLP sẽ đóng vai trò làm trung gian quản lý các connection, các buffer được gửi đi, ... Các bước GLP Conn cần làm khi bắt đầu 1 kết nối `connect` (chủ động - client) và `accept` (thụ động - server):

- TLS Handshake qua ssl, cụ thể hơn thì bọc connection socket bằng ngữ cảnh ssl
- GLP Handshake qua SYS.PING được gửi đi (khi chủ động - client) và OK.PONG trả về (khi thụ động - server)

GLP Connection dùng 1 buffer chung để handle các request, lý do là đảm bảo các gói tin được xử lý chỉ khi đã gửi đủ, nếu gửi chưa đủ, buffer sẽ vẫn còn đó và được xử lý ở các lượt sau!

- Buffer làm đệm, \_parse lặp liên tục xử lý dựa trực tiếp vào dữ liệu trong buffer, kiểm tra đầy đủ từng thành phần, nếu có bất kỳ vấn đề nào thì luôn return None thay vì return packet GLP để chống lỗi mất mát packet hoặc ngẽn mạng bất ngờ!
- 2 hàm `send`/`recv` dựa trực tiếp trên 1 parse chung nhờ cấu trúc của response và request giống nhau!

# Server Backend

Server giữ tất cả kết nối với client

Phân Server thành các component chính, cụ thể:

- Database, đa số mọi việc như login/register/chating/file đều có liên quan trực tiếp đến database của server, 1 database dùng chung cho tất cả, dùng sqlite3
- Auth, để login/register và manages users session qua 1 dict đơn giản, mõi session có đầy đủ token, user id, hạn dùng, kết nối, ip user.
- Chat, làm trung gian cho Database và server, khá đơn giản

Cuối cùng là Router, phần chính, đầu tiên router cần được định nghĩa rõ sẽ xử lý những request thế nào và trả về những gì, đầu tiên quy định rõ số lượng Arg cho từng route, đảm bảo không có lỗi ở server khi truyền tham số.

- Mỗi route sẽ gọi qua các API service trung gian mà server có để dễ quản lý, 1 hàm dispath sẽ đưa các request cụ thể thực hiện những việc cụ thể và response phù hợp,
