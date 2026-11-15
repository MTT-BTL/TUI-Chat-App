"""SQLite dùng chung dưới app_lock; autocommit để account/chat còn sau restart."""
import sqlite3
import uuid
from hashlib import sha256
from hmac import compare_digest
from os import urandom
from Error import AppError, AuthError, SessionError


def connect(dbfile) -> sqlite3.Connection:
    database = sqlite3.connect(dbfile, check_same_thread=False, isolation_level=None)
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
    except sqlite3.IntegrityError as error:
        raise AppError("Username đã tồn tại") from error
    return user_id, username, dispname


def login(database: sqlite3.Connection, username: str, password: str):
    user = database.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if user and compare_digest(user["passwd_h"], sha256(password.encode() + user["salt"]).digest()): return user["user_id"]
    raise AuthError("Username hoặc password không đúng")


def set_displayname(database: sqlite3.Connection, user_id: str, name: str):
    changed = database.execute("UPDATE users SET dispname = ? WHERE user_id = ?", (name, user_id))
    if not changed.rowcount: raise SessionError("Account không tồn tại")
    return database.execute(
        "SELECT user_id, username, dispname FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()

def send_msg(database, sender_id, receiver_id, content):
    cursor = database.execute(
        "INSERT INTO messages(sender_id,receiver_id,content) VALUES(?,?,?)",
        (sender_id, receiver_id, content),
    )
    return dict(database.execute("""
        SELECT m.*, u.dispname AS sender_name FROM messages m
        JOIN users u ON u.user_id=m.sender_id WHERE m.msg_id=?
    """, (cursor.lastrowid,)).fetchone())


def get_msg(database, user_id, receiver_id, limit=10, before=0):
    # Chọn từ mới về cũ bằng msg_id; timestamp có thể trùng nhau.
    if receiver_id is None:
        where, values = "m.receiver_id IS NULL", []
    else:
        where = "((m.sender_id=? AND m.receiver_id=?) OR (m.sender_id=? AND m.receiver_id=?))"
        values = [user_id, receiver_id, receiver_id, user_id]
    if before:
        where += " AND m.msg_id < ?"
        values.append(before)
    return database.execute("""
        SELECT m.*, u.dispname AS sender_name FROM messages m
        JOIN users u ON u.user_id=m.sender_id WHERE """ + where
        + " ORDER BY m.msg_id DESC LIMIT ?", values + [limit + 1]).fetchall()
