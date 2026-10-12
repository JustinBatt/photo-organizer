"""Side-by-side comparison of the copies in one duplicate group."""
import html
from typing import Optional

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QScrollArea, QFrame,
)

from core.duplicates import (
    BASIS_METADATA, STATUS_CONFIRMED, DupGroupRecommendation,
)
from core.fileinfo import file_times, format_dt, human_size
from .thumbnails import ThumbnailLoader
from .widgets import ClickableLabel, wrappable_path

THUMB = 240
COL_WIDTH = 250
DIFF_STYLE = "background: #f6d776; color: #222; padding: 2px 4px; border-radius: 3px;"
SAME_STYLE = "padding: 2px 4px;"

BASIS_TEXT = {
    BASIS_METADATA: "same date taken + same camera + same file size",
    "name+size": "no camera data, so matched on same file name + same file size",
}


def _camera(row) -> str:
    return " ".join(x for x in (row["camera_make"], row["camera_model"]) if x) or "—"


class ComparePanel(QWidget):
    confirm_requested = Signal(str)   # group_key
    unflag_requested = Signal(str)    # group_key

    def __init__(self, parent=None):
        super().__init__(parent)
        self._group: Optional[DupGroupRecommendation] = None
        self._thumb_labels: dict[str, list[QLabel]] = {}

        self.loader = ThumbnailLoader(size=THUMB, cache_size=200)
        self.loader.loaded.connect(self._on_thumb_loaded)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.title = QLabel("")
        self.title.setWordWrap(True)
        layout.addWidget(self.title)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, stretch=1)

        self.legend = QLabel(
            "<span style='background:#f6d776;color:#222;padding:1px 4px'>highlighted</span> "
            "= differs between the copies"
        )
        layout.addWidget(self.legend)

        row = QHBoxLayout()
        self.confirm_btn = QPushButton("✔  Confirm: these are duplicates")
        self.confirm_btn.clicked.connect(lambda: self._emit(self.confirm_requested))
        self.unflag_btn = QPushButton("✖  Not duplicates — unflag")
        self.unflag_btn.clicked.connect(lambda: self._emit(self.unflag_requested))
        row.addWidget(self.confirm_btn)
        row.addWidget(self.unflag_btn)
        layout.addLayout(row)

        self.show_message("Click a file on the left to see all of its copies side by side "
                          "and how they compare.")

    def shutdown(self):
        self.loader.shutdown()

    @property
    def group_key(self) -> Optional[str]:
        return self._group.group_key if self._group else None

    def _emit(self, signal):
        if self._group:
            signal.emit(self._group.group_key)

    def show_message(self, text: str):
        self._group = None
        self.loader.cancel_pending()
        self._thumb_labels = {}
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        label.setMargin(8)
        self.scroll.setWidget(label)
        self.title.setText("")
        self.legend.setVisible(False)
        self.confirm_btn.setEnabled(False)
        self.unflag_btn.setEnabled(False)

    def show_group(self, group: DupGroupRecommendation, highlight_path: Optional[str] = None):
        self._group = group
        self.loader.cancel_pending()
        self._thumb_labels = {}
        rows = list(group.photos)

        confirmed = group.status == STATUS_CONFIRMED
        state = ("<span style='color:#1e8449'><b>Confirmed by you</b></span>" if confirmed
                 else "<span style='color:#b9770e'><b>Needs your review</b></span>")
        self.title.setText(
            f"<b>{len(rows)} copies</b> of <b>{html.escape(rows[0]['filename'])}</b> — "
            f"matched because: {BASIS_TEXT.get(group.basis, group.basis)}.<br>{state}"
        )

        times = {r["path"]: file_times(r["path"]) for r in rows}

        def organized(r):
            return "yes" if r["is_organized_folder"] else "no"

        def has_meta(r):
            return "yes" if r["has_exif"] else "no"

        fields = [
            ("Folder", lambda r: wrappable_path(r["folder"])),
            ("File name", lambda r: r["filename"]),
            ("Size", lambda r: f"{human_size(r['size'])}  ({r['size']:,} bytes)"),
            ("Dimensions", lambda r: f"{r['width']} × {r['height']}" if r["width"] and r["height"] else "—"),
            ("Date taken", lambda r: str(r["date_taken"]).replace("T", " ") if r["date_taken"] else "—"),
            ("Camera", _camera),
            ("Created", lambda r: format_dt(times[r["path"]][0])),
            ("Modified", lambda r: format_dt(times[r["path"]][1])),
            ("In an organized folder", organized),
            ("Has camera metadata", has_meta),
        ]

        content = QWidget()
        grid = QGridLayout(content)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)

        # row 0: pictures, row 1: recommendation
        grid.addWidget(QLabel(""), 0, 0)
        grid.addWidget(self._bold("Recommendation"), 1, 0, Qt.AlignmentFlag.AlignTop)
        for col, r in enumerate(rows, start=1):
            path = r["path"]
            thumb = ClickableLabel()
            thumb.setFixedSize(THUMB, THUMB)
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb.setCursor(Qt.CursorShape.PointingHandCursor)
            thumb.setToolTip("Click to open full size")
            border = "3px solid #2e86c1" if path == highlight_path else "1px solid #888"
            thumb.setStyleSheet(f"border: {border};")
            thumb.setPixmap(self.loader.pixmap_or_request(path))
            thumb.clicked.connect(lambda p=path: QDesktopServices.openUrl(QUrl.fromLocalFile(p)))
            self._thumb_labels.setdefault(path, []).append(thumb)
            grid.addWidget(thumb, 0, col, Qt.AlignmentFlag.AlignTop)

            keep = path == group.keep_path
            badge = QLabel(
                ("<b style='color:#1e8449'>KEEP (recommended)</b>" if keep
                 else "<b style='color:#c0392b'>copy — can go</b>")
                + f"<br><span style='color:#888'>{html.escape(group.reasons.get(path, ''))}</span>"
            )
            badge.setWordWrap(True)
            badge.setFixedWidth(COL_WIDTH)
            grid.addWidget(badge, 1, col, Qt.AlignmentFlag.AlignTop)

        for i, (label, getter) in enumerate(fields, start=2):
            values = [str(getter(r)) for r in rows]
            differs = len({v.replace("​", "") for v in values}) > 1
            grid.addWidget(self._bold(label), i, 0, Qt.AlignmentFlag.AlignTop)
            for col, v in enumerate(values, start=1):
                cell = QLabel(html.escape(v).replace("​", "&#8203;"))
                cell.setTextFormat(Qt.TextFormat.RichText)
                cell.setWordWrap(True)
                cell.setFixedWidth(COL_WIDTH)
                cell.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                cell.setStyleSheet(DIFF_STYLE if differs else SAME_STYLE)
                grid.addWidget(cell, i, col, Qt.AlignmentFlag.AlignTop)

        self.scroll.setWidget(content)
        self.legend.setVisible(True)
        self.confirm_btn.setEnabled(not confirmed)
        self.confirm_btn.setText("✔  Confirmed" if confirmed else "✔  Confirm: these are duplicates")
        self.unflag_btn.setEnabled(True)

    @staticmethod
    def _bold(text: str) -> QLabel:
        label = QLabel(f"<b>{html.escape(text)}</b>")
        label.setMinimumWidth(120)
        return label

    def _on_thumb_loaded(self, path: str):
        pm = self.loader.get(path)
        if pm is None:
            return
        for label in self._thumb_labels.get(path, []):
            label.setPixmap(pm)
