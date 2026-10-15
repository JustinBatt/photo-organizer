"""Big preview + file facts for one photo (used by Browse and Albums)."""
import html
from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap, QTextOption
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit, QSizePolicy,
)

from core.fileinfo import file_details, format_dt, human_size
from .thumbnails import ThumbnailLoader

PREVIEW_SIZE = 1100  # decoded size; plenty for a side panel, quick to produce

HINT = "Click a photo to see it larger, with its full path, size and dates."


class PhotoPreview(QWidget):
    def __init__(self, db, small_loader: Optional[ThumbnailLoader] = None, parent=None):
        super().__init__(parent)
        self.db = db
        self._small_loader = small_loader  # already-loaded grid thumbnails, shown instantly
        self._path: Optional[str] = None
        self._full: Optional[QPixmap] = None

        self.loader = ThumbnailLoader(size=PREVIEW_SIZE, cache_size=24)
        self.loader.loaded.connect(self._on_loaded)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.image = QLabel(HINT)
        self.image.setWordWrap(True)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(240, 240)
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.image.setStyleSheet("background: #1e1e1e; color: #aaa; border-radius: 4px;")
        layout.addWidget(self.image, stretch=1)

        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.details.setFixedHeight(190)
        layout.addWidget(self.details)

        row = QHBoxLayout()
        self.open_btn = QPushButton("Open in photo viewer")
        self.open_btn.clicked.connect(self._open_external)
        self.copy_btn = QPushButton("Copy path")
        self.copy_btn.clicked.connect(self._copy_path)
        row.addWidget(self.open_btn)
        row.addWidget(self.copy_btn)
        layout.addLayout(row)
        self._set_buttons(False)

    def shutdown(self):
        self.loader.shutdown()

    # --- showing a photo ---------------------------------------------------

    @property
    def current_path(self) -> Optional[str]:
        return self._path

    def clear(self):
        self._path = None
        self._full = None
        self.loader.cancel_pending()
        self.image.setPixmap(QPixmap())
        self.image.setText(HINT)
        self.details.clear()
        self._set_buttons(False)

    def show_photo(self, path: str):
        self._path = path
        self._full = None
        self._set_buttons(True)
        self._fill_details(path)

        # Instant: the grid thumbnail, enlarged. Then the sharp version replaces it.
        self.loader.cancel_pending()
        quick = None
        if self._small_loader is not None:
            quick = self._small_loader.get(path)
            if quick is self._small_loader.failed_pixmap:
                quick = None
        big = self.loader.get(path)
        if big is self.loader.failed_pixmap:
            self.image.setPixmap(QPixmap())
            self.image.setText("Can't display this file")
            return
        if big is not None:
            self._full = big
        elif quick is not None and not quick.isNull():
            self._full = quick
        if self._full is None:
            self.image.setPixmap(QPixmap())
            self.image.setText("Loading…")
        self._render()
        if big is None:
            self.loader.request(path)

    def _on_loaded(self, path: str):
        if path != self._path:
            return
        pm = self.loader.get(path)
        if pm is self.loader.failed_pixmap:
            if self._full is None:
                self.image.setPixmap(QPixmap())
                self.image.setText("Can't display this file")
            return
        if pm is not None:
            self._full = pm
            self._render()

    def _render(self):
        if self._full is None or self._full.isNull():
            return
        self.image.setText("")
        target = self.image.size()
        self.image.setPixmap(self._full.scaled(
            target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        ))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render()

    # --- facts ----------------------------------------------------------------

    def refresh_details(self):
        if self._path:
            self._fill_details(self._path)

    def _fill_details(self, path: str):
        info = file_details(path)
        row = self.db.get_photo(path)
        e = html.escape

        def line(label, value):
            return f"<tr><td style='color:#888;padding-right:8px' valign='top'>{label}</td><td>{e(str(value))}</td></tr>"

        lines = [line("Full path", path)]
        if info["exists"]:
            lines.append(line("Size", f"{human_size(info['size'])}  ({info['size']:,} bytes)"))
            lines.append(line("Created", format_dt(info["created"])))
            lines.append(line("Modified", format_dt(info["modified"])))
        else:
            lines.append(line("Status", "File not found on disk (moved or deleted?)"))
        if row is not None:
            if row["width"] and row["height"]:
                lines.append(line("Dimensions", f"{row['width']} × {row['height']}"))
            if row["date_taken"]:
                lines.append(line("Date taken", str(row["date_taken"]).replace("T", " ")))
            cam = " ".join(x for x in (row["camera_make"], row["camera_model"]) if x)
            if cam:
                lines.append(line("Camera", cam))
            if row["rating"]:
                lines.append(line("Rating", "★" * row["rating"] + "☆" * (5 - row["rating"])))
            albums = self.db.albums_containing(path)
            if albums:
                lines.append(line("In albums", ", ".join(albums)))
        self.details.setHtml("<table cellspacing='2'>" + "".join(lines) + "</table>")

    # --- buttons ----------------------------------------------------------------

    def _set_buttons(self, enabled: bool):
        self.open_btn.setEnabled(enabled)
        self.copy_btn.setEnabled(enabled)

    def _open_external(self):
        if self._path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._path))

    def _copy_path(self):
        if self._path:
            QGuiApplication.clipboard().setText(self._path)
