from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import socket
import sys
import threading
import time
from os import getenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Error import GLPTimeoutError, ProtocolError, error_message
from GLP import Request
from GLPConn import GLP_Connection
from discovery import ANNOUNCE_INTERVAL, start_discovery, stop_discovery
import server.db as db
from server.auth import AuthService, UserManager
from server.chat import MessageService
from server.router import dispatch
from server.files import Download, FileService

RUNTIME_DIR = Path(getenv("APP_DATA_DIR")) if getenv("APP_DATA_DIR") else Path(__file__).with_name(".runtime") 
app_lock = threading.Lock()
PING_INTERVAL = 15.0
MAX_MISSED_PINGS = 4
HEARTBEAT_TIMEOUT = PING_INTERVAL * MAX_MISSED_PINGS

def handle_client(conn: GLP_Connection, addr, auth_service, online, message):
    print(f"[+] Client {addr} đã kết nối TLS 1.3.",flush=True)
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
                stream_worker.join(HEARTBEAT_TIMEOUT)
                if stream_worker.is_alive():
                    raise GLPTimeoutError("Download chưa kết thúc")
            with app_lock:
                response = dispatch(
                    request, auth_service, conn, ip_addr=addr[0],
                    online=online, message=message, peer_port=addr[1],
                )
            if isinstance(response, Download):
                stream_worker = threading.Thread(target=send_download, args=(response,), daemon=True)
                stream_worker.start()
            elif response is not None:
                conn.send(response)
                if request.route == "FILE.SEGMENT" and response.action != "FILE.STORED":
                    break
    except EOFError:
        print(f"[-] Client {addr} ngắt kết nối.",flush=True)
    except (OSError, ProtocolError) as error:
        print(f"{error_message(str(error))} (kết nối {addr})",flush=True)
    finally:
        try: conn.sock.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        conn.close()
        if stream_worker is not None:
            stream_worker.join(2)
        with app_lock:
            if hasattr(message, "files"): message.files.disconnect(conn)
            auth_service.disconnect(conn)
            online.remove(conn)

def start_server(host="127.0.0.1", port=8443, max_workers=20, *, discovery_port=None):
    RUNTIME_DIR.mkdir(mode=0o700, exist_ok=True)

    database = db.connect(RUNTIME_DIR / "server.db")
    db.init(database)
    
    auth_service = AuthService(database)
    online = UserManager()
    message = MessageService(database)
    message.files = FileService(database, RUNTIME_DIR / "files")

    raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    raw_socket.bind((host, port))
    raw_socket.listen(max_workers)
    
    tcp_endpoint = raw_socket.getsockname()
    with raw_socket, ThreadPoolExecutor(max_workers=max_workers) as executor:
        discovery = None
        try:
            discovery = start_discovery(tcp_endpoint, discovery_port)
            print(f"[*] GLP Server đang lắng nghe tại {host}:{tcp_endpoint[1]}", flush=True)
            if discovery_port is not None: print(f"[*] Broadcast discovery tới UDP {discovery_port} mỗi {ANNOUNCE_INTERVAL:g} giây", flush=True)
            while True:
                try:
                    client_sock, addr = raw_socket.accept()
                    glp_conn = GLP_Connection.accept(client_sock, timeout=30.0)
                    executor.submit(handle_client, glp_conn, addr, auth_service, online, message)
                except (OSError, EOFError, ProtocolError) as error:
                    print(error_message(str(error)),flush=True)
        finally:
            stop_discovery(discovery)
