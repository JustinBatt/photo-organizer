import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTreeWidget,
    QTreeWidgetItem, QLabel, QMessageBox, QSplitter
)
from PySide6.QtCore import Qt

from core.duplicates import (
    build_duplicate_groups, build_folder_groups, individual_only_groups,
    FOLDER_STATUS_FULL_DUP, FOLDER_STATUS_MOSTLY_DUP,
)
from core.actions import send_to_recycle_bin

PATH_ROLE = Qt.ItemDataRole.UserRole
DELETABLE_ROLE = Qt.ItemDataRole.UserRole + 1


class DuplicateReviewTab(QWidget):
    """
    A dedicated tab that ONLY deals with duplicate survivorship - the
    rest of the collection never appears here, by design.
    """

    def __init__(self, db):
        super().__init__()
        self.db = db

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<b>Duplicate Review</b> — this view only shows duplicates. "
            "Nothing is deleted until you click Apply."
        ))

        refresh_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Recompute Duplicates")
        self.refresh_btn.clicked.connect(self.refresh_all)
        refresh_row.addWidget(self.refresh_btn)
        refresh_row.addStretch()
        layout.addLayout(refresh_row)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # --- Folder-level section ---
        folder_box = QWidget()
        folder_layout = QVBoxLayout(folder_box)
        folder_layout.addWidget(QLabel(
            "<b>Whole-folder duplicates</b> — folders that are entirely or "
            "mostly duplicated elsewhere."
        ))
        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderLabels(["Folder / File", "Detail"])
        self.folder_tree.setColumnWidth(0, 480)
        self.folder_tree.itemChanged.connect(self._on_folder_item_changed)
        folder_layout.addWidget(self.folder_tree)
        splitter.addWidget(folder_box)

        # --- Individual duplicate groups section ---
        indiv_box = QWidget()
        indiv_layout = QVBoxLayout(indiv_box)
        indiv_layout.addWidget(QLabel(
            "<b>Individual duplicate photos</b> — not part of a whole-folder "
            "match; recommended keeper is left unchecked."
        ))
        self.indiv_tree = QTreeWidget()
        self.indiv_tree.setHeaderLabels(["File", "Recommendation"])
        self.indiv_tree.setColumnWidth(0, 480)
        indiv_layout.addWidget(self.indiv_tree)
        splitter.addWidget(indiv_box)

        layout.addWidget(splitter, stretch=1)

        self.summary_label = QLabel("")
        layout.addWidget(self.summary_label)

        self.apply_btn = QPushButton("Apply Selected → Move to Recycle Bin")
        self.apply_btn.clicked.connect(self.apply_selected)
        layout.addWidget(self.apply_btn)

        self._suppress_cascade = False

    # --- populate --------------------------------------------------

    def refresh_all(self):
        self.folder_tree.clear()
        self.indiv_tree.clear()

        dup_groups = build_duplicate_groups(self.db)
        folder_groups = build_folder_groups(self.db)
        remaining = individual_only_groups(dup_groups, folder_groups)

        self._populate_folder_tree(folder_groups)
        self._populate_indiv_tree(remaining)
        self._update_summary()

    def _populate_folder_tree(self, folder_groups):
        self._suppress_cascade = True
        for fg in folder_groups:
            is_full = fg.status == FOLDER_STATUS_FULL_DUP
            status_label = "SURVIVE (full duplicate)" if is_full else "DELETE duplicated subset"
            parent = QTreeWidgetItem([
                f"{fg.folder}  ({fg.duplicated}/{fg.total} duplicated)",
                status_label,
            ])
            parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
            # Default: unchecked for full-duplicate (survive), checked for mostly-duplicate (delete subset)
            parent.setCheckState(0, Qt.CheckState.Unchecked if is_full else Qt.CheckState.Checked)
            parent.setData(0, DELETABLE_ROLE, False)
            parent.setToolTip(1, fg.recommendation)

            for path in fg.duplicated_photo_paths:
                child = QTreeWidgetItem([os.path.basename(path), path])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Unchecked if is_full else Qt.CheckState.Checked)
                child.setData(0, PATH_ROLE, path)
                child.setData(0, DELETABLE_ROLE, True)
                parent.addChild(child)

            if fg.unique_photo_paths:
                note = QTreeWidgetItem([
                    f"{len(fg.unique_photo_paths)} unique photo(s) — review in Browse tab",
                    "Not touched here",
                ])
                note.setFlags(Qt.ItemFlag.ItemIsEnabled)  # not checkable
                note.setData(0, DELETABLE_ROLE, False)
                for p in fg.unique_photo_paths:
                    sub = QTreeWidgetItem([os.path.basename(p), p])
                    sub.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    sub.setData(0, DELETABLE_ROLE, False)
                    note.addChild(sub)
                parent.addChild(note)

            self.folder_tree.addTopLevelItem(parent)
            parent.setExpanded(False)
        self._suppress_cascade = False

    def _populate_indiv_tree(self, groups):
        for g in groups:
            parent = QTreeWidgetItem([
                f"{os.path.basename(g.keep_path)}  ({len(g.photos)} copies)", ""
            ])
            parent.setFlags(Qt.ItemFlag.ItemIsEnabled)
            parent.setData(0, DELETABLE_ROLE, False)

            for row in g.photos:
                path = row["path"]
                is_keeper = path == g.keep_path
                child = QTreeWidgetItem([path, g.reasons.get(path, "")])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(
                    0, Qt.CheckState.Unchecked if is_keeper else Qt.CheckState.Checked
                )
                child.setData(0, PATH_ROLE, path)
                child.setData(0, DELETABLE_ROLE, True)
                parent.addChild(child)

            self.indiv_tree.addTopLevelItem(parent)
            parent.setExpanded(True)

    # --- interaction -------------------------------------------------

    def _on_folder_item_changed(self, item: QTreeWidgetItem, column: int):
        if self._suppress_cascade or column != 0:
            return
        if item.childCount() > 0 and item.data(0, DELETABLE_ROLE) is False:
            # A parent folder row was toggled -> cascade to deletable children
            self._suppress_cascade = True
            new_state = item.checkState(0)
            for i in range(item.childCount()):
                child = item.child(i)
                if child.data(0, DELETABLE_ROLE):
                    child.setCheckState(0, new_state)
            self._suppress_cascade = False
        self._update_summary()

    def _collect_checked_paths(self) -> list[str]:
        paths = []

        def walk(tree_widget):
            for i in range(tree_widget.topLevelItemCount()):
                _walk_item(tree_widget.topLevelItem(i))

        def _walk_item(item):
            if item.data(0, DELETABLE_ROLE) and item.checkState(0) == Qt.CheckState.Checked:
                p = item.data(0, PATH_ROLE)
                if p:
                    paths.append(p)
            for i in range(item.childCount()):
                _walk_item(item.child(i))

        walk(self.folder_tree)
        walk(self.indiv_tree)
        return paths

    def _update_summary(self):
        count = len(self._collect_checked_paths())
        self.summary_label.setText(f"{count} file(s) selected for the Recycle Bin")

    def apply_selected(self):
        paths = self._collect_checked_paths()
        if not paths:
            QMessageBox.information(self, "Nothing selected", "No files are checked for deletion.")
            return

        reply = QMessageBox.question(
            self,
            "Confirm",
            f"Move {len(paths)} file(s) to the Recycle Bin?\n\n"
            f"This can be undone from the Recycle Bin afterward, but the app "
            f"won't track them anymore.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        succeeded, failed = send_to_recycle_bin(self.db, paths, reason="Duplicate review")

        msg = f"Moved {len(succeeded)} file(s) to the Recycle Bin."
        if failed:
            msg += f"\n\n{len(failed)} file(s) could not be removed:\n" + "\n".join(failed[:10])
        QMessageBox.information(self, "Done", msg)

        self.refresh_all()
