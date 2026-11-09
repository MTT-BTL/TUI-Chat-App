"""FILE qua GLP thật + SQLite/disk tạm, không dùng DB/file hiện có của app."""
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from GLP import DEFAULT_SESSION, Request, Response
from GLPConn import GLP_Connection
from file_protocol import CHUNK_SIZE, PAGE_SIZE
from server import db
from server.auth import AuthService, UserManager
from server.chat import MessageService
from server.files import FileService
from server.router import dispatch


class Peer:
    def __init__(self):
        self.events = []

    def send(self, response):
        self.events.append(response)


def parse(wire):
    parser = GLP_Connection.__new__(GLP_Connection)
    parser.buffer = bytearray(wire)
    return parser._parse()


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="glp-file-test-")
        self.folder = Path(self.temp.name)
        self.database = db.connect(self.folder / "test.db")
        db.init(self.database)
        self.auth, self.online = AuthService(self.database), UserManager()
        self.messages = MessageService(self.database)
        self.messages.files = FileService(self.database, self.folder / "files")
        self.tokens, self.ids = {}, {}
        self.alice, self.bob, self.eve = Peer(), Peer(), Peer()
        for peer, name in ((self.alice, b"alice001"), (self.bob, b"bobby001"), (self.eve, b"student3")):
            self.tokens[peer] = DEFAULT_SESSION
            response = self.call(peer, "AUTH.REGISTER", name, b"test-password")
            self.ids[peer] = response.args[0]

    def tearDown(self):
        for peer in list(self.messages.files.uploads):
            self.messages.files.disconnect(peer)
        self.database.close()
        self.temp.cleanup()

    def call(self, peer, route, *args, token=None):
        request = parse(Request(route, token or self.tokens[peer], args).to_bytes())
        self.assertIsInstance(request, Request)
        response = dispatch(request, self.auth, peer, "127.0.0.1", self.online, self.messages)
        response = parse(response.to_bytes())
        self.assertIsInstance(response, Response)
        if response.session_update != "-":
            self.tokens[peer] = response.session_update
        return response

    def upload(self, target, data, name=b"binary.bin"):
        response = self.call(self.alice, "FILE.UPLOAD", target, name, str(len(data)).encode())
        self.assertEqual(response.action, "FILE.READY")
        file_id = response.args[0]
        for offset in range(0, len(data), CHUNK_SIZE):
            chunk = data[offset:offset + CHUNK_SIZE]
            response = self.call(self.alice, "FILE.CHUNK", file_id, str(offset).encode(), chunk)
            self.assertEqual(response.action, "FILE.ACK")
            self.assertEqual(response.args, (file_id, str(offset + len(chunk)).encode()))
        response = self.call(self.alice, "FILE.COMPLETE", file_id, sha256(data).hexdigest().encode())
        self.assertEqual(response.action, "FILE.STORED")
        return json.loads(response.args[0])

    def test_binary_larger_than_frame_and_empty_file_round_trip(self):
        data = (bytes(range(256)) + b"\r\nGLP\r\n\x00\xff") * 1300
        self.assertGreater(len(data), 262144)
        for name, payload in (("tài liệu.bin".encode(), data), (b"empty.txt", b"")):
            metadata = self.upload(b"GLOBAL", payload, name)
            received = bytearray()
            while True:
                response = self.call(self.bob, "FILE.GET", metadata["file_id"].encode(), str(len(received)).encode())
                self.assertEqual(response.action, "FILE.DATA")
                self.assertLessEqual(len(response.args[2]), CHUNK_SIZE)
                received.extend(response.args[2])
                if len(received) == len(payload):
                    break
            self.assertEqual(bytes(received), payload)
            self.assertEqual(sha256(received).hexdigest(), metadata["sha256"])
            self.assertEqual((self.folder / "files" / metadata["file_id"]).read_bytes(), payload)
        self.assertEqual(len(json.loads(self.call(self.bob, "FILE.LIST", b"GLOBAL", b"0").args[1])), 2)
        self.assertEqual([event.action for event in self.bob.events], ["FILE.NOTICE"] * 2)

    def test_private_permissions_offline_inbox_and_persistence(self):
        self.online.remove(self.bob)
        metadata = self.upload(self.ids[self.bob], b"private contents")
        self.assertEqual(self.bob.events, [])
        self.assertEqual(self.eve.events, [])
        file_id = metadata["file_id"].encode()
        self.assertEqual(self.call(self.eve, "FILE.GET", file_id, b"0").action, "FILE.ERROR")
        self.assertEqual(json.loads(self.call(self.eve, "FILE.LIST", self.ids[self.alice], b"0").args[1]), [])
        self.assertEqual(json.loads(self.call(self.bob, "FILE.LIST", b"GLOBAL", b"0").args[1]), [])
        self.database.close()
        self.database = db.connect(self.folder / "test.db")
        db.init(self.database)
        self.auth.db = self.database
        self.messages.db = self.database
        self.messages.files = FileService(self.database, self.folder / "files")
        self.assertEqual(self.call(self.bob, "FILE.GET", file_id, b"0").args[2], b"private contents")
        rows = json.loads(self.call(self.bob, "FILE.LIST", self.ids[self.alice], b"0").args[1])
        self.assertEqual(rows[0]["file_id"], metadata["file_id"])
        peers = json.loads(self.call(self.bob, "FILE.CHATS").args[0])
        self.assertEqual(peers, [{"user_id": self.ids[self.alice].decode(), "dispname": "alice001"}])
        self.assertEqual(self.database.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_incomplete_wrong_offset_oversized_and_bad_hash_not_published(self):
        cases = (("FILE.COMPLETE", b"0" * 64), ("FILE.CHUNK", b"1", b"x"),
                 ("FILE.CHUNK", b"0", b"x" * (CHUNK_SIZE + 1)))
        for route, *args in cases:
            with self.subTest(route=route, argument_size=len(args[-1])):
                ready = self.call(self.alice, "FILE.UPLOAD", b"GLOBAL", b"bad.bin", b"10")
                response = self.call(self.alice, route, ready.args[0], *args)
                self.assertEqual(response.action, "FILE.ERROR")
                self.assertEqual(list((self.folder / "files").iterdir()), [])
        ready = self.call(self.alice, "FILE.UPLOAD", b"GLOBAL", b"hash.bin", b"3")
        self.call(self.alice, "FILE.CHUNK", ready.args[0], b"0", b"abc")
        self.assertEqual(self.call(self.alice, "FILE.COMPLETE", ready.args[0], b"0" * 64).action, "FILE.ERROR")
        self.assertEqual(json.loads(self.call(self.bob, "FILE.LIST", b"GLOBAL", b"0").args[1]), [])
        self.assertEqual(list((self.folder / "files").iterdir()), [])

    def test_auth_paths_cancel_and_disconnect_cleanup(self):
        for name in (b"../outside.txt", b"a\\b.txt", b".", b"bad\nname", b"x" * 256):
            self.assertEqual(self.call(self.alice, "FILE.UPLOAD", b"GLOBAL", name, b"1").action, "FILE.ERROR")
        self.assertEqual(self.call(self.eve, "FILE.LIST", b"GLOBAL", b"0",
                                   token=self.tokens[self.alice]).action, "DENY.AUTH")
        for stop in ("FILE.CANCEL", "AUTH.LOGOUT", "disconnect"):
            if self.tokens[self.alice] == DEFAULT_SESSION:
                self.call(self.alice, "AUTH.LOGIN", b"alice001", b"test-password")
            ready = self.call(self.alice, "FILE.UPLOAD", b"GLOBAL", b"partial.bin", b"100")
            self.assertEqual(self.call(self.alice, "FILE.CHUNK", ready.args[0], b"0", b"abc").action, "FILE.ACK")
            self.assertTrue(list((self.folder / "files").glob("*.part")))
            if stop == "disconnect":
                self.messages.files.disconnect(self.alice)
            else:
                args = (ready.args[0],) if stop == "FILE.CANCEL" else ()
                self.call(self.alice, stop, *args)
            self.assertEqual(list((self.folder / "files").iterdir()), [])

    def test_list_pagination_and_restart_removes_only_incomplete_file(self):
        for index in range(PAGE_SIZE + 2):
            self.upload(b"GLOBAL", b"", f"file-{index:03}.txt".encode())
        first = self.call(self.bob, "FILE.LIST", b"GLOBAL", b"0")
        rows1 = json.loads(first.args[1])
        second = self.call(self.bob, "FILE.LIST", b"GLOBAL", first.args[2])
        rows2 = json.loads(second.args[1])
        self.assertEqual((len(rows1), len(rows2), second.args[2]), (PAGE_SIZE, 2, b""))
        self.assertEqual(len({row["file_id"] for row in rows1 + rows2}), PAGE_SIZE + 2)
        stale = self.folder / "files" / "stale.part"
        stale.write_bytes(b"interrupted")
        self.messages.files = FileService(self.database, self.folder / "files")
        self.assertFalse(stale.exists())
        self.assertEqual(len(list((self.folder / "files").iterdir())), PAGE_SIZE + 2)

    def test_binary_frames_survive_tcp_split_and_coalesced_boundaries(self):
        data = (b"GLP\r\n\r\n\x00\xff" * 5000)[:CHUNK_SIZE]
        frames = [Request("FILE.CHUNK", "a" * 32, (b"id", b"0", data)),
                  Response("FILE.DATA", args=(b"id", b"0", data))]
        wire = b"".join(frame.to_bytes() for frame in frames)
        for step in (1, 4096, len(wire)):
            parser = GLP_Connection.__new__(GLP_Connection)
            parser.buffer = bytearray()
            received = []
            for offset in range(0, len(wire), step):
                parser.buffer.extend(wire[offset:offset + step])
                while (frame := parser._parse()) is not None:
                    received.append(frame)
            self.assertEqual(received, frames)
            self.assertEqual(parser.buffer, b"")


if __name__ == "__main__":
    unittest.main()
