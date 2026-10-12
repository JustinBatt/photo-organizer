import os
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTreeWidget,
    QTreeWidgetItem, QLabel, QMessageBox, QSplitter, QCheckBox, QFileDialog,
)

from core.duplicates import (
    build_duplicate_groups, build_folder_groups, individual_only_groups,
    default_selection, groups_losing_every_copy,
    FOLDER_STATUS_FULL_DUP, FOLDER_STATUS_MOSTLY_DUP,
    STATUS_CONFIRMED, STATUS_PENDING,
    DECISION_CONFIRMED, DECISION_NOT_DUPLICATE,
)
from core.actions import send_to_recycle_bin
from core.quarantine import quarantine_folder, QuarantineError, QUARANTINE_SETTING
from .compare_panel import ComparePanel
from .quarantine_dialog import QuarantinedFoldersDialog

PATH_ROLE = Qt.ItemDataRole.UserRole
DELETABLE_ROLE = Qt.ItemDataRole.UserRole + 1
FOLDER_ROLE = Qt.ItemDataRole.UserRole + 2
GROUP_ROLE = Qt.ItemDataRole.UserRole + 3

STATUS_TEXT = {STATUS_PENDING: "Needs review", STATUS_CONFIRMED: "Confirmed"}


class DuplicateReviewTab(QWidget):
    """
    A dedicated tab that ONLY deals with duplicate survivorship - the
    rest of the collection never appears here, by design.

    Duplicates are found from metadata only (date taken + camera + size, or
    file name + size when there is no camera data). Click any file to see
    every copy side by side; confirm or unflag the match; remove copies via
    the Recycle Bin, or quarantine a whole folder.
    """

    # Emitted after files were removed/moved, so other tabs can refresh.
    files_deleted = Signal()

    def __init__(self, db):
        super().__init__()
        self.db = db
        self._suppress_cascade = False
        self._all_groups = []
        self._remaining = []
        self._folder_groups = []
        self._group_by_key = {}
        self._group_by_path = {}
        self._active_tree: Optional[QTreeWidget] = None
        self._remembered: dict[str, bool] = {}
        self._defaults: set[str] = set()

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<b>Duplicate Review</b> — this view only shows duplicates, found from photo "
            "metadata. Click a file to compare its copies. Nothing is deleted until you "
            "click Apply."
        ))

        top = QHBoxLayout()
        self.refresh_btn = QPushButton("Recompute Duplicates")
        self.refresh_btn.clicked.connect(lambda: self.refresh_all())
        top.addWidget(self.refresh_btn)
        self.hide_confirmed = QCheckBox("Hide confirmed groups")
        self.hide_confirmed.toggled.connect(self._on_hide_toggled)
        top.addWidget(self.hide_confirmed)
        self.reset_btn = QPushButton("Reset my confirm/unflag choices…")
        self.reset_btn.clicked.connect(self.reset_decisions)
        top.addWidget(self.reset_btn)
        top.addStretch()
        self.quarantine_btn = QPushButton("Quarantine selected folder…")
        self.quarantine_btn.setToolTip(
            "Moves the whole folder to your quarantine location (nothing is deleted). "
            "Select a folder row, or a file to use its folder."
        )
        self.quarantine_btn.clicked.connect(self.quarantine_selected)
        top.addWidget(self.quarantine_btn)
        self.quarantined_btn = QPushButton("Quarantined folders…")
        self.quarantined_btn.clicked.connect(self.show_quarantined)
        top.addWidget(self.quarantined_btn)
        self.location_btn = QPushButton("Set quarantine location…")
        self.location_btn.clicked.connect(lambda: self.choose_quarantine_location())
        top.addWidget(self.location_btn)
        layout.addLayout(top)

        main_split = QSplitter(Qt.Orientation.Horizontal)

        # --- left: the two lists ---
        left_split = QSplitter(Qt.Orientation.Vertical)

        folder_box = QWidget()
        folder_layout = QVBoxLayout(folder_box)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.addWidget(QLabel(
            "<b>Whole-folder duplicates</b> — folders that are entirely or "
            "mostly duplicated elsewhere."
        ))
        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderLabels(["Folder / File", "Detail"])
        self.folder_tree.setColumnWidth(0, 330)
        self.folder_tree.itemChanged.connect(self._on_folder_item_changed)
        self.folder_tree.currentItemChanged.connect(
            lambda cur, _prev: self._on_item_selected(self.folder_tree, cur))
        folder_layout.addWidget(self.folder_tree)
        left_split.addWidget(folder_box)

        indiv_box = QWidget()
        indiv_layout = QVBoxLayout(indiv_box)
        indiv_layout.setContentsMargins(0, 0, 0, 0)
        indiv_layout.addWidget(QLabel(
            "<b>Individual duplicate photos</b> — not part of a whole-folder "
            "match; recommended keeper is left unchecked."
        ))
        self.indiv_tree = QTreeWidget()
        self.indiv_tree.setHeaderLabels(["File", "Status", "Recommendation"])
        self.indiv_tree.setColumnWidth(0, 330)
        self.indiv_tree.setColumnWidth(1, 90)
        self.indiv_tree.itemChanged.connect(lambda *_: self._update_summary())
        self.indiv_tree.currentItemChanged.connect(
            lambda cur, _prev: self._on_item_selected(self.indiv_tree, cur))
        indiv_layout.addWidget(self.indiv_tree)
        left_split.addWidget(indiv_box)
        main_split.addWidget(left_split)

        # --- right: comparison ---
        self.compare = ComparePanel()
        self.compare.confirm_requested.connect(self._confirm_group)
        self.compare.unflag_requested.connect(self._unflag_group)
        main_split.addWidget(self.compare)
        main_split.setStretchFactor(0, 2)
        main_split.setStretchFactor(1, 3)
        main_split.setSizes([520, 640])

        layout.addWidget(main_split, stretch=1)

        self.summary_label = QLabel("")
        layout.addWidget(self.summary_label)

        self.apply_btn = QPushButton("Apply Selected → Move to Recycle Bin")
        self.apply_btn.clicked.connect(self.apply_selected)
        layout.addWidget(self.apply_btn)

    def shutdown(self):
        self.compare.shutdown()

    # --- populate --------------------------------------------------

    def refresh_all(self, keep_checks: bool = False):
        remembered = self._collect_check_states() if keep_checks else {}
        shown_key = self.compare.group_key

        self._all_groups = build_duplicate_groups(self.db)
        self._folder_groups = build_folder_groups(self.db, self._all_groups)
        self._remaining = individual_only_groups(self._all_groups, self._folder_groups)
        self._group_by_key = {g.group_key: g for g in self._all_groups}
        self._group_by_path = {r["path"]: g for g in self._all_groups for r in g.photos}
        self._defaults = default_selection(self._folder_groups, self._remaining, self._all_groups)
        self._remembered = remembered

        self.folder_tree.clear()
        self.indiv_tree.clear()
        self._populate_folder_tree(self._folder_groups)
        self._populate_indiv_tree(self._remaining)
        self._update_summary()

        if shown_key in self._group_by_key:
            self.compare.show_group(self._group_by_key[shown_key])
        else:
            self.compare.show_message(
                "Click a file on the left to see all of its copies side by side "
                "and how they compare." if self._all_groups else
                "No duplicates found. (Duplicates need to match on date taken + camera + size, "
                "or file name + size when there is no camera data.)"
            )

    def _want_checked(self, path: str) -> bool:
        if path in self._remembered:
            return self._remembered[path]
        return path in self._defaults

    def _populate_folder_tree(self, folder_groups):
        self._suppress_cascade = True
        for fg in folder_groups:
            is_full = fg.status == FOLDER_STATUS_FULL_DUP
            status_label = "SURVIVE (full duplicate)" if is_full else "DELETE duplicated subset"
            parent = QTreeWidgetItem([
                f"{fg.folder}  ({fg.duplicated}/{fg.total} duplicated)",
                status_label,
            ])
            # The folder row's tick is kept in step with its files by hand
            # (see _on_folder_item_changed) - Qt's automatic tri-state mode drops
            # the checkbox when a folder also holds a non-checkable "unique photos" row.
            parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            parent.setData(0, DELETABLE_ROLE, False)
            parent.setData(0, FOLDER_ROLE, fg.folder)
            parent.setToolTip(0, fg.folder)
            parent.setToolTip(1, fg.recommendation)

            for path in fg.duplicated_photo_paths:
                child = QTreeWidgetItem([os.path.basename(path), path])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                on = self._want_checked(path)
                child.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
                child.setData(0, PATH_ROLE, path)
                child.setData(0, DELETABLE_ROLE, True)
                parent.addChild(child)
            self._sync_folder_state(parent)

            if fg.unique_photo_paths:
                note = QTreeWidgetItem([
                    f"{len(fg.unique_photo_paths)} unique photo(s) — review in Browse tab",
                    "Not touched here",
                ])
                note.setFlags(Qt.ItemFlag.ItemIsEnabled)  # not checkable
                note.setData(0, DELETABLE_ROLE, False)
                note.setData(0, FOLDER_ROLE, fg.folder)
                for p in fg.unique_photo_paths:
                    sub = QTreeWidgetItem([os.path.basename(p), p])
                    sub.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    sub.setData(0, DELETABLE_ROLE, False)
                    sub.setData(0, PATH_ROLE, p)
                    note.addChild(sub)
                parent.addChild(note)

            self.folder_tree.addTopLevelItem(parent)
            parent.setExpanded(False)
        self._suppress_cascade = False

    def _populate_indiv_tree(self, groups):
        hide = self.hide_confirmed.isChecked()
        for g in groups:
            if hide and g.status == STATUS_CONFIRMED:
                continue
            parent = QTreeWidgetItem([
                f"{os.path.basename(g.keep_path)}  ({len(g.photos)} copies)",
                STATUS_TEXT[g.status], "",
            ])
            parent.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            parent.setData(0, DELETABLE_ROLE, False)
            parent.setData(0, GROUP_ROLE, g.group_key)

            for row in g.photos:
                path = row["path"]
                child = QTreeWidgetItem([path, "", g.reasons.get(path, "")])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(
                    0, Qt.CheckState.Checked if self._want_checked(path) else Qt.CheckState.Unchecked
                )
                child.setData(0, PATH_ROLE, path)
                child.setData(0, DELETABLE_ROLE, True)
                child.setData(0, GROUP_ROLE, g.group_key)
                parent.addChild(child)

            self.indiv_tree.addTopLevelItem(parent)
            parent.setExpanded(True)

    # --- interaction -------------------------------------------------

    def _on_hide_toggled(self, _on):
        remembered = self._collect_check_states()
        self._remembered = remembered
        self.indiv_tree.clear()
        self._populate_indiv_tree(self._remaining)
        self._update_summary()

    def _on_folder_item_changed(self, item: QTreeWidgetItem, column: int):
        if self._suppress_cascade or column != 0:
            return
        if not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return
        self._suppress_cascade = True
        try:
            if item.data(0, DELETABLE_ROLE) is False and item.childCount() > 0:
                # A folder row was toggled -> apply the same tick to its files
                new_state = item.checkState(0)
                if new_state == Qt.CheckState.PartiallyChecked:
                    new_state = Qt.CheckState.Checked
                    item.setCheckState(0, new_state)
                for i in range(item.childCount()):
                    child = item.child(i)
                    if child.data(0, DELETABLE_ROLE):
                        child.setCheckState(0, new_state)
            else:
                parent = item.parent()
                if parent is not None:
                    self._sync_folder_state(parent)
        finally:
            self._suppress_cascade = False
        self._update_summary()

    @staticmethod
    def _sync_folder_state(parent: QTreeWidgetItem):
        """Set a folder row to unchecked / partly / fully ticked to match its files."""
        files = [parent.child(i) for i in range(parent.childCount())
                 if parent.child(i).data(0, DELETABLE_ROLE)]
        on = sum(1 for f in files if f.checkState(0) == Qt.CheckState.Checked)
        parent.setCheckState(0, (
            Qt.CheckState.Unchecked if on == 0
            else Qt.CheckState.Checked if on == len(files)
            else Qt.CheckState.PartiallyChecked
        ))

    def _on_item_selected(self, tree: QTreeWidget, item: Optional[QTreeWidgetItem]):
        self._active_tree = tree
        if item is None:
            return
        # Selecting in one list clears the highlight in the other, so it's
        # always clear which list the Quarantine button will act on.
        other = self.indiv_tree if tree is self.folder_tree else self.folder_tree
        other.blockSignals(True)
        other.setCurrentItem(None)
        other.blockSignals(False)

        path = item.data(0, PATH_ROLE)
        key = item.data(0, GROUP_ROLE)
        group = self._group_by_key.get(key) if key else (self._group_by_path.get(path) if path else None)
        if group is not None:
            self.compare.show_group(group, highlight_path=path)
            return
        folder = item.data(0, FOLDER_ROLE)
        if folder and not path:
            fg = next((f for f in self._folder_groups if f.folder == folder), None)
            self.compare.show_message(
                f"<b>{folder}</b><br><br>{fg.recommendation if fg else ''}<br><br>"
                "Expand the folder and click a file to compare it with its other copies. "
                "To set the whole folder aside, use <b>Quarantine selected folder…</b>."
            )
        elif path:
            self.compare.show_message(
                f"<b>{os.path.basename(path)}</b> has no duplicate: it is unique, so "
                "there is nothing to compare."
            )

    def _confirm_group(self, key: str):
        group = self._group_by_key.get(key)
        if not group:
            return
        self.db.set_dup_decision(key, DECISION_CONFIRMED)
        group.status = STATUS_CONFIRMED
        for i in range(self.indiv_tree.topLevelItemCount()):
            top = self.indiv_tree.topLevelItem(i)
            if top.data(0, GROUP_ROLE) == key:
                top.setText(1, STATUS_TEXT[STATUS_CONFIRMED])
        self.compare.show_group(group)

    def _unflag_group(self, key: str):
        group = self._group_by_key.get(key)
        if not group:
            return
        names = ", ".join(sorted({r["filename"] for r in group.photos})[:3])
        reply = QMessageBox.question(
            self, "Not duplicates?",
            f"Mark these {len(group.photos)} photos ({names}) as NOT duplicates?\n\n"
            "They will disappear from this list and won't be flagged again. "
            "You can bring them back with “Reset my confirm/unflag choices…”.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.db.set_dup_decision(key, DECISION_NOT_DUPLICATE)
        self.refresh_all(keep_checks=True)
        self.compare.show_message(
            "Marked as <b>not duplicates</b> — they've been taken off the list."
        )

    def reset_decisions(self):
        reply = QMessageBox.question(
            self, "Reset choices",
            "Forget every “confirmed” and “not duplicates” choice you've made, and "
            "recompute the list?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.db.reset_dup_decisions()
            self.refresh_all()

    # --- checked files --------------------------------------------------

    def _walk_items(self, tree: QTreeWidget):
        stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            yield item
            stack.extend(item.child(i) for i in range(item.childCount()))

    def _collect_check_states(self) -> dict[str, bool]:
        states: dict[str, bool] = {}
        for tree in (self.indiv_tree, self.folder_tree):
            for item in self._walk_items(tree):
                if item.data(0, DELETABLE_ROLE):
                    path = item.data(0, PATH_ROLE)
                    if path:
                        # a file shown in both lists counts as checked if checked in either
                        states[path] = states.get(path, False) or item.checkState(0) == Qt.CheckState.Checked
        return states

    def _collect_checked_paths(self) -> list[str]:
        return sorted(p for p, on in self._collect_check_states().items() if on)

    def _update_summary(self):
        count = len(self._collect_checked_paths())
        self.summary_label.setText(f"{count} file(s) selected for the Recycle Bin")

    def apply_selected(self):
        paths = self._collect_checked_paths()
        if not paths:
            QMessageBox.information(self, "Nothing selected", "No files are checked for deletion.")
            return

        # Safety: never allow removing every copy of a photo.
        risky = groups_losing_every_copy(paths, self._all_groups)
        if risky:
            examples = "\n".join(
                f"  • {risky_g.photos[0]['filename']}  ({len(risky_g.photos)} copies)"
                for risky_g in risky[:6]
            )
            QMessageBox.warning(
                self, "Blocked — nothing was removed",
                f"This selection would remove EVERY copy of {len(risky)} photo(s):\n\n{examples}"
                f"{chr(10) + '  …' if len(risky) > 6 else ''}\n\n"
                "Uncheck at least one copy of each (the copy marked KEEP is the recommended one), "
                "then apply again.",
            )
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
        if succeeded:
            self.files_deleted.emit()

    # --- quarantine -----------------------------------------------------------

    def quarantine_root(self) -> Optional[str]:
        return self.db.get_setting(QUARANTINE_SETTING)

    def choose_quarantine_location(self, first_time: bool = False) -> Optional[str]:
        start = self.quarantine_root() or os.path.expanduser("~")
        text = (
            "Choose the folder where quarantined folders will be kept.\n\n"
            "Tip: pick a folder on the SAME drive as your photos - then moving is instant. "
            "A different drive works too, but has to copy every file first."
        )
        if first_time:
            QMessageBox.information(self, "Quarantine location", text)
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose the quarantine folder (a folder to hold quarantined folders)", start
        )
        if not chosen:
            return None
        chosen = os.path.normpath(chosen)
        self.db.set_setting(QUARANTINE_SETTING, chosen)
        return chosen

    def _selected_folder(self) -> Optional[str]:
        tree = self._active_tree
        item = tree.currentItem() if tree is not None else None
        if item is None:
            return None
        folder = item.data(0, FOLDER_ROLE)
        if folder:
            return folder
        path = item.data(0, PATH_ROLE)
        if path:
            row = self.db.get_photo(path)
            return row["folder"] if row else os.path.dirname(path)
        return None

    def quarantine_selected(self):
        folder = self._selected_folder()
        if not folder:
            QMessageBox.information(
                self, "Select a folder",
                "Click a folder row (or a file inside the folder) first, then quarantine."
            )
            return
        root = self.quarantine_root()
        if not root:
            root = self.choose_quarantine_location(first_time=True)
            if not root:
                return

        count = self.db.count_visible_in_folder(folder)
        reply = QMessageBox.question(
            self, "Quarantine this folder?",
            f"Move the whole folder\n\n    {folder}\n\n"
            f"(including its sub-folders, {count:,} photo(s) in the app) to the quarantine "
            f"location:\n\n    {root}\n\n"
            "This moves EVERYTHING in the folder - including any photos that are not "
            "duplicates. Nothing is deleted, and you can restore it any time from "
            "“Quarantined folders…”.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            result = quarantine_folder(self.db, folder, root)
        except QuarantineError as e:
            QMessageBox.warning(self, "Couldn't quarantine", str(e))
            return
        QMessageBox.information(
            self, "Quarantined",
            f"Moved the folder ({result.photo_count:,} photo(s)) to:\n{result.destination}\n\n"
            "Restore it any time from “Quarantined folders…”."
        )
        self.refresh_all(keep_checks=True)
        self.files_deleted.emit()

    def show_quarantined(self):
        dlg = QuarantinedFoldersDialog(self.db, self)
        dlg.exec()
        if dlg.changed:
            self.refresh_all()
            self.files_deleted.emit()
