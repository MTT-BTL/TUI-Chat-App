"""Account giả, DB tạm: đổi tên không đổi identity/session, kiểm quyền và persistence."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from GLP import DEFAULT_SESSION, Request
from GLPConn import GLP_Connection
from server import db
from server.auth import AuthService, UserManager
from server.chat import MessageService
from server.router import dispatch


class Peer:
    def __init__(self): self.received = []
    def send(self, frame): self.received.append(frame)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "accounts.db"
        self.database = db.connect(self.path)
        db.init(self.database)
        self.auth = AuthService(self.database)
        self.online = UserManager()
        self.message = MessageService(self.database)
        self.peer = Peer()
        reply = self.call("AUTH.REGISTER", b"student01", b"test-password", token=DEFAULT_SESSION)
        self.token, self.uid = reply.session_update, reply.args[0].decode()

    def tearDown(self):
        self.database.close()
        self.temp.cleanup()

    def call(self, route, *args, token=None, peer=None):
        request = Request(route, self.token if token is None else token, args)
        wire = request.to_bytes()
        # Qua parser thật để bắt lỗi allowlist/UTF-8 byte length của API mới.
        parser = GLP_Connection.__new__(GLP_Connection)
        parser.buffer = bytearray(wire)
        return dispatch(parser._parse(), self.auth, peer or self.peer,
                        "127.0.0.1", self.online, self.message)

    def test_rename_utf8_preserves_identity_and_survives_reopen(self):
        name = "Nguyễn 👾 [green]"
        response = self.call("DASH.RENAME", name.encode())
        self.assertEqual(response.action, "OK.PROFILE")
        self.assertEqual(response.session_update, "-")
        self.assertEqual(response.args, (self.uid.encode(), b"student01", name.encode()))
        self.assertEqual(self.auth.get_session(self.token, self.peer).user_id, self.uid)
        self.database.close()
        self.database = db.connect(self.path)
        self.auth.db = self.database
        self.database.row_factory = db.sqlite3.Row
        self.assertEqual(self.call("DASH.SELF").args[2], name.encode())
        rows = json.loads(self.call("SYS.USERS").args[0])
        self.assertEqual(rows[0]["dispname"], name)

    def test_invalid_names_do_not_change_profile(self):
        for name in (b"", b"   ", b"x" * 33, b"Neo\nGhost", b"\x1b[31mNeo", b"\xff"):
            with self.subTest(name=name):
                self.assertEqual(self.call("DASH.RENAME", name).action, "ERROR.CLIENT")
                self.assertEqual(self.call("DASH.SELF").args[2], b"student01")
        self.assertEqual(self.call("DASH.RENAME").action, "ERROR.CLIENT")
        self.assertEqual(self.call("DASH.RENAME", b"a", b"b").action, "ERROR.CLIENT")

    def test_default_or_stolen_session_cannot_rename(self):
        self.assertEqual(self.call("DASH.RENAME", b"Ghost", token=DEFAULT_SESSION, peer=Peer()).action, "DENY.AUTH")
        self.assertEqual(self.call("DASH.RENAME", b"Ghost", peer=Peer()).action, "DENY.AUTH")
        self.assertEqual(self.call("DASH.SELF").args[2], b"student01")

    def test_future_messages_use_new_name(self):
        recipient = Peer()
        self.online.add(recipient, "b" * 32, "127.0.0.1")
        self.call("DASH.RENAME", b"  Ghost Operator  ")
        self.assertEqual(self.call("MSG.GLOBAL", "Xin chào".encode()).action, "EVT.SENT")
        event = recipient.received[0]
        self.assertEqual(event.action, "EVT.RECV")
        self.assertEqual(event.args[2:], (b"Ghost Operator", "Xin chào".encode()))


if __name__ == "__main__": unittest.main()
