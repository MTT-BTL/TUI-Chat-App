"""Đọc lệnh trên stdin và hiển thị kết quả từ Backend."""
import getpass
from pathlib import Path
import shlex
import sys
import threading

try:
    import readline  # Giữ phần đang gõ trên terminal có readline.
except ImportError:
    readline = None

from Error import AppError, GLPError, error_message
from .client_backend import Backend

HELP = """listchat                  danh sách chat/người dùng
chat <số|username|ID> [n]  vào chat, xem n tin mới nhất (mặc định 10)
history [n] / more [n]    xem lại mới nhất / thêm tin cũ hơn
files [more]              danh sách file của chat đang mở
upload <đường dẫn>        gửi file qua server vào chat đang mở
download <số|file-ID> [thư mục]
back / logout / quit
Trong chat: gõ nội dung rồi Enter để gửi; lệnh bắt đầu bằng /.
Đường dẫn có khoảng trắng: đặt trong dấu nháy. /help để xem lại."""


def clean(value): return "".join(char for char in str(value) if char.isprintable() or char in "\n\t")


def file_size(size):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


class CLI:
    def __init__(self, host="localhost", port=8443, cafile=None, download_dir="downloads",
                 backend=None, reader=input, password_reader=getpass.getpass, output=None):
        self.output = output or sys.stdout
        self.reader, self.password_reader = reader, password_reader
        self._print_lock = threading.RLock()
        self._prompt = ""
        self.backend = backend or Backend(host, port, self.notify, cafile=cafile)
        self.download_dir = Path(download_dir)
        self.target = None
        self.chats = []
        self.history_cursors, self.file_cursors, self.file_items = {}, {}, {}

    def write(self, text):
        with self._print_lock:
            redraw = bool(self._prompt and self.output.isatty())
            if redraw:
                self.output.write("\r\033[2K")
            self.output.write(clean(text) + "\n")
            if redraw:
                self.output.write(self._prompt)
                if readline is not None:
                    self.output.write(readline.get_line_buffer())
            self.output.flush()

    def read(self, prompt):
        with self._print_lock:
            self._prompt = prompt
        try:
            return self.reader(prompt)
        finally:
            with self._print_lock:
                self._prompt = ""

    def message(self, row):
        mine = row["sender_id"] == self.backend.user.get("user_id")
        name = "tôi" if mine else row.get("sender_name", row["sender_id"])
        return f"[{row.get('time', '')}] {name}: {row['content']}"

    def notify(self, result):
        """Hiển thị event từ reader hoặc tiến độ từ thao tác truyền file."""
        if result.kind == "message":
            row = result.data
            target = "GLOBAL" if row["receiver_id"] is None else (
                row["receiver_id"] if row["sender_id"] == self.backend.user.get("user_id") else row["sender_id"]
            )
            prefix = "" if self.target and self.target["target"] == target else f"[thông báo {row['sender_name']}] "
            self.write(prefix + self.message(row))
        elif result.kind == "file_notice":
            row = result.data
            self.write(f"[file] {row['sender_name']}: {row['name']} ({file_size(row['size'])}) | ID {row['file_id']}")
        elif result.kind == "progress":
            label, done, total = result.data
            blocks = done * 10 // total
            self.write(f"{label} [{'#' * blocks}{'.' * (10 - blocks)}] {done}/{total} segment")
        else:
            self.write(result.message)

    def accept(self, result):
        if not result.ok:
            self.write(result.message)
            return False
        return True

    def reset(self):
        self.target, self.chats = None, []
        self.history_cursors.clear()
        self.file_cursors.clear()
        self.file_items.clear()

    def listchat(self):
        result = self.backend.list_chats()
        if not self.accept(result):
            return
        self.chats = result.data
        self.target = None
        for index, chat in enumerate(self.chats):
            status = "online" if chat["online"] else "offline"
            self.write(f"{index:>2}  {chat['dispname']} (@{chat['username']})  [{status}]")
        self.write("Vào chat: chat <số hoặc username>")

    def select_chat(self, token):
        if not self.chats:
            self.listchat()
        if token.isdecimal() and int(token) < len(self.chats):
            return self.chats[int(token)]
        matches = [chat for chat in self.chats if token.casefold() in
                   (chat["target"].casefold(), chat["username"].casefold(), chat["dispname"].casefold())]
        if len(matches) != 1:
            raise AppError("Không tìm được chat duy nhất. Dùng listchat rồi chọn số")
        return matches[0]

    def history(self, limit=10, more=False):
        target = self.require_chat()
        cursor = self.history_cursors.get(target, "") if more else "0"
        if not cursor:
            self.write("Đã hết tin nhắn cũ")
            return
        result = self.backend.history(target, limit, cursor)
        if not self.accept(result):
            return
        self.history_cursors[target] = result.data["cursor"]
        rows = result.data["items"]
        self.write(f"--- {self.target['dispname']}: {len(rows)} tin {'cũ hơn' if more else 'gần nhất'} ---")
        for row in rows:
            self.write(self.message(row))
        if result.data["cursor"]:
            self.write("/more [n] để xem thêm tin cũ")

    def require_chat(self):
        if self.target is None:
            raise AppError("Chọn chat trước bằng lệnh chat <số hoặc username>")
        return self.target["target"]

    def files(self, more=False):
        target = self.require_chat()
        cursor = self.file_cursors.get(target, "") if more else "0"
        if not cursor:
            self.write("Đã hết file")
            return
        result = self.backend.list_files(target, cursor)
        if not self.accept(result):
            return
        self.file_cursors[target] = result.data["cursor"]
        if not more:
            self.file_items[target] = []
        items = self.file_items.setdefault(target, [])
        first = len(items)
        items.extend(result.data["items"])
        for index, row in enumerate(items[first:], first + 1):
            self.write(f"{index:>2}  {row['name']} | {file_size(row['size'])} | {row['segments']} segment | {row['file_id']}")
        if not items:
            self.write("Chưa có file trong chat này")
        if result.data["cursor"]:
            self.write("/files more để xem thêm")

    def execute(self, line):
        if not line.strip():
            return True
        if self.target and not line.startswith("/"):
            result = self.backend.send_chat(self.target["target"], line)
            if self.accept(result):
                self.write(self.message(result.data))
            return True
        parts = shlex.split(line.removeprefix("/"))
        if not parts:
            return True
        command, args = parts[0].lower(), parts[1:]
        if command in ("quit", "exit") and not args:
            return False
        if command == "help" and not args:
            self.write(HELP)
        elif command == "listchat" and not args:
            self.listchat()
        elif command == "chat" and 1 <= len(args) <= 2:
            self.target = self.select_chat(args[0])
            self.history(int(args[1]) if len(args) == 2 else 10)
        elif command in ("history", "more") and len(args) <= 1:
            self.history(int(args[0]) if args else 10, more=command == "more")
        elif command == "back" and not args:
            self.target = None
        elif command == "logout" and not args:
            if self.accept(self.backend.logout()):
                self.reset()
                self.write("Đã đăng xuất")
        elif command == "files" and (not args or args == ["more"]):
            self.files(bool(args))
        elif command == "upload" and len(args) == 1:
            result = self.backend.upload(self.require_chat(), args[0])
            if self.accept(result):
                self.write(f"Đã gửi {result.data['name']} | ID {result.data['file_id']}")
        elif command == "download" and 1 <= len(args) <= 2:
            target = self.require_chat()
            file_id = args[0]
            if len(file_id) < 32 and file_id.isdecimal():
                items = self.file_items.get(target, [])
                index = int(file_id) - 1
                if not 0 <= index < len(items):
                    raise AppError("Dùng /files rồi chọn đúng số file")
                file_id = items[index]["file_id"]
            folder = args[1] if len(args) == 2 else self.download_dir
            result = self.backend.download(file_id, folder)
            if self.accept(result):
                self.write(f"Đã tải: {result.data['path']} (SHA-256 khớp)")
        else:
            raise AppError("Lệnh hoặc args không đúng. Dùng help hoặc /help")
        return True

    def authenticate(self):
        action = self.read("login / register / quit [login]> ").strip().lower() or "login"
        if action in ("quit", "exit"):
            return False
        if action not in ("login", "register"):
            raise AppError("Chọn login, register hoặc quit")
        username = self.read("username> ").strip()
        password = self.password_reader("password> ")
        result = self.backend.login(username, password, register=action == "register")
        if self.accept(result):
            self.write(f"Đã đăng nhập: {result.data['username']}")
            self.listchat()
        return True

    def run(self):
        try:
            self.backend.start()
            self.write("GLP CLI | help để xem lệnh | Ctrl+C/Ctrl+D để thoát")
            while self.backend.connected:
                try:
                    if not self.backend.user:
                        self.reset()
                        if not self.authenticate():
                            break
                    else:
                        label = clean(self.target["username"]) if self.target else "menu"
                        if not self.execute(self.read(f"{label}> ")):
                            break
                except (GLPError, OSError, ValueError) as error:
                    self.write(error_message(str(error)))
        except (GLPError, OSError, ValueError) as error:
            self.write(error_message(str(error)))
            return 1
        except (EOFError, KeyboardInterrupt):
            self.write("Đã thoát")
        finally:
            self.backend.close()
