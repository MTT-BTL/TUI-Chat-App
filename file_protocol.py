"""Chỉ các tên/giới hạn FILE; không thay format hoặc parser GLP."""

CHUNK_SIZE = 32 * 1024
MAX_FILE_SIZE = 1024 * 1024 * 1024
PAGE_SIZE = 50
FILE_ROUTES = ["UPLOAD", "CHUNK", "COMPLETE", "CANCEL", "LIST", "GET", "CHATS"]
FILE_STATUSES = ["READY", "ACK", "STORED", "CANCELLED", "ITEMS", "DATA", "PEERS", "NOTICE", "ERROR"]


def filename(value: str) -> str:
    """Tên hiển thị/local basename, không bao giờ là đường dẫn từ peer."""
    if (not value or value in (".", "..") or not value.isprintable()
            or any(char in value for char in '/\\:*?"<>|')
            or value.endswith((" ", ".")) or len(value.encode("utf-8")) > 255):
        raise ValueError("Tên file không hợp lệ hoặc quá 255 byte UTF-8")
    return value


def file_size(size: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
