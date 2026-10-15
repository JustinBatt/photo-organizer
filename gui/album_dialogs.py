"""One-screen dialog for filing photos into an album (existing or new, any level)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QCheckBox, QGroupBox, QSplitter, QWidget,
)

from core.albums import (
    suggest_for_photos, validate_album_name, compose_album_name, has_date_prefix,
)
from .album_tree import AlbumTree


class AlbumDialog(QDialog):
    """
    Everything in one screen:
      left  - your albums (a tree, so sub-albums are visible); pick one to add to it
      right - or make a new album: date prefix (recommended, optional), name
              (suggested from the photos' folder), optionally inside the picked album.

    After exec(): `album_id` + `album_name` say where the photos go. `created` is
    True when a new album has to be made first (album_id is None then), unless the
    typed name already exists, in which case album_id is that album.
    """

    def __init__(self, db, photo_rows, parent=None):
        super().__init__(parent)
        self.db = db
        self.album_id = None
        self.album_name = ""
        self.created = False
        self.setWindowTitle("Add to album")
        self.resize(820, 520)

        suggestion = suggest_for_photos(photo_rows)
        self._suggestion = suggestion

        layout = QVBoxLayout(self)
        intro = QLabel(
            f"<b>{len(photo_rows):,} photo(s)</b> → pick an album on the left, or make a new "
            "one on the right. Nothing on disk is moved or copied; the photos just leave "
            "the Browse list."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        split = QSplitter(Qt.Orientation.Horizontal)

        # --- left: existing albums ---
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("<b>Your albums</b>"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Type to filter albums…")
        self.filter_edit.textChanged.connect(self._reload)
        ll.addWidget(self.filter_edit)
        self.tree = AlbumTree()
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.itemDoubleClicked.connect(lambda *_: self._add_to_selected())
        ll.addWidget(self.tree, stretch=1)
        self.add_btn = QPushButton("Add to the selected album")
        self.add_btn.clicked.connect(self._add_to_selected)
        ll.addWidget(self.add_btn)
        split.addWidget(left)

        # --- right: new album ---
        box = QGroupBox("…or create a new album")
        form = QFormLayout(box)

        self.inside_check = QCheckBox("Inside the selected album (as a sub-album)")
        self.inside_check.toggled.connect(self._on_inside_toggled)
        form.addRow(self.inside_check)

        self.prefix_check = QCheckBox("Start the name with a date (recommended)")
        self.prefix_check.setChecked(True)
        self.prefix_check.toggled.connect(self._on_prefix_toggled)
        form.addRow(self.prefix_check)

        self.format_combo = QComboBox()
        self.format_combo.addItem(f"YY_MM  →  {suggestion.yy_mm}", suggestion.yy_mm)
        self.format_combo.addItem(f"Year only  →  {suggestion.year}", suggestion.year)
        self.format_combo.currentIndexChanged.connect(
            lambda: self.prefix_edit.setText(self.format_combo.currentData()))
        form.addRow("Date style:", self.format_combo)

        self.prefix_edit = QLineEdit(suggestion.yy_mm)
        self.prefix_edit.setMaxLength(5)
        self.prefix_edit.textChanged.connect(self._validate)
        form.addRow("Date:", self.prefix_edit)

        self.name_combo = QComboBox()
        self.name_combo.setEditable(True)
        self.name_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for name, count in suggestion.names:
            self.name_combo.addItem(name)
            self.name_combo.setItemData(
                self.name_combo.count() - 1, f"{count:,} photo(s) from a folder with this name",
                Qt.ItemDataRole.ToolTipRole)
        if suggestion.names:
            self.name_combo.setCurrentIndex(0)
        else:
            self.name_combo.setCurrentText("")
        self.name_combo.lineEdit().setPlaceholderText("e.g. Beach Weekend")
        self.name_combo.currentTextChanged.connect(self._validate)
        form.addRow("Name:", self.name_combo)

        hint = f"Date suggested from {suggestion.date_source}."
        if suggestion.names:
            top = ", ".join(f"{n} ({c:,})" for n, c in suggestion.names[:4])
            hint += f"<br>Name suggested from the photos' folders: {top}."
        self.hint_label = QLabel(hint)
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: #888;")
        form.addRow(self.hint_label)

        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        self.preview_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.preview_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(self.preview_label)
        self.problem_label = QLabel("")
        self.problem_label.setWordWrap(True)
        form.addRow(self.problem_label)

        self.create_btn = QPushButton("Create album and add")
        self.create_btn.setDefault(True)
        self.create_btn.clicked.connect(self._create)
        form.addRow(self.create_btn)
        split.addWidget(box)
        split.setSizes([360, 440])
        layout.addWidget(split, stretch=1)

        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        layout.addLayout(row)

        self._albums = []
        self._reload()
        self._on_selection_changed()

    # --- helpers --------------------------------------------------------------

    def _reload(self, *_):
        self._albums = self.db.list_albums()
        self.tree.load(self._albums, self.filter_edit.text())
        self._on_selection_changed()

    def _selected(self):
        return self.tree.current_album()

    def _on_selection_changed(self):
        sel = self._selected()
        self.add_btn.setEnabled(sel is not None)
        self.add_btn.setText(f"Add to “{sel[1]}”" if sel else "Add to the selected album")
        self.inside_check.setEnabled(sel is not None)
        if sel is None and self.inside_check.isChecked():
            self.inside_check.setChecked(False)
        self._validate()

    def _on_inside_toggled(self, on: bool):
        # a sub-album usually doesn't need its own date; a top-level album usually does
        self.prefix_check.setChecked(not on)
        self._validate()

    def _on_prefix_toggled(self, on: bool):
        self.prefix_edit.setEnabled(on)
        self.format_combo.setEnabled(on)
        self._validate()

    def current_name(self) -> str:
        sel = self._selected()
        parent = sel[1] if (sel and self.inside_check.isChecked()) else ""
        prefix = self.prefix_edit.text() if self.prefix_check.isChecked() else ""
        return compose_album_name(prefix, self.name_combo.currentText(), parent)

    def _validate(self, *_):
        name = self.current_name()
        has_text = bool(self.name_combo.currentText().strip())
        self.preview_label.setText(f"New album: {name}" if has_text else "")
        self.create_btn.setEnabled(False)
        self.create_btn.setText("Create album and add")
        if not has_text:
            self.problem_label.setText("")
            return
        problem = validate_album_name(name)
        if problem:
            self.problem_label.setStyleSheet("color: #c0392b;")
            self.problem_label.setText(problem)
            return
        existing = self.db.album_id_by_name(name)
        if existing is not None:
            self.problem_label.setStyleSheet("color: #b9770e;")
            self.problem_label.setText("An album with this name already exists - the photos will be added to it.")
            self.create_btn.setText("Add to that album")
        elif not has_date_prefix(name) and "/" not in name:
            self.problem_label.setStyleSheet("color: #888;")
            self.problem_label.setText("Tip: a date at the start (like 23_06) keeps albums in date order. Optional.")
        else:
            self.problem_label.setText("")
        self.create_btn.setEnabled(True)

    # --- results ------------------------------------------------------------------

    def _add_to_selected(self):
        sel = self._selected()
        if sel is None:
            return
        self.album_id, self.album_name = sel
        self.created = False
        self.accept()

    def _create(self):
        name = self.current_name()
        if validate_album_name(name):
            return
        existing = self.db.album_id_by_name(name)
        self.album_name = name
        self.album_id = existing
        self.created = existing is None
        self.accept()
