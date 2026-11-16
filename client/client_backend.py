"""API cho CLI, xử lý response và quản lý kết nối GLP."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import socket
from tempfile import NamedTemporaryFile
import threading
import time

from Error import FileError, GLPConnectionError, GLPTimeoutError, ProtocolError, error_message
from GLP import DEFAULT_SESSION, HOLDING_SESSION, Request, Response
from GLPConn import GLP_Connection
from file_transfer import CHUNK_SIZE, FileMetadata, SegmentWriter, read_segments


@dataclass(frozen=True)
class Result:
    kind: str
    data: object = None
    message: str = ""
    ok: bool = True


JSON_RESULTS = {
    "AUTH.OK": "login",
    "CHAT.ITEMS": "chats",
    "CHAT.SENT": "sent",
    "CHAT.MESSAGE": "message",
    "FILE.STORED": "stored",
    "FILE.META": "metadata",
    "FILE.NOTICE": "file_notice",
}


def dispatch(response: Response) -> Result:
    action, args = response.action, response.args
    try:
        if action.startswith("ERROR."):
            message, = args
            return Result("error", message=error_message(message.decode("utf-8")), ok=False)
        if action in JSON_RESULTS:
            raw, = args
            value = json.loads(raw)
            expected_type = list if action == "CHAT.ITEMS" else dict
            if not isinstance(value, expected_type):
                raise ProtocolError(f"Response {action} có kiểu JSON không hợp lệ")
            return Result(JSON_RESULTS[action], value)
        if action in ("CHAT.PAGE", "FILE.ITEMS"):
            target, raw, cursor = args
            items = json.loads(raw)
            if not isinstance(items, list):
                raise ProtocolError(f"Response {action} cần một list kết quả")
            kind = "history" if action == "CHAT.PAGE" else "files"
            return Result(kind, {"target": target.decode(), "items": items, "cursor": cursor.decode()})
        if action == "FILE.READY":
            file_id, count = args
            return Result("ready", (file_id.decode("ascii"), int(count)))
        if action == "FILE.DATA":
            file_id, index, data = args
            return Result("segment", (file_id.decode("ascii"), int(index), data))
        if action == "AUTH.BYE" and not args:
            return Result("logout", message="Đã đăng xuất")
    except ProtocolError: raise
    except ValueError as error: raise ProtocolError(f"Response {action}: {error}") from error
    raise ProtocolError(f"Response chưa được xử lý: {action}")

class Backend:
    CONNECT_TIMEOUT = 7.5
    REQUEST_TIMEOUT = 15.0
    PING_INTERVAL = 15.0
    MAX_MISSED_PONGS = 4
    EVENTS = {"CHAT.MESSAGE", "FILE.NOTICE"}

    def __init__(self, host="localhost", port=8443, emit=lambda result: None):
        self.host, self.port, self.emit = host, port, emit
        self.connected = False
        self.user = {}
        self._session = DEFAULT_SESSION
        self._conn = None
        self._pending = None
        self._last_pong = 0.0
        self._error = ""
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._threads = []

    def start(self):
        self._conn = GLP_Connection.connect(self.host, self.port, timeout=self.CONNECT_TIMEOUT)
        self.connected = True
        self._last_pong = time.monotonic()
        for target in (self._read_loop, self._heartbeat_loop):
            thread = threading.Thread(target=target, daemon=True)
            self._threads.append(thread)
            thread.start()

    def _disconnect(self, reason=None):
        with self._state_lock:
            if self._stop.is_set():
                return
            self._error = error_message(str(reason or "Đã đóng kết nối"))
            notify = reason is not None and self._pending is None
            self.connected = False
            self._stop.set()
        if self._conn is not None:
            try:
                self._conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._conn.close()
        if notify:
            self.emit(Result("closed", message=self._error, ok=False))

    def close(self):
        self._disconnect()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(2)

    def _read_loop(self):
        try:
            while not self._stop.is_set():
                try:
                    frame = self._conn.recv()
                except TimeoutError:
                    continue
                if not isinstance(frame, Response):
                    raise ProtocolError("Server phải gửi Response")
                if frame.session_update != HOLDING_SESSION:
                    self._session = frame.session_update
                if frame.action == "OK.PONG":
                    if frame.args:
                        raise ProtocolError("PONG phải không có args")
                    self._last_pong = time.monotonic()
                elif frame.action in self.EVENTS:
                    self.emit(dispatch(frame))
                else:
                    self._queue_reply(frame)
        except (OSError, EOFError, ValueError) as error:
            self._disconnect(error)

    def _queue_reply(self, frame):
        with self._state_lock:
            inbox = self._pending
        if inbox is None:
            raise ProtocolError("Response không có giao dịch đang chờ")
        while not self._stop.is_set():
            try:
                inbox.put(frame, timeout=0.1)
                return
            except Full:
                continue

    def _heartbeat_loop(self):
        next_ping = time.monotonic() + self.PING_INTERVAL
        try:
            while not self._stop.wait(min(self.PING_INTERVAL, 0.5)):
                now = time.monotonic()
                if now - self._last_pong >= self.PING_INTERVAL * self.MAX_MISSED_PONGS:
                    raise GLPTimeoutError("Không nhận OK.PONG đúng hạn")
                if now >= next_ping:
                    self._send("SYS.PING")
                    next_ping = now + self.PING_INTERVAL
        except (OSError, ProtocolError) as error:
            self._disconnect(error)

    def _send(self, route, *args):
        if not self.connected:
            raise GLPConnectionError(self._error or "Chưa kết nối server")
        self._conn.send(Request(route, self._session, args))

    @contextmanager
    def _exchange(self):
        # GLP chưa có request ID. Cả luồng file giữ một inbox tới khi kết thúc.
        with self._request_lock:
            inbox = Queue(maxsize=2)
            with self._state_lock:
                if not self.connected:
                    raise GLPConnectionError(self._error or "Chưa kết nối server")
                self._pending = inbox
            try:
                yield inbox
            except BaseException as error:
                # Response còn trên stream có thể bị nhầm với giao dịch kế tiếp.
                self._disconnect(error)
                raise
            finally:
                with self._state_lock:
                    self._pending = None

    def _receive(self, inbox, expected):
        deadline = time.monotonic() + self.REQUEST_TIMEOUT
        while True:
            try:
                frame = inbox.get(timeout=0.1)
                break
            except Empty:
                if not self.connected:
                    raise GLPConnectionError(self._error)
                if time.monotonic() >= deadline:
                    raise GLPTimeoutError("Server không trả response đúng hạn")
        result = dispatch(frame)
        if result.ok and result.kind != expected:
            raise ProtocolError(f"Cần {expected}, nhận {result.kind}")
        if frame.action == "ERROR.AUTH" and frame.session_update == DEFAULT_SESSION:
            self.user = {}
        return result

    def _call(self, route, *args, expected):
        with self._exchange() as inbox:
            self._send(route, *args)
            return self._receive(inbox, expected)

    def login(self, username, password, register=False):
        route = "AUTH.REGISTER" if register else "AUTH.LOGIN"
        result = self._call(route, username.encode(), password.encode(), expected="login")
        if result.ok:
            self.user = result.data
        return result

    def logout(self):
        result = self._call("AUTH.LOGOUT", expected="logout")
        if result.ok:
            self.user = {}
        return result

    def list_chats(self):
        return self._call("CHAT.LIST", expected="chats")

    def history(self, target, limit=10, before="0"):
        return self._call("CHAT.HISTORY", target.encode(), str(limit).encode(),
                          str(before).encode(), expected="history")

    def send_chat(self, target, text):
        return self._call("CHAT.SEND", target.encode(), text.encode(), expected="sent")

    def list_files(self, target, cursor="0"):
        return self._call("FILE.LIST", target.encode(), str(cursor).encode(), expected="files")

    def _progress(self, label, done, total):
        if done * 10 // total != (done - 1) * 10 // total:
            self.emit(Result("progress", (label, done, total)))

    def upload(self, target, raw_path):
        path = Path(raw_path).expanduser()
        if not path.is_file(): raise FileError("Đường dẫn upload phải là file")
        with path.open("rb") as source:
            meta = FileMetadata.from_file(source, path.name)
            self.emit(Result("notice", message=f"Upload {meta.name} ({meta.segments} segment)"))
            with self._exchange() as inbox:
                self._send("FILE.UPLOAD", target.encode(), json.dumps(asdict(meta)).encode())
                result = self._receive(inbox, "ready" if meta.segments else "stored")
                if not result.ok: return result
                file_id = None
                if meta.segments:
                    file_id, count = result.data
                    if count != meta.segments: raise ProtocolError("READY sai số segment")

                    for index, data in read_segments(source, meta.size):
                        self._send("FILE.SEGMENT", file_id.encode(), str(index).encode(), data)
                        self._progress("Gửi", index + 1, count)
                    result = self._receive(inbox, "stored")
                if result.ok and (FileMetadata.from_dict(result.data) != meta or (file_id is not None and result.data.get("file_id") != file_id)):
                    raise ProtocolError("STORED sai metadata")
                return result

    def download(self, file_id, raw_folder):
        folder = Path(raw_folder).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=True)
        part = None
        try:
            with self._exchange() as inbox:
                self._send("FILE.DOWNLOAD", file_id.encode())
                result = self._receive(inbox, "metadata")
                if not result.ok: return result
                info = result.data
                meta = FileMetadata.from_dict(info)
                if info.get("file_id") != file_id or info.get("segment_size") != CHUNK_SIZE:
                    raise ProtocolError("META sai file ID hoặc segment size")
                self.emit(Result("notice", message=f"Download {meta.name} ({meta.segments} segment)"))
                with NamedTemporaryFile(mode="wb", suffix=".part", dir=folder, delete=False) as stream:
                    part = Path(stream.name)
                    writer = SegmentWriter(stream, meta)
                    for _ in range(meta.segments):
                        segment = self._receive(inbox, "segment")
                        if not segment.ok: return segment
                        frame_id, index, data = segment.data
                        if frame_id != file_id:
                            raise ProtocolError("DATA sai file ID")
                        writer.append(index, data)
                        self._progress("Nhận", writer.next_index, meta.segments)

            writer.verify()
            name = Path(meta.name)
            destination = folder / name
            suffix = 0
            while True:
                try:
                    os.link(part, destination)
                    return Result("downloaded", {"path": str(destination), "metadata": info})
                except FileExistsError:
                    suffix += 1
                    destination = folder / f"{name.stem} ({suffix}){name.suffix}"
        finally:
            if part is not None:
                part.unlink(missing_ok=True)
