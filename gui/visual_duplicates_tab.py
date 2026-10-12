import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTreeWidget,
    QTreeWidgetItem, QLabel, QProgressBar, QMessageBox
)
from PySide6.QtCore import Qt

from core.perceptual import (
    PERCEPTUAL_AVAILABLE, find_visual_duplicate_groups,
)
from core.actions import send_to_recycle_bin
from .workers import HashWorker

PATH_ROLE = Qt.ItemDataRole.UserRole


class VisualDuplicatesTab(QWidget):
    """
    STRETCH GOAL (Phase 5): finds visually-similar photos that don't
    match on filename/size. Separate, optional, slower pass - kept off
    the main Duplicate Review tab so it never blocks the core workflow.
    """

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.worker: HashWorker | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<b>Visual Duplicates (Experimental)</b><br>"
            "Finds photos that look alike even if the file name/size doesn't "
            "match (resized copies, re-saved copies, screenshots). This is a "
            "similarity match, not a certainty - review before deleting."
        ))

        if not PERCEPTUAL_AVAILABLE:
            layout.addWidget(QLabel(
                "<i>The 'imagehash' package isn't installed. "
                "Run: pip install imagehash</i>"
            ))
            return

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

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "Similarity"])
        self.tree.setColumnWidth(0, 480)
        layout.addWidget(self.tree)

        self.apply_btn = QPushButton("Apply Selected → Move to Recycle Bin")
        self.apply_btn.clicked.connect(self.apply_selected)
        layout.addWidget(self.apply_btn)

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

    def find_duplicates(self):
        self.tree.clear()
        try:
            groups = find_visual_duplicate_groups(self.db)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return

        if not groups:
            QMessageBox.information(
                self, "No matches",
                "No visually similar photos found (or fingerprints haven't "
                "been computed yet - use step 1 first)."
            )
            return

        for cluster in groups:
            parent = QTreeWidgetItem([f"Similar group ({len(cluster)} photos)", ""])
            parent.setFlags(Qt.ItemFlag.ItemIsEnabled)
            for row in cluster:
                child = QTreeWidgetItem([row["path"], "visually similar"])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Unchecked)  # never pre-checked
                child.setData(0, PATH_ROLE, row["path"])
                parent.addChild(child)
            self.tree.addTopLevelItem(parent)
            parent.setExpanded(True)

    def apply_selected(self):
        paths = []
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            for j in range(parent.childCount()):
                child = parent.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    paths.append(child.data(0, PATH_ROLE))

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
        self.find_duplicates()
