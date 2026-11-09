"""Hàm account dùng object sqlite3.Connection; mở/đóng DB ở thread sử dụng nó."""
import sqlite3
import uuid
from hashlib import sha256
from hmac import compare_digest
from os import urandom
from .config import AppError


def connect(dbfile) -> sqlite3.Connection:
    database = sqlite3.connect(dbfile, check_same_thread=False)
    return database

def init(database: sqlite3.Connection):
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA journal_mode=WAL;")
    database.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
            dispname TEXT NOT NULL, salt BLOB NOT NULL, passwd_h BLOB NOT NULL
        )
    """)
    database.execute("""
        CREATE TABLE IF NOT EXISTS messages(
            msg_id INTEGER PRIMARY KEY AUTOINCREMENT, sender_id TEXT NOT NULL, receiver_id TEXT,
            content TEXT NOT NULL, time DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

def register(database: sqlite3.Connection, username: str, password: str, dispname: str | None = None):
    user_id, salt = uuid.uuid4().hex, urandom(32)
    dispname = username if dispname is None else dispname
    digest = sha256(password.encode() + salt).digest()
    try:
        database.execute(
            "INSERT INTO users(user_id,username,dispname,salt,passwd_h) VALUES(?,?,?,?,?)",
            (user_id, username, dispname, salt, digest)
        )
    except sqlite3.IntegrityError as error: raise AppError("[!] Username already exists!", "CONFLICT") from error
    return user_id, username, dispname


def login(database: sqlite3.Connection, username: str, password: str):
    user = database.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if user and compare_digest(user["passwd_h"], sha256(password.encode() + user["salt"]).digest()): return user["user_id"]
    raise AppError("Username or password is incorrect", "LOGIN_FAILED")


def set_displayname(database: sqlite3.Connection, user_id: str, name: str):
    changed = database.execute("UPDATE users SET dispname = ? WHERE user_id = ?", (name, user_id))
    if not changed.rowcount: raise AppError("[!] Account không tồn tại", "SESSION_INVALID")
    return database.execute(
        "SELECT user_id, username, dispname FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()

def send_msg(database: sqlite3.Connection, sender_id: str, receiver_id: str|None, msg: str):
    try:
        database.execute("INSERT INTO messages(sender_id, receiver_id, content) VALUES(?, ?, ?)",
            (sender_id, receiver_id, msg)
        )
    except: raise AppError("[!] Lỗi khi gửi tin nhắn!")
    return sender_id, receiver_id, msg

"""Lấy message cho từng recevier, None là Global vì app chỉ hỗ trợ private và global chat"""
def get_msg(database: sqlite3.Connection, user_id: str, receiver_id: str|None):
    msgs = []  
    try: 
        if receiver_id: msgs = database.execute("""
                SELECT sender_id, content, time FROM messages 
                WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?) 
                ORDER BY time ASC LIMIT 50
            """, (user_id, receiver_id, receiver_id, user_id)).fetchall()
        
        else: msgs = database.execute("""
                SELECT sender_id, content, time FROM messages 
                WHERE receiver_id IS NULL 
                ORDER BY time ASC LIMIT 50
            """).fetchall()
    except: raise AppError("[!] Lỗi truy vấn lịch sử chat!") 
        
    return msgs

"""Lấy Chat cho từng user, hiển thị lại dù nếu từng có trước đó để nếu đối phương offline vẫn đọc được tin nhắn cũ!"""
def get_chat (database: sqlite3.Connection, user_id: str):
    try:
        rows = database.execute("""
            SELECT DISTINCT u.user_id, u.dispname
            FROM users u
            JOIN messages m ON
                (m.sender_id = ? AND m.receiver_id = u.user_id)
                OR (m.receiver_id = ? AND m.sender_id = u.user_id)
        """, (user_id, user_id)).fetchall()
        return [{"user_id": r["user_id"], "dispname": r["dispname"]} for r in rows]
    except: raise AppError("[!] Lỗi lấy danh sách Inbox!")


