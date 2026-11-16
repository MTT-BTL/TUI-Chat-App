import json
import socket
import sys
import threading
import time

from Error import GLPConnectionError, error_message

DISCOVERY_PORT = 8444
ANNOUNCE_INTERVAL = 10.0
DISCOVERY_TIMEOUT = 30.0
BROADCAST = "255.255.255.255"
MAGIC = b"GLP-DISC"
MAX_DATAGRAM = 256

# parse Port
def _announced_port(raw):
    if len(raw) > MAX_DATAGRAM or not raw.startswith(MAGIC): return None
    try: value = json.loads(raw[len(MAGIC):].decode("ascii"))
    except ValueError: return None
    if isinstance(value, dict) and value.get("type") == "ANNOUNCE":
        port = value.get("tcp_port")
        if type(port) is int and 1 <= port <= 65535: return port
    return None

# client listening
def discover(port=DISCOVERY_PORT, *, timeout=DISCOVERY_TIMEOUT):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        # nếu cùng 1 máy khởi tạo nhiều client sẽ bị trùng port, nên nhiều client cùng chờ
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"): udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        udp.bind(("0.0.0.0", port))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0: break
            udp.settimeout(remaining)
            try: data, peer = udp.recvfrom(65535)
            except TimeoutError: break
            tcp_port = _announced_port(data)
            if tcp_port is not None: return peer[0], tcp_port
    raise GLPConnectionError(f"Không tìm thấy GLP server sau {timeout:g} giây. Dùng --host và --port để kết nối tay")

# broadcast
def _broadcast(udp, port, tcp_port, stop):
    announcement = {"type": "ANNOUNCE", "tcp_port": tcp_port}
    data = MAGIC + json.dumps(announcement, separators=(",", ":")).encode("ascii")
    while not stop.is_set():
        try: udp.sendto(data, (BROADCAST, port))
        except OSError as error:
            print(error_message(f"Không phát được UDP discovery ({error})"), file=sys.stderr, flush=True)
        stop.wait(ANNOUNCE_INTERVAL)

# Server send
def start_discovery(tcp_endpoint, port):
    if port is None: return None
    tcp_host, tcp_port = tcp_endpoint
    stop = threading.Event()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp.bind((tcp_host, 0)) # auto choose port
        udp.settimeout(1.0)
        worker = threading.Thread(target=_broadcast, args=(udp, port, tcp_port, stop), daemon=True)
        worker.start()
    except BaseException:
        stop.set()
        udp.close()
        raise
    return udp, stop, worker

def stop_discovery(discovery):
    if discovery is None: return
    udp, stop, worker = discovery
    stop.set()
    try: worker.join()
    finally: udp.close()
