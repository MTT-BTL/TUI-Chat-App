def error_message(message: str) -> str:
    text = message.strip().removeprefix("[!]").lstrip()
    return f"[!] {text}"


class GLPError(Exception):
    def __init__(self, message: str):
        super().__init__(error_message(message))


class ProtocolError(GLPError, ValueError):
    """Frame hoặc response sai format."""


class AppError(GLPError):
    """Yêu cầu nghiệp vụ không hợp lệ."""


class AuthError(AppError):
    """Thông tin đăng nhập sai."""


class SessionError(AuthError):
    """Session không dùng được trên connection hiện tại."""


class FileError(AppError, ValueError):
    """Metadata, segment hoặc thao tác file không hợp lệ."""


class GLPConnectionError(GLPError, ConnectionError):
    """Không thiết lập hoặc duy trì được connection."""


class GLPTimeoutError(GLPError, TimeoutError):
    """Trao đổi qua connection quá hạn."""


class PeerClosedError(GLPError, EOFError):
    """Peer đóng stream giữa các frame."""
