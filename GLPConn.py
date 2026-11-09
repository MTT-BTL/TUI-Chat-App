
import socket
import ssl
import socket
from GLP import CRLF, MAGIC_BYTES, Request, Response, ProtocolError, ROUTE, DEFAULT_SESSION

class GLPConnectionError(Exception): """"""

class GLP_Connection:
    def __init__(self, sock: socket.socket, timeout: float = 10.0):
        self.sock = sock
        self.sock.settimeout(timeout)
        self.buffer = bytearray()

    @classmethod
    def connect(s, host, port, certfile, timeout=10):
        context = ssl.create_default_context(cafile=certfile)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        try: # TLS handshake
            raw = socket.create_connection((host,  port), timeout=timeout)
            tls = context.wrap_socket(raw, server_hostname=host)
        except OSError as err:
            if raw: raw.close()
            raise GLPConnectionError(f"[!] connect/TLS thất bại: {err!r}") from err

        conn = s(tls, timeout=timeout)
        try: # GLP handshake SYS.PING -> OK.PONG -> start Communication
            conn.send(Request("SYS.PING"), DEFAULT_SESSION)
            msg = conn.recv(timeout=timeout)
            if not isinstance(msg, Response) or msg.action != "OK.PONG": raise ProtocolError("Handshake Error")
        except Exception: 
            conn.close()
            raise 
        return conn

    @classmethod
    def accept(s, sock, context, *, timeout=10):
        try:
            tls = context.wrap_socket(sock, server_side=True)
        except OSError as error:
            sock.close()
            raise GLPConnectionError(f"TLS accept thất bại: {error!r}") from error

        conn = s(tls, timeout=timeout)
        try:
            msg = conn.recv()
            if not isinstance(msg, Request) or msg.route != "SYS.PING": raise ProtocolError("Handshake thất bại: Gói tin đầu tiên phải là SYS.PING")
            conn.send(Response("OK.PONG", DEFAULT_SESSION))
        except TimeoutError:
            conn.close()
            raise GLPConnectionError("Handshake quá hạn: client không gửi PING") from None
        except Exception:
            conn.close()
            raise
        return conn

    def send(self, frame: Request | Response):
        try: self.sock.sendall(frame.to_bytes())
        except TimeoutError: raise

    def _parse(self) -> Request | Response | None:
        if len(self.buffer) < 5: return None # GLP\r\n MAGIC BYTES
        if not self.buffer.startswith(MAGIC_BYTES):  raise ProtocolError("[!] Bắt đầu không phải GLP\\r\\n")

        header_end = self.buffer.find(b"\r\n\r\n") # Body & Head
        if header_end == -1:
            if len(self.buffer) > 4096: raise ProtocolError("[!] Header quá dài")
            return None # Chưa nhận đủ header, chờ thêm

        header_bytes = self.buffer[5:header_end]
        lines = header_bytes.split(CRLF)
        if len(lines) != 2: raise ProtocolError("[!] Head chỉ 2 dòng gồm route/action và session")
        route, session = lines[0].decode(), lines[1].decode()

        pos = header_end + 4 # Bắt đầu đọc body sau \r\n\r\n
        
        # Đọc số lượng arguments (argc)
        next_crlf = self.buffer.find(CRLF, pos)
        if next_crlf == -1: return None
        try: argc = int(self.buffer[pos:next_crlf])
        except ValueError: raise ProtocolError("[!] Số lượng args phải là số nguyên dương")
        
        pos = next_crlf + 2
        args = []
        
        for _ in range(argc):
            next_crlf = self.buffer.find(CRLF, pos) # CRLF index 
            if next_crlf == -1: return None # thiếu CRLF
            
            arg_len = int(self.buffer[pos:next_crlf])
            pos = next_crlf + 2
            
            # Kiểm tra buffer đã chứa đủ chưa
            if len(self.buffer) < pos + arg_len + 2: return None
            
            if self.buffer[pos+arg_len : pos+arg_len+2] != CRLF: raise ProtocolError("[!] Thiếu CRLF sau dữ liệu của argument")
                
            arg_data = bytes(self.buffer[pos : pos+arg_len])
            args.append(arg_data)
            
            pos += arg_len + 2 # next

        del self.buffer[:pos]

        if ROUTE.fullmatch(route): return Request(route, session, tuple(args))
        return Response(route, session, tuple(args))

    def recv(self) -> Request | Response:
        while True:
            frame = self._parse()
            if frame is not None: return frame
            try:
                data = self.sock.recv(4096)
                if not data: raise EOFError("Peer đã ngắt kết nối")
                self.buffer.extend(data)
            except socket.timeout:
                raise TimeoutError("Quá thời gian chờ mạng")

    def close(self): self.sock.close()
