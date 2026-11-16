"""TCP/TLS thật trên loopback: backend/stream/chat/heartbeat và lỗi kết nối."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import queue
import socket
import ssl
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from support import LiveServer, GLP_Connection, Request, Response, db
from GLP import DEFAULT_SESSION
from file_transfer import CHUNK_SIZE
from server import server
from server.files import Download, reply


class NetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = TemporaryDirectory(prefix="glp-test-tls-")
        cls.addClassCleanup(folder.cleanup)
        pem = Path(folder.name) / "server.pem"
        tls = patch.multiple(GLP_Connection, _server_pem=pem, _server_tls=None)
        tls.start()
        cls.addClassCleanup(tls.stop)

    def live(self, handler=None):
        live = LiveServer(handler)
        self.addCleanup(live.close)
        return live

    def test_counted_download_chat_pushes_and_heartbeat_share_one_connection(self):
        with patch.object(server, "HEARTBEAT_TIMEOUT", 0.5):
            live = self.live()
            alice, _ = live.backend("sender01", interval=0.12)
            bob, bob_events = live.backend("receiver", interval=0.12)
            eve, eve_events = live.backend("third001", interval=0.12)
            source = live.folder / "tệp nhị phân.bin"
            payload = (bytes(range(256)) + b"\r\nGLP\r\n\x00\xff") * 2100
            source.write_bytes(payload)
            stored = alice.upload(bob.user["user_id"], source)
            self.assertTrue(stored.ok, stored)
            file_id = stored.data["file_id"]
            self.assertFalse(eve.download(file_id, live.folder / "eve").ok)
            endpoint = bob._conn.sock.getsockname()
            old_frames = Download.frames
            failures = []

            def slow_frames(download):
                for frame in old_frames(download):
                    time.sleep(0.04)
                    yield frame

            def send_messages():
                try:
                    for index in range(12):
                        result = alice.send_chat(bob.user["user_id"], f"message-{index}:" + "x" * 3000)
                        self.assertTrue(result.ok)
                        time.sleep(0.02)
                except Exception as error:
                    failures.append(error)

            thread = threading.Thread(target=send_messages)
            with patch.object(live.messages.files, "dispatch", wraps=live.messages.files.dispatch) as routes:
                with patch.object(Download, "frames", slow_frames):
                    thread.start()
                    try:
                        downloaded = bob.download(file_id, live.folder / "received")
                    finally:
                        thread.join(3)
                self.assertEqual([call.args[0].route for call in routes.call_args_list], ["FILE.DOWNLOAD"])
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            self.assertTrue(downloaded.ok, downloaded)
            self.assertEqual((live.folder / "received" / source.name).read_bytes(), payload)
            self.assertTrue(bob.list_chats().ok)
            self.assertEqual(len([event for event in list(bob_events.queue) if event.kind == "message"]), 12)
            self.assertFalse(any(event.kind in ("message", "file_notice") for event in list(eve_events.queue)))
            self.assertEqual(bob._conn.sock.getsockname(), endpoint)
            self.assertEqual(bob._conn.sock.version(), "TLSv1.3")

    def test_upload_replies_only_ready_and_stored(self):
        live = self.live()
        conn = GLP_Connection.connect("localhost", live.port, timeout=1)
        self.addCleanup(conn.close)
        conn.send(Request("AUTH.REGISTER", DEFAULT_SESSION, (b"sender01", b"test-password")))
        token = conn.recv().session_update
        parts = (b"x" * CHUNK_SIZE, b"y" * CHUNK_SIZE, b"end")
        payload = b"".join(parts)
        meta = {"name": "upload.bin", "size": len(payload), "segments": 3,
                "sha256": sha256(payload).hexdigest()}
        conn.send(Request("FILE.UPLOAD", token, (b"GLOBAL", json.dumps(meta).encode())))
        ready = conn.recv()
        self.assertEqual(ready.action, "FILE.READY")
        file_id, count = ready.args
        self.assertEqual(count, b"3")

        conn.send(Request("FILE.SEGMENT", token, (file_id, b"0", parts[0])))
        conn.send(Request("SYS.PING", token))
        self.assertEqual(conn.recv().action, "OK.PONG")  # Bất kỳ ACK nào sẽ đến trước PONG.
        conn.sock.sendall(b"".join(
            Request("FILE.SEGMENT", token, (file_id, str(i).encode(), piece)).to_bytes()
            for i, piece in enumerate(parts[1:], 1)
        ))
        stored = conn.recv()
        self.assertEqual(stored.action, "FILE.STORED")
        self.assertEqual(json.loads(stored.args[0])["file_id"], file_id.decode())
        self.assertEqual((live.folder / "files" / file_id.decode()).read_bytes(), payload)
        self.assertFalse(list((live.folder / "files").glob("*.part")))

    def test_invalid_segment_aborts_only_that_connection_and_cleans_upload(self):
        live = self.live()
        peer, _ = live.backend("receiver")
        conn = GLP_Connection.connect("localhost", live.port, timeout=1)
        self.addCleanup(conn.close)
        conn.send(Request("AUTH.REGISTER", DEFAULT_SESSION, (b"sender01", b"test-password")))
        token = conn.recv().session_update
        meta = {"name": "bad.bin", "size": CHUNK_SIZE + 3, "segments": 2,
                "sha256": "0" * 64}
        conn.send(Request("FILE.UPLOAD", token, (b"GLOBAL", json.dumps(meta).encode())))
        ready = conn.recv()
        self.assertEqual(ready.action, "FILE.READY")
        file_id = ready.args[0]
        conn.send(Request("FILE.SEGMENT", token, (file_id, b"0", b"x" * CHUNK_SIZE)))
        conn.send(Request("FILE.SEGMENT", token, (file_id, b"1", b"xx")))
        self.assertEqual(conn.recv().action, "ERROR.FILE")
        with self.assertRaises((OSError, EOFError)) as closed:
            conn.recv()
        self.assertNotIsInstance(closed.exception, TimeoutError)
        self.assertEqual(list((live.folder / "files").iterdir()), [])
        self.assertEqual(peer.list_files("GLOBAL").data["items"], [])

    def test_empty_exact_and_multi_segment_files_and_name_collision(self):
        live = self.live()
        alice, _ = live.backend("sender01")
        bob, _ = live.backend("receiver")
        folder = live.folder / "downloads"
        for index, payload in enumerate((b"", b"x" * CHUNK_SIZE, b"x" * (CHUNK_SIZE + 1))):
            source = live.folder / f"test-{index}.bin"
            source.write_bytes(payload)
            stored = alice.upload("GLOBAL", source)
            self.assertTrue(stored.ok, stored)
            first = bob.download(stored.data["file_id"], folder)
            second = bob.download(stored.data["file_id"], folder)
            self.assertNotEqual(first.data["path"], second.data["path"])
            self.assertEqual((folder / source.name).read_bytes(), payload)
            self.assertEqual(sha256((folder / source.name).read_bytes()).hexdigest(), stored.data["sha256"])
        self.assertFalse(list(folder.glob("*.part")))

    def test_bad_hash_discards_download_and_keeps_connection_usable(self):
        live = self.live()
        client, _ = live.backend("student1")
        source = live.folder / "source.bin"
        source.write_bytes(b"abc")
        stored = client.upload("GLOBAL", source)
        (live.folder / "files" / stored.data["file_id"]).write_bytes(b"xyz")
        folder = live.folder / "received"
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            client.download(stored.data["file_id"], folder)
        self.assertEqual(list(folder.iterdir()), [])
        self.assertTrue(client.list_chats().ok)

    def test_mid_download_disconnect_cleans_partial_output(self):
        live = self.live()
        client, _ = live.backend("student1")
        source = live.folder / "source.bin"
        source.write_bytes(b"x" * (CHUNK_SIZE * 2 + 1))
        stored = client.upload("GLOBAL", source)
        original = Download.frames

        def interrupted(download):
            frames = iter(original(download))
            yield next(frames)
            yield next(frames)
            raise OSError("simulated disconnect")

        folder = live.folder / "received"
        with patch.object(Download, "frames", interrupted):
            with self.assertRaises(ConnectionError):
                client.download(stored.data["file_id"], folder)
        self.assertEqual(list(folder.iterdir()), [])

    def test_missing_pong_closes_client_even_with_live_notifications(self):
        def ignore_pings(conn):
            while True:
                request = conn.recv()
                row = {"sender_id": "peer", "receiver_id": None, "sender_name": "Peer", "content": "live", "time": "now"}
                conn.send(Response("CHAT.MESSAGE", args=(json.dumps(row).encode(),)))

        live = self.live(ignore_pings)
        client, events = live.backend(interval=0.03)
        closed = None
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            event = events.get(timeout=1)
            if event.kind == "closed":
                closed = event
                break
        self.assertIsNotNone(closed)
        self.assertIn("OK.PONG", closed.message)
        self.assertFalse(client.connected)

    def test_heartbeat_before_login_after_logout_and_session_is_preserved(self):
        with patch.object(server, "HEARTBEAT_TIMEOUT", 0.2):
            live = self.live()
            client, _ = live.backend(interval=0.03)
            time.sleep(0.3)
            self.assertTrue(client.connected)
            self.assertTrue(client.login("student1", "test-password", register=True).ok)
            token = client._session
            time.sleep(0.2)
            self.assertEqual(client._session, token)
            self.assertTrue(client.list_chats().ok)
            self.assertTrue(client.logout().ok)
            time.sleep(0.3)
            self.assertTrue(client.connected)
            self.assertEqual(client._session, DEFAULT_SESSION)
            self.assertEqual(live.online.user_ids(), [])

    def test_request_timeout_closes_connection_instead_of_reusing_late_reply(self):
        def ignore_requests(conn):
            while True:
                request = conn.recv()
                if request.route == "SYS.PING":
                    conn.send(Response("OK.PONG"))

        live = self.live(ignore_requests)
        client, _ = live.backend()
        client.REQUEST_TIMEOUT = 0.15
        with self.assertRaises(TimeoutError):
            client.list_chats()
        self.assertFalse(client.connected)
        with self.assertRaises(ConnectionError):
            client.list_chats()

    def test_tls_and_login_track_the_peer_endpoint(self):
        live = self.live()
        client, _ = live.backend("student1")
        self.assertIsInstance(client._conn.sock, ssl.SSLSocket)
        self.assertEqual(client._conn.sock.version(), "TLSv1.3")
        session = live.auth.session_manager[client._session]
        self.assertEqual((session.ip_addr, session.peer_port), client._conn.sock.getsockname())
        self.assertTrue(client.list_chats().ok)

    def test_twenty_clients_list_each_other_and_receive_global_chat(self):
        live = self.live()
        with ThreadPoolExecutor(max_workers=20) as pool:
            peers = list(pool.map(live.backend, (f"student{i:02d}" for i in range(20))))
        users = {client.user["user_id"] for client, _ in peers}
        self.assertEqual(len(users), 20)
        for client, _ in peers:
            result = client.list_chats()
            self.assertTrue(result.ok)
            online = {item["target"] for item in result.data if item["online"]}
            self.assertEqual(online - {"GLOBAL", client.user["user_id"]}, users - {client.user["user_id"]})
        sender = peers[0][0]
        self.assertTrue(sender.send_chat("GLOBAL", "Tin nhắn thử tải").ok)
        for _, events in peers[1:]:
            event = events.get(timeout=2)
            self.assertEqual(event.kind, "message")
            self.assertEqual(event.data["content"], "Tin nhắn thử tải")

    def test_server_missing_ping_expires_session_and_partial_upload(self):
        with patch.object(server, "HEARTBEAT_TIMEOUT", 0.2):
            live = self.live()
            db.register(live.database, "student1", "test-password")
            for traffic in ("other_request", "invalid_ping", "partial_frame"):
                with self.subTest(traffic=traffic):
                    conn = GLP_Connection.connect("localhost", live.port, timeout=1)
                    self.addCleanup(conn.close)
                    conn.send(Request("AUTH.LOGIN", DEFAULT_SESSION, (b"student1", b"test-password")))
                    token = conn.recv().session_update
                    meta = {"name": "partial.bin", "size": CHUNK_SIZE + 1, "segments": 2, "sha256": "0" * 64}
                    conn.send(Request("FILE.UPLOAD", token, (b"GLOBAL", json.dumps(meta).encode())))
                    self.assertEqual(conn.recv().action, "FILE.READY")
                    if traffic == "partial_frame":
                        # Gửi nhỏ giọt một PING cũng không được reset deadline.
                        try:
                            for byte in Request("SYS.PING", token).to_bytes():
                                conn.sock.sendall(bytes([byte]))
                                time.sleep(0.02)
                        except OSError:
                            pass
                        with self.assertRaises((OSError, EOFError)):
                            conn.recv()
                    else:
                        route, args = ("CHAT.LIST", ()) if traffic == "other_request" else ("SYS.PING", (b"extra",))
                        while True:
                            try:
                                conn.send(Request(route, token, args))
                                conn.recv()
                                time.sleep(0.02)
                            except (EOFError, OSError):
                                break
                    worker = live.workers[-1]
                    worker.join(timeout=1)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(live.messages.files.uploads, {})
                    self.assertEqual(live.auth.session_manager, {})
                    self.assertEqual(live.online.user_ids(), [])
                    self.assertFalse(list((live.folder / "files").glob("*.part")))

    def test_file_session_error_clears_profile_and_allows_login_again(self):
        live = self.live()
        client, _ = live.backend("student1")
        source = live.folder / "empty.bin"
        source.write_bytes(b"")
        with server.app_lock:
            live.auth.session_manager.clear()
        result = client.upload("GLOBAL", source)
        self.assertFalse(result.ok)
        self.assertIsNone(result.data)
        self.assertEqual(result.message, "[!] Session không tồn tại, hết hạn hoặc sai connection")
        self.assertEqual(client.user, {})
        self.assertEqual(client._session, DEFAULT_SESSION)
        self.assertTrue(client.login("student1", "test-password").ok)
