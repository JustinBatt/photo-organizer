from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget,
    QLabel, QProgressBar, QTextEdit, QMessageBox, QFileDialog
)
from PySide6.QtCore import Qt

from .workers import ScanWorker


class ScanTab(QWidget):
    def __init__(self, db, on_scan_complete):
        super().__init__()
        self.db = db
        self.on_scan_complete = on_scan_complete
        self.roots: list[str] = []
        self.worker: ScanWorker | None = None

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            "<b>1. Choose folders to scan</b><br>"
            "Add every drive or folder you want indexed. The scan skips "
            "Windows/Program Files/AppData automatically."
        ))

        self.root_list = QListWidget()
        layout.addWidget(self.root_list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Folder…")
        add_btn.clicked.connect(self.add_folder)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self.remove_selected)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        layout.addLayout(btn_row)

        self.scan_btn = QPushButton("Start Scan")
        self.scan_btn.clicked.connect(self.start_scan)
        layout.addWidget(self.scan_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate until we know a total
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(150)
        layout.addWidget(self.log)

        layout.addStretch()

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder to scan")
        if folder and folder not in self.roots:
            self.roots.append(folder)
            self.root_list.addItem(folder)

    def remove_selected(self):
        for item in self.root_list.selectedItems():
            self.roots.remove(item.text())
            self.root_list.takeItem(self.root_list.row(item))

    def start_scan(self):
        if not self.roots:
            QMessageBox.warning(self, "No folders", "Add at least one folder first.")
            return

        self.scan_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.log.clear()
        self.log.append(f"Scanning {len(self.roots)} folder(s)…")

        self.worker = ScanWorker(list(self.roots), self.db)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_scan.connect(self.on_finished)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def on_progress(self, count: int, current_path: str):
        self.log.append(f"  {count} files indexed… ({current_path})")

    def on_finished(self, count: int):
        self.progress_bar.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.log.append(f"\nDone. Indexed {count} photo files.")
        self.on_scan_complete()

    def on_error(self, message: str):
        self.progress_bar.setVisible(False)
        self.scan_btn.setEnabled(True)
        QMessageBox.critical(self, "Scan error", message)
