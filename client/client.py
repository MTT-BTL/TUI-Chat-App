"""
GLP TUI Client
Chạy từ workspace: python3 demo/app/app.py client [--host HOST] [--port PORT]
Giao diện hacker: python3 demo/app/app.py hacker
Tắt hiệu ứng mưa ký tự: GLP_FX=0 python3 demo/app/app.py hacker

Chia file:
  client.py          file này: App, nối mạng ↔ dữ liệu ↔ giao diện, xử lý từng loại phản hồi của server
  client_backend.py  mạng: TLS, luồng đọc / luồng ghi (không biết gì về giao diện)
  client_state.py    dữ liệu: người online, lịch sử từng kênh, số tin chưa đọc
  client_screens.py  LoginScreen, MainScreen
  client_widgets.py  widget: khung chat, sidebar, thông báo nổi, mưa ký tự…
  client_theme.py    bảng màu + hàm xử lý chữ
GLP.py / GLPConn.py dùng chung ở demo/app. Đổi tên: DASH.RENAME → OK.PROFILE.
"""
from __future__ import annotations

import json
import os
import sys
import time
from functools import partial

from textual import on
from textual.app import App
from textual.binding import Binding
from textual.message import Message

from GLP import DEFAULT_SESSION, Response
from .client_backend import Backend
from .client_screens import LoginScreen, MainScreen, RenameScreen
from .client_files import FileTransfers
from .client_state import GLOBAL_KEY, ChatState, Entry, key_uid, pm_key, rows_to_entries
from .client_theme import clean, clip, skin

# True: hiện thông báo nổi cho mọi EVT.RECV, kể cả khi đang mở đúng kênh đó.
# False: chỉ báo khi tin đến kênh khác với kênh đang xem.
TOAST_WHEN_VIEWING = True

# Quá thời gian này mà server chưa trả OK.HISTORY thì báo lỗi và cho phép thử lại khi mở lại kênh.
HISTORY_TIMEOUT = 10.0


class NetEvent(Message):
    """Cầu nối luồng mạng → luồng giao diện (post_message an toàn khi gọi từ thread khác)."""

    def __init__(self, kind: str, payload: object = None) -> None:
        super().__init__()
        self.kind, self.payload = kind, payload


class GLPApp(App):
    TITLE = "GLP"
    ENABLE_COMMAND_PALETTE = False
    LOGIN_SCREEN = LoginScreen
    MAIN_SCREEN = MainScreen
    PROFILE_SCREEN = RenameScreen
    CSS = skin("""
    Screen { background: @ink; }
    * {
        scrollbar-size-vertical: 1;
        scrollbar-background: @night;
        scrollbar-background-hover: @night;
        scrollbar-background-active: @night;
        scrollbar-color: @line2;
        scrollbar-color-hover: @dim;
        scrollbar-color-active: @cyan;
        scrollbar-corner-color: @night;
    }
    """)
    BINDINGS = [Binding("ctrl+q", "quit", "Thoát")]

    def __init__(self, host: str = "localhost", port: int = 8443) -> None:
        super().__init__()
        self.backend = Backend(host, port, self._emit_net)
        self.store = ChatState()
        self.session_id = DEFAULT_SESSION
        self.username = ""        # tên gõ ở ô đăng nhập
        self.ping_ms: float | None = None
        self.login_status: tuple[str, str] | None = None  # để LoginScreen đọc nếu mạng xong trước khi nó mở
        self._poll_t0 = 0.0
        # Số phản hồi "âm thầm" đang chờ (do chính client gửi), nuốt đi để khỏi in ra khung chat.
        self._silent = {"home": 0, "self": 0, "users": 0, "ping":0, "chats": 0}
        self._ping_timer = None
        self.files = FileTransfers(self)

    # ------------------------------------------------------------ vòng đời
    def on_mount(self) -> None:
        self.push_screen(self.LOGIN_SCREEN())
        self.backend.start()

    def on_unmount(self) -> None:
        self.files.close()
        self.backend.close()

    def _emit_net(self, kind: str, payload: object = None) -> None:
        try:
            self.post_message(NetEvent(kind, payload))  # gọi từ luồng mạng
        except Exception:
            pass

    @on(NetEvent)
    def route_net_event(self, event: NetEvent) -> None:
        kind = event.kind
        if kind == "frame":
            self.handle_response(event.payload)
        elif kind == "status":
            self.update_login_status(*event.payload)
        elif kind == "ready":
            self.update_login_status("Kênh bảo mật đã sẵn sàng. Mời bạn đăng nhập.", "ok")
        elif kind == "closed":
            self.handle_disconnect(event.payload)

    # ---------------------------------------------------------------- gửi
    def send_request(self, route: str, session: str, *args: bytes, silent: str | None = None) -> bool:
        err = self.backend.send(route, session, *args)
        if err:
            self._report(err)
            return False
        if silent:
            self._silent[silent] = min(self._silent[silent] + 1, 3)
        return True

    def request_users(self) -> None:
        """Hỏi danh sách online; thời gian khứ hồi của lần hỏi này cũng chính là ping hiển thị."""
        self._poll_t0 = time.monotonic()
        self.send_request("SYS.USERS", self.session_id, silent="users")

    def load_history(self, key: str) -> None:
        """Lấy lịch sử của một kênh (GLOBAL → MSG.GETGLO, PM → MSG.GETPRI). Mỗi kênh chỉ hỏi một lần;
        gọi lại khi đang chờ hoặc đã có rồi thì không làm gì."""
        st = self.store
        if not st.begin_load(key):
            return
        uid = key_uid(key)
        if uid is None:
            ok = self.send_request("MSG.GETGLO", self.session_id)
        else:
            ok = self.send_request("MSG.GETPRI", self.session_id, uid.encode())
        if not ok:
            st.abort_load(key)
            return
        self.set_timer(HISTORY_TIMEOUT, partial(self._history_timeout, key))

    def _history_timeout(self, key: str) -> None:
        t0 = self.store.loading.get(key)
        if t0 is None or time.monotonic() - t0 < HISTORY_TIMEOUT - 0.05:
            return  # đã xong, hoặc đây là timer cũ của một lần nạp trước đó
        self._fail_history(key, "không tải được lịch sử: server không trả OK.HISTORY (quá thời gian)")

    def _fail_history(self, key: str, reason: str) -> None:
        self.store.abort_load(key)
        if isinstance(self.screen, MainScreen):
            self.screen.history_failed(key, reason)

    # ----------------------------------------------------------------- nhận
    def handle_response(self, resp: Response) -> None:
        if resp.session_update != "-":
            self.session_id = resp.session_update
        action = resp.action
        if self.files.handle_response(resp):
            return
        args = [a.decode("utf-8", errors="replace") for a in resp.args]
        scr = self.screen
        # Khi dialog đổi tên đang mở, chat vẫn nhận events ở screen phía dưới.
        main = next((s for s in reversed(self.screen_stack) if isinstance(s, MainScreen)), None)

        # Tin đẩy từ server: luôn ghi vào ChatState trước, vẽ sau.
        if action == "EVT.RECV":
            self._on_recv(args, main)
            return
        if action == "EVT.SENT":
            return  # server xác nhận đã nhận tin của mình, tin đã hiện sẵn từ lúc gửi
        if action == "OK.HISTORY":
            self._on_history(args, main)
            return
        if action == "OK.CHATS":
            self._take_silent("chats")
            try: 
                users = json.loads(args[0])
                for u in users: 
                    self.store.touch_peer(u["user_id"], u["dispname"], True)
                if main is not None:
                    main.sync_sidebar()
            except:
                pass
            return
        # --- đăng nhập
        if isinstance(scr, LoginScreen):
            if scr.granted:
                return
            if action == "REDIRECT.HOME":
                self.store.reset()
                self.store.me_id = args[0] if args else ""
                self.store.me_user = self.username
                scr.grant_access()
                self.send_request("MSG.CHATS", self.session_id, silent="chats")
                if self._ping_timer is None:
                    self._ping_timer = self.set_interval(10.0, self.send_heartbeat)
            elif action.startswith(("ERROR", "DENY")):
                self.update_login_status(args[1] if len(args) > 1 else "Lỗi không xác định", "error")
                scr.shake()
            return

        # --- phiên hết hạn / đăng xuất
        if action == "REDIRECT.LOGIN":
            if self._ping_timer is None: self._ping_timer = self.set_interval(10.0, self.send_heartbeat)
            self.return_to_login("Đã đăng xuất.")
            return
        if action == "DENY.AUTH" and args and args[0] == "SESSION_INVALID":
            self.return_to_login(args[1] if len(args) > 1 else "Phiên đăng nhập không còn hợp lệ.")
            return

        if main is None:
            return
        if action == "OK.PONG":
            self._take_silent("ping")
            return
        # --- trong phòng chat
        if action == "OK.USERS":
            try:
                rows = json.loads(args[0])
            except (ValueError, IndexError):
                rows = []
            self.store.apply_online(rows if isinstance(rows, list) else [])
            main.sync_sidebar()
            if self._take_silent("users"):
                self.ping_ms = (time.monotonic() - self._poll_t0) * 1000
            else:
                main.post_server(action, args)
        elif action in ("OK.AUTH", "OK.PROFILE") and len(args) >= 3:
            st = self.store
            st.me_id, st.me_user, st.me_name = args[0], args[1], args[2] or args[1]
            main.profile_card.refresh()
            main.topbar.refresh()
            if action == "OK.PROFILE":
                if isinstance(scr, RenameScreen): scr.dismiss()
                main.post_system(f"Đã đổi tên thành {clean(st.me_name)}", "ok")
                main.show_notice("global", "Tên hiển thị đã cập nhật", st.me_name)
                self.request_users()
            elif not self._take_silent("self"):
                main.post_server(action, args)
        elif action == "OK.AUTH" and args[:1] == ["Welcome"]:  # DASH.HOME
            if not self._take_silent("home"):
                main.post_server(action, args)
        elif action.startswith(("ERROR", "DENY")):
            err = args[1] if len(args) > 1 else "Thao tác thất bại"
            if isinstance(scr, RenameScreen):
                scr.set_error(err)
                return
            main.post_system(f"DENIED · {err}", "error")
            main.show_notice("error", action, clip(err, 60))
            # Server từ chối trong lúc đang chờ lịch sử (vd chưa có route MSG.GETGLO) thì khỏi chờ hết timeout.
            for key in list(self.store.loading):
                self._fail_history(key, f"không tải được lịch sử: {err}")
        else:
            main.post_server(action, args)

    def _take_silent(self, name: str) -> bool:
        if self._silent[name] > 0:
            self._silent[name] -= 1
            return True
        return False

    def _on_history(self, args: list[str], main: MainScreen | None) -> None:
        """OK.HISTORY: ["GLOBAL", json] hoặc ["PRIVATE", target_id, json];
        json là list {sender_id, sender_name, content, time}."""
        st = self.store
        if args[:1] == ["GLOBAL"] and len(args) >= 2:
            key, payload = GLOBAL_KEY, args[1]
        elif args[:1] == ["PRIVATE"] and len(args) >= 3:
            key, payload = pm_key(args[1]), args[2]
        else:
            return
        try:
            rows = json.loads(payload)
        except ValueError:
            rows = []
        if not isinstance(rows, list):
            rows = []

        if key not in st.loading:
            # Không phải do client hỏi (vd người dùng tự gõ MSG.GETGLO) hoặc về trễ sau timeout:
            # không ghép vào kênh, chỉ báo số lượng để khỏi tạo tin trùng.
            if main is not None:
                main.post_system(f"history {st.title(key)}: server trả {len(rows)} tin", "info")
            return
        peer_name = st.title(key) if key_uid(key) is not None else None
        st.finish_load(key, rows_to_entries(rows, st.me_id, st.me_label, peer_name))
        if main is not None:
            main.history_loaded(key)

    def _on_recv(self, args: list[str], main: MainScreen | None) -> None:
        """EVT.RECV: [loại, sender_id, sender_name, nội dung]; loại là GLOBAL hoặc PRIVATE."""
        if len(args) < 4:
            return
        kind, uid, name, content = args[0], args[1], args[2] or "?", clean(args[3])
        st = self.store
        mine = bool(uid) and uid == st.me_id  # cùng tài khoản đăng nhập ở máy khác
        if kind == "GLOBAL":
            key = GLOBAL_KEY
        elif kind == "PRIVATE" and not mine:
            key = pm_key(uid)
            st.touch_peer(uid, name,from_inbox=False)
        else:
            return

        viewing = main is not None and st.active == key
        entry = Entry("msg", content, uid=uid, name=name, mine=mine)
        st.add(key, entry, read=viewing or mine)
        if main is None:
            return
        main.deliver(key, entry)

        if not mine and (TOAST_WHEN_VIEWING or not viewing):
            preview = content.split("\n", 1)[0]
            if kind == "GLOBAL":
                main.show_notice("global", f"GLOBAL · {name}", preview, key)
            else:
                main.show_notice("private", f"{name} nhắn riêng cho bạn", preview, key)

    # ---------------------------------------------------------- chuyển màn hình
    def enter_main(self) -> None:
        if isinstance(self.screen, LoginScreen):
            self.switch_screen(self.MAIN_SCREEN())

    def return_to_login(self, reason: str) -> None:
        self.files.reset(reason)
        self.store.reset()
        self.session_id = DEFAULT_SESSION
        self.login_status = (reason, "info")
        if self._ping_timer:
            self._ping_timer.pause()
            self._ping_timer = None
        if isinstance(self.screen, RenameScreen): self.pop_screen()
        if isinstance(self.screen, MainScreen):
            self.switch_screen(self.LOGIN_SCREEN())

    def update_login_status(self, text: str, kind: str = "info") -> None:
        self.login_status = (text, kind)
        if isinstance(self.screen, LoginScreen):
            self.screen.set_status(text, kind)

    def _report(self, text: str) -> None:
        if isinstance(self.screen, MainScreen):
            self.screen.post_system(text, "error")
            self.screen.show_notice("error", "Lỗi", clip(text, 60))
        else:
            self.update_login_status(text, "error")

    def handle_disconnect(self, reason: str) -> None:
        self.files.reset(reason)
        if isinstance(self.screen, RenameScreen): self.pop_screen()
        if self._ping_timer:
            self._ping_timer.pause()
            self._ping_timer = None
        if isinstance(self.screen, MainScreen):
            self.screen.post_system(reason, "error")
            self.screen.show_notice("error", "Mất kết nối", clip(reason, 60))
            self.screen.topbar.refresh()
        elif isinstance(self.screen, LoginScreen):
            self.update_login_status(reason, "error")

    def send_heartbeat(self) -> None:
        """Gửi PING ngầm mỗi 10 giây để giữ kết nối không bị Server đóng."""
        # Chỉ PING khi đã có session hợp lệ (đã đăng nhập)
        if self.session_id != DEFAULT_SESSION:
            # Truyền silent="ping" để giấu log OK.PONG khỏi khung chat
            self.send_request("SYS.PING", self.session_id, silent="ping")
