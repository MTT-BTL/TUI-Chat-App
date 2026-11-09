"""Dữ liệu của phòng chat: người dùng, lịch sử từng cuộc trò chuyện, số tin chưa đọc.
Không import Textual hay mạng nên test được độc lập.

Mỗi cuộc trò chuyện có một `key`:  "GLOBAL"  hoặc  "PM:<user_id>".
Lịch sử được nạp từ server (MSG.GETGLO / MSG.GETPRI → OK.HISTORY) đúng một lần cho mỗi kênh,
vào lúc kênh đó được mở lần đầu. Tin đến trong lúc đang nạp được gộp lại, không bị trùng.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .client_theme import clean

GLOBAL_KEY = "GLOBAL"
MAX_HISTORY = 400     # số mục giữ lại cho mỗi cuộc trò chuyện
MAX_MSG_CHARS = 2000  # độ dài tối đa một tin nhắn gửi đi
DEDUP_TAIL = 60       # khi gộp, chỉ so trùng với chừng này tin cuối của lịch sử

# Cột `time` server trả về nếu là chuỗi không có múi giờ ("2026-10-10 14:29:36"):
#   True  = giờ UTC (đúng với SQLite CURRENT_TIMESTAMP, mặc định)
#   False = giờ máy chủ (khi server lưu bằng datetime.now())
NAIVE_TIME_IS_UTC = True


def pm_key(uid: str) -> str:
    return f"PM:{uid}"


def key_uid(key: str) -> str | None:
    return key[3:] if key.startswith("PM:") else None


@dataclass(slots=True)
class Peer:
    uid: str
    name: str
    online: bool = True
    inbox: bool = False


@dataclass(slots=True)
class Entry:
    """Một mục trong khung chat.
    kind: "msg" = tin nhắn · "sys" = thông báo hệ thống · "srv" = phản hồi thô của server."""
    kind: str
    text: str
    ts: float = field(default_factory=time.time)
    uid: str = ""
    name: str = ""
    mine: bool = False
    level: str = "info"  # với "sys": info | ok | warn | error


# ------------------------------------------------------------------ lịch sử
def parse_time(value: object) -> float:
    """Cột `time` của server → epoch (giây). Nhận số (giây hoặc mili-giây), chuỗi số,
    hoặc chuỗi ISO. Không đọc được thì trả 0.0 (UI hiển thị --:--:--)."""
    try:
        ts = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        s = str(value or "").strip()
        if not s:
            return 0.0
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        if dt.tzinfo is None and NAIVE_TIME_IS_UTC:
            dt = dt.replace(tzinfo=timezone.utc)
        try:
            return dt.timestamp()  # dt "naive" thì Python hiểu là giờ máy
        except (OverflowError, OSError, ValueError):
            return 0.0
    if ts > 1e11:  # mili-giây
        ts /= 1000.0
    return ts if ts > 0 else 0.0


def rows_to_entries(rows: list, me_id: str, me_label: str, peer_name: str | None = None) -> list[Entry]:
    """JSON lịch sử từ server → danh sách Entry, cũ → mới.

    Server có thể trả cũ→mới hoặc mới→cũ nên ở đây tự nhận ra và sắp lại (ổn định, tin cùng giây
    giữ nguyên thứ tự server). Tên: tin của mình luôn hiện tên mình; ở kênh riêng, người còn lại hiện
    `peer_name` (server chỉ trả "Đối tác" chung chung cho kênh riêng nên không dùng)."""
    items: list[Entry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        uid = str(row.get("sender_id", ""))
        mine = bool(uid) and uid == me_id
        if mine:
            name = me_label
        elif peer_name:
            name = peer_name
        else:
            name = str(row.get("sender_name") or uid or "?")
        items.append(Entry("msg", clean(str(row.get("content", ""))), ts=parse_time(row.get("time")),
                           uid=uid, name=name, mine=mine))
    if len(items) > 1 and items[0].ts > items[-1].ts:
        items.reverse()
    items.sort(key=lambda e: e.ts)
    return items[-MAX_HISTORY:]


class ChatState:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.me_id = ""
        self.me_user = ""
        self.me_name = ""
        self.peers: dict[str, Peer] = {}
        self.online_total = 0
        self.history: dict[str, deque[Entry]] = {GLOBAL_KEY: deque(maxlen=MAX_HISTORY)}
        self.unread: dict[str, int] = {}
        self.active = GLOBAL_KEY
        self.loaded: set[str] = set()          # kênh đã nạp lịch sử xong
        self.loading: dict[str, float] = {}    # kênh đang chờ server trả lịch sử → thời điểm bắt đầu

    # ------------------------------------------------------------ lịch sử
    @property
    def me_label(self) -> str:
        return self.me_name or self.me_user or "bạn"

    def hist(self, key: str) -> deque[Entry]:
        h = self.history.get(key)
        if h is None:
            h = self.history[key] = deque(maxlen=MAX_HISTORY)
        return h

    def add(self, key: str, entry: Entry, read: bool) -> None:
        self.hist(key).append(entry)
        if not read:
            self.unread[key] = self.unread.get(key, 0) + 1

    def open(self, key: str) -> None:
        self.active = key
        self.unread[key] = 0

    def total_unread(self) -> int:
        return sum(self.unread.values())

    def title(self, key: str) -> str:
        uid = key_uid(key)
        if uid is None:
            return "GLOBAL"
        peer = self.peers.get(uid)
        return peer.name if peer else uid

    # --------------------------------------------------------- nạp lịch sử
    def begin_load(self, key: str) -> bool:
        """True nếu cần gửi request lấy lịch sử (chưa nạp và chưa có request nào đang chờ)."""
        if key in self.loaded or key in self.loading:
            return False
        self.loading[key] = time.monotonic()
        return True

    def abort_load(self, key: str) -> None:
        self.loading.pop(key, None)

    def finish_load(self, key: str, entries: list[Entry]) -> None:
        """Ghép lịch sử vào đầu kênh. Những gì đã có trong kênh (tin đến lúc đang chờ, tin mình vừa gửi,
        dòng hệ thống) được giữ lại phía sau. Tin đã có trong lịch sử thì bỏ bản live để khỏi trùng."""
        self.loading.pop(key, None)
        self.loaded.add(key)
        d = self.hist(key)
        live = list(d)
        tail_from = max(0, len(entries) - DEDUP_TAIL)
        used: set[int] = set()
        kept: list[Entry] = []
        for e in live:
            if e.kind == "msg":
                dup = next((i for i in range(tail_from, len(entries))
                            if i not in used and entries[i].uid == e.uid and entries[i].text == e.text), None)
                if dup is not None:
                    used.add(dup)
                    continue
            kept.append(e)
        d.clear()
        d.extend(entries + kept)

    # ------------------------------------------------------------ người dùng
    def touch_peer(self, uid: str, name: str, from_inbox: False) -> None:
        """Người vừa gửi tin riêng cho mình chắc chắn đang online."""
        peer = self.peers.get(uid)
        if peer is None:
            self.peers[uid] = Peer(uid, name or uid, not from_inbox, inbox=from_inbox)
        else:
            if not from_inbox: 
                peer.online = True
            if from_inbox:
                peer.inbox = True
            if name:
                peer.name = name

    def apply_online(self, rows: list) -> None:
        """Nhận kết quả SYS.USERS: [{user_id, username, dispname}, ...]."""
        seen: set[str] = set()
        total = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            uid = str(row.get("user_id", ""))
            if not uid:
                continue
            total += 1
            if uid == self.me_id:
                self.me_name = str(row.get("dispname") or row.get("username") or self.me_user)
                continue
            name = str(row.get("dispname") or row.get("username") or uid)
            seen.add(uid)
            peer = self.peers.get(uid)
            if peer is None:
                self.peers[uid] = Peer(uid, name, True)
            else:
                peer.name, peer.online = name, True
        for uid, peer in self.peers.items():
            if uid not in seen:
                peer.online = False
        self.online_total = total

    def visible_peers(self) -> list[Peer]:
        """Người đang online + người đã offline nhưng còn tin nhắn/chưa đọc/đang mở chat.
        Online xếp trước, mỗi nhóm theo tên."""
        active_uid = key_uid(self.active)
        out = []
        for uid, peer in self.peers.items():
            key = pm_key(uid)
            if (peer.online or self.history.get(key) or peer.inbox or self.unread.get(key) 
                    or uid == active_uid):
                out.append(peer)
        out.sort(key=lambda p: (not p.online, p.name.lower()))
        return out
