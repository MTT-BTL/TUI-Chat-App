"""FILE controller: một chunk chờ ACK/DATA, UI không đọc cả file vào RAM."""
from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from file_protocol import CHUNK_SIZE, MAX_FILE_SIZE, filename, file_size
from .client_screens import MainScreen
from .client_state import GLOBAL_KEY, Entry, key_uid, pm_key
from .client_theme import clean
from .file_ui import FileBar, FileScreen


@dataclass
class Transfer:
    kind: str
    key: str
    stream: object
    name: str
    size: int
    file_id: str = ""
    offset: int = 0
    expected_offset: int = 0
    digest: object = field(default_factory=sha256)
    metadata: dict = field(default_factory=dict)
    part: Path | None = None
    folder: Path | None = None


class FileTransfers:
    def __init__(self, app):
        self.app = app
        self.transfer = None
        self.items, self.cursors, self._listing_more = {}, {}, {}
        self._refresh_after_list = set()
        self._seen = set()
        self.status = "Chọn file để tải, hoặc nhập đường dẫn để gửi."
        self.progress, self.total = 0, 1

    @property
    def percent(self):
        return min(100, int(self.progress * 100 / max(self.total, 1)))

    def main(self):
        return next((screen for screen in reversed(self.app.screen_stack)
                     if isinstance(screen, MainScreen)), None)

    def _changed(self):
        main = self.main()
        if main is not None:
            main.query_one(FileBar).sync()
        for screen in self.app.screen_stack:
            if isinstance(screen, FileScreen) and screen.is_mounted:
                screen.sync_progress()

    def open(self):
        if self.main() is not None and not isinstance(self.app.screen, FileScreen):
            self.app.push_screen(FileScreen(self.app.store.active))

    def load_peers(self):
        self._send("FILE.CHATS")

    def _send(self, route, *args):
        error = self.app.backend.send(route, self.app.session_id, *args)
        if error:
            self._fail(error)
            return False
        return True

    def request_list(self, key, more=False):
        if key in self._listing_more:
            if not more:
                self._refresh_after_list.add(key)
            return
        cursor = self.cursors.get(key, "") if more else "0"
        if not cursor:
            return
        self._listing_more[key] = more
        self._changed()
        if not self._send("FILE.LIST", (key_uid(key) or "GLOBAL").encode(), cursor.encode()):
            self._listing_more.pop(key, None)

    def upload(self, raw_path, key):
        if self.transfer is not None:
            return
        try:
            path = Path(raw_path.strip()).expanduser()
            if not raw_path.strip() or not path.is_file():
                raise ValueError("Hãy nhập đường dẫn tới một file")
            name = filename(path.name)
            size = path.stat().st_size
            if size > MAX_FILE_SIZE:
                raise ValueError("File vượt giới hạn 1 GiB")
            stream = path.open("rb")
        except (OSError, ValueError) as error:
            self._fail(str(error))
            return
        self.transfer = Transfer("upload", key, stream, name, size)
        self.progress, self.total = 0, max(size, 1)
        self.status = f"Đang gửi {name} · {file_size(size)}"
        self._changed()
        self._send("FILE.UPLOAD", (key_uid(key) or "GLOBAL").encode(), name.encode("utf-8"), str(size).encode())

    def _next_chunk(self):
        transfer = self.transfer
        data = transfer.stream.read(CHUNK_SIZE)
        if data:
            transfer.digest.update(data)
            transfer.expected_offset = transfer.offset + len(data)
            self._send("FILE.CHUNK", transfer.file_id.encode(), str(transfer.offset).encode(), data)
        else:
            self.status = f"Đang kiểm tra {transfer.name} trên server…"
            self._changed()
            self._send("FILE.COMPLETE", transfer.file_id.encode(), transfer.digest.hexdigest().encode())

    def download(self, metadata, raw_folder, key):
        if self.transfer is not None:
            return
        try:
            name = filename(metadata["name"])
            if not raw_folder.strip():
                raise ValueError("Hãy nhập thư mục tải về")
            folder = Path(raw_folder.strip()).expanduser().resolve()
            folder.mkdir(parents=True, exist_ok=True)
            stream = NamedTemporaryFile(mode="wb", prefix=".glp-", suffix=".part", dir=folder, delete=False)
        except (OSError, ValueError) as error:
            self._fail(str(error))
            return
        self.transfer = Transfer("download", key, stream, name, metadata["size"],
                                 file_id=metadata["file_id"], metadata=metadata,
                                 part=Path(stream.name), folder=folder)
        self.progress, self.total = 0, max(metadata["size"], 1)
        self.status = f"Đang tải {name} · {file_size(metadata['size'])}"
        self._changed()
        self._send("FILE.GET", metadata["file_id"].encode(), b"0")

    def handle_response(self, response):
        action, args = response.action, response.args
        if not action.startswith("FILE."):
            return False
        transfer = self.transfer
        try:
            if action == "FILE.ERROR":
                route, reason = (arg.decode("utf-8") for arg in args)
                if route in ("FILE.UPLOAD", "FILE.CHUNK", "FILE.COMPLETE", "FILE.GET"):
                    self._fail(reason)
                elif route != "FILE.CANCEL":
                    if route == "FILE.LIST":
                        self._listing_more.clear()
                        self._refresh_after_list.clear()
                    self.status = reason
                    self._changed()

            elif action == "FILE.ITEMS":
                target = args[0].decode()
                key = GLOBAL_KEY if target == "GLOBAL" else pm_key(target)
                rows = json.loads(args[1])
                if self._listing_more.pop(key, False):
                    known = {row["file_id"] for row in self.items.get(key, [])}
                    self.items.setdefault(key, []).extend(row for row in rows if row["file_id"] not in known)
                else:
                    self.items[key] = rows
                self.cursors[key] = args[2].decode()
                for screen in self.app.screen_stack:
                    if isinstance(screen, FileScreen) and screen.key == key:
                        screen.show_items()
                if key in self._refresh_after_list:
                    self._refresh_after_list.remove(key)
                    self.request_list(key)

            elif action == "FILE.PEERS":
                for row in json.loads(args[0]):
                    self.app.store.touch_peer(row["user_id"], row["dispname"], from_inbox=True)
                if self.main() is not None:
                    self.main().sync_sidebar()

            elif action in ("FILE.NOTICE", "FILE.STORED"):
                metadata = json.loads(args[0])
                self._notice(metadata)
                if action == "FILE.STORED" and transfer is not None and metadata["file_id"] == transfer.file_id:
                    self._finish(f"Đã lưu {transfer.name} trên server · SHA-256 đúng")

            elif action == "FILE.READY":
                if transfer is not None and transfer.kind == "upload":
                    transfer.file_id = args[0].decode("ascii")
                    self._next_chunk()
                else:
                    self._send("FILE.CANCEL", args[0])

            elif action == "FILE.ACK":
                if transfer is not None and transfer.kind == "upload" and args[0].decode() == transfer.file_id:
                    if int(args[1]) != transfer.expected_offset:
                        raise ValueError("ACK không khớp offset chunk vừa gửi")
                    transfer.offset = int(args[1])
                    self.progress = transfer.offset
                    self._changed()
                    self._next_chunk()

            elif action == "FILE.DATA":
                if transfer is not None and transfer.kind == "download" and args[0].decode() == transfer.file_id:
                    offset, data = int(args[1]), args[2]
                    if (offset != transfer.offset or len(data) > CHUNK_SIZE
                            or offset + len(data) > transfer.size or (not data and offset < transfer.size)):
                        raise ValueError("Chunk tải về sai offset hoặc size")
                    transfer.stream.write(data)
                    transfer.digest.update(data)
                    transfer.offset += len(data)
                    self.progress = transfer.offset
                    self._changed()
                    if transfer.offset == transfer.size:
                        self._save_download()
                    else:
                        self._send("FILE.GET", args[0], str(transfer.offset).encode())
        except (OSError, ValueError) as error:
            self._fail(str(error))
        return True

    def _notice(self, metadata):
        if metadata["file_id"] in self._seen:
            return
        self._seen.add(metadata["file_id"])
        st, main = self.app.store, self.main()
        if metadata["receiver_id"] is None:
            key = GLOBAL_KEY
        else:
            mine = metadata["sender_id"] == st.me_id
            peer = metadata["receiver_id"] if mine else metadata["sender_id"]
            name = metadata["receiver_name"] if mine else metadata["sender_name"]
            st.touch_peer(peer, name, from_inbox=True)
            key = pm_key(peer)
        text = f"FILE · {clean(metadata['name'])} · {file_size(metadata['size'])} · {clean(metadata['sender_name'])}"
        entry = Entry("sys", text, level="ok")
        st.add(key, entry, read=(st.active == key and main is not None) or metadata["sender_id"] == st.me_id)
        if main is not None:
            main.deliver(key, entry)
        if isinstance(self.app.screen, FileScreen) and self.app.screen.key == key:
            self.request_list(key)

    def _save_download(self):
        transfer = self.transfer
        if transfer.digest.hexdigest() != transfer.metadata["sha256"]:
            raise ValueError("SHA-256 không khớp; đã bỏ file tải dở")
        transfer.stream.close()
        destination = transfer.folder / transfer.name
        index = 1
        while destination.exists():
            index += 1
            source_name = Path(transfer.name)
            destination = transfer.folder / f"{source_name.stem} ({index}){source_name.suffix}"
        transfer.part.rename(destination)
        if self.main() is not None:
            self.main().post_system(f"Đã tải file: {destination} · SHA-256 đúng", "ok", transfer.key)
        self._finish(f"Đã tải {destination.name} · SHA-256 đúng")

    def _finish(self, text):
        self.transfer.stream.close()
        self.transfer = None
        self.progress = self.total
        self.status = text
        self._changed()

    def close(self):
        transfer, self.transfer = self.transfer, None
        if transfer is not None:
            transfer.stream.close()
            if transfer.part is not None:
                transfer.part.unlink(missing_ok=True)

    def reset(self, reason):
        self.close()
        self.items.clear()
        self.cursors.clear()
        self._listing_more.clear()
        self._refresh_after_list.clear()
        self._seen.clear()
        self.status, self.progress, self.total = reason, 0, 1
        if isinstance(self.app.screen, FileScreen):
            self.app.pop_screen()
        self._changed()

    def _fail(self, reason):
        transfer = self.transfer
        if transfer is not None and transfer.kind == "upload" and transfer.file_id:
            self.app.backend.send("FILE.CANCEL", self.app.session_id, transfer.file_id.encode())
        self.close()
        self.status = "FILE · " + str(reason)
        self._changed()
