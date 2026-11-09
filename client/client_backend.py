"""Backend mạng của GLP client: TCP → TLS → handshake PING, một luồng đọc, một luồng ghi.
Không import gì của giao diện. Muốn báo cho UI thì gọi `emit(kind, payload)`:

    "status"  (text, kind)   tiến trình kết nối, hiện ở màn hình đăng nhập
    "ready"   None           handshake xong, sẵn sàng đăng nhập
    "frame"   Response       một gói tin từ server (cả phản hồi lẫn tin đẩy EVT.*)
    "closed"  str            mất kết nối (lý do)

`send()` không bao giờ chặn giao diện: gói tin được bỏ vào hàng đợi, luồng ghi lo phần còn lại.
Chỉ có một luồng ghi nên các frame không bao giờ bị trộn lẫn vào nhau.
"""
from __future__ import annotations

import queue
import socket
import ssl
import threading
from typing import Callable

from GLP import DEFAULT_SESSION, ROUTE, ProtocolError, Request
from GLPConn import GLP_Connection

Emit = Callable[[str, object], None]


def parse_raw(text: str) -> tuple[str, tuple[bytes, ...]] | None:
    """'DASH.SELF' hoặc 'AUTH.LOGIN user pass' → (route, args). Không phải route hợp lệ → None.
    Chỉ nhận route có thật nên một câu chat viết hoa như 'LOL.' không bị gửi nhầm thành lệnh."""
    parts = text.split()
    if parts and ROUTE.fullmatch(parts[0]):
        return parts[0], tuple(p.encode("utf-8") for p in parts[1:])
    return None


class Backend:
    CONNECT_TIMEOUT = 8.0

    def __init__(self, host: str, port: int, emit: Emit) -> None:
        self.host, self.port, self._emit = host, port, emit
        self._conn: GLP_Connection | None = None
        self._sock: ssl.SSLSocket | None = None
        self._out: queue.Queue = queue.Queue()
        self._running = True
        self._closed_sent = False
        self.connected = False

    # ---------------------------------------------------------------- vòng đời
    def start(self) -> None:
        threading.Thread(target=self._run, name="glp-net", daemon=True).start()

    def close(self) -> None:
        self._running = False
        self.connected = False
        self._out.put(None)
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass

    def _emit_closed(self, reason: str) -> None:
        self.connected = False
        if self._running and not self._closed_sent:
            self._closed_sent = True
            self._emit("closed", reason)

    # ------------------------------------------------------------------- gửi
    def send(self, route: str, session: str, *args: bytes) -> str | None:
        """Xếp một request vào hàng đợi. Trả về None nếu ổn, hoặc chuỗi mô tả lỗi."""
        if not self.connected or self._conn is None:
            return "Chưa kết nối tới máy chủ."
        try:
            req = Request(route, session, tuple(args))
            req.to_bytes()  # kiểm tra route/session/kích thước ngay, lỗi thì báo tại chỗ
        except ProtocolError as error:
            return f"Gói tin không hợp lệ ({route}): {error}"
        self._out.put(req)
        return None

    # ------------------------------------------------------------ các luồng nền
    def _run(self) -> None:
        try:
            ctx = ssl.create_default_context()
            ctx.minimum_version = ssl.TLSVersion.TLSv1_3
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            raw = socket.create_connection((self.host, self.port), timeout=self.CONNECT_TIMEOUT)
            self._sock = ctx.wrap_socket(raw, server_hostname=self.host)
            conn = GLP_Connection(self._sock)
            self._conn = conn

            conn.send(Request("SYS.PING", DEFAULT_SESSION))  # handshake
            pong = conn.recv()
            if pong.action != "OK.PONG":
                self._emit("status", ("Bắt tay với máy chủ thất bại.", "error"))
                return
            self._sock.settimeout(None)  # từ đây recv() chờ vô hạn
        except Exception as error:
            if self._running:
                self._emit("status", (f"Lỗi mạng: {error}", "error"))
            return

        self.connected = True
        threading.Thread(target=self._write_loop, name="glp-tx", daemon=True).start()
        self._emit("ready", None)
        self._read_loop()

    def _read_loop(self) -> None:
        while self._running and self.connected:
            try:
                resp = self._conn.recv()
            except EOFError:
                self._emit_closed("Mất kết nối: server đã đóng phiên.")
                return
            except Exception as error:
                self._emit_closed(f"Lỗi mạng: {error}")
                return
            self._emit("frame", resp)

    def _write_loop(self) -> None:
        while True:
            req = self._out.get()
            if req is None or not self._running:
                return
            try:
                self._conn.send(req)
            except Exception as error:
                self._emit_closed(f"Lỗi gửi: {error}")
                return
