"""FILE ở cả hai skin: thao tác thật qua Pilot, network giả + file tạm."""
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from test_ui import demo_app, enter_chat
from GLP import Response
from client.client import GLPApp
from client.hacker import HackerApp
from client.client_state import GLOBAL_KEY, pm_key
from client.file_ui import FileBar, FileScreen
from file_protocol import CHUNK_SIZE
from textual.widgets import Button, DataTable, Input, ProgressBar
from textual.geometry import Region


def metadata(data, target=None):
    return {"seq": 1, "file_id": "d" * 32, "sender_id": "b" * 32,
            "sender_name": "Ghost Operator", "receiver_id": target,
            "receiver_name": "Cipher" if target else None,
            "name": "payload.bin", "size": len(data), "sha256": sha256(data).hexdigest(),
            "time": "2026-11-07 10:00:00"}


def last_file_request(app):
    # Poll SYS.USERS độc lập có thể chạy trong lúc Pilot chờ UI.
    return next(request for request in reversed(app.backend.sent) if request[0].startswith("FILE."))


class FileUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    async def test_file_button_list_channels_and_layout_for_both_skins(self):
        for cls in (GLPApp, HackerApp):
            app = demo_app(cls)
            async with app.run_test(size=(120, 36)) as pilot:
                await enter_chat(app, pilot)
                main = app.screen
                for width, height in ((120, 36), (80, 24), (60, 20)):
                    await pilot.resize_terminal(width, height)
                    await pilot.pause()
                    bar = main.query_one(FileBar)
                    self.assertEqual(bar.region.y, main.chat_area.content_region.y)
                    self.assertGreaterEqual(bar.region.x, main.chat_area.content_region.x)
                    self.assertEqual(bar.region.height, 1)
                    button = main.query_one("#open-files", Button)
                    self.assertEqual(button.content_region.height, 1)
                    await pilot.hover("#open-files")
                    await pilot.pause()
                    self.assertEqual(button.content_region.height, 1)
                    self.assertGreater(main.chat.region.height, 3)
                    for key in (GLOBAL_KEY, pm_key("c" * 32)):
                        main.switch_to(key)
                        await pilot.click("#open-files")
                        await pilot.pause()
                        self.assertIsInstance(app.screen, FileScreen)
                        self.assertEqual(app.screen.key, key)
                        target = b"GLOBAL" if key == GLOBAL_KEY else b"c" * 32
                        self.assertEqual(last_file_request(app), ("FILE.LIST", "a" * 32, target, b"0"))
                        record = metadata(b"abc", None if key == GLOBAL_KEY else "c" * 32)
                        app.handle_response(Response("FILE.ITEMS", args=(target, json.dumps([record]).encode(), b"")))
                        await pilot.pause()
                        self.assertEqual(app.screen.query_one(DataTable).row_count, 1)
                        for selector in ("#file-list", "#file-path", "#file-folder", "#file-actions", "#file-progress", "#file-status"):
                            region = app.screen.query_one(selector).region
                            self.assertGreater(region.height, 0, selector)
                            self.assertGreaterEqual(region.x, 0, selector)
                            self.assertLessEqual(region.right, width, selector)
                            self.assertLessEqual(region.bottom, height, selector)
                        await pilot.press("escape")
                        await pilot.pause()
                        self.assertIs(app.screen, main)
                await pilot.press("ctrl+f")
                await pilot.pause()
                self.assertIsInstance(app.screen, FileScreen)

    async def test_upload_download_progress_and_chat_while_file_dialog_open(self):
        data = (bytes(range(256)) + b"\r\n\x00\xff") * 280
        for cls in (GLPApp, HackerApp):
            with tempfile.TemporaryDirectory(prefix="glp-file-ui-") as folder:
                source = Path(folder) / "source.bin"
                source.write_bytes(data)
                app = demo_app(cls)
                async with app.run_test(size=(80, 24)) as pilot:
                    await enter_chat(app, pilot)
                    main = app.screen
                    await pilot.click("#open-files")
                    await pilot.pause()
                    app.screen.query_one("#file-path", Input).value = str(source)
                    await pilot.click("#file-send")
                    await pilot.pause()
                    self.assertEqual(last_file_request(app), ("FILE.UPLOAD", "a" * 32, b"GLOBAL", b"source.bin", str(len(data)).encode()))
                    app.handle_response(Response("FILE.READY", args=(b"d" * 32,)))
                    app.handle_response(Response("EVT.RECV", args=(b"GLOBAL", b"c" * 32, b"Cipher", b"Chat while uploading")))
                    self.assertEqual(app.store.hist(GLOBAL_KEY)[-1].text, "Chat while uploading")
                    received = bytearray()
                    while last_file_request(app)[0] == "FILE.CHUNK":
                        _, _, file_id, offset, chunk = last_file_request(app)
                        self.assertEqual(int(offset), len(received))
                        self.assertLessEqual(len(chunk), CHUNK_SIZE)
                        received.extend(chunk)
                        if len(received) == CHUNK_SIZE:
                            await pilot.press("escape")
                            await pilot.pause()
                            self.assertIs(app.screen, main)
                            self.assertIsNotNone(app.files.transfer)
                            await pilot.press("ctrl+f")
                            await pilot.pause()
                        app.handle_response(Response("FILE.ACK", args=(file_id, str(len(received)).encode())))
                    self.assertEqual(bytes(received), data)
                    self.assertEqual(last_file_request(app), ("FILE.COMPLETE", "a" * 32, b"d" * 32, sha256(data).hexdigest().encode()))
                    self.assertIn("100%", str(main.query_one("#open-files", Button).label))
                    await pilot.pause()
                    button = main.query_one("#open-files", Button)
                    visible = button.render_lines(Region(0, 0, button.size.width, button.size.height))[0].text
                    self.assertIn("100%", visible)
                    record = metadata(data)
                    app.handle_response(Response("FILE.STORED", args=(json.dumps(record).encode(),)))
                    app.handle_response(Response("FILE.ITEMS", args=(b"GLOBAL", json.dumps([record]).encode(), b"")))
                    await pilot.pause()
                    self.assertIsNone(app.files.transfer)
                    self.assertEqual(app.screen.query_one(ProgressBar).progress, len(data))
                    downloads = Path(folder) / "downloads"
                    downloads.mkdir()
                    (downloads / "payload.bin").write_bytes(b"keep existing file")
                    app.screen.query_one("#file-folder", Input).value = str(downloads)
                    await pilot.click("#file-download")
                    await pilot.pause()
                    self.assertEqual(last_file_request(app), ("FILE.GET", "a" * 32, b"d" * 32, b"0"))
                    for offset in range(0, len(data), CHUNK_SIZE):
                        app.handle_response(Response("FILE.DATA", args=(b"d" * 32, str(offset).encode(), data[offset:offset + CHUNK_SIZE])))
                    self.assertIsNone(app.files.transfer)
                    self.assertEqual((downloads / "payload.bin").read_bytes(), b"keep existing file")
                    self.assertEqual((downloads / "payload (2).bin").read_bytes(), data)
                    self.assertEqual(list(downloads.glob("*.part")), [])

    async def test_corrupt_download_and_disconnect_remove_partial_files(self):
        with tempfile.TemporaryDirectory(prefix="glp-file-corrupt-") as folder:
            app = demo_app(GLPApp)
            async with app.run_test(size=(60, 20)) as pilot:
                await enter_chat(app, pilot)
                await pilot.press("ctrl+f")
                await pilot.pause()
                app.files.download(metadata(b"good"), folder, GLOBAL_KEY)
                app.handle_response(Response("FILE.DATA", args=(b"d" * 32, b"0", b"oops")))
                self.assertIsNone(app.files.transfer)
                self.assertIn("SHA-256", app.files.status)
                self.assertEqual(list(Path(folder).iterdir()), [])
                app.files.download(metadata(b"large"), folder, GLOBAL_KEY)
                app.handle_response(Response("FILE.DATA", args=(b"d" * 32, b"0", b"l")))
                self.assertTrue(list(Path(folder).glob("*.part")))
                app.handle_disconnect("Test EOF during download")
                await pilot.pause()
                self.assertIsNone(app.files.transfer)
                self.assertEqual(list(Path(folder).iterdir()), [])
                self.assertNotIsInstance(app.screen, FileScreen)

    async def test_notice_during_list_refresh_and_older_page(self):
        app = demo_app(GLPApp)
        async with app.run_test(size=(80, 24)) as pilot:
            await enter_chat(app, pilot)
            await pilot.press("ctrl+f")
            await pilot.pause()
            record = metadata(b"new")
            # NOTICE tới sau snapshot LIST ở server, nhưng trước response ITEMS.
            app.handle_response(Response("FILE.NOTICE", args=(json.dumps(record).encode(),)))
            before = len([request for request in app.backend.sent if request[0] == "FILE.LIST"])
            app.handle_response(Response("FILE.ITEMS", args=(b"GLOBAL", b"[]", b"")))
            after = len([request for request in app.backend.sent if request[0] == "FILE.LIST"])
            self.assertEqual(after, before + 1)
            app.handle_response(Response("FILE.ITEMS", args=(b"GLOBAL", json.dumps([record]).encode(), b"10")))
            await pilot.pause()
            self.assertEqual(app.screen.query_one(DataTable).row_count, 1)
            await pilot.click("#file-more")
            await pilot.pause()
            self.assertEqual(last_file_request(app), ("FILE.LIST", "a" * 32, b"GLOBAL", b"10"))
            self.assertTrue(app.screen.query_one("#file-more", Button).disabled)
            older = dict(record, file_id="e" * 32, name="older.txt")
            app.handle_response(Response("FILE.ITEMS", args=(b"GLOBAL", json.dumps([older]).encode(), b"")))
            self.assertEqual(app.screen.query_one(DataTable).row_count, 2)


if __name__ == "__main__":
    unittest.main()
