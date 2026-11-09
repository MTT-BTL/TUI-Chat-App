"""Hai client TCP/TLS loopback, handler thật, account/cert/DB đều tạm."""
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
from server import db
from server.auth import AuthService, UserManager
from server.chat import MessageService
from server.server import handle_client


class TLSProfileTests(unittest.TestCase):
    def test_rename_then_chat_on_same_connections(self):
        with tempfile.TemporaryDirectory(prefix="glp-profile-tls-") as folder:
            cert, key = Path(folder) / "server.pem", Path(folder) / "server.key"
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                "-out", str(cert), "-keyout", str(key),
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            context.load_cert_chain(cert, key)
            client_context = ssl.create_default_context(cafile=str(cert))
            client_context.minimum_version = ssl.TLSVersion.TLSv1_3
            database = db.connect(Path(folder) / "accounts.db")
            db.init(database)
            auth, online, messages = AuthService(database), UserManager(), MessageService(database)
            clients, workers, tokens = [], [], {}
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(2)
            listener.settimeout(2)

            def accept_two():
                for _ in range(2):
                    raw, addr = listener.accept()
                    raw.settimeout(2)
                    conn = GLP_Connection.accept(raw, context, timeout=2)
                    worker = threading.Thread(target=handle_client, args=(conn, addr, auth, online, messages))
                    workers.append(worker)
                    worker.start()

            acceptor = threading.Thread(target=accept_two)
            acceptor.start()

            def connect():
                raw = socket.create_connection(listener.getsockname(), timeout=2)
                conn = GLP_Connection(client_context.wrap_socket(raw, server_hostname="localhost"), timeout=2)
                clients.append(conn)
                tokens[conn] = DEFAULT_SESSION
                conn.send(Request("SYS.PING", DEFAULT_SESSION))
                self.assertEqual(conn.recv().action, "OK.PONG")
                return conn

            def call(conn, route, *args):
                conn.send(Request(route, tokens[conn], args))
                response = conn.recv()
                if response.session_update != "-": tokens[conn] = response.session_update
                return response

            try:
                alice, bob = connect(), connect()
                alice_id = call(alice, "AUTH.REGISTER", b"alice001", b"test-password").args[0]
                bob_id = call(bob, "AUTH.REGISTER", b"bobby001", b"test-password").args[0]
                endpoint = alice.sock.getsockname()
                name = "Ghost Nguyễn".encode()
                self.assertEqual(call(alice, "DASH.RENAME", name).action, "OK.PROFILE")
                self.assertEqual(call(alice, "DASH.SELF").args[2], name)
                users = json.loads(call(bob, "SYS.USERS").args[0])
                self.assertEqual(next(u for u in users if u["user_id"] == alice_id.decode())["dispname"], name.decode())
                self.assertEqual(call(alice, "MSG.GLOBAL", b"global test").action, "EVT.SENT")
                self.assertEqual(bob.recv().args, (b"GLOBAL", alice_id, name, b"global test"))
                self.assertEqual(call(alice, "MSG.PRIVATE", bob_id, b"private test").action, "EVT.SENT")
                self.assertEqual(bob.recv().args, (b"PRIVATE", alice_id, name, b"private test"))
                self.assertEqual(alice.sock.getsockname(), endpoint)
                self.assertEqual(alice.sock.version(), "TLSv1.3")
            finally:
                for conn in clients:
                    try: conn.sock.shutdown(socket.SHUT_RDWR)
                    except OSError: pass
                    conn.close()
                acceptor.join(3)
                for worker in workers: worker.join(3)
                listener.close()
                database.close()
                self.assertFalse(acceptor.is_alive())
                self.assertTrue(all(not worker.is_alive() for worker in workers))


if __name__ == "__main__": unittest.main()
