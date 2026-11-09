"""FILE + chat cùng TCP/TLS thật; ba client loopback, chỉ dùng storage tạm."""
from hashlib import sha256
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from GLP import DEFAULT_SESSION, Request
from GLPConn import GLP_Connection
from file_protocol import CHUNK_SIZE
from server import db
from server.auth import AuthService, UserManager
from server.chat import MessageService
from server.files import FileService
from server.server import handle_client


class FileTLSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="glp-file-tls-")
        self.folder = Path(self.temp.name)
        self.clients, self.workers, self.tokens, self.events = [], [], {}, {}
        self.database = db.connect(self.folder / "test.db")
        db.init(self.database)
        self.listener = socket.socket()
        self.acceptor = None
        self.addCleanup(self.clean)
        cert, key = self.folder / "cert.pem", self.folder / "key.pem"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
            "-out", str(cert), "-keyout", str(key),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_3
        self.context.load_cert_chain(cert, key)
        self.client_context = ssl.create_default_context(cafile=str(cert))
        self.client_context.minimum_version = ssl.TLSVersion.TLSv1_3
        self.auth, self.online, self.messages = AuthService(self.database), UserManager(), MessageService(self.database)
        self.messages.files = FileService(self.database, self.folder / "files")
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(3)
        self.listener.settimeout(3)
        self.acceptor = threading.Thread(target=self.accept_three)
        self.acceptor.start()

    def accept_three(self):
        for _ in range(3):
            raw, addr = self.listener.accept()
            conn = GLP_Connection.accept(raw, self.context, timeout=5)
            worker = threading.Thread(target=handle_client, args=(conn, addr, self.auth, self.online, self.messages))
            self.workers.append(worker)
            worker.start()

    def connect(self, username):
        raw = socket.create_connection(self.listener.getsockname(), timeout=5)
        conn = GLP_Connection(self.client_context.wrap_socket(raw, server_hostname="localhost"), timeout=5)
        self.clients.append(conn)
        self.tokens[conn], self.events[conn] = DEFAULT_SESSION, []
        self.assertEqual(self.call(conn, "SYS.PING").action, "OK.PONG")
        response = self.call(conn, "AUTH.REGISTER", username, b"test-password")
        self.assertEqual(response.action, "REDIRECT.HOME")
        return conn, response.args[0]

    def call(self, conn, route, *args):
        conn.send(Request(route, self.tokens[conn], args))
        while True:
            response = conn.recv()
            if response.action in ("FILE.NOTICE", "EVT.RECV"):
                self.events[conn].append(response)
                continue
            if response.session_update != "-":
                self.tokens[conn] = response.session_update
            return response

    def clean(self):
        for conn in self.clients:
            try:
                conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        if self.acceptor is not None:
            self.acceptor.join(4)
        for worker in self.workers:
            worker.join(4)
        self.listener.close()
        self.database.close()
        self.temp.cleanup()
        self.assertTrue(all(not worker.is_alive() for worker in self.workers))

    def test_private_binary_transfer_with_concurrent_chat_and_mid_frame_eof(self):
        alice, alice_id = self.connect(b"sender01")
        bob, bob_id = self.connect(b"receiver")
        eve, _ = self.connect(b"third001")
        endpoints = (alice.sock.getsockname(), bob.sock.getsockname())
        data = (bytes(range(256)) + b"\r\nGLP\r\n\x00\xff") * 1600
        ready = self.call(alice, "FILE.UPLOAD", bob_id, "tệp nhị phân.bin".encode(), str(len(data)).encode())
        self.assertEqual(ready.action, "FILE.READY")
        file_id = ready.args[0]
        for offset in range(0, len(data), CHUNK_SIZE):
            self.assertEqual(self.call(alice, "FILE.CHUNK", file_id, str(offset).encode(),
                                       data[offset:offset + CHUNK_SIZE]).action, "FILE.ACK")
            if offset == 0:
                self.assertEqual(self.call(bob, "MSG.PRIVATE", alice_id, b"chat during upload").action, "EVT.SENT")
        stored = self.call(alice, "FILE.COMPLETE", file_id, sha256(data).hexdigest().encode())
        self.assertEqual(stored.action, "FILE.STORED")
        metadata = json.loads(stored.args[0])
        self.assertEqual(self.call(eve, "FILE.GET", file_id, b"0").action, "FILE.ERROR")
        self.assertEqual(self.call(eve, "SYS.PING").action, "OK.PONG")
        self.assertTrue(any(event.action == "EVT.RECV" for event in self.events[alice]))

        # Response chứa 32 KiB và tin đẩy từ thread khác cùng ghi socket Bob.
        # Nếu không serialize frame, parser/hash hoặc số tin sẽ thất bại.
        failures = []
        barrier = threading.Barrier(2)

        def chat_while_downloading():
            try:
                barrier.wait(3)
                for index in range(12):
                    content = f"message-{index}:".encode() + b"x" * 45000
                    self.assertEqual(self.call(alice, "MSG.PRIVATE", bob_id, content).action, "EVT.SENT")
            except Exception as error:
                failures.append(error)

        writer = threading.Thread(target=chat_while_downloading)
        writer.start()
        barrier.wait(3)
        downloaded = bytearray()
        while len(downloaded) < len(data):
            response = self.call(bob, "FILE.GET", file_id, str(len(downloaded)).encode())
            self.assertEqual(response.action, "FILE.DATA")
            self.assertEqual(int(response.args[1]), len(downloaded))
            downloaded.extend(response.args[2])
        writer.join(5)
        self.assertFalse(writer.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(self.call(bob, "SYS.PING").action, "OK.PONG")
        self.assertEqual(len([event for event in self.events[bob] if event.action == "EVT.RECV"]), 12)
        self.assertEqual(len([event for event in self.events[bob] if event.action == "FILE.NOTICE"]), 1)
        self.assertEqual(bytes(downloaded), data)
        self.assertEqual(sha256(downloaded).hexdigest(), metadata["sha256"])
        self.assertEqual((alice.sock.getsockname(), bob.sock.getsockname()), endpoints)
        self.assertEqual(bob.sock.version(), "TLSv1.3")

        partial = self.call(alice, "FILE.UPLOAD", b"GLOBAL", b"interrupted.bin", b"100")
        self.assertEqual(self.call(alice, "FILE.CHUNK", partial.args[0], b"0", b"abc").action, "FILE.ACK")
        wire = Request("FILE.CHUNK", self.tokens[alice], (partial.args[0], b"3", b"incomplete")).to_bytes()
        alice.sock.sendall(wire[:-5])
        alice.sock.shutdown(socket.SHUT_RDWR)
        alice.close()
        self.workers[0].join(3)
        self.assertFalse(self.workers[0].is_alive())
        self.assertEqual(list((self.folder / "files").glob("*.part")), [])
        self.assertEqual(self.call(bob, "SYS.PING").action, "OK.PONG")
        self.assertEqual(json.loads(self.call(bob, "FILE.LIST", b"GLOBAL", b"0").args[1]), [])


if __name__ == "__main__":
    unittest.main()
