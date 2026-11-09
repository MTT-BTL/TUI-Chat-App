from dataclasses import dataclass
from secrets import token_hex
from time import time

from . import db 
from .config import AppError


@dataclass(frozen=True)
class Session:
    token: str
    user_id: str
    expires: float
    connection_id: object
    ip_addr: str

def valid_str_input(value: bytes) -> bool: return isinstance(value, bytes) and bool(value) and all(0x20 <= c < 0x7f for c in value)

class AuthService:
    def __init__(self, database):
        self.session_manager: dict[str, Session] = {}   # 10-20 user, co the dung object der quarn lys
        self.db = database 
        self.session_ttl = 3600*12      # 12 hours, tha ho tang ca, login every day 

    def _user_account(self, username: bytes, password: bytes):
        if (not valid_str_input(username) or not valid_str_input(password) or 
            len(username) > 32 or len (username) < 6 or 
            len(password) > 32 or len(password) < 6):
            raise AppError("Username/password phải là ASCII printable. Độ dài từ 6 đến 32")
        return username.decode("ascii"), password.decode("ascii")

    """Connection checking"""
    def _available(self, connection_id: object):
        if connection_id is None: raise AppError("Cần object connection để bind session")
        for token, session in list(self.session_manager.items()):
            if session.expires <= time(): del self.session_manager[token]
        if any(session.connection_id is connection_id for session in self.session_manager.values()):
            raise AppError("Connection đã login; logout trước khi đổi account", "INVALID_STATE")

    """Update session to session manage"""
    def _new_session(self, user_id: str, ip_addr: str, connection_id: object) -> Session:
        token = token_hex(16)  # 16 random bytes -> 32 lowercase hex, đúng header GLP.
        while token in self.session_manager: token = token_hex(16)
        session = Session(token, user_id, time() + self.session_ttl, connection_id, ip_addr)
        self.session_manager[token] = session
        return session
    
    """Checking in database, return blinded session"""
    def login(self, username: bytes, password: bytes, ip_addr: str, connection_id: object) -> Session:
        username_text, password_text = self._user_account(username, password)
        self._available(connection_id)
        user_id = db.login(self.db, username_text, password_text)
        return self._new_session(user_id, ip_addr, connection_id)
    
    """Checking in database, return blinded session, default displayname is username"""
    def register(self, username: bytes, password: bytes, ip_addr: str, connection_id: object) -> Session:
        username_text, password_text = self._user_account(username, password)
        self._available(connection_id)  
        user_id, _, _ = db.register(self.db, username_text, password_text, username_text)
        return self._new_session(user_id, ip_addr, connection_id)

    """Check if the connection had a sesison"""
    def get_session(self, token: str, connection_id: object) -> Session:
        session = self.session_manager.get(token)
        if session is not None and session.expires <= time():
            del self.session_manager[token]
            session = None
        if session is None or session.connection_id is not connection_id:
            raise AppError("Session không tồn tại, hết hạn hoặc sai connection", "SESSION_INVALID")
        return session


    def rename(self, user_id: str, value: bytes):
        try: name = value.decode("utf-8").strip()
        except UnicodeDecodeError as error: raise AppError("Tên hiển thị phải là UTF-8") from error
        if not 6 <= len(name) <= 32 or not name.isprintable(): raise AppError("Tên hiển thị cần 6-32 ký tự, không chứa ký tự điều khiển")
        return db.set_displayname(self.db, user_id, name)

    def logout(self, token: str, connection_id: object):
        self.get_session(token, connection_id=connection_id)
        del self.session_manager[token] 

    def disconnect(self, connection_id: object):
        for token, session in list(self.session_manager.items()):
            if session.connection_id is connection_id:
                del self.session_manager[token]
    
class UserManager:
    def __init__(self):
        self.conns = {}
        self.users = {}

    def add(self, conn, user_id, ip_addr):
        self.conns[conn] = user_id
        self.users[user_id] = conn
    def remove(self, conn):
        if conn in self.conns:
            user_id = self.conns.pop(conn)
            self.users.pop(user_id, None)

    def user_ids(self) -> list: return list(self.users.keys())
    def get_conn(self, user_id): return self.users.get(user_id)
