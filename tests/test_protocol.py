"""Wire fixtures độc lập encoder; kiểm split/coalesced/EOF và limits."""
import socket
import unittest

from support import GLP_Connection, Request, Response, parse
from Error import ProtocolError
from GLP import DEFAULT_SESSION, HOLDING_SESSION, FUNCS, STATUSES


class ProtocolTests(unittest.TestCase):
    def test_independent_wire_all_splits_and_coalesced(self):
        wire = (b"GLP\r\nCHAT.SEND\r\n" + b"a" * 32
                + b"\r\n\r\n2\r\n6\r\nGLOBAL\r\n7\r\na\r\n\x00\xffbc\r\n")
        expected = Request("CHAT.SEND", "a" * 32, (b"GLOBAL", b"a\r\n\x00\xffbc"))
        self.assertEqual(expected.to_bytes(), wire)
        reply = Response("CHAT.SENT", args=(b"{}",))
        for cut in range(len(wire)):
            parser = GLP_Connection.__new__(GLP_Connection)
            parser.buffer = bytearray(wire[:cut])
            self.assertIsNone(parser._parse())
            self.assertEqual(parser.buffer, wire[:cut])
            parser.buffer.extend(wire[cut:] + reply.to_bytes() + wire[:10])
            self.assertEqual(parser._parse(), expected)
            self.assertEqual(parser._parse(), reply)
            self.assertIsNone(parser._parse())
            self.assertEqual(parser.buffer, wire[:10])

    def test_bad_headers_counts_lengths_and_old_routes_rejected(self):
        head = b"GLP\r\nCHAT.SEND\r\n" + b"a" * 32 + b"\r\n\r\n"
        wires = [b"BAD\r\n", head + b"-1\r\n", head + b"5\r\n", head + b"01\r\n",
                 head + b"1\r\n-1\r\n", head + b"1\r\n65537\r\n",
                 head + b"1\r\n1\r\naXX", head.replace(b"CHAT.SEND", b"MSG.PRIVATE") + b"0\r\n",
                 head.replace(b"a" * 32, b"bad-session") + b"0\r\n",
                 b"GLP\r\n" + b"x" * 66]
        for wire in wires:
            with self.subTest(wire=wire[:40]), self.assertRaises(ProtocolError):
                parse(wire)

    def test_eof_at_each_partial_frame(self):
        wire = Request("AUTH.LOGIN", DEFAULT_SESSION, (b"student1", b"password")).to_bytes()
        for cut in range(1, len(wire)):
            left, right = socket.socketpair()
            conn = GLP_Connection(left, timeout=1)
            try:
                right.sendall(wire[:cut])
                right.shutdown(socket.SHUT_WR)
                with self.assertRaises(ProtocolError):
                    conn.recv()
            finally:
                conn.close()
                right.close()

    def test_route_action_sets_disjoint_and_local_caps(self):
        routes = {f"{kind}.{action}" for kind, actions in FUNCS.items() for action in actions}
        statuses = {f"{kind}.{action}" for kind, actions in STATUSES.items() for action in actions}
        self.assertFalse(routes & statuses)
        self.assertEqual(len(DEFAULT_SESSION), 32)
        self.assertEqual(parse(Response("AUTH.BYE", DEFAULT_SESSION).to_bytes()).session_update, DEFAULT_SESSION)
        self.assertEqual(parse(Response("OK.PONG").to_bytes()).session_update, HOLDING_SESSION)
        with self.assertRaises(ProtocolError):
            Request("CHAT.SEND", "a" * 32, (b"x" * 65537,)).to_bytes()
        with self.assertRaises(ProtocolError):
            Request("SYS.PING", "a" * 32, (b"",) * 5).to_bytes()
        with self.assertRaises(ProtocolError):
            Request("SYS.PING", HOLDING_SESSION).to_bytes()
