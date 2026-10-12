import os
import time

from PySide6.QtCore import Qt, QDate, QTimer, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit, QLabel,
    QPushButton, QDateEdit, QAbstractItemView, QDialog, QListWidget,
    QListWidgetItem, QSplitter, QMessageBox,
)

from .thumbnails import ThumbnailLoader
from .photo_grid import (  # noqa: F401  (re-exported for other modules/tests)
    PhotoListModel, PhotoDelegate, make_photo_view, selected_paths,
    PATH_ROLE, FOLDER_ROLE, THUMB, CELL_W, CELL_H,
)
from .preview_panel import PhotoPreview
from .album_dialogs import CreateAlbumDialog, AddToAlbumDialog

# Ignore clicks closer together than this are dropped, so the second click of
# a double-click can't ignore whatever photo slid into place after the first.
IGNORE_DEBOUNCE_SECONDS = 0.35


def _under_folder(folder: str, target_lower: str, prefix_lower: str) -> bool:
    f = folder.lower()
    return f == target_lower or f.startswith(prefix_lower)


class IgnoredDialog(QDialog):
    """Lists everything the user has ignored, with a way to bring it back."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.changed = False
        self.setWindowTitle("Ignored from future scans")
        self.resize(780, 420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Everything here is skipped by future scans and hidden from every tab. "
            "A folder entry also covers all of its sub-folders.<br>"
            "Select entries and click <b>Un-ignore</b> to bring them back."
        ))
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.list)

        row = QHBoxLayout()
        self.unignore_btn = QPushButton("Un-ignore selected")
        self.unignore_btn.clicked.connect(self._unignore)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row.addWidget(self.unignore_btn)
        row.addStretch()
        row.addWidget(close_btn)
        layout.addLayout(row)
        self._reload()

    def _reload(self):
        self.list.clear()
        rules = self.db.list_ignore_rules()
        if not rules:
            item = QListWidgetItem("Nothing is ignored yet.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
        for r in rules:
            label = ("FOLDER   " if r["kind"] == "folder" else "FILE         ") + r["path"]
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, (r["kind"], r["path"]))
            self.list.addItem(item)
        self.unignore_btn.setEnabled(bool(rules))

    def _unignore(self):
        for item in self.list.selectedItems():
            data = item.data(Qt.ItemDataRole.UserRole)
            if data:
                self.db.remove_ignore_rule(*data)
                self.changed = True
        self._reload()


class BrowseTab(QWidget):
    # Emitted whenever ignore rules change, so other tabs can refresh.
    ignores_changed = Signal()
    # Emitted after photos were put into an album (Albums tab must refresh).
    albums_changed = Signal()

    def __init__(self, db):
        super().__init__()
        self.db = db
        self._undo_stack: list[tuple[str, str]] = []
        self._last_ignore_time = 0.0

        self.loader = ThumbnailLoader(size=THUMB)
        self.model = PhotoListModel(self.loader, self)

        layout = QVBoxLayout(self)

        # --- filters -------------------------------------------------
        filter_row = QHBoxLayout()

        filter_row.addWidget(QLabel("Folder:"))
        self.folder_combo = QComboBox()
        self.folder_combo.addItem("All folders", None)
        self.folder_combo.currentIndexChanged.connect(self.run_filter)
        filter_row.addWidget(self.folder_combo, stretch=2)

        filter_row.addWidget(QLabel("From:"))
        self.date_from = QDateEdit(calendarPopup=True)
        self.date_from.setDate(QDate(2000, 1, 1))
        self.date_from.dateChanged.connect(self.run_filter)
        filter_row.addWidget(self.date_from)

        filter_row.addWidget(QLabel("To:"))
        self.date_to = QDateEdit(calendarPopup=True)
        self.date_to.setDate(QDate.currentDate())
        self.date_to.dateChanged.connect(self.run_filter)
        filter_row.addWidget(self.date_to)

        self.use_dates = QPushButton("Apply date range")
        self.use_dates.setCheckable(True)
        self.use_dates.toggled.connect(self.run_filter)
        filter_row.addWidget(self.use_dates)

        filter_row.addWidget(QLabel("Search:"))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("filename or path contains…")
        filter_row.addWidget(self.search_box, stretch=2)

        layout.addLayout(filter_row)

        # Typing in the search box re-queries; wait for a short pause so a
        # 65,000-photo collection isn't re-queried on every keystroke.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.run_filter)
        self.search_box.textChanged.connect(lambda _text: self._search_timer.start())

        # --- ignore controls -------------------------------------------
        ignore_row = QHBoxLayout()
        armed_style = (
            "QPushButton:checked { background-color: #c0392b; color: white; "
            "font-weight: bold; border: 1px solid #7b241c; }"
        )
        self.ignore_file_btn = QPushButton("Ignore FILE from future scans")
        self.ignore_file_btn.setCheckable(True)
        self.ignore_file_btn.setStyleSheet(armed_style)
        self.ignore_file_btn.toggled.connect(lambda on: self._on_mode_toggled("file", on))
        self.ignore_folder_btn = QPushButton("Ignore FOLDER from future scans")
        self.ignore_folder_btn.setCheckable(True)
        self.ignore_folder_btn.setStyleSheet(armed_style)
        self.ignore_folder_btn.toggled.connect(lambda on: self._on_mode_toggled("folder", on))
        self.undo_btn = QPushButton("Undo last ignore")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self.undo_last_ignore)
        self.manage_btn = QPushButton("Manage ignored…")
        self.manage_btn.clicked.connect(self.manage_ignored)
        for w in (self.ignore_file_btn, self.ignore_folder_btn, self.undo_btn, self.manage_btn):
            ignore_row.addWidget(w)
        ignore_row.addStretch()
        layout.addLayout(ignore_row)

        self.mode_label = QLabel("")
        self.mode_label.setWordWrap(True)
        layout.addWidget(self.mode_label)

        self.count_label = QLabel("")
        layout.addWidget(self.count_label)

        # --- album actions ---------------------------------------------
        album_row = QHBoxLayout()
        self.select_all_btn = QPushButton("Select all shown")
        self.select_all_btn.clicked.connect(lambda: self.view.selectAll())
        self.clear_sel_btn = QPushButton("Clear selection")
        self.clear_sel_btn.clicked.connect(lambda: self.view.clearSelection())
        self.create_album_btn = QPushButton("Create album from selected…")
        self.create_album_btn.clicked.connect(self.create_album_from_selected)
        self.add_album_btn = QPushButton("Add selected to album…")
        self.add_album_btn.clicked.connect(self.add_selected_to_album)
        self.selected_label = QLabel("")
        for w in (self.select_all_btn, self.clear_sel_btn, self.create_album_btn, self.add_album_btn):
            album_row.addWidget(w)
        album_row.addStretch()
        album_row.addWidget(self.selected_label)
        layout.addLayout(album_row)

        # --- photo grid + preview panel ----------------------------------
        self.view = make_photo_view(self.model)
        self.view.clicked.connect(self._on_clicked)
        self.view.doubleClicked.connect(self._on_double_clicked)
        self.view.entered.connect(self._show_path)

        self.preview = PhotoPreview(self.db, small_loader=self.loader)
        self.view.selectionModel().currentChanged.connect(self._on_current_changed)
        self.view.selectionModel().selectionChanged.connect(self._update_selection_ui)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.view)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([700, 400])
        layout.addWidget(splitter, stretch=1)

        # Full path of the photo under the mouse / selected, always visible.
        self.path_label = QLabel(" ")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.path_label)

        self._update_mode_ui()

    # --- data ----------------------------------------------------------

    @property
    def results(self):
        """The (path, filename, folder) rows currently shown."""
        return self.model.rows()

    def shutdown(self):
        self.loader.shutdown()
        self.preview.shutdown()

    def refresh_folder_list(self):
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo.addItem("All folders", None)
        for folder in self.db.distinct_folders():
            self.folder_combo.addItem(folder, folder)
        self.folder_combo.blockSignals(False)
        self.run_filter()

    def run_filter(self, *_args):
        folder = self.folder_combo.currentData()
        date_from = self.date_from.date().toString("yyyy-MM-dd") if self.use_dates.isChecked() else None
        date_to = self.date_to.date().toString("yyyy-MM-dd") if self.use_dates.isChecked() else None
        search = self.search_box.text().strip() or None

        rows = self.db.browse(
            folder=folder, date_from=date_from, date_to=date_to, search_text=search
        )
        self.loader.cancel_pending()
        self.model.set_rows(rows)
        self.count_label.setText(f"{len(rows):,} photo(s) match — scroll to see them all")
        self.path_label.setText(" ")
        self.preview.clear()
        self._update_selection_ui()

    def _show_path(self, index):
        if index.isValid():
            self.path_label.setText(index.data(PATH_ROLE) or " ")

    # --- ignore modes ----------------------------------------------------

    def _current_mode(self) -> str | None:
        if self.ignore_file_btn.isChecked():
            return "file"
        if self.ignore_folder_btn.isChecked():
            return "folder"
        return None

    def _on_mode_toggled(self, which: str, checked: bool):
        if checked:  # only one mode can be armed at a time
            other = self.ignore_folder_btn if which == "file" else self.ignore_file_btn
            if other.isChecked():
                other.blockSignals(True)
                other.setChecked(False)
                other.blockSignals(False)
        self._update_mode_ui()

    def _update_mode_ui(self):
        mode = self._current_mode()
        if mode == "file":
            self.mode_label.setText(
                "<b style='color:#c0392b'>IGNORE-FILE MODE IS ON</b> — every photo you click is "
                "ignored immediately (no confirmation) and skipped by future scans. "
                "Click the red button again to turn it off."
            )
            self.view.viewport().setCursor(Qt.CursorShape.CrossCursor)
        elif mode == "folder":
            self.mode_label.setText(
                "<b style='color:#c0392b'>IGNORE-FOLDER MODE IS ON</b> — clicking a photo ignores "
                "its whole folder (and sub-folders) immediately, with no confirmation. "
                "Click the red button again to turn it off."
            )
            self.view.viewport().setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.mode_label.setText(
                "Tip: turn on an ignore button above, then click photos to ignore them "
                "from future scans. <b>Undo last ignore</b> reverses the most recent one."
            )
            self.view.viewport().unsetCursor()

    # --- preview / selection ------------------------------------------------

    def _on_current_changed(self, current, _previous):
        # While an ignore button is armed a click means "ignore", not "look at".
        if not current.isValid() or self._current_mode() is not None:
            return
        path = current.data(PATH_ROLE)
        if path:
            self.preview.show_photo(path)

    def _on_double_clicked(self, index):
        if index.isValid() and self._current_mode() is None:
            path = index.data(PATH_ROLE)
            if path:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _update_selection_ui(self, *_):
        n = len(self.view.selectionModel().selectedIndexes())
        self.selected_label.setText(f"{n:,} selected" if n else "")
        for btn in (self.create_album_btn, self.add_album_btn):
            btn.setEnabled(n > 0)
        self.clear_sel_btn.setEnabled(n > 0)

    # --- albums ---------------------------------------------------------------

    def _selected_photo_rows(self):
        paths = selected_paths(self.view)
        if not paths:
            QMessageBox.information(
                self, "Nothing selected",
                "Select one or more photos first (Ctrl-click or Shift-click to pick several, "
                "or use “Select all shown”)."
            )
            return [], []
        return paths, self.db.photos_by_paths(paths)

    def create_album_from_selected(self):
        paths, rows = self._selected_photo_rows()
        if not paths:
            return
        dlg = CreateAlbumDialog(self.db, rows, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._put_in_album(dlg.album_name, paths, dlg.existing_album_id)

    def add_selected_to_album(self):
        paths, _rows = self._selected_photo_rows()
        if not paths:
            return
        dlg = AddToAlbumDialog(self.db, len(paths), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if dlg.wants_new:
            self.create_album_from_selected()
            return
        self._put_in_album(dlg.album_name, paths, dlg.album_id)

    def _put_in_album(self, name: str, paths: list[str], album_id):
        created = album_id is None
        if created:
            try:
                album_id = self.db.create_album(name)
            except ValueError as e:  # created elsewhere in the meantime
                QMessageBox.warning(self, "Album exists", str(e))
                return
        self.db.add_photos_to_album(album_id, paths)

        moved = set(paths)
        self._remove_rows(lambda r: r[0] in moved)
        self._resync_folder_combo()
        self.count_label.setText(f"{len(self.model.rows()):,} photo(s) match — scroll to see them all")
        self.path_label.setText(" ")
        if self.preview.current_path in moved:
            self.preview.clear()
        self._update_selection_ui()
        self.mode_label.setText(
            f"<b>{'Created album' if created else 'Added to album'} “{name}”</b> with "
            f"{len(moved):,} photo(s). They no longer show here - find them in the "
            f"<b>Albums</b> tab."
        )
        self.albums_changed.emit()

    def _resync_folder_combo(self):
        """Drop folders that no longer have photos left to show (all albumed/ignored)."""
        current = self.folder_combo.currentData()
        folders = self.db.distinct_folders()
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo.addItem("All folders", None)
        for folder in folders:
            self.folder_combo.addItem(folder, folder)
        idx = self.folder_combo.findData(current) if current is not None else 0
        self.folder_combo.setCurrentIndex(max(idx, 0))
        self.folder_combo.blockSignals(False)
        if current is not None and idx < 0:
            self.run_filter()  # the folder we were viewing is now empty

    def _on_clicked(self, index):
        self._show_path(index)
        mode = self._current_mode()
        if mode is None or not index.isValid():
            return
        now = time.monotonic()
        if now - self._last_ignore_time < IGNORE_DEBOUNCE_SECONDS:
            return
        self._last_ignore_time = now

        path, _name, folder = self.model.row_data(index.row())
        target = path if mode == "file" else folder
        self._ignore(mode, target)
        self._last_ignore_time = time.monotonic()

    def _ignore(self, mode: str, target: str):
        hidden = self.db.add_ignore_rule(mode, target)
        self._undo_stack.append((mode, target))
        self.undo_btn.setEnabled(True)

        if mode == "file":
            target_key = target.lower()
            removed = self._remove_rows(lambda r: r[0].lower() == target_key)
            self.mode_label.setText(
                f"<b>Ignored file</b> {target} — hidden and skipped from future scans. "
                f"<i>(Ignore mode is still on.)</i>"
            )
        else:
            target_lower = target.lower()
            prefix_lower = target.rstrip("\\/").lower() + os.sep
            removed = self._remove_rows(lambda r: _under_folder(r[2], target_lower, prefix_lower))
            self._remove_folder_from_combo(target_lower, prefix_lower)
            self.mode_label.setText(
                f"<b>Ignored folder</b> {target} — {max(removed, hidden):,} photo(s) hidden and "
                f"skipped from future scans. <i>(Ignore mode is still on.)</i>"
            )
        self.count_label.setText(f"{len(self.model.rows()):,} photo(s) match — scroll to see them all")
        self.path_label.setText(" ")
        if self.preview.current_path and not self.model.has_path(self.preview.current_path):
            self.preview.clear()
        self._update_selection_ui()
        self.ignores_changed.emit()

    def _remove_rows(self, predicate) -> int:
        bar = self.view.verticalScrollBar()
        position = bar.value()
        removed = self.model.remove_where(predicate)
        # Keep the user's place in the list after the rows disappear.
        QTimer.singleShot(0, lambda: bar.setValue(position))
        return removed

    def _remove_folder_from_combo(self, target_lower: str, prefix_lower: str):
        current = self.folder_combo.currentData()
        self.folder_combo.blockSignals(True)
        for i in range(self.folder_combo.count() - 1, 0, -1):
            data = self.folder_combo.itemData(i)
            if data and _under_folder(data, target_lower, prefix_lower):
                self.folder_combo.removeItem(i)
        self.folder_combo.blockSignals(False)
        if current is not None and self.folder_combo.findData(current) < 0:
            self.folder_combo.setCurrentIndex(0)  # triggers run_filter

    def undo_last_ignore(self):
        if not self._undo_stack:
            return
        mode, target = self._undo_stack.pop()
        self.db.remove_ignore_rule(mode, target)
        self.undo_btn.setEnabled(bool(self._undo_stack))
        self.refresh_folder_list()
        self.mode_label.setText(f"<b>Undone:</b> {target} is no longer ignored.")
        self.ignores_changed.emit()

    def manage_ignored(self):
        dlg = IgnoredDialog(self.db, self)
        dlg.exec()
        if dlg.changed:
            self._undo_stack.clear()
            self.undo_btn.setEnabled(False)
            self.refresh_folder_list()
            self.ignores_changed.emit()
