import socket
import ssl
import threading
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from GLPConn import GLP_Connection
import server.db as db
from server.auth import AuthService, UserManager
from server.chat import MessageService
from server.router import dispatch
from server.files import FileConnection, FileService

app_lock = threading.Lock()

def generate_tls_keys(cert_path, key_path):
    if not os.path.exists(cert_path) or not os.path.exists(key_path):
        print("[*] Đang tự động tạo Self-Signed TLS Certificate...")
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key_path, "-out", cert_path,
            "-days", "365", "-nodes", "-subj", "/CN=localhost"
        ], check=True, stderr=subprocess.DEVNULL)

def handle_client(conn: GLP_Connection, addr, auth_service, online, message):
    if hasattr(message, "files"):
        conn = FileConnection(conn)
    print(f"[+] Client {addr} đã kết nối TLS 1.3.")
    try:
        while True:
            # Chờ gói tin (Không block thread khác)
            request = conn.recv()
            
            # Xử lý Logic (Đã lock DB và Dict)
            with app_lock:
                response = dispatch(request, auth_service, conn, ip_addr=addr[0], online=online, message=message)
            
            conn.send(response)
            
    except EOFError:
        print(f"[-] Client {addr} ngắt kết nối.")
    except Exception as e:
        print(f"[!] Lỗi kết nối {addr}: {e}")
    finally:
        # Dọn dẹp session và xóa khỏi danh sách online
        with app_lock:
            if hasattr(message, "files"):
                message.files.disconnect(conn)
            auth_service.disconnect(conn)
            online.remove(conn)
        conn.close()

def start_server(host="127.0.0.1", port=8443, max_workers=20):
    cert_path = os.path.join(os.path.dirname(__file__), "server.crt")
    key_path = os.path.join(os.path.dirname(__file__), "server.key")
    generate_tls_keys(cert_path, key_path)

    db_file = os.path.join(os.path.dirname(__file__), "server.db")
    database = db.connect(db_file)
    db.init(database)
    
    auth_service = AuthService(database)
    online = UserManager()  # Khởi tạo Switchboard
    message = MessageService(database)
    message.files = FileService(database, os.path.join(os.path.dirname(__file__), "files"))

    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    context.minimum_version = ssl.TLSVersion.TLSv1_3

    raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    raw_socket.bind((host, port))
    raw_socket.listen(max_workers)
    
    print(f"[*] GLP Server đang lắng nghe tại {host}:{port}...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while True:
            try:
                client_sock, addr = raw_socket.accept()
                glp_conn = GLP_Connection.accept(client_sock, context, timeout=30.0)
                # Truyền online vào worker
                executor.submit(handle_client, glp_conn, addr, auth_service, online, message)
            except Exception as e:
                print(f"[!] Bỏ qua kết nối lỗi: {e}")
