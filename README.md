# GLP

Định nghĩa giao thức tầng ứng dụng, trong đó gói tin GLP bao gồm các thành phần:

- Magic Bytes: `GLP` + CRLF.
- Route/Action: dạng `TYPE.ACTION`, để định tuyến request hoặc nhận biết response.
- Session: token đăng nhập hoặc marker quy định sẵn, kết thúc bằng CRLF.
- Một dòng CRLF trống chia Head và Body.
- Body gồm số lượng arg, hay `argc`, rồi lần lượt size và dữ liệu của từng arg.

```text
GLP\r\n
TYPE.ACTION\r\n
session\r\n
\r\n
argc\r\n
size_arg1\r\n
arg1\r\n
...
```

Cả Request và Response đều tuân thủ format này! (`GLP.py` có 2 dataclass `Request`/`Response`). Kiểm tra route/session và đóng gói qua `to_bytes()`. Đọc đúng size thay vì split toàn bộ Body. Ý tưởng chính lấy từ giao thức Redis kết hợp với HTTP

Session đăng nhập là 16 byte random, chuyển thành 32 ký tự hex thường. `DEFAULT_SESSION` chỉ trạng thái chưa đăng nhập; `HOLDING_SESSION` chỉ dùng trong response để yêu cầu client giữ token đang có. Hai session đặc biệt này giúp đảm bảo format của gói tin

# GLP Connection

`GLPConn.py` quản lý socket và buffer dùng chung cho cả hai phía. Các bước khi
bắt đầu một kết nối `connect` (client) và `accept` (server):

- TLS Handshake qua `ssl`, bọc TCP socket bằng ngữ cảnh TLS
- GLP Handshake: client gửi `SYS.PING`, server trả `OK.PONG` rồi mới giao tiếp tiếp

- `send()` gọi `to_bytes()` rồi `sendall()`. Mỗi lần send được lock theo frame, đảm bảo bytes của file, chat và PONG không bị trộn khi nhiều thread cùng gửi
- `recv()` gọi `_parse()` nếu chưa đủ dữ liệu thì đọc thêm vào buffer:

```python
frame = self._parse()
if frame is not None:
    return frame
data = self.sock.recv(4096)
```

Sau khi kiểm tra, `data` được thêm vào buffer để parse tiếp. `_parse()` đọc Head, argc, từng length và đúng số byte của arg. Thiếu dữ liệu thì trả `None` và giữ buffer, sai format thì raise `ProtocolError`. Parse xong chỉ bỏ bytes thuộc
frame đó, phần dư dành cho frame sau. Giới hạn hiện tại: 4 args, 64 KiB/arg, 256 KiB/frame.

# Server Backend

Server giữ tất cả kết nối với client. Phân Server thành các component chính:

- `server.py`: mở listener, TLS handshake, đưa connection vào worker chạy, `handle_client()`. Worker nhận request, gọi Router rồi gửi response
- `db.py`: một SQLite database dùng chung, lưu account và messages; autocommit để dữ liệu còn sau restart. Password hiện dùng SHA-256 kèm salt
- `auth.py`: `AuthService` login/register, quản lý session qua dict. Mỗi session có token, user ID, hạn dùng, connection và IP; `UserManager` giữ user online
- `chat.py`: `MessageService` kiểm tra người nhận/nội dung, gọi Database để gửi tin, lấy history và danh sách chat
- `files.py`: `FileService` giữ upload dở, lưu metadata trong SQLite và bytes file trên disk, `Download.frames()` sinh các response để tải file
- `router.py` định nghĩa số args hợp lệ và xử lý request bằng `dispatch()`. Các lớp lỗi nằm trong `Error.py` cạnh `GLP.py`.

Luồng xử lý chính trong `handle_client()`:

```python
request = conn.recv(deadline=last_ping + HEARTBEAT_TIMEOUT)
with app_lock:
    response = dispatch(
      request, auth_service, conn,
      ip_addr=addr[0], online=online, message=message
    )
```

`app_lock` bảo vệ Database và state dùng chung giữa các worker. Response thường được gửi ngay; nếu là `Download`, server dùng thread gửi file riêng để worker vẫn nhận/trả PING. Khi disconnect, server dọn session, online list và upload dở

Router gọi service tương ứng rồi trả action phù hợp:

- `AUTH.LOGIN/REGISTER`: nhận username/password, trả `AUTH.OK` kèm profile và token mới. Session bind với connection để đảm bảo, copy token sang connection khác không dùng được.
- `AUTH.LOGOUT`: xóa session, trả `AUTH.BYE` và reset token về default
- `CHAT.LIST`: trả GLOBAL và metadata các user khác, kèm trạng thái online (2 trong 1)
- `CHAT.SEND`: nhận target và text, lưu tin rồi trả `CHAT.SENT`, người nhận đang online được đẩy `CHAT.MESSAGE`. Target là `GLOBAL` và các user ID
- `CHAT.HISTORY`: nhận target, limit và cursor `before`. Database lấy từ mới về cũ theo `msg_id`, service đảo lại để hiển thị từ cũ đến mới.

Mặc định history lấy 10 tin của cả hai phía. Cursor là ID để lấy các tin cũ hơn cursor rỗng nghĩa là hết trang. Lỗi nghiệp vụ được trả qua `ERROR.AUTH/CLIENT/FILE`. Client gửi PING mỗi 15 giây, server chỉ gia hạn heartbeat khi nhận PING hợp lệ, quá 60 giây thì đóng connection

Các lớp lỗi như `ProtocolError`, `AppError`, `SessionError` và `FileError`
nhận một chuỗi. `Error.py` thêm tiền tố `[!]` nếu chuỗi chưa có. Một
response `ERROR.*` mang đúng một arg UTF-8 để CLI hiển thị. Với session
hết hạn, server gửi `ERROR.AUTH` với `DEFAULT_SESSION` ở header để client
quay về login. Chạy client và server cùng phiên bản để dùng format này.

```python
raise AppError("Người nhận không tồn tại")
# CLI hiển thị [!] Người nhận không tồn tại
```

sequenceDiagram
participant A as Client Alice
participant S as Server
participant B as Client Bob
A->>S: Kết nối TCP, bắt tay TLS
A->>S: SYS.PING
S-->>A: OK.PONG
A->>S: AUTH.LOGIN(username, password)
S-->>A: AUTH.OK(profile), token mới
A->>S: CHAT.LIST
S-->>A: CHAT.ITEMS
Note over B,S: Bob cũng đã kết nối và login
A->>S: CHAT.SEND(bob_id, text)
S-->>B: CHAT.MESSAGE
S-->>A: CHAT.SENT

# Client

Client có hai module. `client_cli.py` đọc stdin, chọn chat và hiển thị
kết quả. `client_backend.py` chứa API login, chat, file, cùng logic gửi và
nhận GLP. Ví dụ API gửi tin trong Backend như sau.

```python
def send_chat(self, target, text):
    return self._call("CHAT.SEND", target.encode(), text.encode(), expected="sent")
```

`_call()` gửi request qua GLP connection và chờ `_receive()` trả
`Result(kind, data, message, ok)`. `dispatch()` đọc args của response để tạo
`Result`. Với session hết hạn, Backend xóa profile để CLI quay lại login.

Backend có hai thread nền. `_read_loop()` đọc frame và chuyển response vào
queue của giao dịch đang chờ. `_heartbeat_loop()` gửi PING và kiểm tra hạn PONG.
`OK.PONG` cập nhật heartbeat. `CHAT.MESSAGE` và `FILE.NOTICE` đi qua callback
hiển thị. Mỗi connection chờ một giao dịch nghiệp vụ tại một thời điểm vì GLP
chưa có request ID.

```text
stdin -> CLI -> Backend -> GLP_Connection -> Server
stdout <- CLI <- Result <- dispatch(Response)
```

`notify()` chỉ in thông báo, không gửi request từ thread nhận. Khi đang nhập,
CLI dùng print lock và readline (nếu có) để vẽ lại prompt cùng phần đang gõ.

# Truyền File

Service file cần giải quyết việc gửi file vào một chat, lưu trên server và cho người có quyền chủ động tải về. Đường đi là client gửi -> server lưu -> client nhận tải qua ID file, khi upload xong có thể đóng kết nối, người nhận offline vẫn tải sau được. Hiện file hoàn tất được giữ trên server, chưa có policy tự xóa theo TTL/quota.

Một file được quản lý thành ba phần, cụ thể:

- Metadata: file ID, người gửi/nhận, tên, size, SHA-256 và thời gian trong SQLite
- Bytes: nội dung trong file; server đặt tên bằng file ID, tên gốc chỉ để hiển thị trong chat
- Transfer state: upload dở trong `uploads[connection]`, có token, metadata, đường dẫn `.part`, `next_index` và digest đang tính. State này chỉ nằm trong RAM

`FileService` ở server kiểm quyền, quản lý SQLite và gửi thông báo file mới.
Backend client gọi upload/download và trả `Result` cho CLI. Cả hai dùng
`file_transfer.py`, đặt cạnh `GLP.py`. Đầu gửi dùng `read_segments()` để chia
bytes. Đầu nhận dùng `SegmentWriter` để ghi từng phần và kiểm hash.

## Segment và điều kiện hoàn tất

`file_transfer.py` quy định segment 32 KiB và file tối đa 1 GiB. Một frame GLP
có thể cần nhiều lần `recv()`. Một lần `recv()` cũng có thể nhận nhiều frame.
Parser GLP tìm ranh giới frame bằng argc và byte length.

```python
@dataclass(frozen=True)
class FileMetadata:
    name: str
    size: int
    segments: int
    sha256: str
```

Ví dụ file 70.000 byte có 3 segment: index 0 và 1 dài 32.768 byte, index 2 dài
4.464 byte. Segment đánh số từ 0. Có số lượng trước giúp biết lúc nào kết thúc,
nhưng vẫn phải kiểm file ID, thứ tự, length và hash để biết dữ liệu có đúng không.

## Upload từ client lên server

1. `Backend.upload()` mở file và dùng `FileMetadata.from_file()` để lấy size,
   tính SHA-256 theo block rồi `seek(0)`. Hai lượt đọc cho phép gửi hash ngay
   trong request mở đầu.
2. Client gửi `FILE.UPLOAD(target, metadata_json)`. Router xác thực session.
   `FileService` kiểm người nhận và định dạng metadata.
3. Server tạo `file_id` ngẫu nhiên, file `.part` và upload state gắn connection/
   token, trả `FILE.READY(file_id, N)`. Mỗi connection chỉ có một upload đang dở.
4. Client gửi liên tiếp N `FILE.SEGMENT(file_id, index, bytes)`.
   `SegmentWriter` kiểm index/kích thước, ghi vào cùng một `.part` và cập nhật
   hash sau mỗi lần ghi.
5. Segment cuối gọi `_store()` để kiểm hash, đóng file, đổi tên `.part` thành
   file ID và INSERT metadata vào SQLite. Người gửi nhận `FILE.STORED`. Các
   connection được phép nhận thông báo sẽ nhận `FILE.NOTICE`.

```text
Client gửi                         Server
FILE.UPLOAD(target, metadata)   ->  kiểm tra, tạo .part
                               <-  FILE.READY(file_id, N)
FILE.SEGMENT(file_id, 0, data0)  ->  ghi segment 0
... gửi đến segment N-1 ...     ->  kiểm hash và lưu
                               <-  FILE.STORED(metadata)
```

Mục 6 của đề yêu cầu chia và ghép file qua TCP đúng nội dung. ACK ở mục 2
là xác nhận bắt tay. Với upload, TCP giữ thứ tự byte và điều tiết tốc độ
gửi. Xác nhận ở tầng ứng dụng gồm `READY` cho cả luồng và `STORED` sau khi
kiểm hash và lưu. Client bỏ một lượt chờ phản hồi cho mỗi segment trung gian.
Khi lỗi segment, server gửi lỗi rồi đóng connection để dừng luồng còn đang gửi
dở. Chạy client và server cùng phiên bản vì luồng phản hồi đã đổi.

`FILE.NOTICE` báo người nhận có file để chủ động tải. Thanh tiến độ CLI
đếm segment đã gửi hoặc đã ghi local. Kết quả cuối vẫn dựa trên xác nhận
`FILE.STORED` cho upload và kiểm hash cùng thao tác lưu local cho download.

## Download từ server về client

1. Client gửi một `FILE.DOWNLOAD(file_id)`. Server tra metadata và xác thực
   session. Với file private, server đối chiếu user ID với sender/recipient.
2. `Download.frames()` mở file, đối chiếu size rồi yield `FILE.META`. Metadata có
   số segment và `segment_size`, nên client biết chính xác phải nhận bao nhiêu.
3. Generator lần lượt đọc một block và yield `FILE.DATA(file_id, index, bytes)`.
   Worker gửi file riêng để thread nhận vẫn xử lý PING trên cùng connection.
4. Client kiểm META và tạo file tạm trong thư mục đích. Mỗi DATA phải khớp
   file ID. `SegmentWriter` kiểm index/kích thước, ghi bytes và cập nhật hash.
5. Đủ N DATA thì Backend kết thúc giao dịch, kiểm hash và lưu file local.
   TCP connection tiếp tục phục vụ chat và heartbeat.

```text
Client nhận                        Server
FILE.DOWNLOAD(file_id)         ->  kiểm session/quyền/file
                              <-  FILE.META(metadata có N)
                              <-  FILE.DATA(file_id, 0, data0)
... đến FILE.DATA(file_id, N-1, data_cuối) ...
ghép đủ -> kiểm size/hash -> công bố file local
```

Client dùng `os.link(part, destination)` trong cùng thư mục: tên đích chỉ xuất
hiện sau khi file đã ghép và kiểm xong; tên đã tồn tại sẽ báo `FileExistsError`
để thử thêm `(1)`, `(2)`, tránh ghi đè khi có thao tác đồng thời. Sau đó dọn `.part`.
Filesystem không hỗ trợ hard link sẽ báo lỗi. Server hiện chưa nhận ACK cuối
báo client đã lưu thành công; `Result("downloaded", ...)` chỉ tồn tại bên client.

File rỗng có N=0: upload trả STORED ngay nếu hash rỗng đúng; download chỉ nhận
META rồi kiểm/lưu file rỗng, không chờ DATA. `FILE.LIST` lấy 10 file/trang bằng
cursor `seq`; `FILE.CANCEL` chỉ hủy upload dở, không xóa file đã STORED.

## Vấn đề và cách xử lý đang có

| Vấn đề                                 | Cách xử lý trong code và lý do                                                                                                                                            |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Giả session hoặc đoán file ID private  | Router kiểm token bind connection; service kiểm sender/recipient. Biết ID chưa đủ quyền tải.                                                                              |
| Tên chứa đường dẫn, trùng tên          | `FileMetadata` kiểm basename. Server lưu theo ID. Client thử tên mới khi trùng.                                                                                           |
| Sai số lượng, index, length, hash      | Kiểm metadata từ đầu, kiểm từng segment và hash cuối. Upload lỗi segment bị hủy, file hỏng không được công bố cho user.                                                   |
| File cache mất/cắt ngắn hoặc bytes đổi | Server kiểm mở file/size/length khi đọc; client đối chiếu hash trước khi lưu tên cuối. Hash bảo đảm khớp nội dung khai báo, không làm file nguồn thành snapshot bất biến. |
| Cancel/logout/disconnect giữa upload   | Server đóng file tạm và xóa state cùng `.part`. Nếu client gặp lỗi local khi stream, Backend đóng connection để server dọn. Server dọn các `.part` còn sót khi khởi động. |
| Download đứt hoặc ghi disk lỗi         | Context chưa đọc đủ response sẽ đóng connection, client dọn `.part`; lỗi size/hash sau khi đã nhận đủ N DATA có thể giữ connection dùng tiếp.                             |
| Response không về                      | Mỗi lần chờ response có timeout 15 giây; timeout đóng connection để response trễ không bị gán cho request mới. Đây chưa phải deadline toàn bộ file.                       |
| Chat/PONG xen giữa DATA                | Backend xử lý event/PONG riêng. Các frame thuộc giao dịch đang chờ đi vào queue. Send lock bảo vệ ranh giới từng frame.                                                   |

TLS bảo vệ đường truyền từng client-server; server vẫn đọc được file.
SHA-256 để kiểm nội dung, không phải mã hóa hay chữ ký xác thực nguồn file.

## Những tối ưu đã có, cùng tradeoff

| Cách làm                                           | Lợi ích và lý do                                                                                           | Giới hạn                                                                       |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Stream từng block, hash tăng dần                   | Không giữ toàn bộ bytes file trong RAM; server không cần đọc lại toàn bộ file chỉ để tính hash khi commit. | Chưa đo peak RAM thực tế; client vẫn đọc nguồn hai lượt.                       |
| Một request cho download, META rồi N DATA          | Bỏ vòng request/response cho từng chunk; server có thể gửi tiếp khi socket cho phép.                       | TCP vẫn có flow control; client chưa có resume.                                |
| N segment xác định kết thúc                        | Bỏ COMPLETE/DONE riêng, giảm trạng thái protocol.                                                          | Mỗi segment vẫn cần length/index; server chưa biết client download thành công. |
| Queue nhận 2 frame và gửi qua socket               | Giới hạn dữ liệu xếp hàng. Socket tạo backpressure khi một đầu xử lý chậm.                                 | PONG có thể bị chậm khi reader nghẽn vì dùng cùng connection.                  |
| Generator + worker download riêng                  | Đọc file đến đâu gửi đến đó, reader server vẫn nhận PING.                                                  | Thêm thread và lock; chưa giải quyết fairness giữa client nhanh/chậm.          |
| Metadata SQLite, bytes trên disk; LIST dùng cursor | Truy vấn/quyền/phân trang metadata riêng, không đọc bytes file khi list.                                   | Phải quản lý tính nhất quán giữa disk và Database.                             |

32 KiB vừa giới hạn arg GLP 64 KiB và giữ block nhỏ. Kích thước này chưa được
benchmark. Server mở file tạm một lần cho cả upload, đóng khi hoàn tất hoặc
khi dọn upload dở.

## Các khoảng trống cần tính tiếp

- Quota/TTL: cap 1 GiB chỉ áp dụng từng file, chưa chặn tổng dung lượng nhiều file.
  Cần cap theo user/toàn server, kiểm dung lượng khả dụng và policy dọn cache.
- Tiến độ: client có thể giữ PING nhưng không gửi segment; heartbeat vẫn hợp lệ.
  Cần `last_progress`/deadline riêng cho transfer để thu hồi upload dở.
- Crash lúc commit: rename và INSERT là hai thao tác khác nhau. Crash giữa hai
  bước có thể để lại file đã đổi tên nhưng chưa có row; dọn `.part` chưa xử lý
  được trường hợp này. Cần state commit/recovery, đối chiếu disk-DB khi restart
  và fsync theo mức durability muốn bảo đảm; transaction SQLite một mình không
  biến thao tác filesystem thành atomic cùng Database.
- Retry/resume: server có thể đã STORED nhưng client mất response. Upload lại
  hiện tạo ID mới, có thể trùng nội dung. Segment lặp trong một luồng bị từ chối.
  Cần chốt idempotency/tra trạng thái trước khi thêm retry hoặc resume.
- Slow client: dispatch còn giữ `app_lock` chung khi ghi upload và gửi notice.
  Peer đọc chậm có thể làm các request khác chờ; nên đưa send ra ngoài lock state
  và có queue/deadline ghi riêng nếu cần xử lý tải lớn hơn.

Tests hiện có kiểm file rỗng/binary, số lượng/order/size/hash sai, private permission,
cleanup, collision và download chung connection với chat/heartbeat. Chưa có
benchmark throughput/peak RAM, crash đúng giữa rename-INSERT hoặc xác nhận LAN/cross-OS.

# CLI và cách chạy

`app.py` chọn mode server hoặc client. Server gọi `GLP_Connection.accept(sock)`,
client gọi `GLP_Connection.connect(host, port)`. Hai hàm tự bắt tay TLS rồi trao
đổi `SYS.PING` và `OK.PONG`. Server nạp cert và key từ một file `server.pem`
ở kết nối đầu tiên và dùng lại TLS context cho các kết nối sau.
TLS 1.3 tự thiết lập khóa phiên và mã hóa GLP. Demo dùng giả định server đáng
tin và tắt xác minh danh tính server ở phía client. GLP dùng username/password
để server xác thực tài khoản.

Trên máy server, tạo PEM một lần từ thư mục dự án khi chưa có file này.

```sh
(
  umask 077
  mkdir -p server/.runtime
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj /CN=GLP \
    -keyout server/.runtime/server.pem -out server/.runtime/server.pem
)
```

Chạy hai lệnh ở hai terminal nếu demo trên một máy.

```sh
python3 app.py server
python3 app.py client
```

## Chạy trên hai máy cùng LAN

Dùng cùng bản source trên cả hai máy. Ví dụ máy A chạy server và có IPv4 LAN
là `192.168.1.20`. Thay địa chỉ ví dụ bằng IPv4 của máy A.

1. Trên máy A, tạo PEM theo lệnh trên rồi chạy server với IPv4 LAN.

   ```sh
   python3 app.py server --host 192.168.1.20 --port 8443
   ```

2. Trên máy B, chạy client với địa chỉ của máy A.

   ```sh
   python3 app.py client --host 192.168.1.20 --port 8443
   ```

Client cần truy cập được cổng TCP 8443 trên máy A. Nếu timeout, kiểm tra
IPv4, firewall và việc mạng Wi-Fi có cho hai máy liên lạc trực tiếp hay không.

`server/.runtime/` chứa PEM, SQLite và file đã upload, thư mục này được Git bỏ qua.
Client ghi file vào
`downloads/` hoặc thư mục chọn qua `--download-dir`.

```text
menu> listchat
menu> chat 1
bobby001> Xin chào
bobby001> /more 10
bobby001> /history 25
bobby001> /upload "./tài liệu.pdf"
bobby001> /files
bobby001> /download 1
bobby001> /back
menu> logout
```

Vào chat mặc định hiện 10 tin gần nhất; `more` lấy thêm tin cũ, `history` lấy lại
trang mới nhất. Trong chat, lệnh bắt đầu bằng `/`, nội dung thường gửi khi Enter.
`/help` xem lệnh, `/quit` thoát. Chạy bộ test bằng `python3 -B test-server.py`.
test localhost chưa chứng minh hoạt động LAN/cross-OS. Trạng thái học tập vẫn EXPERIMENT.
