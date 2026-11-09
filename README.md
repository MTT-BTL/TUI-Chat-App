# GLP message app

Chạy từ thư mục gốc workspace, cần Python + `textual` + `rich` và `openssl`
(môi trường hiện tại đã có):

```sh
python3 demo/app/app.py server
python3 demo/app/app.py client
python3 demo/app/app.py hacker
```

Mỗi lệnh chạy ở một terminal riêng. `client` và `hacker` đều kết nối cùng server,
đăng nhập/đăng ký và dùng chung routes, session, lịch sử và message events.
`hacker` là skin NEON // GLP: xanh phosphor, nền tối, console log, node panel
bên phải và thanh trạng thái LINK/RTT/NODES/UNREAD. Số liệu lấy từ state thật;
không có thao tác hacking hoặc giả lập truy cập hệ thống khác.

```sh
python3 demo/app/app.py hacker --host 127.0.0.1 --port 8443
GLP_FX=0 python3 demo/app/app.py hacker
```

`--host`/`--port` cũng dùng cho server và client thường; mặc định localhost:8443,
hoặc `GLP_HOST`/`GLP_PORT`. Server giữ DB account/message ở `server/server.db`.
Chạy server thật có thể ghi thêm dữ liệu vào DB đó; tests dùng DB tạm riêng.

## Dùng UI

| Phím/thao tác | Chức năng |
|---|---|
| `i`, `Esc` | Bắt đầu gõ / quay về điều hướng |
| `Enter` | Gửi nội dung trong prompt |
| `j` / `k`, click vào một người | Chọn kênh global/private |
| `g` | Quay về global |
| `Ctrl+N`, click ô hồ sơ | Mở dialog đổi tên |
| `/name Ghost Operator` | Đổi tên ngay từ prompt; giữ nguyên phần sau `/name` thành một arg |
| `F5` | Làm mới danh sách online |
| `Ctrl+Q` | Thoát |

Ô hồ sơ tách tên hiển thị, `@username`, trạng thái và gợi ý đổi tên.
Username/user ID dùng cho identity; đổi tên không thay chúng hoặc token.
Tên dài được cắt theo độ rộng ô terminal. Bản hacker ẩn sidebar dưới 65 cột;
vẫn chọn kênh bằng `j/k/g`, đổi tên bằng `Ctrl+N`.

## Client hoạt động thế nào?

1. `app.py` chọn `GLPApp` hoặc `HackerApp`. App mở LoginScreen và gọi Backend.
2. Backend chạy thread nền: TCP connect → TLS 1.3 → GLP `SYS.PING`/`OK.PONG`.
   PING là bắt tay ở tầng app; TCP/TLS đã bắt tay trước đó.
3. UI gọi `send_request()`. Backend tạo Request rồi đưa vào queue; thread ghi
   lấy request và `sendall()`. UI tiếp tục nhận phím/vẽ, không chờ socket.
4. Thread đọc gọi `GLP_Connection.recv()`: buffer gom stream bytes, parser trả
   từng Response hoàn chỉnh. `NetEvent` đưa frame vào event loop của Textual.
5. `handle_response()` cập nhật session/ChatState, rồi gọi screen để vẽ.
   `EVT.RECV` là tin đẩy; `OK.HISTORY` là lịch sử; `OK.USERS` là danh sách online.
   Events và responses dùng chung connection. Dialog đổi tên không chặn chat.
6. Gõ tin thường gửi `MSG.GLOBAL` hoặc `MSG.PRIVATE` theo kênh đang chọn.
   Tin của mình hiện ngay khi vào queue; `EVT.SENT` xác nhận server xử lý,
   chưa có trạng thái “người nhận đã đọc”. Khung ChatView chỉ vẽ dòng đang thấy.

`client_state.py` giữ dữ liệu; `client_widgets.py` vẽ widget; `client_screens.py`
nhận thao tác; `client.py` nối chúng với mạng. `hacker.py` kế thừa logic này,
đổi layout/render/CSS; backend và protocol vẫn dùng cùng implementation.

Textual cung cấp event loop, screens và headless Pilot để thử thao tác UI:
[screens](https://textual.textualize.io/guide/screens/),
[testing](https://textual.textualize.io/guide/testing/).

## API đổi tên

```python
Request("DASH.RENAME", session_token, ("Nguyễn Ghost".encode("utf-8"),))
# → Response("OK.PROFILE", "-", (user_id_bytes, username_bytes, displayname_bytes))
```

Một arg UTF-8, trim đầu/cuối, 1–32 ký tự printable (có thể dùng tiếng Việt/emoji).
Server lấy user ID từ session đã bind với connection; client không chọn account
khác để sửa. `auth.rename()` kiểm tên → DB UPDATE/commit → router trả profile.
UI chỉ thay tên sau `OK.PROFILE`; tên lỗi giữ nguyên và dialog cho sửa lại.
Tên mới xuất hiện trong `DASH.SELF`, `SYS.USERS` và các message events tiếp theo.
Client khác thấy thay đổi khi poll online (mỗi 5 giây).

## Giới hạn hiện tại và tests

Backend hiện tắt kiểm certificate/hostname để thử self-signed cert: có mã hóa
TLS, chưa xác thực danh tính server. App còn dùng SHA-256 cho password và push
socket bên trong lock nghiệp vụ. Đây là những phần riêng cần học/sửa tiếp;
skin hacker không thay hành vi mạng/security đó.

```sh
python3 -m unittest discover -s demo/app/tests -p 'test_*.py' -v
```

Tests dùng account, DB, cert/key tạm. UI tests dùng backend giả; test TLS dùng
hai connection loopback và handler thật. Xem [VERIFICATION.md](VERIFICATION.md).
Preview ở [previews/hacker.png](previews/hacker.png) dùng dữ liệu giả lập.
Milestone học tập vẫn EXPERIMENT, chưa chứng minh LAN/cross-OS.

## Bổ sung FILE — 07/11/2026

Trong GLOBAL hoặc chat riêng, bấm **FILE** ở góc trên trái khung chat (cùng
hàng với tên kênh), hoặc **Ctrl+F**. Cả `client` và `hacker` đều có chức năng này.

1. Gửi: nhập đường dẫn file ở ô **File gửi vào chat này**, bấm **Gửi file**.
   Đường dẫn có khoảng trắng nhập nguyên văn, không cần bọc dấu nháy.
2. Nhận: chọn một dòng trong danh sách, nhập thư mục lưu rồi bấm **Tải file**
   (hoặc Enter trên dòng đó). File trùng tên được thêm `(2)`, `(3)`…
3. Thanh tiến độ hiển thị lượng byte đã gửi/nhận. Đóng dialog bằng Esc/**Đóng**
   vẫn tiếp tục transfer; nút FILE hiện phần trăm. Bấm **Thêm** để xem trang cũ.

File qua server trên chính TCP/TLS connection của user: chia chunk **32 KiB**
trước khi đóng mỗi request, chờ ACK rồi gửi chunk tiếp theo. Size + SHA-256
được kiểm tra trước khi công bố file. Người nhận chủ động tải; người nhận
offline vẫn xem được file riêng khi đăng nhập lại. Server thêm bảng `files`
và thư mục `server/files/`; không đưa file bytes vào bảng messages.

Code riêng ở `file_protocol.py`, `server/files.py`, `client/client_files.py`,
`client/file_ui.py`. Các file cũ chỉ thêm điểm nối FILE; giữ nguyên các dòng
logic cũ, parser, AUTH và MSG. [FILE.md](FILE.md) ghi routes, luồng, storage và
giới hạn (1 GiB/file, chưa resume/tự xóa file hoàn chỉnh).

Môi trường workspace hiện có Textual trong Python 3.12:

```sh
python3.12 demo/app/app.py server
python3.12 demo/app/app.py client
python3.12 demo/app/app.py hacker
python3.12 -m unittest discover -s demo/app/tests -p 'test_*.py' -v
```

Chạy server/client ở các terminal riêng; có thể dùng `python3` nếu interpreter
đó đã cài `textual` và `rich`. Preview FILE dùng account/file giả ở
`previews/files-client*.svg`, `previews/files-hacker*.svg`.
