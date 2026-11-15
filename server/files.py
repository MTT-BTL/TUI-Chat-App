"""FILE: khai báo metadata, nhận đủ N segment rồi tự commit."""
from dataclasses import dataclass
import json
from os import fstat
from pathlib import Path
from sqlite3 import Error as DatabaseError
from uuid import uuid4

from Error import FileError, error_message
from GLP import Response, error_response
from file_transfer import CHUNK_SIZE, FileMetadata, SegmentWriter, read_segments, segment_count
from .chat import packed

PAGE_SIZE = 10


def reply(action, *args):
    return Response("FILE." + action, args=tuple(
        arg if isinstance(arg, bytes) else str(arg).encode("utf-8") for arg in args
    ))


def failure(error):
    if isinstance(error, FileError):
        return error_response("ERROR.FILE", str(error))
    if isinstance(error, (OSError, DatabaseError)):
        print(error_message(f"Lỗi lưu trữ file. {error}"))
        return error_response("ERROR.FILE", "Server không đọc hoặc lưu được file")
    return error_response("ERROR.FILE", "Tham số FILE không hợp lệ")


@dataclass
class Download:
    metadata: dict
    path: Path

    def frames(self):
        """Một META, đúng N DATA; client không cần GET từng segment."""
        try:
            with self.path.open("rb") as stream:
                size = self.metadata["size"]
                if fstat(stream.fileno()).st_size != size:
                    raise FileError("File cache không còn đúng size")
                yield reply("META", packed(self.metadata))
                for index, data in read_segments(stream, size):
                    yield reply("DATA", self.metadata["file_id"], index, data)
        except (OSError, ValueError) as error:
            yield failure(error)


@dataclass
class Upload:
    file_id: str
    sender_id: str
    receiver_id: str | None
    token: str
    path: Path
    writer: SegmentWriter


class FileService:
    COUNTS = {"FILE.UPLOAD": 2, "FILE.SEGMENT": 3,
              "FILE.CANCEL": 1, "FILE.LIST": 2, "FILE.DOWNLOAD": 1}
    SELECT = """
        SELECT f.*, s.dispname AS sender_name, r.dispname AS receiver_name
        FROM files f JOIN users s ON s.user_id=f.sender_id
        LEFT JOIN users r ON r.user_id=f.receiver_id
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
        for part in self.folder.glob("*.part"):
            part.unlink()  # Chỉ dọn upload chưa hoàn tất, giữ các file đã lưu.

    def disconnect(self, connection):
        upload = self.uploads.pop(connection, None)
        if upload is not None:
            try:
                upload.writer.stream.close()
            finally:
                upload.path.unlink(missing_ok=True)

    def _target(self, raw):
        target = raw.decode("utf-8")
        if target == "GLOBAL":
            return None
        if not self.db.execute("SELECT 1 FROM users WHERE user_id=?", (target,)).fetchone():
            raise FileError("Không có người nhận này")
        return target

    def _upload(self, connection, session, raw_id):
        upload = self.uploads.get(connection)
        if (upload is None or upload.file_id != raw_id.decode("ascii")
                or upload.token != session.token):
            raise FileError("Không có upload này trên session hiện tại")
        return upload

    def _metadata(self, row):
        metadata = dict(row)
        metadata["segments"] = segment_count(metadata["size"])
        metadata["segment_size"] = CHUNK_SIZE
        return metadata

    def _store(self, connection, upload, online):
        upload.writer.verify()
        upload.writer.stream.close()
        meta = upload.writer.metadata
        destination = self.folder / upload.file_id
        upload.path.rename(destination)
        try:
            self.db.execute("""
                INSERT INTO files(file_id,sender_id,receiver_id,name,size,sha256)
                VALUES(?,?,?,?,?,?)
            """, (upload.file_id, upload.sender_id, upload.receiver_id,
                  meta.name, meta.size, meta.sha256))
        except DatabaseError:
            destination.unlink(missing_ok=True)
            raise
        self.uploads.pop(connection)
        row = self.db.execute(self.SELECT + " WHERE f.file_id=?", (upload.file_id,)).fetchone()
        metadata = self._metadata(row)
        notice = reply("NOTICE", packed(metadata))
        for peer, uid in list(online.conns.items()):
            if peer is not connection and (upload.receiver_id is None
                    or uid in (upload.sender_id, upload.receiver_id)):
                try:
                    peer.send(notice)
                except OSError:
                    pass
        return reply("STORED", packed(metadata))

    def dispatch(self, request, session, connection, online):
        route, args = request.route, request.args
        started = False
        try:
            if route not in self.COUNTS or len(args) != self.COUNTS[route]:
                raise FileError("Route FILE hoặc số args không đúng")
            if route == "FILE.UPLOAD":
                if connection in self.uploads:
                    raise FileError("Hoàn tất/hủy upload hiện tại trước")
                receiver = self._target(args[0])
                metadata = FileMetadata.from_dict(json.loads(args[1]))
                file_id = uuid4().hex
                path = self.folder / (file_id + ".part")
                writer = SegmentWriter(path.open("xb", buffering=0), metadata)
                upload = Upload(file_id, session.user_id, receiver, session.token, path, writer)
                self.uploads[connection] = upload
                started = True
                if writer.complete:
                    return self._store(connection, upload, online)
                return reply("READY", file_id, metadata.segments)
            if route == "FILE.SEGMENT":
                upload = self._upload(connection, session, args[0])
                upload.writer.append(int(args[1]), args[2])
                if upload.writer.complete:
                    return self._store(connection, upload, online)
                return None  # Segment trung gian không có phản hồi GLP.
            if route == "FILE.CANCEL":
                upload = self._upload(connection, session, args[0])
                self.disconnect(connection)
                return reply("CANCELLED", upload.file_id)
            if route == "FILE.LIST":
                receiver, cursor = self._target(args[0]), int(args[1])
                if cursor < 0:
                    raise FileError("Cursor phải không âm")
                where, values = "f.receiver_id IS NULL", []
                if receiver is not None:
                    where = "((f.sender_id=? AND f.receiver_id=?) OR (f.sender_id=? AND f.receiver_id=?))"
                    values = [session.user_id, receiver, receiver, session.user_id]
                if cursor:
                    where += " AND f.seq < ?"
                    values.append(cursor)
                rows = self.db.execute(self.SELECT + " WHERE " + where
                                       + " ORDER BY f.seq DESC LIMIT ?", values + [PAGE_SIZE + 1]).fetchall()
                page = [self._metadata(row) for row in rows[:PAGE_SIZE]]
                cursor = str(page[-1]["seq"]) if len(rows) > PAGE_SIZE else ""
                return reply("ITEMS", args[0], packed(page), cursor)
            if route == "FILE.DOWNLOAD":
                file_id = args[0].decode("ascii")
                row = self.db.execute(self.SELECT + " WHERE f.file_id=?", (file_id,)).fetchone()
                if row is None or (row["receiver_id"] is not None
                        and session.user_id not in (row["sender_id"], row["receiver_id"])):
                    raise FileError("File không tồn tại hoặc bạn không có quyền tải")
                return Download(self._metadata(row), self.folder / row["file_id"])
        except (ValueError, OSError, DatabaseError, OverflowError) as error:
            if route == "FILE.SEGMENT" or started:
                self.disconnect(connection)
            return failure(error)
