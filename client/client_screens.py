"""Hai màn hình: đăng nhập và phòng chat. Màn hình chỉ vẽ + nhận phím,
không đụng socket: muốn gửi gì thì gọi `self.app.send_request(...)`."""
from __future__ import annotations

import json
import time
from functools import partial

from rich.style import Style
from rich.text import Text

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Input, Static

from GLP import DEFAULT_SESSION
from .client_backend import parse_raw
from .file_ui import FileBar
from .client_state import GLOBAL_KEY, MAX_MSG_CHARS, Entry, key_uid, pm_key
from .client_theme import PAL, clean, clip, skin
from .client_widgets import (ChatInput, ChatView, ConvItem, HintBar, Notice, NoticeStack, ProfileCard,
                            RainField, SectionLabel, StatusLine, TopBar, build_logo)


# ============================================================================
# MÀN HÌNH 1: ĐĂNG NHẬP
# ============================================================================
class LoginScreen(Screen):
    CSS = skin("""
    LoginScreen { layers: bg fg; align: center middle; background: @ink; overflow: hidden hidden; }
    LoginScreen #login-box {
        layer: fg; width: 58; height: auto; padding: 1 3;
        background: @night; border: heavy @cyan;
        border-title-color: @cyan; border-title-style: bold; border-subtitle-color: @dim;
    }
    LoginScreen.-compact #login-box { padding: 0 3; }
    LoginScreen #logo { width: 100%; height: auto; margin-bottom: 1; }
    LoginScreen.-compact #logo { display: none; }
    LoginScreen #tagline { width: 100%; height: 1; margin-bottom: 1; content-align: center middle; color: @mist; }
    LoginScreen Input {
        width: 100%; height: 3; margin-bottom: 1; padding: 0 1;
        background: @dusk; color: @text; border: solid @line2; border-title-color: @mist;
    }
    LoginScreen Input:focus { border: solid @cyan; border-title-color: @cyan; background: @dusk; }
    LoginScreen Input > .input--placeholder { color: @dim; }
    LoginScreen Input > .input--cursor { background: @cyan; color: @ink; }
    LoginScreen #actions { width: 100%; height: 3; margin-top: 1; }
    LoginScreen Button {
        width: 1fr; height: 3; min-width: 12; margin: 0 1; border: solid @line2;
        background: @dusk; color: @haze; text-style: bold;
    }
    LoginScreen Button:hover { border: solid @magenta; color: @magenta; background: @dusk; }
    LoginScreen Button:focus { border: solid @cyan; text-style: bold; }
    LoginScreen Button.-active { background: @line; }
    LoginScreen #btn-login { background: @cyan; color: @ink; border: solid @cyan; }
    LoginScreen #btn-login:hover { background: @lime; color: @ink; border: solid @lime; }
    LoginScreen #btn-login:focus { background: @cyan; color: @ink; border: solid @text; text-style: bold; }
    LoginScreen #btn-login.-active { background: @lime; border: solid @lime; }
    LoginScreen #status-log { margin-top: 1; }
    LoginScreen.-compact #status-log { margin-top: 0; }
    """)

    def __init__(self) -> None:
        super().__init__()
        self.granted = False

    def compose(self) -> ComposeResult:
        self.rain = RainField()
        yield self.rain
        with Vertical(id="login-box"):
            yield Static(build_logo(), id="logo")
            yield Static("// kênh TLS 1.3 · giao thức GLP", id="tagline")
            yield Input(placeholder="tên đăng nhập", id="input-user")
            yield Input(placeholder="mật khẩu", id="input-pass", password=True)
            with Horizontal(id="actions"):
                yield Button("Đăng nhập", id="btn-login")
                yield Button("Đăng ký", id="btn-register")
            yield StatusLine(id="status-log")

    def on_mount(self) -> None:
        box = self.query_one("#login-box")
        box.border_title = " ◈ access terminal "
        box.border_subtitle = " glp/1 "
        self.query_one("#input-user").border_title = " tài khoản "
        self.query_one("#input-pass").border_title = " mật khẩu "
        text, kind = self.app.login_status or ("Đang kết nối tới máy chủ…", "busy")
        self.set_status(text, kind)
        self.query_one("#input-user").focus()

    def on_resize(self, event) -> None:
        self.set_class(event.size.height < 30, "-compact")

    def set_status(self, text: str, kind: str = "info") -> None:
        self.query_one("#status-log", StatusLine).set(text, kind)
        if kind == "error":
            self._flash()

    def _flash(self) -> None:
        """Viền đỏ 0.8s khi lỗi (hai lần đổi style, không có animation chạy liên tục)."""
        box = self.query_one("#login-box")
        box.styles.border = ("heavy", PAL["red"])
        self.set_timer(0.8, partial(setattr, box.styles, "border", ("heavy", PAL["cyan"])))

    def shake(self) -> None:
        box = self.query_one("#login-box")
        for i, dx in enumerate((3, -3, 2, -2, 1, -1, 0)):
            self.set_timer(0.045 * (i + 1), partial(setattr, box.styles, "offset", (dx, 0)))

    def grant_access(self) -> None:
        if self.granted:
            return
        self.granted = True
        self.set_status("ACCESS GRANTED · đang mở phòng chat", "ok")
        self.rain.speed = 3.5  # mưa tăng tốc như đang "lao" vào hệ thống
        self.set_timer(0.9, self.app.enter_main)

    @on(Button.Pressed)
    def handle_auth(self, event: Button.Pressed) -> None:
        if self.granted:
            return
        username = self.query_one("#input-user", Input).value.strip()
        password = self.query_one("#input-pass", Input).value.strip()
        if not username or not password:
            self.set_status("Nhập đủ tên đăng nhập và mật khẩu.", "error")
            self.shake()
            return
        route = "AUTH.LOGIN" if event.button.id == "btn-login" else "AUTH.REGISTER"
        self.set_status(f"Đang gửi {route}…", "busy")
        self.app.username = username
        self.app.send_request(route, DEFAULT_SESSION, username.encode(), password.encode())

    @on(Input.Submitted)
    def on_enter(self, event: Input.Submitted) -> None:
        if event.input.id == "input-user":
            self.query_one("#input-pass").focus()
        else:
            self.query_one("#btn-login", Button).press()


# ============================================================================
# MÀN HÌNH 2: PHÒNG CHAT
# ============================================================================
def format_args(args: list[str]) -> str:
    """Phản hồi thô của server → vài dòng chữ dễ đọc (JSON dict/list được trải ra)."""
    lines: list[str] = []
    for raw in args:
        s = raw.strip()
        if s[:1] in ("{", "["):
            try:
                obj = json.loads(s)
            except ValueError:
                obj = None
            if isinstance(obj, dict):
                lines += [f"{k}  {v}" for k, v in obj.items()]
                continue
            if isinstance(obj, list):
                for item in obj:
                    lines.append("  ".join(f"{k}={v}" for k, v in item.items())
                                 if isinstance(item, dict) else str(item))
                continue
        lines.append(raw)
    return "\n".join(lines) or "(không có dữ liệu)"


class RenameScreen(ModalScreen):
    """Giữ dialog mở đến khi server xác nhận; Escape hủy chỉnh sửa."""
    CSS = skin("""
    RenameScreen { align: center middle; background: @ink 80%; }
    RenameScreen #profile-edit {
        width: 52; max-width: 95%; height: auto; padding: 1 2;
        background: @night; border: round @cyan;
    }
    RenameScreen #rename-heading { height: 2; color: @cyan; text-style: bold; }
    RenameScreen Input { margin-bottom: 1; background: @dusk; border: solid @line2; }
    RenameScreen Input:focus { border: solid @cyan; }
    RenameScreen #rename-status { height: 2; color: @mist; }
    RenameScreen Horizontal { height: 3; }
    RenameScreen Button { width: 1fr; min-width: 8; margin: 0 1; }
    RenameScreen #rename-save { background: @cyan; color: @ink; border: none; }
    """)
    BINDINGS = [Binding("escape", "cancel", "Hủy")]

    def compose(self) -> ComposeResult:
        with Vertical(id="profile-edit"):
            yield Static("ĐỔI TÊN HIỂN THỊ", id="rename-heading")
            yield Input(value=self.app.store.me_label, max_length=32, id="displayname")
            yield Static("1–32 ký tự · username và tài khoản vẫn giữ nguyên", id="rename-status")
            with Horizontal():
                yield Button("Hủy", id="rename-cancel")
                yield Button("Lưu tên", id="rename-save")

    def on_mount(self) -> None:
        self.query_one("#displayname", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss()

    def set_error(self, text: str) -> None:
        self.query_one("#rename-status", Static).update(Text(text, style=PAL["red"]))
        self.query_one("#rename-save", Button).disabled = False

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "rename-cancel": self.dismiss()
        else: self.save_name()

    @on(Input.Submitted, "#displayname")
    def submitted(self) -> None:
        self.save_name()

    def save_name(self) -> None:
        if self.query_one("#rename-save", Button).disabled: return
        name = self.query_one("#displayname", Input).value.strip()
        if not name or not name.isprintable():
            self.set_error("Tên cần 1–32 ký tự, không chứa ký tự điều khiển.")
            return
        if self.app.send_request("DASH.RENAME", self.app.session_id, name.encode("utf-8")):
            self.query_one("#rename-save", Button).disabled = True
            self.query_one("#rename-status", Static).update("Đang lưu tên trên server…")
            self.set_timer(10, self.save_timeout)
        else:
            self.set_error("Chưa gửi được. Kiểm tra kết nối rồi thử lại.")

    def save_timeout(self) -> None:
        if self.query_one("#rename-save", Button).disabled:
            self.set_error("Chưa nhận xác nhận. Hãy tải lại hồ sơ trước khi thử lại.")


class MainScreen(Screen):
    """Phòng chat: sidebar (GLOBAL ở đầu, rồi người online, rồi hồ sơ), khung chat, dòng lệnh."""

    CSS = skin("""
    MainScreen { layers: base overlay; background: @ink; }
    MainScreen #root { layer: base; width: 100%; height: 100%; }
    MainScreen #body { height: 1fr; padding: 0 1; }

    MainScreen #sidebar {
        width: 30; height: 100%; margin-right: 1; background: @night; border: solid @line2;
        border-title-color: @cyan; border-title-style: bold; border-subtitle-color: @mist;
    }
    MainScreen.-narrow #sidebar { width: 26; }
    MainScreen #peer-scroll { height: 1fr; background: @night; }

    MainScreen #chat-area {
        width: 1fr; height: 100%; background: @night; border: solid @line2;
        border-title-color: @cyan; border-title-style: bold; border-subtitle-color: @mist;
    }
    MainScreen #chat-log { height: 1fr; }

    MainScreen #prompt { height: 2; padding: 0 1; background: @dusk; border-top: solid @line2; }
    MainScreen #prompt.-insert { border-top: solid @cyan; }
    MainScreen #prompt-label { width: auto; height: 1; padding: 0 1 0 0; }
    MainScreen #chat-input {
        width: 1fr; height: 1; padding: 0; border: none; background: @dusk; color: @text;
    }
    MainScreen #chat-input:focus { border: none; background: @dusk; }
    MainScreen #chat-input > .input--placeholder { color: @dim; }
    MainScreen #chat-input > .input--cursor { background: @cyan; color: @ink; }
    """)

    BINDINGS = [
        Binding("ctrl+n", "edit_profile", "Đổi tên", priority=True),
        Binding("ctrl+f", "open_files", "File", priority=True),
        Binding("escape", "normal_mode", "Thoát gõ", show=False),
        Binding("i", "insert_mode", "Nhập tin", show=False),
        Binding("j", "next_conv(1)", show=False),
        Binding("k", "next_conv(-1)", show=False),
        Binding("g", "goto_global", show=False),
        Binding("f5,ctrl+r", "force_refresh", show=False),
    ]

    NORMAL_HINT = "nhấn i để gõ tin nhắn…"
    INSERT_HINT = "tin nhắn, hoặc lệnh thô như DASH.SELF…"
    CONV_ITEM = ConvItem

    def __init__(self) -> None:
        super().__init__()
        self.typing = False
        self.conv_items: dict[str, ConvItem] = {}
        self._order: list[str] = []

    # ---------------------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        self.topbar = TopBar()
        self.global_item = ConvItem(GLOBAL_KEY, "GLOBAL", is_global=True)
        self.online_label = SectionLabel("online")
        self.peer_scroll = VerticalScroll(id="peer-scroll")
        self.profile_card = ProfileCard()
        self.chat = ChatView(id="chat-log")
        self.prompt_label = Static(id="prompt-label")
        self.chat_input = ChatInput(placeholder=self.NORMAL_HINT, disabled=True,
                                    id="chat-input", max_length=MAX_MSG_CHARS)
        self.hintbar = HintBar()
        self.notices = NoticeStack()
        self.sidebar = Vertical(id="sidebar")
        self.chat_area = Vertical(id="chat-area")
        self.prompt = Horizontal(id="prompt")

        with Vertical(id="root"):
            yield self.topbar
            with Horizontal(id="body"):
                with self.sidebar:
                    yield self.global_item
                    yield self.online_label
                    yield self.peer_scroll
                    yield self.profile_card
                with self.chat_area:
                    yield FileBar()
                    yield self.chat
                    with self.prompt:
                        yield self.prompt_label
                        yield self.chat_input
            yield self.hintbar
        yield self.notices

    def on_mount(self) -> None:
        self.sidebar.border_title = " // channels "
        self.conv_items = {GLOBAL_KEY: self.global_item}
        self._set_mode(False)
        self.sync_sidebar()

        app = self.app
        app.files.load_peers()
        # DASH.* gửi trước: server trả lời theo đúng thứ tự nên tên hiển thị của mình đã có
        # khi lịch sử GLOBAL (gửi ngay sau trong switch_to) về tới.
        app.send_request("DASH.HOME", app.session_id, silent="home")
        app.send_request("DASH.SELF", app.session_id, silent="self")
        self.switch_to(app.store.active)

        for delay, text, level in (
            (0.2, "init secure channel…", "info"),
            (0.5, "TLS 1.3 handshake · OK", "ok"),
            (0.8, "session attached · dashboard ready", "ok"),
            (1.1, "i = gõ tin · Esc = thoát gõ · j/k = đổi kênh", "info"),
        ):
            self.set_timer(delay, partial(self.post_system, text, level, GLOBAL_KEY))

        self.set_timer(0.4, app.request_users)
        self.set_interval(5.0, app.request_users)   # danh sách online, 5 giây một lần
        self.set_interval(1.0, self._tick)          # đồng hồ + thời gian phiên, đây là timer duy nhất
        self.chat.focus()

    def on_resize(self, event) -> None:
        self.set_class(event.size.width < 90, "-narrow")

    def _tick(self) -> None:
        self.topbar.refresh()
        self.profile_card.refresh()

    # ----------------------------------------------------------- chế độ Vim
    def _set_mode(self, typing: bool) -> None:
        self.typing = typing
        self.chat_input.disabled = not typing
        self.chat_input.placeholder = self.INSERT_HINT if typing else self.NORMAL_HINT
        self.prompt.set_class(typing, "-insert")
        self.hintbar.set_mode(typing)
        if typing:
            self.chat_area.border_subtitle = f"[b {PAL['ink']} on {PAL['cyan']}] insert [/]"
        else:
            self.chat_area.border_subtitle = f"[{PAL['haze']} on {PAL['line']}] normal [/]"

    def action_normal_mode(self) -> None:
        self._set_mode(False)
        self.chat.focus()

    def action_insert_mode(self) -> None:
        self._set_mode(True)
        self.call_after_refresh(self.chat_input.focus)

    def action_force_refresh(self) -> None:
        self.post_system("làm mới danh sách online…", "info")
        self.app.request_users()

    @on(ProfileCard.EditRequested)
    def action_edit_profile(self) -> None:
        self.app.push_screen(self.app.PROFILE_SCREEN())

    def action_goto_global(self) -> None:
        self.switch_to(GLOBAL_KEY)

    def action_open_files(self) -> None:
        self.app.files.open()

    def action_next_conv(self, delta: int) -> None:
        st = self.app.store
        keys = [GLOBAL_KEY] + [pm_key(p.uid) for p in st.visible_peers()]
        i = keys.index(st.active) if st.active in keys else 0
        self.switch_to(keys[(i + delta) % len(keys)])

    # ------------------------------------------------------------- sidebar
    def sync_sidebar(self) -> None:
        """Đồng bộ danh sách bên trái với ChatState. Chỉ dựng lại widget khi danh sách người thay đổi;
        còn lại (badge chưa đọc, đang chọn, online/offline) chỉ cập nhật hàng nào đổi."""
        st = self.app.store
        peers = st.visible_peers()
        order = [p.uid for p in peers]
        if order != self._order:
            self._order = order
            for key in [k for k in self.conv_items if k != GLOBAL_KEY]:
                del self.conv_items[key]
            self.peer_scroll.remove_children()
            items = []
            for p in peers:
                item = self.CONV_ITEM(pm_key(p.uid), p.name)
                self.conv_items[item.key] = item
                items.append(item)
            if items:
                self.peer_scroll.mount(*items)
        for key, item in self.conv_items.items():
            if key == GLOBAL_KEY:
                item.sync("GLOBAL", st.unread.get(key, 0), st.active == key, True)
            else:
                peer = st.peers.get(key_uid(key))
                if peer:
                    item.sync(peer.name, st.unread.get(key, 0), st.active == key, peer.online)
        self.online_label.set_count(st.online_total)
        self.sidebar.border_subtitle = f" {st.online_total} online " if st.online_total else ""
        self.topbar.refresh()
        self.profile_card.refresh()

    @on(ConvItem.Picked)
    def _picked(self, event: ConvItem.Picked) -> None:
        self.switch_to(event.key)
        self.action_insert_mode()

    @on(Notice.Picked)
    def _notice_picked(self, event: Notice.Picked) -> None:
        event.notice.remove()
        if event.target:
            self.switch_to(event.target)
            self.action_insert_mode()

    # ------------------------------------------------------------- đổi kênh
    def _hint(self, key: str) -> str:
        st = self.app.store
        if key_uid(key) is None:
            return "kênh chung · mọi người đang online đều nhận được"
        return f"tin riêng với {st.title(key)}"

    def switch_to(self, key: str) -> None:
        st = self.app.store
        uid = key_uid(key)
        if uid is not None and uid not in st.peers:
            return
        st.open(key)
        self.query_one(FileBar).sync()
        self.app.load_history(key)  # chỉ gửi request nếu kênh này chưa nạp lịch sử
        if uid is None:
            title, color, who = "GLOBAL", PAL["cyan"], "global"
        else:
            title, color, who = "@" + st.title(key), PAL["magenta"], clip(st.title(key), 12)
        self.chat.show(st.hist(key), self._hint(key), loading=key in st.loading)

        self.chat_area.border_title = Text(f" // {clean(title)} ")
        self.chat_area.styles.border = ("solid", color)
        self.chat_area.styles.border_title_color = color
        label = Text(no_wrap=True)
        label.append("glp@", Style(color=PAL["dim"]))
        label.append(who, Style(color=color, bold=True))
        label.append(" ❯", Style(color=PAL["cyan"], bold=True))
        self.prompt_label.update(label)
        self.sync_sidebar()

    # ------------------------------------------------- nhận dữ liệu (App gọi)
    def deliver(self, key: str, entry: Entry) -> None:
        """Tin mới đã được ghi vào ChatState; chỉ vẽ ra nếu đó là kênh đang mở."""
        if key == self.app.store.active:
            self.chat.append(entry)
        self.sync_sidebar()

    def history_loaded(self, key: str) -> None:
        """Lịch sử của `key` đã được ghép vào ChatState. Chỉ vẽ lại nếu đó là kênh đang mở."""
        st = self.app.store
        if key == st.active:
            self.chat.show(st.hist(key), self._hint(key))
        self.sync_sidebar()

    def history_failed(self, key: str, reason: str) -> None:
        self._record(Entry("sys", reason, level="warn"), key)
        if key == self.app.store.active:
            self.chat.set_loading(False)

    def show_notice(self, kind: str, title: str, body: str = "", target: str = "") -> None:
        self.notices.push(kind, title, body, target)

    def _record(self, entry: Entry, key: str | None = None) -> None:
        st = self.app.store
        key = key or st.active
        st.add(key, entry, read=True)
        if key == st.active:
            self.chat.append(entry)

    def post_system(self, text: str, level: str = "info", key: str | None = None) -> None:
        self._record(Entry("sys", text, level=level), key)

    def post_server(self, action: str, args: list[str]) -> None:
        self._record(Entry("srv", format_args(args), name=action))

    # ------------------------------------------------------------- gửi tin
    @on(Input.Submitted, "#chat-input")
    def on_chat_submit(self, event: Input.Submitted) -> None:
        msg = clean(event.value).strip()
        if not msg:
            return
        self.chat_input.value = ""
        self.chat_input.remember(msg)
        app, st = self.app, self.app.store

        if msg == "/name":
            self.action_edit_profile()
            return
        if msg.startswith("/name "):
            app.send_request("DASH.RENAME", app.session_id, msg[6:].strip().encode("utf-8"))
            return

        raw = parse_raw(msg)  # lệnh thô như DASH.SELF để test, gửi nguyên văn
        if raw:
            self.post_system(f"$ {msg}", "info")
            app.send_request(raw[0], app.session_id, *raw[1])
            return

        key = st.active
        uid = key_uid(key)
        if uid is None:
            ok = app.send_request("MSG.GLOBAL", app.session_id, msg.encode("utf-8"))
        else:
            ok = app.send_request("MSG.PRIVATE", app.session_id, uid.encode(), msg.encode("utf-8"))
        if ok:  # hiện ngay tin của mình, không đợi server (EVT.SENT chỉ để xác nhận)
            entry = Entry("msg", msg, ts=time.time(), uid=st.me_id, name=st.me_label, mine=True)
            st.add(key, entry, read=True)
            self.chat.append(entry, force=True)
