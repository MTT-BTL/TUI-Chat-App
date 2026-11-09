"""FILE: chunk tuần tự trên cùng GLP connection, cache disk + metadata SQLite.

Router/handler gọi dưới app_lock hiện có; không thêm thread hay socket.
"""
from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import threading
from uuid import uuid4

from GLP import Response
from file_protocol import CHUNK_SIZE, MAX_FILE_SIZE, PAGE_SIZE, filename


def reply(action, *args):
    return Response("FILE." + action, args=tuple(
        arg if isinstance(arg, bytes) else str(arg).encode("utf-8") for arg in args
    ))


def packed(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class FileConnection:
    """Serialize sendall từng frame, kể cả MSG cũ, khi FILE cùng dùng socket."""
    def __init__(self, connection):
        self.connection = connection
        self.lock = threading.Lock()

    def send(self, frame):
        with self.lock:
            self.connection.send(frame)

    def recv(self):
        return self.connection.recv()

    def close(self):
        self.connection.close()


@dataclass
class Upload:
    file_id: str
    sender_id: str
    receiver_id: str | None
    name: str
    size: int
    token: str
    path: Path
    offset: int = 0
    digest: object = field(default_factory=sha256)


class FileService:
    COUNTS = {"FILE.UPLOAD": 3, "FILE.CHUNK": 3, "FILE.COMPLETE": 2,
              "FILE.CANCEL": 1, "FILE.LIST": 2, "FILE.GET": 2, "FILE.CHATS": 0}
    SELECT = """
        SELECT f.*, s.dispname AS sender_name, r.dispname AS receiver_name
        FROM files f JOIN users s ON s.user_id = f.sender_id
        LEFT JOIN users r ON r.user_id = f.receiver_id
    """

    def __init__(self, database, folder):
        self.db, self.folder = database, Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.uploads = {}
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS files (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, file_id TEXT NOT NULL UNIQUE,
                sender_id TEXT NOT NULL, receiver_id TEXT, name TEXT NOT NULL,
                size INTEGER NOT NULL, sha256 TEXT NOT NULL,
                time DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.db.commit()
        for part in self.folder.glob("*.part"):
            part.unlink()  # upload chưa commit của lần server dừng trước

    def disconnect(self, connection):
        upload = self.uploads.pop(connection, None)
        if upload is not None:
            upload.path.unlink(missing_ok=True)

    def _target(self, raw):
        target = raw.decode("utf-8")
        if target == "GLOBAL":
            return None
        if not self.db.execute("SELECT 1 FROM users WHERE user_id=?", (target,)).fetchone():
            raise ValueError("Không có người nhận này")
        return target

    def _upload(self, connection, session, raw_id):
        upload = self.uploads.get(connection)
        if (upload is None or upload.file_id != raw_id.decode("ascii")
                or upload.token != session.token):
            raise ValueError("Không có upload này trên session hiện tại")
        return upload

    def _metadata(self, file_id):
        return dict(self.db.execute(self.SELECT + " WHERE f.file_id=?", (file_id,)).fetchone())

    def dispatch(self, request, session, connection, online):
        route, args = request.route, request.args
        try:
            if route not in self.COUNTS or len(args) != self.COUNTS[route]:
                raise ValueError("Route FILE hoặc số args không đúng")

            if route == "FILE.UPLOAD":
                if connection in self.uploads:
                    raise ValueError("Hãy hoàn tất/hủy upload hiện tại trước")
                receiver = self._target(args[0])
                name, size = filename(args[1].decode("utf-8")), int(args[2])
                if not 0 <= size <= MAX_FILE_SIZE:
                    raise ValueError("File phải từ 0 byte đến 1 GiB")
                file_id = uuid4().hex
                path = self.folder / (file_id + ".part")
                path.touch(exist_ok=False)
                self.uploads[connection] = Upload(file_id, session.user_id, receiver,
                                                  name, size, session.token, path)
                return reply("READY", file_id)

            if route == "FILE.CHUNK":
                upload = self._upload(connection, session, args[0])
                offset, data = int(args[1]), args[2]
                if (offset != upload.offset or not 0 < len(data) <= CHUNK_SIZE
                        or offset + len(data) > upload.size):
                    raise ValueError("Chunk sai offset hoặc kích thước")
                with upload.path.open("ab") as stream:
                    stream.write(data)
                upload.digest.update(data)
                upload.offset += len(data)
                return reply("ACK", upload.file_id, upload.offset)

            if route == "FILE.COMPLETE":
                upload = self._upload(connection, session, args[0])
                digest = args[1].decode("ascii")
                if upload.offset != upload.size or upload.digest.hexdigest() != digest:
                    raise ValueError("File chưa đủ size hoặc SHA-256 không khớp")
                destination = self.folder / upload.file_id
                upload.path.rename(destination)
                try:
                    with self.db:
                        self.db.execute("""
                            INSERT INTO files(file_id,sender_id,receiver_id,name,size,sha256)
                            VALUES(?,?,?,?,?,?)
                        """, (upload.file_id, upload.sender_id, upload.receiver_id,
                              upload.name, upload.size, digest))
                except Exception:
                    destination.unlink(missing_ok=True)
                    raise
                self.uploads.pop(connection)
                metadata = self._metadata(upload.file_id)
                notice = reply("NOTICE", packed(metadata))
                for peer, user_id in list(online.conns.items()):
                    if peer is not connection and (upload.receiver_id is None
                            or user_id in (upload.sender_id, upload.receiver_id)):
                        try:
                            peer.send(notice)
                        except OSError:
                            pass
                return reply("STORED", packed(metadata))

            if route == "FILE.CANCEL":
                upload = self._upload(connection, session, args[0])
                self.disconnect(connection)
                return reply("CANCELLED", upload.file_id)

            if route == "FILE.LIST":
                receiver, cursor = self._target(args[0]), int(args[1])
                if cursor < 0:
                    raise ValueError("Cursor phải không âm")
                where, values = "f.receiver_id IS NULL", []
                if receiver is not None:
                    where = "((f.sender_id=? AND f.receiver_id=?) OR (f.sender_id=? AND f.receiver_id=?))"
                    values = [session.user_id, receiver, receiver, session.user_id]
                if cursor:
                    where += " AND f.seq < ?"
                    values.append(cursor)
                rows = self.db.execute(self.SELECT + " WHERE " + where
                                       + " ORDER BY f.seq DESC LIMIT ?", values + [PAGE_SIZE + 1]).fetchall()
                page = [dict(row) for row in rows[:PAGE_SIZE]]
                next_cursor = str(page[-1]["seq"]) if len(rows) > PAGE_SIZE else ""
                return reply("ITEMS", args[0], packed(page), next_cursor)

            if route == "FILE.GET":
                file_id, offset = args[0].decode("ascii"), int(args[1])
                row = self.db.execute("SELECT * FROM files WHERE file_id=?", (file_id,)).fetchone()
                if row is None or (row["receiver_id"] is not None
                        and session.user_id not in (row["sender_id"], row["receiver_id"])):
                    raise ValueError("File không tồn tại hoặc bạn không có quyền tải")
                if not 0 <= offset <= row["size"]:
                    raise ValueError("Offset tải không hợp lệ")
                with (self.folder / row["file_id"]).open("rb") as stream:
                    stream.seek(offset)
                    data = stream.read(min(CHUNK_SIZE, row["size"] - offset))
                return reply("DATA", file_id, offset, data)

            if route == "FILE.CHATS":
                rows = self.db.execute("""
                    SELECT DISTINCT u.user_id, u.dispname FROM users u JOIN files f ON
                    (f.sender_id=? AND f.receiver_id=u.user_id) OR
                    (f.receiver_id=? AND f.sender_id=u.user_id)
                """, (session.user_id, session.user_id)).fetchall()
                return reply("PEERS", packed([dict(row) for row in rows]))

        except (ValueError, OSError) as error:
            if route in ("FILE.CHUNK", "FILE.COMPLETE"):
                self.disconnect(connection)
            return reply("ERROR", route, str(error))
