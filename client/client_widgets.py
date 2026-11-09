"""Các widget của GLP client. Nguyên tắc cho nhẹ:
  * Hầu hết widget tĩnh: chỉ `refresh()` khi dữ liệu đổi, không có timer chạy nền.
  * Timer duy nhất: đồng hồ 1 Hz (ở MainScreen), mưa ký tự (chỉ màn đăng nhập),
    và spinner (chỉ chạy khi đang "busy").
  * Khung chat (`ChatView`) tự vẽ theo kiểu cuộn ảo: chỉ trả Strip cho các dòng nhìn thấy,
    chat dài mấy trăm tin vẫn mượt, đổi kênh chỉ là dựng lại từ danh sách tin trong bộ nhớ.
"""
from __future__ import annotations

import random
import time
from datetime import datetime
from functools import lru_cache

from rich.align import Align
from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style
from rich.text import Text

from textual.binding import Binding
from textual.containers import Vertical
from textual.geometry import Size
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Input, Static

from .client_state import Entry, key_uid
from .client_theme import FX, PAL, author_color, clip, grad, mix, skin, wrap_text

SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

LOGO = [
    " ██████╗ ██╗     ██████╗ ",
    "██╔════╝ ██║     ██╔══██╗",
    "██║  ███╗██║     ██████╔╝",
    "██║   ██║██║     ██╔═══╝ ",
    "╚██████╔╝███████╗██║     ",
    " ╚═════╝ ╚══════╝╚═╝     ",
]


def build_logo() -> Align:
    """Logo gradient cyan→violet, dựng đúng một lần (không animate)."""
    out = Text(no_wrap=True)
    span = len(LOGO[0]) + len(LOGO) * 2
    for r, row in enumerate(LOGO):
        if r:
            out.append("\n")
        for c, ch in enumerate(row):
            if ch == " ":
                out.append(" ")
                continue
            col = grad((c + r * 2) / span)
            if ch != "█":  # phần nét đổ bóng tối hơn phần khối
                col = mix(col, PAL["dusk"], 0.45)
            out.append(ch, Style(color=col, bold=True))
    return Align.center(out)


# ============================================================================
# MÀN ĐĂNG NHẬP
# ============================================================================
class RainField(Widget):
    """Mưa ký tự nền. 10 fps, chỉ vẽ những cột đang có giọt, và dừng hẳn khi rời màn hình."""

    DEFAULT_CSS = skin("RainField { layer: bg; width: 100%; height: 100%; background: @ink; }")
    GLYPHS = "0123456789abcdef<>/\\|:;=+*#%$&"
    TRAIL, BANDS, FPS = 10, 5, 10

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.speed = 1.0
        self._dims = (0, 0)
        self._drops: dict[int, list] = {}
        self._xs: list[int] = []
        self._glyphs: list[list[str]] = []
        bg = PAL["ink"]
        self._blank = Style(bgcolor=bg)
        self._levels: list[list[Style]] = []
        for b in range(self.BANDS):
            base = grad(b / (self.BANDS - 1))
            row = [Style(color=mix(PAL["text"], bg, 0.25), bgcolor=bg, bold=True)]
            for k in range(1, self.TRAIL + 1):
                row.append(Style(color=mix(base, bg, 0.35 + 0.65 * k / self.TRAIL), bgcolor=bg))
            self._levels.append(row)

    def on_mount(self) -> None:
        if FX:
            self.set_interval(1 / self.FPS, self._step)

    @staticmethod
    def _new_drop(h: int, above: bool) -> list:
        head = random.uniform(-h * 0.7, -1) if above else random.uniform(-h, h)
        return [head, random.uniform(0.25, 0.7), random.randint(5, RainField.TRAIL)]

    def _reset(self) -> None:
        w, h = self.size
        self._dims = (w, h)
        self._glyphs = [[random.choice(self.GLYPHS) for _ in range(h)] for _ in range(w)]
        self._drops = {x: self._new_drop(h, False) for x in range(w) if random.random() < 0.3}
        self._xs = sorted(self._drops)

    def _step(self) -> None:
        w, h = self.size
        if w <= 0 or h <= 0:
            return
        if (w, h) != self._dims:
            self._reset()
        for x, d in self._drops.items():
            d[0] += d[1] * self.speed
            if d[0] - d[2] > h:
                self._drops[x] = self._new_drop(h, True)
        for _ in range(max(2, w // 10)):
            self._glyphs[random.randrange(w)][random.randrange(h)] = random.choice(self.GLYPHS)
        self.refresh()

    def render_line(self, y: int) -> Strip:
        w, h = self.size
        if (w, h) != self._dims or y >= h:
            return Strip.blank(w, self._blank)
        cells = []
        for x in self._xs:
            d = self._drops[x]
            k = int(d[0]) - y
            if 0 <= k < d[2] and x < w:
                cells.append((x, self._glyphs[x][y], self._levels[min(self.BANDS - 1, x * self.BANDS // w)][k]))
        if not cells:
            return Strip.blank(w, self._blank)
        segs, pos = [], 0
        for x, ch, style in cells:
            if x > pos:
                segs.append(Segment(" " * (x - pos), self._blank))
            segs.append(Segment(ch, style))
            pos = x + 1
        if pos < w:
            segs.append(Segment(" " * (w - pos), self._blank))
        return Strip(segs, w)


class StatusLine(Widget):
    """Một dòng trạng thái. Spinner chỉ có timer khi đang busy, còn lại hoàn toàn tĩnh."""

    DEFAULT_CSS = "StatusLine { width: 100%; height: 1; }"

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.text, self.kind = "", "info"
        self._timer = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.1, self.refresh, pause=True)
        self._sync_timer()

    def _sync_timer(self) -> None:
        if self._timer is not None:
            self._timer.resume() if self.kind == "busy" else self._timer.pause()

    def set(self, text: str, kind: str = "info") -> None:
        self.text, self.kind = text, kind
        self._sync_timer()
        self.refresh()

    def render(self):
        if self.kind == "busy":
            icon, color = SPIN[int(time.monotonic() * 10) % len(SPIN)], PAL["cyan"]
        elif self.kind == "ok":
            icon, color = "●", PAL["lime"]
        elif self.kind == "error":
            icon, color = "✕", PAL["red"]
        else:
            icon, color = "◇", PAL["mist"]
        out = Text(no_wrap=True, overflow="ellipsis")
        out.append(f"{icon} ", Style(color=color, bold=True))
        out.append(self.text, Style(color=color if self.kind != "info" else PAL["haze"]))
        return Align.center(out)


# ============================================================================
# PHÒNG CHAT: THANH TRÊN / DƯỚI, SIDEBAR
# ============================================================================
class TopBar(Widget):
    DEFAULT_CSS = skin("TopBar { width: 100%; height: 1; background: @dusk; padding: 0 1; }")

    def render(self):
        app, st = self.app, self.app.store
        w = self.content_size.width
        key = st.active
        is_pm = key_uid(key) is not None

        left = Text(no_wrap=True)
        left.append("◢◤ ", Style(color=PAL["cyan"], bold=True))
        left.append("GLP", Style(color=PAL["cyan"], bold=True))
        left.append("//net", Style(color=PAL["dim"]))
        left.append("  ▸ ", Style(color=PAL["dim"]))
        left.append(("@" if is_pm else "#") + st.title(key),
                    Style(color=PAL["magenta"] if is_pm else PAL["cyan"], bold=True))

        right = Text(no_wrap=True)
        unread = st.total_unread()
        if unread:
            right.append(f"✉ {unread}   ", Style(color=PAL["amber"], bold=True))
        live = app.backend.connected
        right.append("● ", Style(color=PAL["lime"] if live else PAL["red"]))
        right.append("online" if live else "mất kết nối", Style(color=PAL["lime"] if live else PAL["red"]))
        if app.ping_ms is not None:
            right.append(f"  {app.ping_ms:.0f}ms", Style(color=PAL["haze"]))
        right.append(f"  {datetime.now():%H:%M:%S}", Style(color=PAL["mist"]))

        out = Text(no_wrap=True, overflow="crop")
        out.append_text(left)
        out.append(" " * max(1, w - left.cell_len - right.cell_len))
        out.append_text(right)
        return out


class HintBar(Static):
    """Dòng gợi ý phím tắt ở đáy, đổi nội dung theo chế độ normal / insert."""

    DEFAULT_CSS = skin("HintBar { width: 100%; height: 1; background: @ink; padding: 0 1; }")
    NORMAL = (("i", "viết tin"), ("j/k", "đổi kênh"), ("g", "global"),
              ("^N", "đổi tên"), ("F5", "làm mới"), ("^Q", "thoát"))
    INSERT = (("Enter", "gửi"), ("Esc", "thoát gõ"), ("↑/↓", "tin đã gửi"))

    def __init__(self, **kw) -> None:
        super().__init__(self._build(self.NORMAL), **kw)

    @staticmethod
    def _build(pairs) -> Text:
        out = Text(no_wrap=True, overflow="ellipsis")
        for i, (key, desc) in enumerate(pairs):
            if i:
                out.append("  ", Style(color=PAL["line2"]))
            out.append(f" {key} ", Style(color=PAL["ink"], bgcolor=PAL["cyan"], bold=True))
            out.append(f" {desc}", Style(color=PAL["mist"]))
        return out

    def set_mode(self, insert: bool) -> None:
        self.update(self._build(self.INSERT if insert else self.NORMAL))


class ConvItem(Widget):
    """Một hàng trong sidebar: kênh GLOBAL hoặc một người. Tĩnh, chỉ vẽ lại khi `sync()` thấy thay đổi."""

    DEFAULT_CSS = skin("""
    ConvItem { width: 100%; height: 1; padding: 0 1; background: @night; }
    ConvItem:hover { background: @line; }
    ConvItem.-active { background: @line2; }
    """)

    class Picked(Message):
        def __init__(self, key: str) -> None:
            super().__init__()
            self.key = key

    def __init__(self, key: str, label: str, is_global: bool = False) -> None:
        super().__init__()
        self.key, self.label, self.is_global = key, label, is_global
        self.unread, self.active, self.online = 0, False, True
        self.accent = PAL["cyan"] if is_global else author_color(key_uid(key) or key)

    def sync(self, label: str, unread: int, active: bool, online: bool) -> None:
        if (label, unread, active, online) == (self.label, self.unread, self.active, self.online):
            return
        self.label, self.unread, self.active, self.online = label, unread, active, online
        self.set_class(active, "-active")
        self.refresh()

    def on_click(self) -> None:
        self.post_message(self.Picked(self.key))

    def render(self):
        w = max(self.content_size.width, 8)
        if self.is_global:
            icon, icol = "#", PAL["cyan"]
        else:
            icon, icol = ("●", self.accent) if self.online else ("○", PAL["dim"])
        left = Text(no_wrap=True)
        left.append("▌" if self.active else " ", Style(color=icol, bold=True))
        left.append(f"{icon} ", Style(color=icol, bold=True))
        badge = Text()
        if self.unread:
            badge.append(f" {min(self.unread, 99)}{'+' if self.unread > 99 else ''} ",
                         Style(color=PAL["ink"], bgcolor=PAL["amber"], bold=True))
        room = w - left.cell_len - badge.cell_len - (1 if self.unread else 0)
        bright = self.online or self.is_global
        left.append(clip(self.label, room),
                    Style(color=PAL["text"] if bright else PAL["mist"], bold=self.active or bool(self.unread)))
        out = Text(no_wrap=True, overflow="crop")
        out.append_text(left)
        out.append(" " * max(0, w - left.cell_len - badge.cell_len))
        out.append_text(badge)
        return out


class SectionLabel(Widget):
    DEFAULT_CSS = "SectionLabel { width: 100%; height: 1; padding: 0 1; }"

    def __init__(self, label: str, **kw) -> None:
        super().__init__(**kw)
        self.label, self.count = label, 0

    def set_count(self, n: int) -> None:
        if n != self.count:
            self.count = n
            self.refresh()

    def render(self):
        w = self.content_size.width
        head, tail = f"── {self.label} ", f" {self.count} "
        out = Text(no_wrap=True, overflow="crop")
        out.append(head, Style(color=PAL["mist"]))
        out.append("─" * max(0, w - len(head) - len(tail)), Style(color=PAL["line2"]))
        out.append(tail, Style(color=PAL["lime"], bold=True))
        return out


class ProfileCard(Widget):
    """Tên/username riêng từng dòng; click hoặc Ctrl+N để đổi tên."""

    DEFAULT_CSS = skin("""
    ProfileCard {
        width: 100%; height: 6; padding: 0 1; background: @dusk;
        border-top: solid @line2; border-title-color: @amber;
    }
    ProfileCard:hover { background: @line; }
    """)

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.t0 = time.monotonic()

    def on_mount(self) -> None:
        self.tooltip = "Đổi tên hiển thị · Ctrl+N"

    class EditRequested(Message):
        pass

    def on_click(self) -> None:
        self.post_message(self.EditRequested())

    def render(self):
        app, st = self.app, self.app.store
        w = max(self.content_size.width, 1)
        live = app.backend.connected
        up = int(time.monotonic() - self.t0)
        hh, rem = divmod(up, 3600)
        mm, ss = divmod(rem, 60)
        out = Text(no_wrap=True, overflow="crop")
        for i, (text, color, bold) in enumerate((
            (st.me_label, PAL["amber"], True),
            (f"@{st.me_user or '?'}", PAL["mist"], False),
            (f"{'● online' if live else '○ offline'}  {hh:02d}:{mm:02d}:{ss:02d}",
             PAL["lime"] if live else PAL["red"], False),
            ("Ctrl+N · đổi tên", PAL["dim"], False),
        )):
            if i:
                out.append("\n")
            out.append(clip(text, w), Style(color=color, bold=bold))
        return out


# ============================================================================
# KHUNG CHAT
# ============================================================================
BG = PAL["night"]
BLANK = Style(bgcolor=BG)
S_TEXT = Style(color=PAL["text"], bgcolor=BG)
S_DIM = Style(color=PAL["dim"], bgcolor=BG)
S_MIST = Style(color=PAL["mist"], bgcolor=BG)
S_LINE = Style(color=PAL["line2"], bgcolor=BG)
SYS_LEVEL = {  # level → (icon, màu)
    "info": ("◇", PAL["cyan"]), "ok": ("✓", PAL["lime"]),
    "warn": ("!", PAL["amber"]), "error": ("✕", PAL["red"]),
}
GROUP_SECONDS = 300   # cùng người gửi trong 5 phút thì gộp chung một khối
MAX_VIEW = 400        # số mục tối đa ChatView giữ để dựng dòng
STAMP_W = 8           # "HH:MM:SS"


@lru_cache(maxsize=64)
def _author_styles(color: str) -> tuple[Style, Style, Style]:
    """(thanh ở dòng tên, thanh ở dòng nội dung, tên) cho một màu."""
    return (Style(color=color, bgcolor=BG, bold=True),
            Style(color=mix(color, BG, 0.55), bgcolor=BG),
            Style(color=color, bgcolor=BG, bold=True))


def _stamp(ts: float) -> str:
    """Hôm nay: HH:MM:SS. Ngày khác (tin trong lịch sử): dd/mm HH:MM."""
    if ts <= 0:
        return "--:--:--"
    lt = time.localtime(ts)
    if time.strftime("%Y%j", lt) == time.strftime("%Y%j"):
        return time.strftime("%H:%M:%S", lt)
    return time.strftime("%d/%m %H:%M", lt)


class ChatView(ScrollView):
    """Khung chat kiểu Discord, mọi tin nằm bên trái, gom thành khối theo người gửi:

        ▌ alice  14:32:07
        ▌ tin đầu tiên
        ▌ tin thứ hai (cùng khối vì cùng người, cách nhau < 5 phút)

        ▌ bob  14:33:10
        ▌ ...

    Giữ danh sách `Entry` của kênh đang mở; đổi kênh = `show()` danh sách khác.
    """

    DEFAULT_CSS = skin("""
    ChatView {
        background: @night; overflow-x: hidden; overflow-y: auto;
        scrollbar-gutter: stable; padding: 0 1;
    }
    """)

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._entries: list[Entry] = []
        self._rows: list[Strip] = []
        self._prev: Entry | None = None
        self._w = 0
        self.hint = ""
        self.loading = False

    # ----------------------------------------------------------------- API
    def show(self, entries, hint: str = "", loading: bool = False) -> None:
        self.hint, self.loading = hint, loading
        self._entries = list(entries)[-MAX_VIEW:]
        self._relayout(stick=True)

    def set_loading(self, loading: bool) -> None:
        if loading != self.loading:
            self.loading = loading
            if not self._rows:
                self.refresh()

    def append(self, entry: Entry, force: bool = False) -> None:
        """Thêm một mục mới ở cuối. Chỉ tự cuộn xuống đáy nếu người dùng đang ở đáy (hoặc `force`)."""
        self._entries.append(entry)
        if len(self._entries) > MAX_VIEW:
            del self._entries[:-MAX_VIEW]
            self._relayout(stick=True)
            return
        if self.size.width <= 0:
            return
        stick = force or self._at_end()
        self._rows.extend(self._make_rows(entry, self.size.width))
        self._commit(stick)

    # --------------------------------------------------------------- dựng dòng
    def _at_end(self) -> bool:
        return self.max_scroll_y - self.scroll_y <= 1

    def on_resize(self, event) -> None:
        if self.size.width != self._w:
            self._relayout()

    def _relayout(self, stick: bool | None = None) -> None:
        w = self.size.width
        self._w = w
        self._prev = None
        if w <= 0:
            self._rows = []  # chưa biết bề rộng: đợi on_resize dựng lại
            return
        if stick is None:
            stick = self._at_end()
        rows: list[Strip] = []
        for entry in self._entries:
            rows.extend(self._make_rows(entry, w))
        self._rows = rows
        self._commit(stick)

    def _commit(self, stick: bool) -> None:
        self.virtual_size = Size(self.size.width, len(self._rows))
        self.refresh()
        if stick:
            self.call_after_refresh(self.scroll_end, animate=False)

    @staticmethod
    def _strip(segs: list[Segment], w: int) -> Strip:
        return Strip(segs).adjust_cell_length(w, BLANK)

    def _make_rows(self, e: Entry, w: int) -> list[Strip]:
        rows: list[Strip] = []
        prev, self._prev = self._prev, e
        blank = Strip.blank(w, BLANK)

        if e.kind == "msg":
            color = PAL["amber"] if e.mine else author_color(e.uid or e.name)
            bar_head, bar_body, name_style = _author_styles(color)
            cont = (prev is not None and prev.kind == "msg" and prev.uid == e.uid
                    and prev.mine == e.mine and 0 <= e.ts - prev.ts < GROUP_SECONDS)
            if not cont:
                if prev is not None:
                    rows.append(blank)
                rows.append(self._strip([
                    Segment("▌", bar_head), Segment(" ", S_TEXT),
                    Segment(clip(e.name or "?", max(w - 17, 4)), name_style),
                    Segment("  " + _stamp(e.ts), S_DIM),
                ], w))
            for line in wrap_text(e.text, w - 2):
                rows.append(self._strip([Segment("▌", bar_body), Segment(" ", S_TEXT), Segment(line, S_TEXT)], w))
            return rows

        if prev is not None and not (e.kind == "sys" and prev.kind == "sys"):
            rows.append(blank)

        if e.kind == "sys":
            icon, color = SYS_LEVEL.get(e.level, SYS_LEVEL["info"])
            tone = Style(color=PAL["haze"] if e.level in ("info", "ok") else color, bgcolor=BG)
            lines = wrap_text(e.text, max(w - STAMP_W - 5, 8))
            for i, line in enumerate(lines):
                if i == 0:
                    segs = [Segment(_stamp(e.ts), S_DIM), Segment(" │ ", S_LINE),
                            Segment(icon + " ", Style(color=color, bgcolor=BG, bold=True)), Segment(line, tone)]
                else:
                    segs = [Segment(" " * STAMP_W, S_DIM), Segment(" │ ", S_LINE),
                            Segment("  ", S_TEXT), Segment(line, tone)]
                rows.append(self._strip(segs, w))
            return rows

        # "srv": phản hồi thô của server, tiêu đề là tên action
        rows.append(self._strip([
            Segment("◂ ", Style(color=PAL["cyan"], bgcolor=BG, bold=True)),
            Segment(clip(e.name, max(w - 14, 4)), Style(color=PAL["cyan"], bgcolor=BG, bold=True)),
            Segment("  " + _stamp(e.ts), S_DIM),
        ], w))
        for line in wrap_text(e.text, w - 4):
            rows.append(self._strip([Segment("  │ ", S_LINE), Segment(line, S_MIST)], w))
        return rows

    # ------------------------------------------------------------------- vẽ
    def render_line(self, y: int) -> Strip:
        w = self.size.width
        if not self._rows:
            return self._empty_line(y, w)
        idx = y + self.scroll_offset.y
        if 0 <= idx < len(self._rows):
            return self._rows[idx]
        return Strip.blank(w, BLANK)

    def _empty_line(self, y: int, w: int) -> Strip:
        mid = self.size.height // 2
        first = "// đang tải lịch sử…" if self.loading else "// chưa có tin nhắn nào"
        lines = (first, self.hint)
        if y in (mid - 1, mid) and lines[y - (mid - 1)]:
            text = lines[y - (mid - 1)]
            left = max(0, (w - cell_len(text)) // 2)
            style = Style(color=PAL["dim"] if y == mid - 1 else PAL["line2"], bgcolor=BG)
            return Strip([Segment(" " * left, BLANK), Segment(text, style)]).adjust_cell_length(w, BLANK)
        return Strip.blank(w, BLANK)


class ChatInput(Input):
    """Ô nhập có lịch sử ↑ / ↓ như terminal."""

    BINDINGS = [
        Binding("up", "history(-1)", show=False),
        Binding("down", "history(1)", show=False),
    ]

    def __init__(self, *args, **kwargs) -> None:
        try:  # Textual mới tự bôi đen toàn bộ khi focus, tắt đi để gõ tiếp được ngay
            super().__init__(*args, select_on_focus=False, **kwargs)
        except TypeError:
            super().__init__(*args, **kwargs)
        self.history: list[str] = []
        self._hpos = 0

    def remember(self, text: str) -> None:
        if text and (not self.history or self.history[-1] != text):
            self.history.append(text)
        self._hpos = len(self.history)

    def action_history(self, delta: int) -> None:
        if not self.history:
            return
        self._hpos = max(0, min(len(self.history), self._hpos + delta))
        self.value = "" if self._hpos == len(self.history) else self.history[self._hpos]
        self.cursor_position = len(self.value)


# ============================================================================
# THÔNG BÁO NỔI (góc trên trái)
# ============================================================================
class Notice(Static):
    """Một thông báo. Bấm vào để nhảy sang kênh của tin đó (thông báo nằm đè lên sidebar nên
    nếu không xử lý click thì nó sẽ chặn mất thao tác bấm vào danh sách bên dưới)."""

    DEFAULT_CSS = skin("""
    Notice { width: 100%; height: auto; margin: 0 0 1 0; padding: 0 1; background: @dusk; border-left: heavy @cyan; }
    Notice.-private { border-left: heavy @magenta; }
    Notice.-error { border-left: heavy @red; }
    Notice.-info { border-left: heavy @lime; }
    """)

    class Picked(Message):
        def __init__(self, notice: "Notice") -> None:
            super().__init__()
            self.notice = notice
            self.target = notice.target

    def __init__(self, content, target: str = "", **kw) -> None:
        super().__init__(content, **kw)
        self.target = target

    def on_click(self) -> None:
        self.post_message(self.Picked(self))


class NoticeStack(Vertical):
    """Chồng thông báo ở góc trên trái, nằm trên một layer riêng nên không đẩy bố cục.
    Thông báo mới xếp dần xuống dưới; mỗi cái tự biến mất sau LIFE giây (không animation cho nhẹ)."""

    DEFAULT_CSS = skin("NoticeStack { layer: overlay; width: 46; height: auto; margin: 2 0 0 2; }")
    LIFE, MAX, TEXT_W = 3.0, 5, 41
    LOOK = {  # kind → (icon, màu)
        "global": ("#", PAL["cyan"]), "private": ("✉", PAL["magenta"]),
        "error": ("✕", PAL["red"]), "info": ("◇", PAL["lime"]),
    }

    def push(self, kind: str, title: str, body: str = "", target: str = "") -> None:
        icon, color = self.LOOK.get(kind, self.LOOK["info"])
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append(f"{icon} ", Style(color=color, bold=True))
        text.append(clip(title, self.TEXT_W - 2), Style(color=color, bold=True))
        if body:
            text.append("\n")
            text.append(clip(body, self.TEXT_W), Style(color=PAL["haze"]))
        notice = Notice(text, target, classes=f"-{kind}")
        self.mount(notice)
        notice.set_timer(self.LIFE, notice.remove)
        kids = list(self.children)
        for old in kids[: max(0, len(kids) - self.MAX)]:
            old.remove()
