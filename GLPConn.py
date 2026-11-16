from pathlib import Path
import re
import socket
import ssl
import threading
import time
import datetime
import os
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from Error import GLPConnectionError, GLPTimeoutError, PeerClosedError, ProtocolError
from GLP import (CRLF, DEFAULT_SESSION, MAGIC_BYTES, MAX_ARG, MAX_ARGS, MAX_FRAME,
                 MAX_LINE, ROUTE, Request, Response)


class GLP_Connection:
    _server_pem = Path(__file__).with_name("server") / ".runtime" / "server.pem"
    _server_tls: ssl.SSLContext | None = None
    _server_tls_lock = threading.Lock()

    def __init__(self, sock: socket.socket, timeout: float = 10.0):
        self.sock = sock
        self.sock.settimeout(timeout)
        self.buffer = bytearray()
        self._send_lock = threading.Lock()


    @classmethod
    def _ensure_server_pem(cls):
        pem_path = Path(cls._server_pem)
        if pem_path.exists():
            return

        print("[!] No key found, generating new key")
        pem_path.parent.mkdir(parents=True, exist_ok=True)

        private_key = ec.generate_private_key(ec.SECP256R1())
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "my-server")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=3650))
            .sign(private_key, hashes.SHA256())
        )

        pem_path.write_bytes(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
            + cert.public_bytes(serialization.Encoding.PEM)
        )
        pem_path.chmod(0o600)
        print("[*] Generated new key")


    # client only
    @classmethod
    def connect(cls, host, port, timeout=10):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        sock = socket.create_connection((host, port), timeout=timeout)
        try: # TLS handshake
            sock = context.wrap_socket(sock)
            conn = cls(sock, timeout)
            # GLP handshake (client side)
            conn.send(Request("SYS.PING", DEFAULT_SESSION))
            response = conn.recv()
            if not isinstance(response, Response) or response.action != "OK.PONG" or response.args:
                raise ProtocolError("Server không trả OK.PONG hợp lệ")
            return conn
        except BaseException:
            sock.close()
            raise

    # server only
    @classmethod
    def accept(cls, sock, *, timeout=10):
        try:
            with cls._server_tls_lock:
                context = cls._server_tls
                if context is None: # TLS handshake (Server side)
                    cls._ensure_server_pem()
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    context.minimum_version = ssl.TLSVersion.TLSv1_3
                    try: context.load_cert_chain(cls._server_pem)
                    except OSError as error: 
                        raise GLPConnectionError(f"Không nạp được PEM {cls._server_pem} ({error})") from error
                    cls._server_tls = context
            sock.settimeout(timeout)
            sock = context.wrap_socket(sock, server_side=True)
            conn = cls(sock, timeout)
            request = conn.recv() # Check SYS PING and response OK PONG
            if (not isinstance(request, Request) or request.route != "SYS.PING" or request.args or request.session_id != DEFAULT_SESSION):
                raise ProtocolError("Gói đầu phải là SYS.PING chưa đăng nhập, không args")
            conn.send(Response("OK.PONG", DEFAULT_SESSION))
            return conn
        except BaseException:
            sock.close()
            raise

    # both 
    def send(self, frame: Request | Response):
        wire = frame.to_bytes()  # Validate trước khi ghi bất kỳ byte nào.
        with self._send_lock:
            self.sock.sendall(wire)

    # ulti function call by recv which called by both
    def _parse(self) -> Request | Response | None:
        position = len(MAGIC_BYTES)
        if len(self.buffer) < position:
            return None
        if self.buffer[:position] != MAGIC_BYTES:
            raise ProtocolError("Sai magic GLP")

        def line(limit=MAX_LINE):
            nonlocal position
            end = self.buffer.find(CRLF, position)
            if end < 0:
                if len(self.buffer) - position > limit + 1:
                    raise ProtocolError("Dòng header/count quá dài")
                return None
            if end - position > limit or end + 2 > MAX_FRAME:
                raise ProtocolError("Vượt giới hạn dòng/frame")
            raw = bytes(self.buffer[position:end])
            position = end + 2
            if b"\r" in raw or b"\n" in raw:
                raise ProtocolError("CR/LF lẻ trong header")
            return raw

        def number(raw, cap):
            if not re.fullmatch(rb"0|[1-9][0-9]*", raw) or int(raw) > cap:
                raise ProtocolError("Count/length không hợp lệ")
            return int(raw)

        header = []
        for _ in range(3):
            raw = line()
            if raw is None:
                return None
            header.append(raw)
        if header[2]:
            raise ProtocolError("Thiếu dòng trống sau header")
        try:
            head, session = (raw.decode("ascii") for raw in header[:2])
        except UnicodeDecodeError as error:
            raise ProtocolError("Header phải là ASCII") from error
        frame_type = Request if ROUTE.fullmatch(head) else Response
        frame_type(head, session).validate()
        raw = line(10)
        if raw is None:
            return None
        args = []
        for _ in range(number(raw, MAX_ARGS)):
            raw = line(10)
            if raw is None:
                return None
            end = position + number(raw, MAX_ARG)
            if end + 2 > MAX_FRAME:
                raise ProtocolError("Frame vượt 256 KiB")
            if len(self.buffer) < end + 2:
                return None
            if self.buffer[end:end + 2] != CRLF:
                raise ProtocolError("Thiếu CRLF sau argument")
            args.append(bytes(self.buffer[position:end]))
            position = end + 2
        frame = frame_type(head, session, tuple(args))
        del self.buffer[:position]
        return frame

    # both
    def recv(self, *, deadline: float | None = None) -> Request | Response:
        previous = self.sock.gettimeout()
        try:
            while True:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise GLPTimeoutError("Quá thời gian chờ SYS.PING")
                    self.sock.settimeout(remaining)
                frame = self._parse()
                if frame is not None:
                    return frame
                data = self.sock.recv(4096)
                if not data:
                    if self.buffer:
                        raise ProtocolError("EOF giữa frame GLP")
                    raise PeerClosedError("Peer đã ngắt kết nối")
                self.buffer.extend(data)
        finally:
            if deadline is not None and self.sock.fileno() != -1:
                self.sock.settimeout(previous)

    # both
    def close(self):
        self.sock.close()
