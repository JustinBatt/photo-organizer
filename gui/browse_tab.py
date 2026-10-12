from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QDateEdit
)
from PySide6.QtCore import Qt, QSize, QDate
from PySide6.QtGui import QIcon

from .thumbnails import load_thumbnail_icon

PAGE_SIZE = 200


class BrowseTab(QWidget):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.results: list = []
        self.shown_count = 0

        layout = QVBoxLayout(self)

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
        self.search_box.textChanged.connect(self.run_filter)
        filter_row.addWidget(self.search_box, stretch=2)

        layout.addLayout(filter_row)

        self.count_label = QLabel("")
        layout.addWidget(self.count_label)

        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setIconSize(QSize(150, 150))
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setSpacing(10)
        self.grid.setWordWrap(True)
        layout.addWidget(self.grid)

        self.load_more_btn = QPushButton("Load more")
        self.load_more_btn.clicked.connect(self.load_more)
        self.load_more_btn.setVisible(False)
        layout.addWidget(self.load_more_btn)

    def refresh_folder_list(self):
        current = self.folder_combo.currentData()
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo.addItem("All folders", None)
        for folder in self.db.distinct_folders():
            self.folder_combo.addItem(folder, folder)
        self.folder_combo.blockSignals(False)
        self.run_filter()

    def run_filter(self):
        folder = self.folder_combo.currentData()
        date_from = self.date_from.date().toString("yyyy-MM-dd") if self.use_dates.isChecked() else None
        date_to = self.date_to.date().toString("yyyy-MM-dd") if self.use_dates.isChecked() else None
        search = self.search_box.text().strip() or None

        self.results = self.db.browse(
            folder=folder, date_from=date_from, date_to=date_to, search_text=search
        )
        self.count_label.setText(f"{len(self.results)} photo(s) match")
        self.grid.clear()
        self.shown_count = 0
        self.load_more()

    def load_more(self):
        next_batch = self.results[self.shown_count: self.shown_count + PAGE_SIZE]
        for row in next_batch:
            icon = load_thumbnail_icon(row["path"])
            label = f"{row['filename']}\n{row['folder']}"
            item = QListWidgetItem(icon if icon else QIcon(), label)
            item.setData(Qt.ItemDataRole.UserRole, row["path"])
            item.setToolTip(row["path"])
            self.grid.addItem(item)
        self.shown_count += len(next_batch)
        self.load_more_btn.setVisible(self.shown_count < len(self.results))
