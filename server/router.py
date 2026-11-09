"""Request -> nghiệp vụ -> Response. Router không recv/send socket."""
import json
from GLP import DEFAULT_SESSION, Request, Response
from .config import AppError

ARG_COUNTS = {
    "SYS.PING": (0,), "SYS.USERS": (0,),
    "AUTH.LOGIN": (2,), "AUTH.REGISTER": (2, 3), "AUTH.LOGOUT": (0,),
    "DASH.HOME": (0,), "DASH.SELF": (0,), "DASH.RENAME": (1,),
    "MSG.GLOBAL": (1,), "MSG.PRIVATE": (2,),
    "MSG.GETGLO": (0,), "MSG.GETPRI": (1,),
    "MSG.CHATS": (0,)
}


def as_bytes(value: bytes | str) -> bytes:
    if isinstance(value, bytes): return value
    return str(value).encode()

def reply(action, *args, session_update="-") -> Response:
    return Response(action, session_update, tuple(as_bytes(arg) for arg in args))

def dispatch(request: Request, auth, connection, ip_addr: str, online, message) -> Response:
    try:
        route, args = request.route, request.args
        if route.startswith("FILE."):
            session = auth.get_session(request.session_id, connection)
            return message.files.dispatch(request, session, connection, online)
        if route not in ARG_COUNTS: raise AppError("[!] Request không hợp lệ")
        if len(args) not in ARG_COUNTS[route]: raise AppError(f"[!] {route} cần chính xác {ARG_COUNTS[route]} arg")

        if route == "SYS.PING": return reply("OK.PONG",session_update="",)

        if route == "AUTH.LOGIN":
            session = auth.login(*args, ip_addr=ip_addr, connection_id=connection)
            online.add(connection, session.user_id, ip_addr)
            return reply("REDIRECT.HOME", session.user_id, session_update=session.token)

        if route == "AUTH.REGISTER":
            name = args[2] if len(args) == 3 else None
            session = auth.register(*args[:2], ip_addr=ip_addr, connection_id=connection, dispname=name)
            online.add(connection, session.user_id, ip_addr)
            return reply("REDIRECT.HOME", session.user_id, session_update=session.token)

        session = auth.get_session(request.session_id, connection)
        if route == "AUTH.LOGOUT":
            if hasattr(message, "files"):
                message.files.disconnect(connection)
            auth.logout(request.session_id, connection)
            online.remove(connection)
            return reply("REDIRECT.LOGIN", session_update=DEFAULT_SESSION)

        if route == "DASH.HOME":
            return reply("OK.AUTH", "Welcome", session.user_id)

        if route == "SYS.USERS":
            ids = sorted(online.user_ids())
            marks = ",".join("?" * len(ids))   # chỉ ghép dấu ?, giá trị vẫn truyền qua tham số
            rows = auth.db.execute(
                f"SELECT user_id, username, dispname FROM users WHERE user_id IN ({marks})", ids
            ).fetchall()
            return reply("OK.USERS", json.dumps([dict(row) for row in rows]))

        if route == "DASH.SELF":
            user = auth.db.execute(
                "SELECT user_id, username, dispname FROM users WHERE user_id = ?", (session.user_id,)
            ).fetchone()
            if user is None:
                raise AppError("[!] Account không còn tồn tại", "SESSION_INVALID")
            return reply("OK.AUTH", user["user_id"], user["username"], user["dispname"])

        if route == "DASH.RENAME":
            user = auth.rename(session.user_id, args[0])
            return reply("OK.PROFILE", user["user_id"], user["username"], user["dispname"])

        if route == "MSG.GLOBAL":
            msg_content = args[0].decode('utf-8')
            
            # 1. Lưu vào Database (Chat Global thì receiver_id = None)
            message.send(session.user_id, None, msg_content)
            
            # 2. Lấy tên (dispname) của người gửi để hiển thị cho đẹp
            sender = auth.db.execute("SELECT dispname FROM users WHERE user_id = ?", (session.user_id,)).fetchone()
            sender_name = sender["dispname"] if sender else "Unknown"
            
            # 3. Tạo gói tin Notification để PUSH xuống cho mọi người
            # Format: EVT.RECV | - | "GLOBAL" | sender_id | sender_name | content
            push_frame = reply("EVT.RECV", "GLOBAL", session.user_id, sender_name, msg_content)
            
            # 4. Phát thanh (Broadcast) đến tất cả user đang online
            for user_conn in online.conns.keys():
                # Không cần gửi ngược lại cho chính người vừa chat
                if user_conn != connection:
                    try:
                        user_conn.send(push_frame)
                    except Exception:
                        pass # Kệ các kết nối bị đứt gãy giữa chừng
                        
            return reply("EVT.SENT", "GLOBAL")

        if route == "MSG.PRIVATE":
            target_id = args[0].decode('utf-8')
            msg_content = args[1].decode('utf-8')
            
            # 1. Lưu DB
            message.send(session.user_id, target_id, msg_content)
            
            # 2. Lấy tên (dispname) của người gửi
            sender = auth.db.execute("SELECT dispname FROM users WHERE user_id = ?", (session.user_id,)).fetchone()
            sender_name = sender["dispname"] if sender else "Unknown"
            
            # 3. Kiểm tra xem người nhận có đang online không?
            target_conn = online.get_conn(target_id)
            if target_conn:
                # Nếu online, lập tức Push (Unicast)
                push_frame = reply("EVT.RECV", "PRIVATE", session.user_id, sender_name, msg_content)
                try:
                    target_conn.send(push_frame)
                except Exception:
                    pass
            
            # Trả về thành công kèm ID người nhận để client biết đã gửi xong
            return reply("EVT.SENT", "PRIVATE", target_id)

        if route == "MSG.GETGLO":
            # 1. Gọi DB lấy lịch sử (receiver_id = None)
            rows = message.get(session.user_id, None)
            if rows == None:
                return reply("OK.HISTORY", "GLOBAL", "")
            # 2. Map tên người gửi vào từng tin nhắn (để UI in ra đẹp hơn)
            history = []
            for r in rows:
                sender = auth.db.execute("SELECT dispname FROM users WHERE user_id = ?", (r["sender_id"],)).fetchone()
                sender_name = sender["dispname"] if sender else "Unknown"
                history.append({
                    "sender_id": r["sender_id"],
                    "sender_name": sender_name,
                    "content": r["content"],
                    "time": r["time"]
                })
            
            # 3. Trả về cho Client dạng JSON
            return reply("OK.HISTORY", "GLOBAL", json.dumps(history))

        if route == "MSG.GETPRI":
            target_id = args[0].decode('utf-8')
            
            # 1. Gọi DB lấy lịch sử 2 chiều
            rows = message.get(session.user_id, target_id)
            if rows == None:
                return reply("OK.HISTORY", "GLOBAL", "")
            history = []
            for r in rows:
                # Nếu mình gửi thì ghi tên mình, nếu đối tác gửi thì tra tên đối tác
                sender_name = "Tôi" if r["sender_id"] == session.user_id else "Đối tác"
                history.append({
                    "sender_id": r["sender_id"],
                    "sender_name": sender_name,
                    "content": r["content"],
                    "time": r["time"]
                })
                
            return reply("OK.HISTORY", "PRIVATE", target_id, json.dumps(history))

        if route == "MSG.CHATS":
            return reply("OK.CHATS", json.dumps(message.chat(session.user_id)))
        
        raise AppError("[!] Route chưa được xử lý")   # thêm route vào ARG_COUNTS mà quên nhánh => lỗi rõ ràng

    except AppError as error:
        if error.code == "SESSION_INVALID":
            auth.disconnect(connection)
            online.remove(connection)
            return reply("DENY.AUTH", error.code, str(error), session_update=DEFAULT_SESSION)
        action = "DENY.AUTH" if error.code == "LOGIN_FAILED" else "ERROR.CLIENT"
        return reply(action, error.code, str(error))
