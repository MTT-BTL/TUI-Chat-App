import re, socket, ssl, threading, time
from Error import *
from GLP import *

class GLP_Connection:
    def __init__(self, sock: socket.socket, timeout: float = 10.0):
        self.sock = sock
        self.sock.settimeout(timeout)
        self.buffer = bytearray()
        self._send_lock = threading.Lock()

    @classmethod
    def connect(cls, host, port, certfile, timeout=10):
        try: context = ssl.create_default_context(cafile=certfile)
        except FileNotFoundError as error: raise GLPConnectionError(f"Thiếu cert tại {certfile}. Tạo cert tại đó hoặc dùng --cafile.") from error
        except OSError as error: raise GLPConnectionError(f"Không đọc được chứng chỉ {certfile}. {error}") from error
        
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        raw = socket.create_connection((host, port), timeout=timeout)
        try: tls = context.wrap_socket(raw, server_hostname=host)
        except ssl.SSLCertVerificationError as error:
            raw.close()
            raise GLPConnectionError(f"Không xác minh được chứng chỉ TLS của {host}. {error.verify_message}") from error
        except Exception:
            raw.close()
            raise
        conn = cls(tls, timeout)
        try:
            conn.send(Request("SYS.PING", DEFAULT_SESSION))
            response = conn.recv()
            if not isinstance(response, Response) or response.action != "OK.PONG" or response.args:
                raise ProtocolError("Server không trả OK.PONG hợp lệ")
            return conn
        except Exception:
            conn.close()
            raise

    @classmethod
    def accept(cls, sock, context, *, timeout=10):
        sock.settimeout(timeout)
        try:
            tls = context.wrap_socket(sock, server_side=True)
        except Exception:
            sock.close()
            raise
        conn = cls(tls, timeout)
        try:
            request = conn.recv()
            if (not isinstance(request, Request) or request.route != "SYS.PING"
                    or request.args or request.session_id != DEFAULT_SESSION):
                raise ProtocolError("Gói đầu phải là SYS.PING chưa đăng nhập, không args")
            conn.send(Response("OK.PONG", DEFAULT_SESSION))
            return conn
        except Exception:
            conn.close()
            raise

    def send(self, frame: Request | Response):
        wire = frame.to_bytes()  # Validate trước khi ghi bất kỳ byte nào.
        with self._send_lock:
            self.sock.sendall(wire)

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

    def close(self):
        self.sock.close()
