import os
from datetime import datetime

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontMetrics
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton,
    QLabel, QProgressBar, QMessageBox, QScrollArea, QFrame, QCheckBox,
)

from core.perceptual import (
    PERCEPTUAL_AVAILABLE, find_visual_duplicate_groups,
)
from core.actions import send_to_recycle_bin
from .thumbnails import ThumbnailLoader
from .workers import HashWorker

THUMB = 220
CARD_WIDTH = 240
COLUMNS = 4
GROUPS_PER_PAGE = 10


def _human_size(n: int) -> str:
    size = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _photo_date(row: dict) -> str:
    if row.get("date_taken"):
        return str(row["date_taken"])[:10]
    try:
        return datetime.fromtimestamp(row["modified_time"]).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class VisualDuplicatesTab(QWidget):
    """
    STRETCH GOAL (Phase 5): finds visually-similar photos that don't
    match on filename/size. Separate, optional, slower pass - kept off
    the main Duplicate Review tab so it never blocks the core workflow.

    Each similar group is shown as a row of photo cards (real thumbnails)
    so the photos can be compared by eye before anything is deleted.
    """

    # Emitted after files were moved to the Recycle Bin, so other tabs can refresh.
    files_deleted = Signal()

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.worker: HashWorker | None = None
        self.loader: ThumbnailLoader | None = None
        self._groups: list[list[dict]] = []
        self._page = 0
        self._checked: set[str] = set()
        self._thumb_labels: dict[str, list[QLabel]] = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<b>Visual Duplicates (Experimental)</b><br>"
            "Finds photos that look alike even if the file name/size doesn't "
            "match (resized copies, re-saved copies, screenshots). This is a "
            "similarity match, not a certainty - compare the photos, then tick "
            "the ones to remove. Click a photo to open it full size."
        ))

        if not PERCEPTUAL_AVAILABLE:
            layout.addWidget(QLabel(
                "<i>The 'imagehash' package isn't installed. "
                "Run: pip install imagehash</i>"
            ))
            return

        self.loader = ThumbnailLoader(size=THUMB, cache_size=800)
        self.loader.loaded.connect(self._on_thumbnail_loaded)

        btn_row = QHBoxLayout()
        self.hash_btn = QPushButton("1. Compute Visual Fingerprints")
        self.hash_btn.clicked.connect(self.start_hashing)
        btn_row.addWidget(self.hash_btn)

        self.find_btn = QPushButton("2. Find Visual Duplicates")
        self.find_btn.clicked.connect(self.find_duplicates)
        btn_row.addWidget(self.find_btn)
        layout.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # --- paging -------------------------------------------------------
        page_row = QHBoxLayout()
        self.prev_btn = QPushButton("◀ Previous")
        self.prev_btn.clicked.connect(lambda: self._go_to_page(self._page - 1))
        self.next_btn = QPushButton("Next ▶")
        self.next_btn.clicked.connect(lambda: self._go_to_page(self._page + 1))
        self.page_label = QLabel("")
        self.selected_label = QLabel("")
        page_row.addWidget(self.prev_btn)
        page_row.addWidget(self.page_label)
        page_row.addWidget(self.next_btn)
        page_row.addStretch()
        page_row.addWidget(self.selected_label)
        layout.addLayout(page_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, stretch=1)

        self.apply_btn = QPushButton("Apply Selected → Move to Recycle Bin")
        self.apply_btn.clicked.connect(self.apply_selected)
        layout.addWidget(self.apply_btn)

        self._render_page()

    def shutdown(self):
        if self.loader is not None:
            self.loader.shutdown()

    # --- fingerprints --------------------------------------------------

    def start_hashing(self):
        self.hash_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        self.worker = HashWorker(self.db)
        self.worker.progress.connect(
            lambda done, total: self.progress_bar.setRange(0, total) or self.progress_bar.setValue(done)
        )
        self.worker.finished_hashing.connect(self.on_hashing_done)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def on_hashing_done(self, count: int):
        self.hash_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        QMessageBox.information(self, "Done", f"Computed fingerprints for {count} photo(s).")

    def on_error(self, message: str):
        self.hash_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        QMessageBox.critical(self, "Error", message)

    # --- finding + showing groups ---------------------------------------

    def find_duplicates(self):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            groups = find_visual_duplicate_groups(self.db)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Error", str(e))
            return
        QApplication.restoreOverrideCursor()

        self._groups = groups
        self._page = 0
        self._checked.clear()
        self._render_page()

        if not groups:
            QMessageBox.information(
                self, "No matches",
                "No visually similar photos found (or fingerprints haven't "
                "been computed yet - use step 1 first)."
            )

    def _page_count(self) -> int:
        return max(1, -(-len(self._groups) // GROUPS_PER_PAGE))

    def _go_to_page(self, page: int):
        self._page = max(0, min(page, self._page_count() - 1))
        self._render_page()
        self.scroll.verticalScrollBar().setValue(0)

    def _render_page(self):
        self.loader.cancel_pending()
        self._thumb_labels = {}

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        if not self._groups:
            content_layout.addWidget(QLabel(
                "No groups to show yet. Run step 1, then step 2, and similar photos "
                "will appear here side by side."
            ))
        else:
            start = self._page * GROUPS_PER_PAGE
            for offset, cluster in enumerate(self._groups[start:start + GROUPS_PER_PAGE]):
                content_layout.addWidget(self._build_group(start + offset, cluster))

        self.scroll.setWidget(content)  # replaces (and deletes) the previous page
        self.prev_btn.setEnabled(self._page > 0)
        self.next_btn.setEnabled(self._page < self._page_count() - 1)
        self.page_label.setText(
            f"Page {self._page + 1} of {self._page_count()}  ({len(self._groups):,} group(s))"
            if self._groups else ""
        )
        self._update_selected_label()

    def _build_group(self, number: int, cluster: list[dict]) -> QWidget:
        box = QFrame()
        box.setFrameShape(QFrame.Shape.StyledPanel)
        box_layout = QVBoxLayout(box)
        box_layout.addWidget(QLabel(
            f"<b>Group {number + 1} of {len(self._groups):,}</b> — {len(cluster)} similar photos"
        ))
        grid = QGridLayout()
        grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        for i, row in enumerate(cluster):
            grid.addWidget(self._build_card(row), i // COLUMNS, i % COLUMNS)
        box_layout.addLayout(grid)
        return box

    def _build_card(self, row: dict) -> QWidget:
        path = row["path"]
        card = QFrame()
        card.setFrameShape(QFrame.Shape.Box)
        card.setFixedWidth(CARD_WIDTH)
        v = QVBoxLayout(card)

        thumb = _ClickableLabel()
        thumb.setFixedSize(THUMB, THUMB)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        thumb.setToolTip("Click to open full size")
        thumb.setPixmap(self.loader.pixmap_or_request(path))
        thumb.clicked.connect(lambda p=path: QDesktopServices.openUrl(QUrl.fromLocalFile(p)))
        self._thumb_labels.setdefault(path, []).append(thumb)
        v.addWidget(thumb, alignment=Qt.AlignmentFlag.AlignHCenter)

        check = QCheckBox("Move to Recycle Bin")
        check.setChecked(path in self._checked)  # never pre-checked
        check.toggled.connect(lambda on, p=path: self._on_check(p, on))
        v.addWidget(check)

        fm = QFontMetrics(self.font())
        width = THUMB
        name = QLabel(fm.elidedText(row["filename"], Qt.TextElideMode.ElideMiddle, width))
        name.setToolTip(path)
        v.addWidget(name)
        folder = QLabel(fm.elidedText(row["folder"], Qt.TextElideMode.ElideLeft, width))
        folder.setToolTip(row["folder"])
        v.addWidget(folder)

        dims = f"{row['width']}×{row['height']}" if row.get("width") and row.get("height") else "?"
        meta = QLabel(f"{dims}  ·  {_human_size(row.get('size', 0))}  ·  {_photo_date(row)}")
        meta.setToolTip(f"{dims}, {_human_size(row.get('size', 0))}")
        v.addWidget(meta)
        return card

    def _on_thumbnail_loaded(self, path: str):
        pm = self.loader.get(path)
        if pm is None:
            return
        for label in self._thumb_labels.get(path, []):
            label.setPixmap(pm)

    # --- selecting + deleting -----------------------------------------------

    def _on_check(self, path: str, on: bool):
        if on:
            self._checked.add(path)
        else:
            self._checked.discard(path)
        self._update_selected_label()

    def _update_selected_label(self):
        self.selected_label.setText(f"{len(self._checked)} photo(s) ticked for the Recycle Bin")

    def apply_selected(self):
        paths = sorted(self._checked)
        if not paths:
            QMessageBox.information(self, "Nothing selected", "No files are checked.")
            return

        reply = QMessageBox.question(
            self, "Confirm",
            f"Move {len(paths)} file(s) to the Recycle Bin?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        succeeded, failed = send_to_recycle_bin(self.db, paths, reason="Visual duplicate review")
        msg = f"Moved {len(succeeded)} file(s) to the Recycle Bin."
        if failed:
            msg += f"\n{len(failed)} failed."
        QMessageBox.information(self, "Done", msg)

        # Update what's on screen without re-running the (slow) comparison.
        done = set(succeeded)
        self._checked -= done
        self._groups = [g for g in ([r for r in grp if r["path"] not in done] for grp in self._groups)
                        if len(g) >= 2]
        self._page = min(self._page, self._page_count() - 1)
        self._render_page()
        if succeeded:
            self.files_deleted.emit()
