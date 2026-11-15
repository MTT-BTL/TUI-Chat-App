from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import socket
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Error import GLPTimeoutError, ProtocolError, error_message
from GLP import Request
from GLPConn import GLP_Connection
import server.db as db
from server.auth import AuthService, UserManager
from server.chat import MessageService
from server.router import dispatch
from server.files import Download, FileService

RUNTIME_DIR = Path(__file__).with_name(".runtime")
app_lock = threading.Lock()
PING_INTERVAL = 15.0
MAX_MISSED_PINGS = 4
HEARTBEAT_TIMEOUT = PING_INTERVAL * MAX_MISSED_PINGS

def handle_client(conn: GLP_Connection, addr, auth_service, online, message):
    print(f"[+] Client {addr} đã kết nối TLS 1.3.")
    last_ping = time.monotonic()
    stream_worker = None

    def send_download(download):
        try:
            for frame in download.frames():
                conn.send(frame)
        except OSError:
            try:
                conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    try:
        while True:
            request = conn.recv(deadline=last_ping + HEARTBEAT_TIMEOUT)
            if not isinstance(request, Request):
                raise ProtocolError("Server chỉ nhận Request")
            ping = request.route == "SYS.PING" and not request.args
            if ping:
                last_ping = time.monotonic()
            elif stream_worker is not None:
                # Request nghiệp vụ tiếp theo phải theo sau toàn bộ download trước.
                stream_worker.join(HEARTBEAT_TIMEOUT)
                if stream_worker.is_alive():
                    raise GLPTimeoutError("Download chưa kết thúc")
            with app_lock:
                response = dispatch(request, auth_service, conn, ip_addr=addr[0], online=online, message=message)
            if isinstance(response, Download):
                # Reader vẫn trả PONG khi file đang stream trên cùng connection.
                stream_worker = threading.Thread(target=send_download, args=(response,), daemon=True)
                stream_worker.start()
            elif response is not None:
                conn.send(response)
                if request.route == "FILE.SEGMENT" and response.action != "FILE.STORED":
                    # Có thể còn segment đang trên đường đi khi upload lỗi.
                    break
    except EOFError:
        print(f"[-] Client {addr} ngắt kết nối.")
    except (OSError, ProtocolError) as error:
        print(f"{error_message(str(error))} (kết nối {addr})")
    finally:
        try:
            conn.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()
        if stream_worker is not None:
            stream_worker.join(2)
        with app_lock:
            if hasattr(message, "files"):
                message.files.disconnect(conn)
            auth_service.disconnect(conn)
            online.remove(conn)

def start_server(host="127.0.0.1", port=8443, max_workers=20):
    RUNTIME_DIR.mkdir(mode=0o700, exist_ok=True)

    database = db.connect(RUNTIME_DIR / "server.db")
    db.init(database)
    
    auth_service = AuthService(database)
    online = UserManager()  # Khởi tạo Switchboard
    message = MessageService(database)
    message.files = FileService(database, RUNTIME_DIR / "files")

    raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    raw_socket.bind((host, port))
    raw_socket.listen(max_workers)
    
    print(f"[*] GLP Server đang lắng nghe tại {host}:{port}", flush=True)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while True:
            try:
                client_sock, addr = raw_socket.accept()
                glp_conn = GLP_Connection.accept(client_sock, timeout=30.0)
                # Truyền online vào worker
                executor.submit(handle_client, glp_conn, addr, auth_service, online, message)
            except (OSError, EOFError, ProtocolError) as error:
                print(error_message(str(error)))
