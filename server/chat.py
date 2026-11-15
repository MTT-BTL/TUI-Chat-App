"""Chat service: global/private dùng chung target và cách phân trang."""
import json
from Error import AppError
from . import db


def packed(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class MessageService:
    def __init__(self, database):
        self.db = database

    def target(self, raw):
        target = raw.decode("utf-8")
        if target == "GLOBAL":
            return None
        if not self.db.execute("SELECT 1 FROM users WHERE user_id=?", (target,)).fetchone():
            raise AppError("Người nhận không tồn tại")
        return target

    def send(self, sender_id, receiver_id, raw):
        content = raw.decode("utf-8")
        if not content.strip() or len(raw) > 4096:
            raise AppError("Tin nhắn cần 1-4096 byte UTF-8")
        if any(not char.isprintable() and char not in "\n\t" for char in content):
            raise AppError("Tin nhắn chứa ký tự điều khiển")
        return db.send_msg(self.db, sender_id, receiver_id, content)

    def history(self, user_id, target, limit=10, before=0):
        if not 1 <= limit <= 100 or before < 0:
            raise AppError("Limit cần 1-100; before không âm")
        rows = db.get_msg(self.db, user_id, target, limit, before)
        page = []
        for row in rows[:limit]:
            candidate = page + [dict(row)]
            if len(packed(candidate)) > 60 * 1024:
                break  # Một JSON arg luôn nằm trong giới hạn GLP 64 KiB.
            page = candidate
        more = len(rows) > len(page)
        cursor = str(page[-1]["msg_id"]) if more and page else ""
        return list(reversed(page)), cursor

    def chats(self, user_id, online):
        # Gom online và metadata user ở đây; không cần SYS.USERS/FILE.CHATS riêng.
        rows = self.db.execute(
            "SELECT user_id,username,dispname FROM users WHERE user_id<>? ORDER BY username",
            (user_id,),
        ).fetchall()
        return [{"target": "GLOBAL", "username": "GLOBAL", "dispname": "GLOBAL", "online": True}] + [
            dict(row, target=row["user_id"], online=row["user_id"] in online.users) for row in rows
        ]
