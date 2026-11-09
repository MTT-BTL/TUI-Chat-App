"""
GLP Automated Integration Test Script
Chạy: python test-server.py
"""
import socket
import ssl
import threading
import time
import json
import uuid

context = ssl.create_default_context()
context.minimum_version = ssl.TLSVersion.TLSv1_3
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE

def create_glp_request(route: str, session: str, *args) -> bytes:
    req = f"GLP\r\n{route}\r\n{session}\r\n\r\n{len(args)}\r\n".encode('utf-8')
    for arg in args:
        arg_bytes = arg.encode('utf-8') if isinstance(arg, str) else arg
        req += f"{len(arg_bytes)}\r\n".encode('utf-8') + arg_bytes + b"\r\n"
    return req

def get_args(raw: bytes) -> list:
    """Hàm "chống mù" bóc tách Arguments từ gói GLP an toàn"""
    try:
        body = raw.split(b'\r\n\r\n')[1]
        parts = body.split(b'\r\n')
        # Lấy giá trị nằm ở các index: 2, 4, 6...
        return [parts[i].decode('utf-8', errors='replace') for i in range(2, len(parts)-1, 2)]
    except:
        return []

def test_client_bot(bot_name: str, password: str, target_bot: dict = None):
    print(f"[{bot_name}] Đang khởi động...")
    client = context.wrap_socket(socket.create_connection(('localhost', 8443)), server_hostname='localhost')
    
    # 1. TEST PING
    client.send(create_glp_request("SYS.PING", "_THIS_IS_THE_DEFAULT_SESSION_ID_"))
    print(f"[{bot_name}] [PING] -> {client.recv(1024).split(b'\r\n')[1].decode()}")
    
    # 2. TEST REGISTER
    unique_user = f"{bot_name}_{uuid.uuid4().hex[:4]}"
    client.send(create_glp_request("AUTH.REGISTER", "_THIS_IS_THE_DEFAULT_SESSION_ID_", unique_user, password, bot_name))
    resp = client.recv(1024)
    session = resp.split(b'\r\n')[2].decode('utf-8')
    
    # [FIX] Lấy UserID thực sự thay vì con số 32
    user_id = get_args(resp)[0] 
    print(f"[{bot_name}] [REGISTER] Lấy session: {session[:8]}... | UserID: {user_id}")
    
    if target_bot is not None:
        target_bot[bot_name] = user_id
    
    # 3. TEST DASHBOARD & USERS
    client.send(create_glp_request("DASH.SELF", session))
    print(f"[{bot_name}] [DASH.SELF] -> {get_args(client.recv(1024))[2]}")
    
    client.send(create_glp_request("SYS.USERS", session))
    print(f"[{bot_name}] [SYS.USERS] -> Lấy được {len(json.loads(get_args(client.recv(4096))[0]))} user online.")
    
    time.sleep(2)
    
    # 4. TEST CHAT GLOBAL
    if bot_name == "Alice":
        print(f"\n[{bot_name}] Gửi tin nhắn Global...")
        client.send(create_glp_request("MSG.GLOBAL", session, "Hello mọi người, tôi là Alice!"))
        print(f"[{bot_name}] [MSG.GLOBAL] (Phản hồi gửi) -> {client.recv(1024).split(b'\r\n')[1].decode()}")
        
    elif bot_name == "Bob":
        print(f"[{bot_name}] Đang chờ nghe thông báo Real-time...")
        push_args = get_args(client.recv(4096))
        if len(push_args) >= 4:
            print(f"\n[!!! {bot_name} NHẬN GLOBAL PUSH !!!] Từ {push_args[2]}: {push_args[3]}")
    
    time.sleep(1)
    
    # 5. TEST CHAT PRIVATE
    if bot_name == "Bob" and "Alice" in target_bot:
        alice_id = target_bot["Alice"]
        print(f"\n[{bot_name}] Gửi tin Private cho Alice ({alice_id[:8]}...)...")
        client.send(create_glp_request("MSG.PRIVATE", session, alice_id, "Bí mật nha Alice!"))
        print(f"[{bot_name}] [MSG.PRIVATE] (Phản hồi gửi) -> {client.recv(1024).split(b'\r\n')[1].decode()}")
        
    elif bot_name == "Alice":
        push_args = get_args(client.recv(4096))
        if len(push_args) >= 4:
            print(f"\n[!!! {bot_name} NHẬN PRIVATE PUSH !!!] Từ {push_args[2]}: {push_args[3]}")

    time.sleep(1)

    # 6. TEST PULL HISTORY (Dùng route MSG.GETGLO của bạn)
    if bot_name == "Alice":
        print(f"\n[{bot_name}] Tải lịch sử Global Chat...")
        client.send(create_glp_request("MSG.GETGLO", session))
        hist_args = get_args(client.recv(4096))
        
        # [FIX] Kiểm tra đủ 2 tham số và lấy chuỗi JSON ở index 1
        if len(hist_args) >= 2:
            chat_hist = json.loads(hist_args[1])
            print(f"[{bot_name}] [MSG.GETGLO] -> Đã lấy thành công {len(chat_hist)} dòng chat lịch sử.")
    print(f"[{bot_name}] Ngắt kết nối.")
    client.close()

if __name__ == "__main__":
    print("=== BẮT ĐẦU AUTOMATED TEST ===")
    bot_info = {}
    
    t1 = threading.Thread(target=test_client_bot, args=("Alice", "password123", bot_info))
    t2 = threading.Thread(target=test_client_bot, args=("Bob", "password456", bot_info))
    
    t1.start()
    time.sleep(0.5) 
    t2.start()
    
    t1.join()
    t2.join()
    print("\n=== KẾT THÚC TEST ===")