from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QSplitter, QMessageBox, QInputDialog, QLineEdit,
)

from core.albums import validate_album_name
from .photo_grid import PhotoListModel, make_photo_view, selected_paths, PATH_ROLE
from .preview_panel import PhotoPreview
from .thumbnails import ThumbnailLoader


class AlbumsTab(QWidget):
    """The directory of albums and what is in each. Albums are virtual:
    nothing is moved or copied on disk."""

    # Emitted when photos leave an album (they reappear in Browse) or an album is deleted.
    albums_changed = Signal()

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.loader = ThumbnailLoader(size=150)
        self.model = PhotoListModel(self.loader, self)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<b>Albums</b> — your virtual albums. Create them from the Browse tab; photos "
            "in an album are hidden from Browse. Nothing on disk is moved or copied."
        ))

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # left: album directory
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter albums…")
        self.filter_edit.textChanged.connect(self.refresh)
        left_l.addWidget(self.filter_edit)
        self.album_list = QListWidget()
        self.album_list.currentItemChanged.connect(self._on_album_selected)
        left_l.addWidget(self.album_list, stretch=1)
        row = QHBoxLayout()
        self.rename_btn = QPushButton("Rename…")
        self.rename_btn.clicked.connect(self.rename_album)
        self.delete_btn = QPushButton("Delete album…")
        self.delete_btn.clicked.connect(self.delete_album)
        row.addWidget(self.rename_btn)
        row.addWidget(self.delete_btn)
        left_l.addLayout(row)
        splitter.addWidget(left)

        # middle: photos in the chosen album
        mid = QWidget()
        mid_l = QVBoxLayout(mid)
        mid_l.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel("")
        mid_l.addWidget(self.title_label)
        self.view = make_photo_view(self.model)
        self.view.entered.connect(self._show_path)
        self.view.doubleClicked.connect(self._open_photo)
        mid_l.addWidget(self.view, stretch=1)
        row2 = QHBoxLayout()
        self.remove_btn = QPushButton("Remove selected from album")
        self.remove_btn.clicked.connect(self.remove_selected)
        self.selected_label = QLabel("")
        row2.addWidget(self.remove_btn)
        row2.addStretch()
        row2.addWidget(self.selected_label)
        mid_l.addLayout(row2)
        self.path_label = QLabel(" ")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        mid_l.addWidget(self.path_label)
        splitter.addWidget(mid)

        # right: big preview
        self.preview = PhotoPreview(db, small_loader=self.loader)
        splitter.addWidget(self.preview)
        self.view.selectionModel().currentChanged.connect(self._on_current_changed)
        self.view.selectionModel().selectionChanged.connect(self._update_buttons)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([260, 560, 360])
        layout.addWidget(splitter, stretch=1)

        self.refresh()

    def shutdown(self):
        self.loader.shutdown()
        self.preview.shutdown()

    # --- album directory ----------------------------------------------------

    def current_album(self):
        item = self.album_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None  # (id, name)

    def refresh(self, *_):
        keep = self.current_album()
        needle = self.filter_edit.text().strip().lower()
        self.album_list.blockSignals(True)
        self.album_list.clear()
        select_row = -1
        for a in self.db.list_albums():
            if needle and needle not in a["name"].lower():
                continue
            item = QListWidgetItem(f"{a['name']}    ({a['photo_count']:,})")
            item.setData(Qt.ItemDataRole.UserRole, (a["id"], a["name"]))
            self.album_list.addItem(item)
            if keep and a["id"] == keep[0]:
                select_row = self.album_list.count() - 1
        if select_row < 0 and self.album_list.count():
            select_row = 0
        if select_row >= 0:
            self.album_list.setCurrentRow(select_row)
        self.album_list.blockSignals(False)
        if self.album_list.count() == 0:
            empty = QListWidgetItem("No albums yet.\nSelect photos in Browse and\nclick “Create album…”.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.album_list.addItem(empty)
        self._load_album()

    def _on_album_selected(self, *_):
        self._load_album()

    def _load_album(self):
        cur = self.current_album()
        self.loader.cancel_pending()
        self.preview.clear()
        if not cur:
            self.model.set_rows([])
            self.title_label.setText("Select an album on the left.")
        else:
            rows = self.db.album_photos(cur[0])
            self.model.set_rows(rows)
            self.title_label.setText(f"<b>{cur[1]}</b> — {len(rows):,} photo(s)")
        self.path_label.setText(" ")
        self._update_buttons()

    def _update_buttons(self, *_):
        has_album = self.current_album() is not None
        n = len(self.view.selectionModel().selectedIndexes())
        self.rename_btn.setEnabled(has_album)
        self.delete_btn.setEnabled(has_album)
        self.remove_btn.setEnabled(has_album and n > 0)
        self.selected_label.setText(f"{n:,} selected" if n else "")

    # --- photos -------------------------------------------------------------

    def _show_path(self, index):
        if index.isValid():
            self.path_label.setText(index.data(PATH_ROLE) or " ")

    def _on_current_changed(self, current, _previous):
        if current.isValid():
            path = current.data(PATH_ROLE)
            if path:
                self.preview.show_photo(path)

    def _open_photo(self, index):
        if index.isValid():
            QDesktopServices.openUrl(QUrl.fromLocalFile(index.data(PATH_ROLE)))

    def remove_selected(self):
        cur = self.current_album()
        paths = selected_paths(self.view)
        if not cur or not paths:
            return
        self.db.remove_photos_from_album(cur[0], paths)
        self.refresh()
        self.albums_changed.emit()

    # --- album actions ----------------------------------------------------------

    def rename_album(self):
        cur = self.current_album()
        if not cur:
            return
        album_id, old = cur
        name = old
        while True:
            name, ok = QInputDialog.getText(
                self, "Rename album",
                "New name (date prefix, space, name — e.g. 23_06 Beach Weekend):",
                QLineEdit.EchoMode.Normal, name,
            )
            if not ok or name == old:
                return
            problem = validate_album_name(name)
            if not problem:
                try:
                    self.db.rename_album(album_id, name)
                except ValueError as e:
                    problem = str(e)
                else:
                    break
            QMessageBox.warning(self, "Can't use that name", problem)
        self.refresh()
        self.albums_changed.emit()

    def delete_album(self):
        cur = self.current_album()
        if not cur:
            return
        reply = QMessageBox.question(
            self, "Delete album",
            f"Delete the album “{cur[1]}”?\n\nOnly the album is removed. Your photos stay "
            f"exactly where they are on disk, and return to the Browse tab.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.db.delete_album(cur[0])
        self.refresh()
        self.albums_changed.emit()
