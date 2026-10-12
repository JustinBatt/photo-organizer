"""Dialogs for creating albums and choosing an existing one."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QListWidget, QListWidgetItem, QDialogButtonBox,
)

from core.albums import (
    suggest_for_photos, validate_album_name, compose_album_name,
)


class CreateAlbumDialog(QDialog):
    """Name a new album. The name is date prefix + name; both are suggested
    from the photos (prefix from when they were taken, name from their folders)."""

    def __init__(self, db, photo_rows, parent=None):
        super().__init__(parent)
        self.db = db
        self.album_name = ""
        self.existing_album_id = None  # set when the chosen name already exists
        self.setWindowTitle("Create album")
        self.setMinimumWidth(520)

        suggestion = suggest_for_photos(photo_rows)
        self._suggestion = suggestion

        layout = QVBoxLayout(self)
        intro = QLabel(
            f"<b>{len(photo_rows):,} photo(s)</b> will go into this album. "
            "Nothing on disk is moved or copied; the photos just leave the Browse list."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.format_combo = QComboBox()
        self.format_combo.addItem(f"YY_MM  →  {suggestion.yy_mm}   (recommended)", suggestion.yy_mm)
        self.format_combo.addItem(f"Year only  →  {suggestion.year}", suggestion.year)
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        form.addRow("Date prefix style:", self.format_combo)

        self.prefix_edit = QLineEdit(suggestion.yy_mm)
        self.prefix_edit.setMaxLength(5)
        self.prefix_edit.textChanged.connect(self._validate)
        form.addRow("Date prefix:", self.prefix_edit)

        self.name_combo = QComboBox()
        self.name_combo.setEditable(True)
        self.name_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for name, count in suggestion.names:
            self.name_combo.addItem(name)
            self.name_combo.setItemData(
                self.name_combo.count() - 1, f"{count:,} photo(s) from a folder with this name",
                Qt.ItemDataRole.ToolTipRole,
            )
        if suggestion.names:
            self.name_combo.setCurrentIndex(0)
        else:
            self.name_combo.setCurrentText("")
        self.name_combo.lineEdit().setPlaceholderText("e.g. Beach Weekend")
        self.name_combo.currentTextChanged.connect(self._validate)
        form.addRow("Album name:", self.name_combo)
        layout.addLayout(form)

        hint = f"Date prefix suggested from {suggestion.date_source}."
        if suggestion.names:
            top = ", ".join(f"{n} ({c:,})" for n, c in suggestion.names[:4])
            hint += f"<br>Names suggested from the photos' folders: {top}."
        else:
            hint += "<br>No folder name could be suggested - type a name."
        self.hint_label = QLabel(hint)
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: #888;")
        layout.addWidget(self.hint_label)

        self.preview_label = QLabel("")
        self.preview_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        self.preview_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.preview_label)
        self.problem_label = QLabel("")
        self.problem_label.setWordWrap(True)
        layout.addWidget(self.problem_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._validate()

    def _on_format_changed(self):
        self.prefix_edit.setText(self.format_combo.currentData())

    def current_name(self) -> str:
        return compose_album_name(self.prefix_edit.text(), self.name_combo.currentText())

    def _validate(self, *_):
        name = self.current_name()
        self.preview_label.setText(f"Album: {name}")
        problem = validate_album_name(name)
        ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.existing_album_id = None
        if problem:
            self.problem_label.setStyleSheet("color: #c0392b;")
            self.problem_label.setText(problem)
            ok_btn.setEnabled(False)
            ok_btn.setText("Create album")
            return
        existing = self.db.album_id_by_name(name)
        if existing is not None:
            self.existing_album_id = existing
            self.problem_label.setStyleSheet("color: #b9770e;")
            self.problem_label.setText("An album with this name already exists - the photos will be added to it.")
            ok_btn.setText("Add to existing album")
        else:
            self.problem_label.setText("")
            ok_btn.setText("Create album")
        ok_btn.setEnabled(True)

    def _accept(self):
        name = self.current_name()
        if validate_album_name(name):
            return
        self.album_name = name
        self.accept()


class AddToAlbumDialog(QDialog):
    """Pick one of the albums that already exist (or ask for a new one)."""

    def __init__(self, db, photo_count: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.album_id = None
        self.album_name = ""
        self.wants_new = False
        self.setWindowTitle("Add to album")
        self.resize(460, 420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Choose the album for <b>{photo_count:,} photo(s)</b>:"))

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Type to filter albums…")
        self.filter_edit.textChanged.connect(self._reload)
        layout.addWidget(self.filter_edit)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _item: self._choose())
        self.list.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.list, stretch=1)

        row = QHBoxLayout()
        self.new_btn = QPushButton("New album…")
        self.new_btn.clicked.connect(self._new)
        self.add_btn = QPushButton("Add to this album")
        self.add_btn.setDefault(True)
        self.add_btn.clicked.connect(self._choose)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(self.new_btn)
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(self.add_btn)
        layout.addLayout(row)

        self._albums = self.db.list_albums()
        self._reload()

    def _reload(self, *_):
        needle = self.filter_edit.text().strip().lower()
        self.list.clear()
        for a in self._albums:
            if needle and needle not in a["name"].lower():
                continue
            item = QListWidgetItem(f"{a['name']}    ({a['photo_count']:,})")
            item.setData(Qt.ItemDataRole.UserRole, (a["id"], a["name"]))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        elif not self._albums:
            item = QListWidgetItem("No albums yet - click “New album…”.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
        self._update_buttons()

    def _update_buttons(self):
        self.add_btn.setEnabled(bool(self.list.selectedItems()) and
                                self.list.selectedItems()[0].data(Qt.ItemDataRole.UserRole) is not None)

    def _choose(self):
        items = self.list.selectedItems()
        data = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        if data:
            self.album_id, self.album_name = data
            self.accept()

    def _new(self):
        self.wants_new = True
        self.accept()
