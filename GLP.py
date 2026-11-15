from dataclasses import dataclass
import re
from Error import ProtocolError, error_message

MAGIC_BYTES = b"GLP\r\n"
CRLF = b"\r\n"
DEFAULT_SESSION = "_THIS_IS_THE_DEFAULT_SESSION_ID_"
HOLDING_SESSION = "_YOU_DO_NOT_CHANGE_YOUR_SESSION_"
FUNCS = { # Request TYPE.ACTION, ROUTE.ACTION
    "SYS": ["PING"],
    "AUTH": ["LOGIN", "REGISTER", "LOGOUT"],
    "CHAT": ["LIST", "SEND", "HISTORY"],
    "FILE": ["UPLOAD", "SEGMENT", "DOWNLOAD", "CANCEL", "LIST"],
}
STATUSES = { # Response tương tự request
    "OK": ["PONG"],
    "AUTH": ["OK", "BYE"],
    "CHAT": ["ITEMS", "PAGE", "SENT", "MESSAGE"],
    "FILE": ["READY", "STORED", "CANCELLED", "ITEMS", "META", "DATA", "NOTICE"],
    "ERROR": ["SERVER", "CLIENT", "AUTH", "FILE"],
}
MAX_LINE, MAX_ARGS, MAX_ARG, MAX_FRAME = 64, 4, 65536, 262144

ROUTE = re.compile("|".join(re.escape(f"{Type}.{Action}") for Type, Actions in FUNCS.items() for Action in Actions))
TOKEN = re.compile(r"[0-9a-f]{32}")
ACTION = re.compile("|".join(re.escape(f"{Type}.{Action}" if Action else Type) for Type, Actions in STATUSES.items() for Action in Actions or [""]))

# ========================== PACKING ========================== #
def _head(value: str) -> bytes: # Một dòng header ASCII + CRLF.
    try: raw = value.encode("ascii")
    except UnicodeEncodeError as error: raise ProtocolError("Header phải là ASCII") from error
    if len(raw) > MAX_LINE or b"\r" in raw or b"\n" in raw: raise ProtocolError("Header line không hợp lệ")
    return raw + CRLF

def _body(args: tuple[bytes, ...]) -> bytes: # argc, rồi length + raw bytes + CRLF cho từng arg.
    if len(args) > MAX_ARGS: raise ProtocolError("Quá nhiều args")
    parts = [str(len(args)).encode("ascii") + CRLF]
    size = len(parts[0])
    for raw in args:
        if not isinstance(raw, bytes) or len(raw) > MAX_ARG: raise ProtocolError("Mỗi arg phải là bytes, tối đa 64 KiB")
        part = str(len(raw)).encode("ascii") + CRLF + raw + CRLF
        size += len(part)
        if size > MAX_FRAME: raise ProtocolError("Frame vượt 256 KiB")
        parts.append(part)
    return b"".join(parts)

def _bounded(wire: bytes) -> bytes:
    if len(wire) > MAX_FRAME: raise ProtocolError("Frame vượt 256 KiB")
    return wire
# ========================== PACKING ========================== #


def valid_session(value: str, default: str) -> bool: return value == default or bool(TOKEN.fullmatch(value))

@dataclass(frozen=True)
class Request:
    route: str
    session_id: str = DEFAULT_SESSION
    args: tuple[bytes, ...] = ()
    def validate(self):
        if not valid_session(self.session_id, DEFAULT_SESSION) or not ROUTE.fullmatch(self.route):
            raise ProtocolError("Session hoặc route TYPE.ACTION sai format")

    def to_bytes(self) -> bytes:
        self.validate()
        return _bounded(MAGIC_BYTES + _head(self.route) + _head(self.session_id) + CRLF + _body(self.args))

    
@dataclass(frozen=True)
class Response:
    action: str
    session_update: str = HOLDING_SESSION 
    args: tuple[bytes, ...] = ()

    def validate(self):
        if not ACTION.fullmatch(self.action): raise ProtocolError("Action không hợp lệ")
        if self.session_update != HOLDING_SESSION and not valid_session(self.session_update, DEFAULT_SESSION):
            raise ProtocolError("Session update không hợp lệ")

    def to_bytes(self) -> bytes:
        self.validate()
        return _bounded(MAGIC_BYTES + _head(self.action) + _head(self.session_update) + CRLF + _body(self.args))


def error_response(action: str, message: str, *, session_update=HOLDING_SESSION) -> Response:
    return Response(action, session_update, (error_message(message).encode("utf-8"),))
