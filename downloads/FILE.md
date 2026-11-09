# FILE qua socket GLP hiện có

Phần bổ sung riêng cho rubric 6 (serialize/chunk/reassemble TCP) và phần file
trong rubric 9 (UI/progress). Không thay đổi format GLP, parser, AUTH hay MSG.

## Luồng và trạng thái

Gửi: chọn chat → nhập đường dẫn → `UPLOAD` → `READY` → (`CHUNK` → `ACK`)
lặp đến hết → `COMPLETE` kèm SHA-256 → `STORED`. Server chỉ công bố file sau
khi đủ size và hash đúng; sau đó gửi `NOTICE` cho các connection được phép.

Nhận: mở FILE trong chat → chọn một dòng → chọn thư mục → (`GET` → `DATA`)
lặp → client kiểm size/SHA-256 → đổi tên file `.part` thành file hoàn chỉnh.
Thông báo đã lưu trên server không có nghĩa người nhận đã tải file.

Mỗi client chỉ truyền một file tại một thời điểm. Mỗi lần chỉ có một chunk
đang chờ ACK/DATA; các request chat vẫn đi cùng connection. Mỗi chunk tối đa
32 KiB, nằm nguyên trong một arg bytes của GLP; không Base64, không split dữ
liệu theo CRLF, không decode file thành UTF-8. TCP có thể chia/gộp dữ liệu;
GLPConn vẫn chịu trách nhiệm ghép frame theo byte length như trước.

## Routes bổ sung (args đều là bytes)

`target` là literal `GLOBAL` hoặc user ID của người còn lại. Offset/size/cursor
là số thập phân ASCII. Metadata/list dùng JSON UTF-8; data là binary nguyên gốc.

| Request | Args | Response |
|---|---|---|
| `FILE.UPLOAD` | target, basename, size | `FILE.READY` (file_id) |
| `FILE.CHUNK` | file_id, offset, raw_bytes | `FILE.ACK` (file_id, next_offset) |
| `FILE.COMPLETE` | file_id, sha256_hex | `FILE.STORED` (metadata JSON) |
| `FILE.CANCEL` | file_id | `FILE.CANCELLED` (file_id) |
| `FILE.LIST` | target, cursor (`0` = mới nhất) | `FILE.ITEMS` (target, JSON list, next_cursor hoặc rỗng) |
| `FILE.GET` | file_id, offset | `FILE.DATA` (file_id, offset, raw_bytes) |
| `FILE.CHATS` | không có | `FILE.PEERS` (JSON các đối tác đã gửi/nhận file riêng) |

Server đẩy `FILE.NOTICE` (metadata JSON) khi file được lưu xong. Lỗi FILE cơ
bản trả `FILE.ERROR` (request route, mô tả); lỗi session dùng cơ chế AUTH cũ.
Tên response khác tên request để parser cũ phân biệt được hai loại frame.

## Storage và giới hạn của bản cơ bản

- Server lưu metadata trong bảng `files` mới; bytes ở `server/files/<file_id>`.
  File riêng chỉ người gửi/người nhận được list/tải; file GLOBAL dành cho user
  đã đăng nhập. Cho phép người nhận offline, tải khi họ đăng nhập lại.
- Một lần upload tối đa 1 GiB. Danh sách trả 50 file mỗi trang; bấm **Thêm**
  để xem tiếp. Chưa có quota tổng hoặc tự xóa file hoàn chỉnh.
- Tên file chỉ là basename printable, tối đa 255 byte UTF-8. Server dùng ID
  làm đường dẫn. Client không ghi đè file trùng tên, giữ file tải dở ở `.part`.
- Mất kết nối/hủy upload: xóa file dở. Server khởi động lại xóa `.part` còn sót;
  file hoàn chỉnh và danh sách vẫn còn. Chưa có resume/retry tự động.
- Chỉ thêm kiểm tra cần cho file: quyền truy cập, tên/size/offset/chunk/hash và
  lỗi I/O. Dùng TLS hiện có; không thêm socket hay thay policy certificate/auth.
- Khi bật FILE, adapter gửi ở server khóa từng frame trên mỗi connection để
  response chứa binary và tin đẩy từ thread khác không trộn bytes.

## UI

Ở cả `client` và `hacker`, nút **FILE** nằm trên trái trong khung chat, cùng
hàng với tên GLOBAL/người nhận. Có thể mở bằng **Ctrl+F**. Dialog có danh sách
file, đường dẫn file cần gửi, thư mục tải về và progress bar. Bấm **Đóng**/Esc
không hủy transfer đang chạy; nút FILE tiếp tục hiện phần trăm.

Đây là evidence implementation của bản demo, không tự xác nhận milestone học
tập hoặc chứng minh LAN/cross-OS.
