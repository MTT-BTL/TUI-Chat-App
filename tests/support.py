"""Fixtures dùng DB/account/certificate/file tạm; không mở storage thật."""
import json
from pathlib import Path
import queue
import socket
import ssl
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from GLP import DEFAULT_SESSION, HOLDING_SESSION, Request, Response
from GLPConn import GLP_Connection
from client.client_backend import Backend
from server import db
from server.auth import AuthService, UserManager
from server.chat import MessageService
from server.files import FileService
from server.router import dispatch
from server.server import handle_client


def parse(wire):
    parser = GLP_Connection.__new__(GLP_Connection)
    parser.buffer = bytearray(wire)
    return parser._parse()


class Peer:
    def __init__(self):
        self.events = []

    def send(self, frame):
        self.events.append(parse(frame.to_bytes()))


class Fixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="glp-cli-test-")
        self.folder = Path(self.temp.name)
        self.database = db.connect(self.folder / "test.db")
        db.init(self.database)
        self.auth, self.online = AuthService(self.database), UserManager()
        self.messages = MessageService(self.database)
        self.messages.files = FileService(self.database, self.folder / "files")
        self.tokens, self.ids = {}, {}

    def call(self, peer, route, *args, token=None):
        request = parse(Request(route, token or self.tokens.get(peer, DEFAULT_SESSION), tuple(args)).to_bytes())
        result = dispatch(request, self.auth, peer, "127.0.0.1", self.online, self.messages)
        if isinstance(result, Response):
            result = parse(result.to_bytes())
            if result.session_update != HOLDING_SESSION:
                self.tokens[peer] = result.session_update
        return result

    def register(self, username):
        peer = Peer()
        response = self.call(peer, "AUTH.REGISTER", username.encode(), b"test-password")
        assert response.action == "AUTH.OK", response
        self.ids[peer] = json.loads(response.args[0])["user_id"].encode()
        return peer

    def close(self):
        for peer in list(self.messages.files.uploads):
            self.messages.files.disconnect(peer)
        self.database.close()
        self.temp.cleanup()


def certificate(folder, stem="server"):
    cert, key = Path(folder) / (stem + ".crt"), Path(folder) / (stem + ".key")
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        "-out", str(cert), "-keyout", str(key),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return cert, key


class LiveServer(Fixture):
    def __init__(self, handler=None):
        super().__init__()
        self.cert, key = certificate(self.folder)
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_3
        self.context.load_cert_chain(self.cert, key)
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(20)
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        self.clients, self.workers, self.errors = [], [], []
        self.handler = handler
        self.acceptor = threading.Thread(target=self._accept, daemon=True)
        self.acceptor.start()

    def _accept(self):
        while not self.stop.is_set():
            try:
                raw, addr = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            worker = threading.Thread(target=self._serve, args=(raw, addr), daemon=True)
            self.workers.append(worker)
            worker.start()

    def _serve(self, raw, addr):
        conn = None
        try:
            conn = GLP_Connection.accept(raw, self.context, timeout=2)
            if self.handler:
                self.handler(conn)
            else:
                handle_client(conn, addr, self.auth, self.online, self.messages)
        except (OSError, EOFError):
            pass
        except Exception as error:
            self.errors.append(error)
        finally:
            if conn:
                conn.close()

    def backend(self, username=None, interval=None):
        events = queue.Queue()
        client = Backend("localhost", self.port, events.put, cafile=self.cert)
        if interval:
            client.PING_INTERVAL = interval
        self.clients.append(client)
        client.start()
        if username:
            result = client.login(username, "test-password", register=True)
            assert result.ok, result
        return client, events

    def close(self):
        self.stop.set()
        self.listener.close()
        for client in self.clients:
            client.close()
        self.acceptor.join(3)
        for worker in self.workers:
            worker.join(3)
        alive = [thread.name for thread in self.workers if thread.is_alive()]
        if alive:
            raise AssertionError(f"Server worker chưa dừng: {alive}")
        errors = self.errors[:]
        super().close()
        if errors:
            raise AssertionError(errors)
