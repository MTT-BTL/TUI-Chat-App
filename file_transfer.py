from dataclasses import dataclass
from hashlib import sha256
from os import fstat
import re
from typing import BinaryIO
from Error import FileError

CHUNK_SIZE = 32 * 1024
MAX_FILE_SIZE = 1024 ** 3


def segment_count(size: int) -> int: return (size + CHUNK_SIZE - 1) // CHUNK_SIZE

def segment_length(size: int, index: int) -> int: return min(CHUNK_SIZE, size - index * CHUNK_SIZE)

def read_segments(stream: BinaryIO, size: int):
    for index in range(segment_count(size)):
        length = segment_length(size, index)
        data = stream.read(length)
        if len(data) != length:
            raise FileError("File bị cắt giữa lúc đọc")
        yield index, data

@dataclass(frozen=True)
class FileMetadata:
    name: str
    size: int
    segments: int
    sha256: str

    def __post_init__(self):
        name = self.name
        if (not isinstance(name, str) or not name or not name.isprintable() or name.endswith((" ", ".")) or len(name.encode("utf-8")) > 255 or any(char in name for char in '/\\:*?"<>|')): raise FileError("Tên file phải là một basename hợp lệ")
        if type(self.size) is not int or not 0 <= self.size <= MAX_FILE_SIZE: raise FileError("Size phải từ 0 byte đến 1 GiB")
        if type(self.segments) is not int or self.segments != segment_count(self.size): raise FileError("Số segment không khớp size")
        if not isinstance(self.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.sha256): raise FileError("SHA-256 phải là 64 ký tự hex thường")

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict): raise FileError("Metadata phải là object JSON")
        return cls(value.get("name"), value.get("size"), value.get("segments"), value.get("sha256"))

    @classmethod
    def from_file(cls, stream: BinaryIO, name: str):
        size = fstat(stream.fileno()).st_size
        if not 0 <= size <= MAX_FILE_SIZE: raise FileError("File vượt giới hạn 1 GiB")
        digest = sha256()
        for _, data in read_segments(stream, size): digest.update(data)
        if stream.read(1): raise FileError("File thay đổi khi đọc metadata")
        stream.seek(0)
        return cls(name, size, segment_count(size), digest.hexdigest())

class SegmentWriter:
    def __init__(self, stream: BinaryIO, metadata: FileMetadata):
        self.stream = stream
        self.metadata = metadata
        self.next_index = 0
        self.digest = sha256()

    @property
    def complete(self) -> bool:
        return self.next_index == self.metadata.segments

    def append(self, index: int, data: bytes):
        if self.complete or index != self.next_index:
            raise FileError("Segment sai thứ tự")
        if len(data) != segment_length(self.metadata.size, index):
            raise FileError("Segment sai kích thước")
        if self.stream.write(data) != len(data):
            raise FileError("Không ghi đủ bytes của segment")
        self.digest.update(data)
        self.next_index += 1

    def verify(self):
        if not self.complete or self.digest.hexdigest() != self.metadata.sha256:
            raise FileError("File chưa đủ segment hoặc SHA-256 không khớp")
