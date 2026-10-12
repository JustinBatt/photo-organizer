from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QAbstractItemView, QMessageBox,
)

from core.quarantine import restore_item, QuarantineError


class QuarantinedFoldersDialog(QDialog):
    """Folders currently held in quarantine, with a Restore button."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.changed = False
        self.setWindowTitle("Quarantined folders")
        self.resize(820, 420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "These folders were moved out of the way - nothing was deleted. "
            "Select one and click <b>Restore</b> to move it back where it came from."
        ))
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemSelectionChanged.connect(self._update)
        layout.addWidget(self.list, stretch=1)

        row = QHBoxLayout()
        self.restore_btn = QPushButton("Restore selected")
        self.restore_btn.clicked.connect(self._restore)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(self.restore_btn)
        row.addStretch()
        row.addWidget(close)
        layout.addLayout(row)
        self._reload()

    def _reload(self):
        self.list.clear()
        items = self.db.quarantine_items()
        if not items:
            empty = QListWidgetItem("Nothing is in quarantine.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(empty)
        for it in items:
            entry = QListWidgetItem(
                f"{it['original_path']}\n      ⮡ now at {it['quarantine_path']}   ({it['moved_at'][:16].replace('T', ' ')})"
            )
            entry.setData(Qt.ItemDataRole.UserRole, it["id"])
            self.list.addItem(entry)
        self._update()

    def _update(self):
        self.restore_btn.setEnabled(any(
            i.data(Qt.ItemDataRole.UserRole) is not None for i in self.list.selectedItems()
        ))

    def _restore(self):
        errors = []
        for item in self.list.selectedItems():
            item_id = item.data(Qt.ItemDataRole.UserRole)
            if item_id is None:
                continue
            try:
                restore_item(self.db, item_id)
                self.changed = True
            except QuarantineError as e:
                errors.append(str(e))
        self._reload()
        if errors:
            QMessageBox.warning(self, "Couldn't restore everything", "\n\n".join(errors))
