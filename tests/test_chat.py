import json
import unittest

from support import Fixture, Peer, db
from GLP import DEFAULT_SESSION, HOLDING_SESSION


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.close)
        self.alice = self.fx.register("alice001")
        self.bob = self.fx.register("bobby001")
        self.eve = self.fx.register("third001")

    def send(self, peer, target, content):
        result = self.fx.call(peer, "CHAT.SEND", target, content.encode())
        self.assertEqual(result.action, "CHAT.SENT")
        return json.loads(result.args[0])

    def test_latest_ten_combines_both_sides_and_cursor_loads_older(self):
        records = []
        for index in range(25):
            peer, target = (self.alice, self.fx.ids[self.bob]) if index % 2 else (self.bob, self.fx.ids[self.alice])
            records.append(self.send(peer, target, f"message-{index}"))
        self.send(self.eve, self.fx.ids[self.alice], "another conversation")
        self.send(self.alice, b"GLOBAL", "global only")
        response = self.fx.call(self.alice, "CHAT.HISTORY", self.fx.ids[self.bob])
        page = json.loads(response.args[1])
        self.assertEqual(page, records[-10:])
        response = self.fx.call(self.alice, "CHAT.HISTORY", self.fx.ids[self.bob], b"20", response.args[2])
        self.assertEqual(json.loads(response.args[1]), records[:15])
        self.assertEqual(response.args[2], b"")
        self.assertEqual(self.fx.call(self.bob, "CHAT.HISTORY", self.fx.ids[self.eve]).args[1], b"[]")

    def test_global_events_private_visibility_and_offline_list(self):
        self.send(self.alice, b"GLOBAL", "hello global")
        self.assertEqual(self.bob.events[-1].action, "CHAT.MESSAGE")
        self.send(self.alice, self.fx.ids[self.bob], "private")
        self.assertEqual(len(self.bob.events), 2)
        self.assertEqual(len(self.eve.events), 1)
        self.fx.online.remove(self.bob)
        self.send(self.alice, self.fx.ids[self.bob], "offline delivery")
        self.assertEqual(len(self.bob.events), 2)
        items = json.loads(self.fx.call(self.alice, "CHAT.LIST").args[0])
        bob = next(row for row in items if row["target"] == self.fx.ids[self.bob].decode())
        self.assertFalse(bob["online"])
        self.assertEqual(items[0]["target"], "GLOBAL")

    def test_history_larger_than_frame_is_paginated_without_losing_messages(self):
        for index in range(30):
            self.send(self.alice, self.fx.ids[self.bob], f"{index}:" + "x" * 4000)
        seen, cursor = [], b"0"
        while cursor:
            response = self.fx.call(self.bob, "CHAT.HISTORY", self.fx.ids[self.alice], b"100", cursor)
            self.assertLess(len(response.args[1]), 65536)
            seen.extend(row["msg_id"] for row in json.loads(response.args[1]))
            cursor = response.args[2]
        self.assertEqual(len(seen), 30)
        self.assertEqual(len(set(seen)), 30)

    def test_invalid_inputs_do_not_break_next_request(self):
        cases = [("CHAT.SEND", b"missing", b"text"), ("CHAT.SEND", b"GLOBAL", b""),
                 ("CHAT.SEND", b"GLOBAL", b"\xff"), ("CHAT.SEND", b"GLOBAL", b"x" * 4097),
                 ("CHAT.HISTORY", b"GLOBAL", b"0"), ("CHAT.HISTORY", b"GLOBAL", b"101"),
                 ("CHAT.HISTORY", b"GLOBAL", b"10", b"-1"), ("CHAT.LIST", b"extra")]
        for route, *args in cases:
            with self.subTest(route=route, args=args[:1]):
                self.assertTrue(self.fx.call(self.alice, route, *args).action.startswith("ERROR."))
                self.assertEqual(self.fx.call(self.alice, "SYS.PING").action, "OK.PONG")

    def test_login_logout_stolen_session_and_register_two_args(self):
        token = self.fx.tokens[self.alice]
        thief = Peer()
        response = self.fx.call(thief, "CHAT.LIST", token=token)
        self.assertEqual((response.action, response.session_update), ("ERROR.AUTH", DEFAULT_SESSION))
        self.assertEqual(self.fx.call(self.alice, "CHAT.LIST").session_update, HOLDING_SESSION)
        self.assertEqual(self.fx.call(self.alice, "AUTH.LOGOUT").action, "AUTH.BYE")
        denied = self.fx.call(self.alice, "AUTH.LOGIN", b"alice001", b"wrong-password")
        self.assertEqual(denied.action, "ERROR.AUTH")
        self.assertEqual(denied.args, ("[!] Username hoặc password không đúng".encode(),))
        self.assertEqual(denied.session_update, HOLDING_SESSION)
        login = self.fx.call(self.alice, "AUTH.LOGIN", b"alice001", b"test-password")
        self.assertEqual(login.action, "AUTH.OK")
        self.assertNotEqual(login.session_update, token)
        self.assertEqual(self.fx.call(Peer(), "AUTH.REGISTER", b"extra001", b"test-password", b"name").action, "ERROR.CLIENT")

    def test_accounts_and_chat_survive_new_database_connection(self):
        self.send(self.alice, self.fx.ids[self.bob], "persist me")
        reopened = db.connect(self.fx.folder / "test.db")
        self.addCleanup(reopened.close)
        db.init(reopened)
        self.assertEqual(db.login(reopened, "alice001", "test-password"), self.fx.ids[self.alice].decode())
        rows = db.get_msg(reopened, self.fx.ids[self.alice].decode(), self.fx.ids[self.bob].decode())
        self.assertEqual(rows[0]["content"], "persist me")

    def test_disconnecting_one_login_keeps_other_connection_online(self):
        second = Peer()
        self.fx.call(second, "AUTH.LOGIN", b"bobby001", b"test-password")
        self.fx.online.remove(self.bob)
        self.assertIn(self.fx.ids[self.bob].decode(), self.fx.online.user_ids())
        self.fx.online.remove(second)
        self.assertNotIn(self.fx.ids[self.bob].decode(), self.fx.online.user_ids())
