"""Kiểm announcement UDP định kỳ, endpoint và vòng đời của socket client."""
from concurrent.futures import ThreadPoolExecutor
import socket
import time
import unittest
from unittest.mock import patch

from Error import GLPConnectionError
from discovery import discover, start_discovery, stop_discovery


def unused_udp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.bind(("0.0.0.0", 0))
        return udp.getsockname()[1]


class DiscoveryTests(unittest.TestCase):
    def test_server_sends_repeated_announcements_with_the_tcp_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.bind(("127.0.0.1", 0))
            udp.settimeout(2)
            udp_port = udp.getsockname()[1]
            expected = b'GLP-DISC{"type":"ANNOUNCE","tcp_port":9037}'
            with (patch("discovery.BROADCAST", "127.0.0.1"),
                  patch("discovery.ANNOUNCE_INTERVAL", 0.03)):
                discovery = start_discovery(("127.0.0.1", 9037), udp_port)
                try:
                    for _ in range(2):
                        payload, sender = udp.recvfrom(1024)
                        self.assertEqual(payload, expected)
                        self.assertEqual(sender[0], "127.0.0.1")
                finally:
                    stop_discovery(discovery)
            broadcaster, _, worker = discovery
            self.assertFalse(worker.is_alive())
            self.assertEqual(broadcaster.fileno(), -1)

    def test_client_ignores_invalid_packets_and_releases_its_udp_port(self):
        udp_port = unused_udp_port()
        packets = [
            b"other service",
            b'GLP-DISC{"type":"OFFER","tcp_port":9036}',
            b'GLP-DISC{"type":"ANNOUNCE","tcp_port":true}',
            b'GLP-DISC{"type":"ANNOUNCE","tcp_port":9037}',
        ]
        with (socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender,
              ThreadPoolExecutor(max_workers=1) as pool):
            sender.bind(("127.0.0.1", 0))

            def send_packets():
                for _ in range(5):
                    for packet in packets:
                        sender.sendto(packet, ("127.0.0.1", udp_port))
                    time.sleep(0.02)

            worker = pool.submit(send_packets)
            self.assertEqual(discover(udp_port, timeout=1.0), ("127.0.0.1", 9037))
            worker.result(timeout=2)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.bind(("0.0.0.0", udp_port))

    def test_client_times_out_and_releases_its_udp_port(self):
        udp_port = unused_udp_port()
        with self.assertRaisesRegex(GLPConnectionError, "sau 0.05 giây"):
            discover(udp_port, timeout=0.05)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.bind(("0.0.0.0", udp_port))
