import io
import unittest

from support import Response
from client.client_cli import CLI
from client.client_backend import Result, dispatch
from Error import AppError, ProtocolError


class FakeBackend:
    def __init__(self):
        self.user, self.calls, self.connected = {}, [], False
        self.rows = [{"msg_id": index, "sender_id": "me" if index % 2 else "bob",
                      "sender_name": "bobby001", "content": f"message-{index}", "time": "12:00"}
                     for index in range(1, 26)]

    def start(self):
        self.connected = True

    def close(self):
        self.connected = False

    def login(self, username, password, register=False):
        self.calls.append(("login", username, register))
        self.user = {"user_id": "me", "username": username, "dispname": username}
        return Result("login", self.user)

    def logout(self):
        self.user = {}
        return Result("logout")

    def list_chats(self):
        return Result("chats", [{"target": "GLOBAL", "username": "GLOBAL", "dispname": "GLOBAL", "online": True},
                                {"target": "bob", "username": "bobby001", "dispname": "Bob", "online": False}])

    def history(self, target, limit=10, before="0"):
        self.calls.append(("history", target, limit, before))
        rows = [row for row in self.rows if not int(before) or row["msg_id"] < int(before)]
        page = rows[-limit:]
        return Result("history", {"items": page, "cursor": str(page[0]["msg_id"]) if len(rows) > limit else ""})

    def send_chat(self, target, content):
        self.calls.append(("send", target, content))
        return Result("sent", {"sender_id": "me", "content": content, "time": "now"})

    def list_files(self, target, cursor="0"):
        return Result("files", {"items": [{"name": "a b.txt", "file_id": "f" * 32, "size": 3, "segments": 1}], "cursor": ""})

    def upload(self, target, path):
        self.calls.append(("upload", target, path))
        return Result("stored", {"name": "a b.txt", "file_id": "f" * 32})

    def download(self, file_id, folder):
        self.calls.append(("download", file_id, folder))
        return Result("downloaded", {"path": str(folder) + "/a b.txt"})


class CLITests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.output = io.StringIO()
        self.cli = CLI(backend=self.backend, output=self.output)
        self.backend.login("alice001", "test-password")

    def test_enter_chat_defaults_ten_both_sides_and_more_uses_cursor(self):
        self.cli.execute("listchat")
        self.cli.execute("chat 1")
        text = self.output.getvalue()
        self.assertIn("10 tin gần nhất", text)
        self.assertIn("tôi: message-25", text)
        self.assertIn("bobby001: message-24", text)
        self.assertNotIn("message-15\n", text)
        self.cli.execute("/more 5")
        self.assertIn(("history", "bob", 5, "16"), self.backend.calls)
        self.cli.execute("/history 20")
        self.assertIn(("history", "bob", 20, "0"), self.backend.calls)
        self.cli.execute("hello with spaces")
        self.assertIn(("send", "bob", "hello with spaces"), self.backend.calls)

    def test_quoted_file_paths_and_file_selection(self):
        self.cli.execute("chat bobby001")
        self.cli.execute('/upload "./a b.txt"')
        self.cli.execute("/files")
        self.cli.execute('/download 1 "my downloads"')
        self.assertIn(("upload", "bob", "./a b.txt"), self.backend.calls)
        self.assertIn(("download", "f" * 32, "my downloads"), self.backend.calls)

    def test_notifications_print_without_reading_stdin_or_sending_requests(self):
        before = list(self.backend.calls)
        self.cli.notify(Result("message", {"sender_id": "bob", "receiver_id": "me", "sender_name": "Bob",
                                           "content": "live message", "time": "now"}))
        self.assertIn("live message", self.output.getvalue())
        self.assertEqual(self.backend.calls, before)

    def test_scripted_login_commands_and_logout_use_only_cli(self):
        lines = iter(["login", "alice001", "chat 1", "hello world", "/back", "logout", "quit"])
        self.backend.user = {}
        self.cli.reader = lambda prompt: next(lines)
        self.cli.password_reader = lambda prompt: "test-password"
        self.cli.run()
        self.assertFalse(self.backend.connected)
        self.assertEqual(self.backend.user, {})
        self.assertIn("Đã đăng xuất", self.output.getvalue())
        self.assertNotIn("test-password", self.output.getvalue())

    def test_response_router_returns_values_without_ui_dependencies(self):
        message = str(AppError("[!] invalid"))
        result = dispatch(Response("ERROR.CLIENT", args=(message.encode(),)))
        self.assertFalse(self.cli.accept(result))
        self.assertEqual(self.output.getvalue(), "[!] invalid\n")
        self.assertEqual((result.kind, result.data, result.message), ("error", None, "[!] invalid"))
        with self.assertRaisesRegex(ProtocolError, r"^\[!\]"):
            dispatch(Response("ERROR.CLIENT", args=(b"BAD_ARGUMENT", b"invalid")))
        with self.assertRaises(ProtocolError):
            dispatch(Response("CHAT.PAGE", args=(b"GLOBAL", b"{}", b"")))
