# GLP Chat

Ứng dụng CLI cho nhiều người dùng trên LAN. Server xác thực tài khoản, xử lý
chat chung và chat riêng, lưu file upload và chuyển file đến người có quyền.
Giao thức GLP chạy trên TCP/TLS 1.3. UDP IPv4 dùng để tìm server.

## Cài đặt

Dùng Python 3.12 có hỗ trợ TLS 1.3. Từ thư mục dự án trên mỗi máy, tạo môi
trường riêng và cài dependency.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Trên Windows PowerShell có thể gọi Python trong môi trường trực tiếp.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Cách chạy trên PowerShell dùng `.\.venv\Scripts\python.exe` thay cho
`python` trong các lệnh bên dưới. Server tự tạo cert
và private key PEM khi có kết nối đầu tiên và file PEM chưa tồn tại. Cấu hình
TLS của demo dựa vào endpoint đáng tin trong LAN.

## Chạy trên một máy

Mở một terminal cho server rồi mở thêm terminal cho mỗi client.

```sh
python app.py server
```

```sh
python app.py client
```

Mặc định dùng `localhost:8443/TCP`. Mỗi client đăng ký một tài khoản để thử
chat riêng và chat chung.

## Chạy trên LAN

**Máy server**

```sh
python app.py server --discover
```

**Máy client**

```sh
python app.py client --discover
```

Server phát `ANNOUNCE` mỗi 10 giây tới UDP 8444. Client lấy IP nguồn và
`tcp_port` trong gói, đóng socket UDP rồi mở TCP/TLS. Nếu chờ 30 giây chưa
thấy announcement hợp lệ, client báo lỗi và thoát. Dùng cùng
`--discovery-port` ở hai đầu nếu cần đổi cổng UDP.

Khi biết IPv4 của máy server, có thể kết nối tay. Thay `192.168.1.20` bằng
địa chỉ thực của máy đang chạy server.

```sh
# Terminal server
python app.py server --host 192.168.1.20 --port 8443

# Terminal client
python app.py client --host 192.168.1.20 --port 8443
```

Server nhận TCP 8443. Ở chế độ discovery, client cần nhận được IPv4 broadcast
trên UDP 8444. Hai máy cần có đường mạng đến nhau qua LAN.

## Dùng CLI

Chọn `register` hoặc `login` rồi nhập username và password. Mỗi trường dài
6–32 ký tự ASCII in được. Từ menu, dùng `listchat` để xem số thứ tự các cuộc
trò chuyện và chọn số của người nhận hoặc `GLOBAL`.

```text
menu> listchat
menu> chat 1
> Xin chào
> /more 10
> /upload "./tài liệu.pdf"
> /files
> /download 1
> /back
menu> logout
```

Trong chat, Enter gửi tin và lệnh bắt đầu bằng `/`. `/history` lấy lại tin gần
nhất, `/more` lấy tin cũ hơn và `/help` hiện các lệnh. Đặt đường dẫn có khoảng
trắng trong dấu nháy. `/download` nhận số từ danh sách `/files` hoặc file ID.
File tải về ở `downloads/` hoặc thư mục được chọn qua `--download-dir`.

## Source code

| Thành phần | Trách nhiệm |
| --- | --- |
| `app.py`, `discovery.py` | Chọn vai trò, địa chỉ TCP và discovery IPv4. |
| `GLP.py`, `GLPConn.py`, `Error.py` | Đóng gói frame, đọc stream, bắt tay TLS/GLP và chuẩn hóa lỗi. |
| `client/client_cli.py`, `client/client_backend.py` | Đọc lệnh, gửi request, xử lý response và in sự kiện. |
| `server/server.py`, `server/router.py` | Listener, worker, heartbeat và điều phối request. |
| `server/auth.py`, `server/chat.py`, `server/files.py`, `server/db.py` | Phiên gắn socket, chat, file và SQLite dùng chung. |
| `file_transfer.py` | Metadata, chia segment, ghi file tạm và kiểm SHA-256. |

Request và response dùng header ASCII, dòng trống rồi đến số arg. Mỗi arg có
chiều dài theo byte, nên nội dung có thể chứa text, JSON hoặc bytes nhị phân.
Upload mở bằng metadata. Với file có dữ liệu, server trả `FILE.READY`, nhận
các segment rồi xác minh hash trước khi trả `FILE.STORED`. Download trả
`FILE.META` và các `FILE.DATA`. Client ghép và kiểm file trước khi hiện tên đích.

## Kiểm thử

```sh
python -B -m unittest discover -s tests -v
```

Bộ test đặt trong `tests/` và dùng SQLite, PEM, file và socket loopback tạm.
Các ca kiểm gồm parser GLP, discovery, CLI, login và quản lý phiên, chat riêng/chung,
upload/download, lỗi lưu trữ, heartbeat và 20 kết nối đồng thời. Kết quả
trên macOS với Python 3.12.12 và dependency đã pin là **42/42**. Một lượt thử
riêng trên cùng máy nhận broadcast IPv4 thật và đăng nhập qua cổng TCP
được quảng bá. Kiểm thử giữa hai máy LAN và các hệ điều hành khác còn cần thực hiện.

## Dữ liệu chạy và báo cáo

`server/.runtime/` chứa SQLite, PEM và file upload. Server tạo thư mục này
khi chạy. Tài khoản, lịch sử và file đã lưu được dùng lại sau khi mở lại DB.
Git bỏ qua thư mục runtime, `downloads/`, môi trường ảo và cache.

Báo cáo nộp riêng dưới dạng PDF. Khi có bản `REPORT.tex` cục bộ, dùng XeLaTeX
hai lượt để cập nhật mục lục.

```sh
mkdir -p .report-build
xelatex -interaction=nonstopmode -halt-on-error -output-directory=.report-build REPORT.tex
xelatex -interaction=nonstopmode -halt-on-error -output-directory=.report-build REPORT.tex
```

PDF xuất ở `.report-build/REPORT.pdf`. Git bỏ qua `REPORT.*` và `.report-build/`.
