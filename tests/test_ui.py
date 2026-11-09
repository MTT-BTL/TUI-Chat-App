"""Headless Textual: UI giả lập mạng, kiểm thao tác và layout, không chứng minh LAN."""
from pathlib import Path
import asyncio
import os
import sys
import unittest

os.environ["GLP_FX"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from GLP import Response
from client.client import GLPApp
from client.hacker import HackerApp, HackerNode
from client.client_screens import MainScreen, RenameScreen
from client.client_state import GLOBAL_KEY, Entry, pm_key
from textual.widgets import Button, Input


class FakeBackend:
    connected, host, port = True, "localhost", 8443
    def __init__(self): self.sent = []
    def start(self): pass
    def close(self): pass
    def send(self, *args):
        self.sent.append(args)
        return None


def demo_app(cls):
    app = cls()
    app.backend = FakeBackend()
    app.session_id = "a" * 32
    return app


async def enter_chat(app, pilot):
    await pilot.pause()
    st = app.store
    st.me_id, st.me_user, st.me_name = "b" * 32, "operator01", "Ghost Operator"
    st.touch_peer("c" * 32, "Cipher", False)
    st.online_total = 2
    st.loaded.add(GLOBAL_KEY)
    st.add(GLOBAL_KEY, Entry("msg", "Hello from Cipher", uid="c" * 32, name="Cipher"), True)
    app.enter_main()
    await pilot.pause()


class UITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    async def test_rename_and_live_messages_for_both_clients(self):
        for cls in (GLPApp, HackerApp):
            with self.subTest(client=cls.__name__):
                app = demo_app(cls)
                async with app.run_test(size=(110, 32)) as pilot:
                    await enter_chat(app, pilot)
                    main = app.screen
                    await pilot.press("ctrl+n")
                    await pilot.pause()
                    self.assertIsInstance(app.screen, RenameScreen)
                    app.screen.query_one("#displayname", Input).value = "Neo Nguyễn"
                    await pilot.click("#rename-save")
                    await pilot.pause()
                    self.assertEqual(app.backend.sent[-1], ("DASH.RENAME", "a" * 32, "Neo Nguyễn".encode()))
                    app.handle_response(Response("EVT.RECV", args=(b"GLOBAL", b"c" * 32, b"Cipher", b"Live while editing")))
                    self.assertEqual(app.store.hist(GLOBAL_KEY)[-1].text, "Live while editing")
                    app.handle_response(Response("OK.PROFILE", args=(b"b" * 32, b"operator01", "Neo Nguyễn".encode())))
                    await pilot.pause()
                    self.assertIs(app.screen, main)
                    self.assertEqual(app.store.me_label, "Neo Nguyễn")
                    self.assertIn("Neo Nguyễn", main.profile_card.render().plain)
                    self.assertNotIn("sid ", main.profile_card.render().plain)
                    await pilot.press("i")
                    main.chat_input.value = "/name Ghost Again"
                    await pilot.press("enter")
                    await pilot.pause()
                    renames = [item for item in app.backend.sent if item[0] == "DASH.RENAME"]
                    self.assertEqual(renames[-1], ("DASH.RENAME", "a" * 32, b"Ghost Again"))

    async def test_rename_rejection_keeps_dialog_and_disconnect_closes_it(self):
        app = demo_app(GLPApp)
        async with app.run_test(size=(80, 24)) as pilot:
            await enter_chat(app, pilot)
            await pilot.click(app.screen.profile_card)
            await pilot.pause()
            self.assertIsInstance(app.screen, RenameScreen)
            app.handle_response(Response("ERROR.CLIENT", args=(b"BAD_ARGUMENT", b"Name invalid")))
            self.assertFalse(app.screen.query_one("#rename-save", Button).disabled)
            self.assertEqual(app.store.me_label, "Ghost Operator")
            app.handle_disconnect("Disconnected during rename")
            await pilot.pause()
            self.assertIsInstance(app.screen, MainScreen)

    async def test_layout_keyboard_and_channel_switch_at_three_sizes(self):
        for cls in (GLPApp, HackerApp):
            app = demo_app(cls)
            async with app.run_test(size=(120, 36)) as pilot:
                await enter_chat(app, pilot)
                for width, height in ((120, 36), (80, 24), (60, 20)):
                    await pilot.resize_terminal(width, height)
                    await pilot.pause()
                    main = app.screen
                    self.assertGreater(main.chat.region.width, 12)
                    self.assertGreater(main.chat.region.height, 3)
                    self.assertLess(main.chat_input.region.bottom, height)
                    self.assertGreater(main.chat_input.region.width, 3)
                    self.assertLessEqual(main.chat_input.region.right, width)
                    self.assertGreater(main.prompt.content_region.height, 0)
                    await pilot.press("j")
                    await pilot.pause()
                    self.assertEqual(app.store.active, pm_key("c" * 32))
                    await pilot.press("g")
                    await pilot.pause()
                    self.assertEqual(app.store.active, GLOBAL_KEY)
                if cls is HackerApp:
                    self.assertIsInstance(app.screen.conv_items[pm_key("c" * 32)], HackerNode)


if __name__ == "__main__": unittest.main()
