"""NEON // GLP: cùng backend/state/routes với client, giao diện console riêng."""
from datetime import datetime

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.strip import Strip
from textual.widgets import Button, Input, Static

from .client import GLPApp
from .file_ui import FileBar
from .client_screens import LoginScreen, MainScreen, RenameScreen
from .client_state import GLOBAL_KEY, MAX_MSG_CHARS, key_uid
from .client_theme import clip, wrap_text
from .client_widgets import (ChatInput, ChatView, ConvItem, HintBar, NoticeStack,
                             ProfileCard, RainField, SectionLabel, StatusLine, TopBar, _stamp)

BLACK, PANEL, GREEN, MUTED = "#020705", "#06110c", "#83ffad", "#3f7858"
ICE, WHITE, AMBER, RED = "#82e9ff", "#d0f5df", "#ffcb6b", "#ff6e83"


class MatrixRain(RainField):
    GLYPHS = "01<>/:;{}[]+-*"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._blank = Style(bgcolor=BLACK)
        self._levels = [[
            Style(color=WHITE if k == 0 else GREEN, bgcolor=BLACK,
                  bold=k == 0, dim=k > 3)
            for k in range(self.TRAIL + 1)
        ] for _ in range(self.BANDS)]


class HackerLogin(LoginScreen):
    CSS = LoginScreen.CSS.replace("LoginScreen", "HackerLogin") + """
    HackerLogin { background: #020705; }
    HackerLogin MatrixRain { background: #020705; }
    HackerLogin #login-box {
        width: 66; max-width: 95%; background: #06110c; border: heavy #83ffad;
        border-title-color: #83ffad; border-subtitle-color: #3f7858;
    }
    HackerLogin #tagline { color: #3f7858; }
    HackerLogin Input { background: #091c12; color: #d0f5df; border: solid #214c32; }
    HackerLogin Input:focus { background: #091c12; border: solid #83ffad; }
    HackerLogin Input > .input--cursor { background: #83ffad; color: #020705; }
    HackerLogin #btn-login { background: #83ffad; color: #020705; border: solid #83ffad; }
    HackerLogin #btn-register { background: #091c12; color: #83ffad; border: solid #214c32; }
    HackerLogin Button:hover { background: #143723; border: solid #82e9ff; color: #82e9ff; }
    """

    def compose(self) -> ComposeResult:
        self.rain = MatrixRain()
        yield self.rain
        logo = Text("N E O N  / /  G L P\n", style=f"bold {GREEN}", justify="center")
        logo.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n", style=MUTED)
        logo.append("ENTER THE SIGNAL", style=f"bold {WHITE}")
        with Vertical(id="login-box"):
            yield Static(logo, id="logo")
            yield Static("MESSAGE CONSOLE  /  LAN EDITION", id="tagline")
            yield Input(placeholder="operator username", id="input-user")
            yield Input(placeholder="access password", id="input-pass", password=True)
            with Horizontal(id="actions"):
                yield Button("CONNECT", id="btn-login")
                yield Button("NEW ID", id="btn-register")
            yield StatusLine(id="status-log")

    def on_mount(self):
        # Textual tự gọi on_mount của base class; chỉ thêm branding sau đó.
        self.call_after_refresh(self._brand)

    def _brand(self):
        self.query_one("#login-box").border_title = Text(" // NEON ACCESS ")
        self.query_one("#login-box").border_subtitle = " // establish your signal "


class HackerTopBar(TopBar):
    def render(self):
        st, backend = self.app.store, self.app.backend
        out = Text("NEON", style=f"bold {GREEN}", no_wrap=True, overflow="ellipsis")
        out.append(" // GLP   ", style=MUTED)
        out.append("PRIVATE" if key_uid(st.active) else "GLOBAL", style=f"bold {ICE}")
        out.append("  /  " + clip(st.title(st.active), 24), style=WHITE)
        out.append("   ● LIVE" if backend.connected else "   ○ OFFLINE",
                   style=GREEN if backend.connected else RED)
        return out


class SignalBar(Static):
    """Số liệu lấy từ state/backend thật; cập nhật cùng timer 1 Hz của screen."""
    def render(self):
        app, st = self.app, self.app.store
        ping = "--" if app.ping_ms is None else f"{app.ping_ms:.0f}ms"
        out = Text(no_wrap=True, overflow="ellipsis")
        for label, value in (
            ("LINK", f"{app.backend.host}:{app.backend.port}"),
            ("RTT", ping), ("NODES", str(st.online_total)),
            ("UNREAD", str(st.total_unread())), ("CLOCK", f"{datetime.now():%H:%M:%S}"),
        ):
            out.append(f" {label} ", style=MUTED)
            out.append(value + "  ", style=f"bold {GREEN}")
        return out


class HackerProfile(ProfileCard):
    def render(self):
        st, live = self.app.store, self.app.backend.connected
        width = max(1, self.content_size.width)
        out = Text(no_wrap=True, overflow="crop")
        for i, (text, color) in enumerate((
            (st.me_label, GREEN), ("@" + st.me_user, WHITE),
            ("● SIGNAL ONLINE" if live else "○ SIGNAL LOST", GREEN if live else RED),
            ("^N  EDIT ALIAS", MUTED),
        )):
            if i: out.append("\n")
            out.append(clip(text, width), style=f"bold {color}" if i == 0 else color)
        return out


class HackerNode(ConvItem):
    def render(self):
        width = max(1, self.content_size.width)
        badge = f" +{self.unread}" if self.unread else ""
        prefix = "> " if self.active else "  "
        dot = "# " if self.is_global else "● " if self.online else "○ "
        out = Text(prefix + dot, style=GREEN if self.active or self.online else MUTED,
                   no_wrap=True, overflow="crop")
        out.append(clip(self.label, width - 4 - len(badge)), style=WHITE if self.active else GREEN)
        out.append(badge, style=f"bold {AMBER}")
        return out


class HackerChat(ChatView):
    """Cuộn ảo có sẵn, nhưng vẽ tin theo console log thay vì khối bubble."""
    def _make_rows(self, entry, width):
        self._prev = entry
        blank = Style(bgcolor=BLACK)
        stamp = Style(color=MUTED, bgcolor=BLACK)
        name = entry.name or ("system" if entry.kind == "sys" else "?")
        accent = AMBER if entry.mine else GREEN
        if entry.kind == "sys": accent = RED if entry.level == "error" else ICE
        head = [Segment(_stamp(entry.ts) + "  ", stamp),
                Segment(clip(name, max(1, width - 12)), Style(color=accent, bgcolor=BLACK, bold=True))]
        rows = [Strip(head).adjust_cell_length(width, blank)]
        for line in wrap_text(entry.text, max(8, width - 4)):
            rows.append(Strip([
                Segment("  └ " if len(rows) == 1 else "    ", stamp),
                Segment(line, Style(color=WHITE, bgcolor=BLACK)),
            ]).adjust_cell_length(width, blank))
        rows.append(Strip.blank(width, blank))
        return rows

    def render_line(self, y):
        index = y + self.scroll_offset.y
        if 0 <= index < len(self._rows): return self._rows[index]
        width = self.size.width
        if not self._rows and y == self.size.height // 2:
            text = "   // receiving history…" if self.loading else "   // waiting for a signal"
            return Strip([Segment(text, Style(color=MUTED, bgcolor=BLACK))]).adjust_cell_length(
                width, Style(bgcolor=BLACK))
        return Strip.blank(width, Style(bgcolor=BLACK))


class HackerHints(HintBar):
    @staticmethod
    def _build(pairs):
        out = Text(no_wrap=True, overflow="ellipsis")
        for key, label in pairs:
            out.append(f" [{key}] ", style=f"bold {GREEN}")
            out.append(label + "  ", style=MUTED)
        return out


class HackerRename(RenameScreen):
    CSS = RenameScreen.CSS.replace("RenameScreen", "HackerRename") + """
    HackerRename #profile-edit { background: #06110c; border: round #83ffad; }
    HackerRename #rename-heading { color: #83ffad; }
    HackerRename Input { background: #091c12; color: #d0f5df; border: solid #214c32; }
    HackerRename Input:focus { border: solid #83ffad; }
    HackerRename #rename-save { background: #83ffad; color: #020705; }
    """


class HackerMain(MainScreen):
    CONV_ITEM = HackerNode
    CSS = MainScreen.CSS.replace("MainScreen", "HackerMain") + """
    HackerMain { background: #020705; }
    HackerMain #body { padding: 0 1; }
    HackerMain HackerTopBar { height: 2; padding: 0 2; background: #0d2417; }
    HackerMain #signal-bar { height: 2; padding: 0 1; background: #020705; }
    HackerMain #sidebar {
        width: 29; margin: 0 0 0 1; background: #06110c; border: solid #214c32;
        border-title-color: #83ffad; border-subtitle-color: #3f7858;
    }
    HackerMain.-narrow #sidebar { width: 23; }
    HackerMain.-tiny #sidebar { display: none; }
    HackerMain #peer-scroll { background: #06110c; }
    HackerMain #chat-area { background: #020705; border: solid #214c32; border-title-color: #83ffad; }
    HackerMain #chat-log { background: #020705; padding: 1; }
    HackerMain HackerNode { background: #06110c; }
    HackerMain HackerNode:hover { background: #102c1b; }
    HackerMain HackerNode.-active { background: #163b25; }
    HackerMain HackerProfile { background: #0d2417; border-top: solid #214c32; }
    HackerMain HackerProfile:hover { background: #163b25; }
    HackerMain #prompt { background: #091c12; height: 3; padding: 0 1; border-top: solid #214c32; }
    HackerMain #prompt.-insert { border-top: solid #83ffad; }
    HackerMain #chat-input, HackerMain #chat-input:focus { background: #091c12; color: #d0f5df; }
    HackerMain #chat-input > .input--cursor { background: #83ffad; color: #020705; }
    HackerMain HackerHints { background: #0d2417; height: 1; }
    """
    INSERT_HINT = "message / /name new alias / DASH.SELF…"
    NORMAL_HINT = "[ i ] open transmit prompt"

    def compose(self) -> ComposeResult:
        self.topbar = HackerTopBar()
        self.global_item = HackerNode(GLOBAL_KEY, "GLOBAL FEED", is_global=True)
        self.online_label = SectionLabel("nodes")
        self.peer_scroll = VerticalScroll(id="peer-scroll")
        self.profile_card = HackerProfile()
        self.chat = HackerChat(id="chat-log")
        self.prompt_label = Static(id="prompt-label")
        self.chat_input = ChatInput(placeholder=self.NORMAL_HINT, disabled=True,
                                    id="chat-input", max_length=MAX_MSG_CHARS)
        self.hintbar = HackerHints()
        self.notices = NoticeStack()
        self.sidebar, self.chat_area = Vertical(id="sidebar"), Vertical(id="chat-area")
        self.prompt = Horizontal(id="prompt")
        self.signal_bar = SignalBar(id="signal-bar")
        with Vertical(id="root"):
            yield self.topbar
            yield self.signal_bar
            with Horizontal(id="body"):
                with self.chat_area:
                    yield FileBar()
                    yield self.chat
                    with self.prompt:
                        yield self.prompt_label
                        yield self.chat_input
                with self.sidebar:
                    yield self.global_item
                    yield self.online_label
                    yield self.peer_scroll
                    yield self.profile_card
            yield self.hintbar
        yield self.notices

    def on_mount(self):
        self.call_after_refresh(setattr, self.sidebar, "border_title", Text(" // NETWORK NODES "))

    def on_resize(self, event):
        self.set_class(event.size.width < 65, "-tiny")

    def _tick(self):
        super()._tick()
        self.signal_bar.refresh()

    def switch_to(self, key):
        super().switch_to(key)
        private = key_uid(self.app.store.active) is not None
        color = ICE if private else GREEN
        self.chat_area.styles.border = ("solid", color)
        self.chat_area.styles.border_title_color = color
        self.prompt_label.update(Text("tx@" + ("private" if private else "global") + " > ",
                                      style=f"bold {color}"))


class HackerApp(GLPApp):
    TITLE = "NEON // GLP"
    LOGIN_SCREEN = HackerLogin
    MAIN_SCREEN = HackerMain
    PROFILE_SCREEN = HackerRename
    CSS = GLPApp.CSS + """
    Screen { background: #020705; }
    * {
        scrollbar-background: #06110c;
        scrollbar-color: #214c32;
        scrollbar-color-hover: #83ffad;
        scrollbar-color-active: #83ffad;
    }
    """
