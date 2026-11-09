"""Widget/dialog FILE riêng; không thay layout hoặc thao tác chat cũ."""
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, ProgressBar, Static

from file_protocol import file_size
from .client_state import GLOBAL_KEY
from .client_theme import clean, clip, skin


class FileBar(Horizontal):
    """Nút FILE luôn ở đầu trái, tên kênh cùng một hàng, cao một dòng."""
    DEFAULT_CSS = skin("""
    FileBar { width: 100%; height: 1; background: @dusk; }
    FileBar #open-files {
        width: 11; min-width: 11; height: 1; min-height: 1; padding: 0; border: none;
        background: @line2; color: @cyan; text-style: bold;
    }
    FileBar #open-files:hover, FileBar #open-files:focus { background: @cyan; color: @ink; }
    FileBar Static { width: 1fr; height: 1; padding: 0 1; color: @haze; }
    FileBar.-neon { background: #0d2417; }
    FileBar.-neon #open-files { background: #163b25; color: #83ffad; }
    FileBar.-neon #open-files:hover, FileBar.-neon #open-files:focus { background: #83ffad; color: #020705; }
    """)

    def compose(self) -> ComposeResult:
        yield Button("FILE", id="open-files", compact=True)
        yield Static(id="file-channel-title")

    def on_mount(self):
        self.set_class(self.app.TITLE == "NEON // GLP", "-neon")
        self.sync()

    def sync(self):
        st = self.app.store
        prefix = "# " if st.active == GLOBAL_KEY else "@ "
        self.query_one(Static).update(Text(prefix + clean(st.title(st.active)), no_wrap=True, overflow="ellipsis"))
        transfer = self.app.files.transfer
        self.query_one(Button).label = "FILE" if transfer is None else f"FILE {self.app.files.percent}%"
        self.query_one(Button).tooltip = "Xem / gửi / tải file · Ctrl+F"

    @on(Button.Pressed, "#open-files")
    def open_files(self, event):
        event.stop()
        self.app.files.open()


class FileScreen(ModalScreen):
    CSS = skin("""
    FileScreen { align: center middle; background: @ink 80%; }
    FileScreen #file-dialog {
        width: 78; max-width: 95%; height: 90%; padding: 0 1;
        background: @night; border: round @cyan;
    }
    FileScreen #file-heading { height: 1; }
    FileScreen #file-heading Static { width: 1fr; height: 1; color: @cyan; text-style: bold; }
    FileScreen #file-more { width: 8; min-width: 8; height: 1; padding: 0; border: none; }
    FileScreen DataTable { height: 1fr; min-height: 3; background: @night; }
    FileScreen Input { height: 3; margin: 0; background: @dusk; border: solid @line2; }
    FileScreen Input:focus { border: solid @cyan; }
    FileScreen #file-actions { height: 3; }
    FileScreen #file-actions Button { width: 1fr; min-width: 8; height: 3; margin: 0; }
    FileScreen #file-send { background: @cyan; color: @ink; }
    FileScreen ProgressBar { height: 1; width: 100%; }
    FileScreen #file-status { height: 1; color: @mist; }
    FileScreen.-neon #file-dialog { background: #06110c; border: round #83ffad; }
    FileScreen.-neon #file-heading Static { color: #83ffad; }
    FileScreen.-neon #file-send { background: #83ffad; color: #020705; }
    """)
    BINDINGS = [Binding("escape", "close", "Đóng")]

    def __init__(self, key):
        super().__init__()
        self.key = key

    def compose(self) -> ComposeResult:
        with Vertical(id="file-dialog"):
            with Horizontal(id="file-heading"):
                yield Static(Text("FILE · " + clean(self.app.store.title(self.key)), no_wrap=True, overflow="ellipsis"))
                yield Button("Thêm", id="file-more", disabled=True)
            yield DataTable(id="file-list", cursor_type="row", zebra_stripes=True)
            yield Input(placeholder="Đường dẫn file cần gửi…", id="file-path")
            yield Input(value=str(Path.cwd() / "downloads"), id="file-folder")
            with Horizontal(id="file-actions"):
                yield Button("Gửi file", id="file-send")
                yield Button("Tải file", id="file-download")
                yield Button("Đóng", id="file-close")
            yield ProgressBar(total=1, show_eta=False, id="file-progress")
            yield Static(id="file-status")

    def on_mount(self):
        self.set_class(self.app.TITLE == "NEON // GLP", "-neon")
        self.query_one("#file-path", Input).border_title = " File gửi vào chat này "
        self.query_one("#file-folder", Input).border_title = " Thư mục lưu file tải về "
        self.query_one(DataTable).add_columns("Tên file", "Người gửi", "Size")
        self.show_items()
        self.sync_progress()
        self.app.files.request_list(self.key)

    def show_items(self):
        table = self.query_one(DataTable)
        old_row = table.cursor_row
        table.clear()
        rows = self.app.files.items.get(self.key, [])
        for row in rows:
            table.add_row(Text(clip(clean(row["name"]), 28)),
                          Text(clip(clean(row["sender_name"]), 14)),
                          file_size(row["size"]), key=row["file_id"])
        if rows:
            table.move_cursor(row=min(old_row, len(rows) - 1))
        heading = "FILE · " + clean(self.app.store.title(self.key)) + f" · {len(rows)} file"
        self.query_one("#file-heading Static", Static).update(Text(heading, no_wrap=True, overflow="ellipsis"))
        self.sync_progress()

    def sync_progress(self):
        service = self.app.files
        self.query_one(ProgressBar).update(total=max(service.total, 1), progress=service.progress)
        self.query_one("#file-status", Static).update(Text(clean(service.status), no_wrap=True, overflow="ellipsis"))
        busy = service.transfer is not None
        self.query_one("#file-send", Button).disabled = busy
        self.query_one("#file-download", Button).disabled = busy or not service.items.get(self.key)
        self.query_one("#file-more", Button).disabled = (self.key in service._listing_more
                                                       or not service.cursors.get(self.key))

    def action_close(self):
        self.dismiss()

    @on(Button.Pressed)
    def pressed(self, event):
        event.stop()
        if event.button.id == "file-close":
            self.dismiss()
        elif event.button.id == "file-more":
            self.app.files.request_list(self.key, more=True)
        elif event.button.id == "file-send":
            self.app.files.upload(self.query_one("#file-path", Input).value, self.key)
        elif event.button.id == "file-download":
            self.download_selected()

    @on(DataTable.RowSelected)
    def selected(self):
        self.download_selected()

    def download_selected(self):
        rows = self.app.files.items.get(self.key, [])
        if rows:
            row = self.query_one(DataTable).cursor_row
            self.app.files.download(rows[row], self.query_one("#file-folder", Input).value, self.key)
