import os
import time

from PySide6.QtCore import (
    Qt, QSize, QDate, QTimer, Signal, QAbstractListModel, QModelIndex,
)
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit, QLabel,
    QPushButton, QDateEdit, QListView, QAbstractItemView, QStyledItemDelegate,
    QStyle, QDialog, QListWidget, QListWidgetItem,
)

from .thumbnails import ThumbnailLoader

PATH_ROLE = Qt.ItemDataRole.UserRole
FOLDER_ROLE = Qt.ItemDataRole.UserRole + 1

THUMB = 150
CELL_W, CELL_H = 180, 222

# Ignore clicks closer together than this are dropped, so the second click of
# a double-click can't ignore whatever photo slid into place after the first.
IGNORE_DEBOUNCE_SECONDS = 0.35


def _under_folder(folder: str, target_lower: str, prefix_lower: str) -> bool:
    f = folder.lower()
    return f == target_lower or f.startswith(prefix_lower)


class PhotoListModel(QAbstractListModel):
    """Holds only (path, filename, folder) per photo; thumbnails come from the
    loader on demand, so 65,000+ rows cost almost nothing until scrolled to."""

    def __init__(self, loader: ThumbnailLoader, parent=None):
        super().__init__(parent)
        self._loader = loader
        self._rows: list[tuple[str, str, str]] = []
        self._index: dict[str, int] = {}
        loader.loaded.connect(self._on_thumbnail_loaded)

    def rows(self) -> list[tuple[str, str, str]]:
        return self._rows

    def set_rows(self, db_rows):
        self.beginResetModel()
        self._rows = [(r["path"], r["filename"], r["folder"]) for r in db_rows]
        self._index = {p: i for i, (p, _, _) in enumerate(self._rows)}
        self.endResetModel()

    def remove_where(self, predicate) -> int:
        before = len(self._rows)
        self.beginResetModel()
        self._rows = [r for r in self._rows if not predicate(r)]
        self._index = {p: i for i, (p, _, _) in enumerate(self._rows)}
        self.endResetModel()
        return before - len(self._rows)

    def row_data(self, row: int) -> tuple[str, str, str]:
        return self._rows[row]

    # --- Qt model interface ------------------------------------------

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        path, filename, folder = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return filename
        if role == Qt.ItemDataRole.DecorationRole:
            return self._loader.pixmap_or_request(path)
        if role == Qt.ItemDataRole.ToolTipRole:
            return path
        if role == PATH_ROLE:
            return path
        if role == FOLDER_ROLE:
            return folder
        return None

    def _on_thumbnail_loaded(self, path: str):
        row = self._index.get(path)
        if row is not None:
            idx = self.index(row)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])


class PhotoDelegate(QStyledItemDelegate):
    """Draws a centred thumbnail, the file name, and the (tail of the) folder path."""

    def sizeHint(self, option, index):
        return QSize(CELL_W, CELL_H)

    def paint(self, painter, option, index):
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.fillRect(option.rect, option.palette.highlight())

        rect = option.rect.adjusted(4, 4, -4, -4)
        pm = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(pm, QPixmap) and not pm.isNull():
            x = rect.x() + (rect.width() - pm.width()) // 2
            y = rect.y() + (THUMB - pm.height()) // 2
            painter.drawPixmap(x, y, pm)

        text_color = option.palette.highlightedText().color() if selected else option.palette.text().color()
        fm = painter.fontMetrics()
        line_h = fm.height()
        top = rect.y() + THUMB + 6

        painter.setPen(text_color)
        name = fm.elidedText(index.data(Qt.ItemDataRole.DisplayRole) or "", Qt.TextElideMode.ElideMiddle, rect.width())
        painter.drawText(rect.x(), top, rect.width(), line_h, Qt.AlignmentFlag.AlignCenter, name)

        folder_color = text_color
        folder_color.setAlpha(170)
        painter.setPen(folder_color)
        folder = fm.elidedText(index.data(FOLDER_ROLE) or "", Qt.TextElideMode.ElideLeft, rect.width())
        painter.drawText(rect.x(), top + line_h + 2, rect.width(), line_h, Qt.AlignmentFlag.AlignCenter, folder)
        painter.restore()


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

        # --- photo grid ------------------------------------------------
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(PhotoDelegate(self.view))
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setUniformItemSizes(True)
        self.view.setGridSize(QSize(CELL_W, CELL_H))
        self.view.setSpacing(0)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setMouseTracking(True)
        self.view.clicked.connect(self._on_clicked)
        self.view.entered.connect(self._show_path)
        layout.addWidget(self.view, stretch=1)

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
