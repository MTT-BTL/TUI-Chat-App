from hashlib import sha256
import json
import unittest

from support import Fixture
from file_transfer import CHUNK_SIZE, segment_count
from server.files import PAGE_SIZE, Download, FileService


def metadata(data, name="binary.bin"):
    return {"name": name, "size": len(data), "segments": segment_count(len(data)),
            "sha256": sha256(data).hexdigest()}


class FileTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.close)
        self.alice = self.fx.register("alice001")
        self.bob = self.fx.register("bobby001")
        self.eve = self.fx.register("third001")

    def start_upload(self, data, target=b"GLOBAL", **updates):
        meta = metadata(data)
        meta.update(updates)
        return self.fx.call(self.alice, "FILE.UPLOAD", target, json.dumps(meta).encode())

    def upload(self, data, target=b"GLOBAL", name="binary.bin"):
        result = self.start_upload(data, target, name=name)
        if data:
            self.assertEqual(result.action, "FILE.READY")
            file_id = result.args[0]
            self.assertEqual(int(result.args[1]), segment_count(len(data)))
            for index, offset in enumerate(range(0, len(data), CHUNK_SIZE)):
                result = self.fx.call(self.alice, "FILE.SEGMENT", file_id, str(index).encode(), data[offset:offset + CHUNK_SIZE])
                if index < segment_count(len(data)) - 1:
                    self.assertIsNone(result)
        self.assertEqual(result.action, "FILE.STORED")
        return json.loads(result.args[0])

    def test_binary_empty_and_exact_multiple_send_exact_declared_segments(self):
        binary = (bytes(range(256)) + b"\r\nGLP\r\n\x00\xff") * 1300
        self.assertGreater(len(binary), 262144)
        for data in (binary, b"", b"x" * CHUNK_SIZE, b"y" * (CHUNK_SIZE + 1)):
            meta = self.upload(data, name="tệp nhị phân.bin")
            download = self.fx.call(self.bob, "FILE.DOWNLOAD", meta["file_id"].encode())
            self.assertIsInstance(download, Download)
            frames = list(download.frames())
            self.assertEqual(len(frames), 1 + meta["segments"])
            self.assertEqual(frames[0].action, "FILE.META")
            self.assertEqual(json.loads(frames[0].args[0]), meta)
            payload = bytearray()
            for index, frame in enumerate(frames[1:]):
                self.assertEqual(frame.action, "FILE.DATA")
                self.assertEqual(frame.args[:2], (meta["file_id"].encode(), str(index).encode()))
                payload.extend(frame.args[2])
            self.assertEqual(bytes(payload), data)
        self.assertEqual(len(self.bob.events), 4)

    def test_private_permission_offline_recipient_and_existing_cache(self):
        self.fx.online.remove(self.bob)
        meta = self.upload(b"private contents", self.fx.ids[self.bob])
        self.assertEqual((self.bob.events, self.eve.events), ([], []))
        file_id = meta["file_id"].encode()
        self.assertEqual(self.fx.call(self.eve, "FILE.DOWNLOAD", file_id).action, "ERROR.FILE")
        self.assertEqual(json.loads(self.fx.call(self.eve, "FILE.LIST", self.fx.ids[self.alice], b"0").args[1]), [])
        self.fx.messages.files = FileService(self.fx.database, self.fx.folder / "files")
        download = self.fx.call(self.bob, "FILE.DOWNLOAD", file_id)
        self.assertEqual(list(download.frames())[1].args[2], b"private contents")
        rows = json.loads(self.fx.call(self.bob, "FILE.LIST", self.fx.ids[self.alice], b"0").args[1])
        self.assertEqual(rows[0]["file_id"], meta["file_id"])

    def test_wrong_count_order_size_and_hash_are_not_published(self):
        self.assertEqual(self.start_upload(b"abc", segments=2).action, "ERROR.FILE")
        for index, data in ((b"1", b"x" * CHUNK_SIZE), (b"0", b"x"),
                            (b"0", b"x" * (CHUNK_SIZE + 1)), (b"-1", b"x")):
            ready = self.start_upload(b"x" * (CHUNK_SIZE + 1))
            self.assertEqual(self.fx.call(self.alice, "FILE.SEGMENT", ready.args[0], index, data).action, "ERROR.FILE")
            self.assertEqual(list((self.fx.folder / "files").iterdir()), [])
        ready = self.start_upload(b"abc", sha256="0" * 64)
        self.assertEqual(self.fx.call(self.alice, "FILE.SEGMENT", ready.args[0], b"0", b"abc").action, "ERROR.FILE")
        self.assertEqual(list((self.fx.folder / "files").iterdir()), [])
        self.assertEqual(self.fx.database.execute("SELECT COUNT(*) FROM files").fetchone()[0], 0)

    def test_duplicate_and_extra_segment_do_not_replace_stored_files(self):
        ready = self.start_upload(b"x" * (CHUNK_SIZE + 1))
        self.fx.call(self.alice, "FILE.SEGMENT", ready.args[0], b"0", b"x" * CHUNK_SIZE)
        self.assertEqual(self.fx.call(self.alice, "FILE.SEGMENT", ready.args[0], b"0", b"x" * CHUNK_SIZE).action, "ERROR.FILE")
        meta = self.upload(b"abc")
        self.assertEqual(self.fx.call(self.alice, "FILE.SEGMENT", meta["file_id"].encode(), b"1", b"x").action, "ERROR.FILE")
        self.assertEqual((self.fx.folder / "files" / meta["file_id"]).read_bytes(), b"abc")

    def test_cancel_logout_disconnect_and_invalid_session_clean_partial_file(self):
        for stop in ("cancel", "logout", "disconnect", "invalid_session"):
            if self.fx.tokens[self.alice].startswith("_"):
                self.fx.call(self.alice, "AUTH.LOGIN", b"alice001", b"test-password")
            ready = self.start_upload(b"x" * (CHUNK_SIZE + 1))
            self.fx.call(self.alice, "FILE.SEGMENT", ready.args[0], b"0", b"x" * CHUNK_SIZE)
            self.assertTrue(list((self.fx.folder / "files").glob("*.part")))
            if stop == "cancel":
                self.fx.call(self.alice, "FILE.CANCEL", ready.args[0])
            elif stop == "logout":
                self.fx.call(self.alice, "AUTH.LOGOUT")
            elif stop == "invalid_session":
                self.fx.call(self.alice, "FILE.LIST", b"GLOBAL", b"0", token="f" * 32)
            else:
                self.fx.messages.files.disconnect(self.alice)
            self.assertEqual(list((self.fx.folder / "files").iterdir()), [])

    def test_bad_metadata_and_paths(self):
        updates = [{"name": "../outside"}, {"name": "a\\b.txt"}, {"name": "bad\nname"},
                   {"size": True}, {"segments": -1}, {"sha256": "bad"}, {"name": "x" * 256}]
        for update in updates:
            self.assertEqual(self.start_upload(b"abc", **update).action, "ERROR.FILE")
        for value in (b"not json", b"[]", b"null"):
            self.assertEqual(self.fx.call(self.alice, "FILE.UPLOAD", b"GLOBAL", value).action, "ERROR.FILE")
        self.assertEqual(list((self.fx.folder / "files").iterdir()), [])

    def test_list_pagination_and_restart_only_removes_partial_file(self):
        for index in range(PAGE_SIZE + 2):
            self.upload(b"", name=f"{index}.txt")
        first = self.fx.call(self.bob, "FILE.LIST", b"GLOBAL", b"0")
        second = self.fx.call(self.bob, "FILE.LIST", b"GLOBAL", first.args[2])
        self.assertEqual((len(json.loads(first.args[1])), len(json.loads(second.args[1])), second.args[2]), (10, 2, b""))
        stale = self.fx.folder / "files/stale.part"
        stale.write_bytes(b"partial")
        self.fx.messages.files = FileService(self.fx.database, self.fx.folder / "files")
        self.assertFalse(stale.exists())
        self.assertEqual(len(list((self.fx.folder / "files").iterdir())), PAGE_SIZE + 2)

    def test_missing_or_truncated_server_cache_is_error(self):
        meta = self.upload(b"abc")
        path = self.fx.folder / "files" / meta["file_id"]
        for remove in (False, True):
            if remove:
                path.unlink()
            else:
                path.write_bytes(b"a")
            frames = list(self.fx.call(self.bob, "FILE.DOWNLOAD", meta["file_id"].encode()).frames())
            self.assertEqual([frame.action for frame in frames], ["ERROR.FILE"])
