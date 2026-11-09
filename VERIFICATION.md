# Evidence UI + display name

11/10/2026, macOS, Python 3.12, Textual 8.2.8. Trạng thái học tập EXPERIMENT.
Không mở/sửa DB hoặc certificate/private key hiện có của app.

```sh
python3 -m unittest discover -s demo/app/tests -p 'test_*.py' -v
```

**8 tests pass, 15.397s** sau lần sửa cuối:

- 4 profile tests: request đi qua encoder/parser thật; tên tiếng Việt/emoji/
  dấu ngoặc được giữ nguyên; user ID/session/username không đổi; DB commit còn
  sau reopen; danh sách online và message mới dùng tên mới. Reject UTF-8 lỗi,
  tên rỗng/quá dài/control chars, sai argc, chưa auth hoặc token sai connection.
- 1 network test: hai socket TCP/TLS 1.3 thật trên 127.0.0.1, cert có SAN và
  client kiểm CA/hostname; dùng handler/router/Auth/MessageService thật với
  DB/account/key tạm. Register → rename → self/users → global/private event;
  giữ cùng endpoint, cleanup worker/socket sau test. Client test này tự kiểm
  certificate; backend UI hiện vẫn tắt kiểm cert như ghi trong README.
- 3 headless UI tests: cả GLPApp/HackerApp; Ctrl+N, click hồ sơ, `/name ...`,
  lỗi đổi tên, mất mạng khi dialog mở. EVT.RECV vẫn vào chat lúc đang chỉnh tên;
  chỉ OK.PROFILE thay tên và đóng dialog. Thử 120×36, 80×24, 60×20; prompt có
  vùng nội dung, input nằm trong bề rộng screen, j/g vẫn đổi kênh.

Đã export SVG từ screen Textual thật và render PNG để xem bố cục:
`previews/client.png`, `hacker.png`, `hacker-login.png`, `hacker-profile.png`.
Preview dùng backend giả/account giả, FX tắt để chụp tĩnh; không là ảnh một
phiên LAN thật. Mưa ký tự có timer 10 fps khi bật GLP_FX; không benchmark FPS.

Chưa kiểm LAN/cross-OS, nhiều client cùng spam chat, slow recipients, hoặc
throughput. Các giới hạn mạng/auth hiện có của app không được thay thế bởi
giao diện mới; milestone chưa VERIFIED.

## FILE — 07/11/2026

macOS, Python 3.12, Textual 8.2.8. Giữ nguyên evidence cũ ở trên.

```sh
python3.12 -m unittest discover -s demo/app/tests -p 'test_*.py' -v
```

**19 tests pass, 20.774s** sau thay đổi code cuối: 8 tests cũ + 11 tests FILE.
Toàn bộ account, DB, certificate/key và bytes file test ở thư mục tạm. Không
chạy server với DB/cert/key hiện có của app. Socket tests cần quyền bind loopback.

- `test_files.py`: 6 tests qua Request/Response encoder + parser thật, SQLite
  và disk tạm. File binary lớn hơn frame 256 KiB, byte `00`/`ff`/CRLF, file
  rỗng; chia/gộp stream ở biên một byte/4096 byte/toàn stream; size/hash/offset
  sai và chunk quá lớn; file riêng không lộ cho user thứ ba; tên traversal bị
  từ chối; logout/cancel/disconnect dọn upload; pagination 50 + 2; metadata/file
  còn sau đóng/mở SQLite; FileService khởi tạo lại chỉ xóa `.part` còn sót.
- `test_file_tls.py`: 3 client TCP/TLS 1.3 thật trên 127.0.0.1, client kiểm CA
  và hostname của certificate tạm. File riêng 424000 byte đi qua handler/router
  và FileService thật. Giữ nguyên endpoints khi gửi/tải; chat trong upload;
  tải đồng thời với 12 tin đẩy 45000+ byte trên chính socket người nhận, hash
  và số tin đúng. User thứ ba bị từ chối nhưng connection vẫn dùng được.
  EOF giữa frame của upload sau đó dọn `.part`, server còn trả PING cho peer khác.
- `test_file_ui.py`: 4 tests, backend giả, file local tạm. Cả client/hacker ở
  120×36, 80×24, 60×20; nút FILE có dòng chữ thật khi hover, progress `100%`
  không bị wrap/mất; danh sách GLOBAL/private, Ctrl+F; upload/download nhiều
  chunk; chat vẫn nhận khi dialog mở; đóng/mở dialog không hủy transfer;
  progress bar; không ghi đè file trùng tên; hash lỗi/disconnect xóa file dở;
  NOTICE tới trước response LIST không làm mất file mới, và tải thêm trang cũ.

Đã export screen Textual thật sang `previews/files-client*.svg` và
`previews/files-hacker*.svg`, render bằng Quick Look và xem PNG để kiểm layout
ở 110×32 và 60×20. Preview dùng dữ liệu giả, không là phiên LAN thật.

So với snapshot source trước task: 6 file Python cũ chỉ thêm tổng cộng 33 dòng
điểm nối FILE; không xóa/thay dòng cũ. `GLPConn.py`, auth, chat, db, backend và
tests cũ giữ nguyên. README/evidence chỉ nối thêm phần FILE; Word gốc và demo
khác không sửa. Không có Git repository tại workspace root nên đối chiếu bằng
snapshot trước chỉnh sửa, không dùng `git diff`.

Chưa kiểm LAN/cross-OS, 10–20 users hoặc slow client. File hoàn chỉnh chưa có
quota tổng/TTL và chưa resume transfer; xem [FILE.md](FILE.md). Milestone học
tập vẫn EXPERIMENT, không tự VERIFIED.
