from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QSplitter, QMessageBox, QInputDialog, QLineEdit,
)

from core.albums import validate_album_name, normalize_album_path
from .album_tree import AlbumTree
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
        self.album_tree = AlbumTree()
        self.album_tree.currentItemChanged.connect(self._on_album_selected)
        self.album_tree.photos_dropped.connect(self._on_photos_dropped)
        left_l.addWidget(self.album_tree, stretch=1)
        self.new_btn = QPushButton("New album / sub-album…")
        self.new_btn.clicked.connect(self.new_album)
        left_l.addWidget(self.new_btn)
        row = QHBoxLayout()
        self.rename_btn = QPushButton("Rename / move…")
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
        self.include_sub = QCheckBox("Also show photos from sub-albums")
        self.include_sub.setChecked(True)
        self.include_sub.toggled.connect(lambda _on: self._load_album())
        mid_l.addWidget(self.include_sub)
        self.view = make_photo_view(self.model)
        self.view.entered.connect(self._show_path)
        self.view.doubleClicked.connect(self._open_photo)
        self.view.rating_requested.connect(self._on_rating_requested)
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
        return self.album_tree.current_album()  # (id, full name) or None

    def refresh(self, *_):
        needle = self.filter_edit.text()
        albums = self.db.list_albums()
        self.album_tree.blockSignals(True)
        had_selection = self.album_tree.load(albums, needle)
        if not had_selection and self.album_tree.topLevelItemCount():
            self.album_tree.setCurrentItem(self.album_tree.topLevelItem(0))
        self.album_tree.blockSignals(False)
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
            rows = self.db.album_photos(cur[0], include_sub=self.include_sub.isChecked())
            self.model.set_rows(rows)
            self.title_label.setText(f"<b>{cur[1]}</b> — {len(rows):,} photo(s)")
        self.path_label.setText(" ")
        self._update_buttons()

    def _update_buttons(self, *_):
        has_album = self.current_album() is not None
        n = len(self.view.selectionModel().selectedIndexes())
        self.rename_btn.setEnabled(has_album)
        self.new_btn.setEnabled(True)
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
        self.db.remove_photos_from_album(cur[0], paths, include_sub=self.include_sub.isChecked())
        self.refresh()
        self.albums_changed.emit()

    def _on_rating_requested(self, paths: list, rating: int):
        self.db.set_ratings(paths, rating)
        for p in paths:
            self.model.set_rating(p, rating)
        if self.preview.current_path in paths:
            self.preview.refresh_details()

    def _on_photos_dropped(self, album_id: int, name: str, paths: list):
        added = self.db.add_photos_to_album(album_id, paths)
        self.refresh()
        self.title_label.setText(self.title_label.text() + f"   (added {added} to {name})")
        self.albums_changed.emit()

    # --- album actions ----------------------------------------------------------

    def _ask_name(self, title: str, label: str, text: str):
        """Ask for an album path until it's valid; returns the name or None."""
        while True:
            text, ok = QInputDialog.getText(
                self, title, label, QLineEdit.EchoMode.Normal, text)
            if not ok:
                return None
            name = normalize_album_path(text)
            problem = validate_album_name(name)
            if not problem:
                return name
            QMessageBox.warning(self, "Can't use that name", problem)
            text = name

    def new_album(self):
        cur = self.current_album()
        name = None
        text = (cur[1] + "/") if cur else ""
        while True:
            name = self._ask_name(
                "New album",
                "Album name. Use / to put it inside another album, e.g.  23_06 Trip/Day 1\n"
                "(a date at the start, like 23_06, is recommended but optional):", text)
            if name is None:
                return
            try:
                self.db.create_album(name)
                break
            except ValueError as e:
                QMessageBox.warning(self, "Can't use that name", str(e))
                text = name
        self.refresh()
        self.albums_changed.emit()

    def rename_album(self):
        cur = self.current_album()
        if not cur:
            return
        album_id, old = cur
        text = old
        while True:
            name = self._ask_name(
                "Rename / move album",
                "New name. Change the part before the last / to move it under another album.\n"
                "Its sub-albums come along.", text)
            if name is None or name == old:
                return
            try:
                self.db.rename_album(album_id, name)
                break
            except ValueError as e:
                QMessageBox.warning(self, "Can't use that name", str(e))
                text = name
        self.refresh()
        self.albums_changed.emit()

    def delete_album(self):
        cur = self.current_album()
        if not cur:
            return
        subs = len(self.db.album_subtree(cur[0])) - 1
        extra = f" and its {subs} sub-album(s)" if subs else ""
        reply = QMessageBox.question(
            self, "Delete album",
            f"Delete the album “{cur[1]}”{extra}?\n\nOnly the album{'s are' if subs else ' is'} "
            f"removed. Your photos stay exactly where they are on disk, and return to the "
            f"Browse tab.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.db.delete_album(cur[0])
        self.refresh()
        self.albums_changed.emit()
