from PySide6.QtWidgets import QMainWindow, QTabWidget

from core.database import Database
from core import config
from .scan_tab import ScanTab
from .browse_tab import BrowseTab
from .duplicate_review_tab import DuplicateReviewTab
from .visual_duplicates_tab import VisualDuplicatesTab


class MainWindow(QMainWindow):
    def __init__(self, db_path: str = config.DEFAULT_DB_FILENAME):
        super().__init__()
        self.setWindowTitle("Photo Organizer")
        self.resize(1100, 750)

        self.db = Database(db_path)

        tabs = QTabWidget()
        self.scan_tab = ScanTab(self.db, on_scan_complete=self.on_scan_complete)
        self.browse_tab = BrowseTab(self.db)
        self.review_tab = DuplicateReviewTab(self.db)
        self.visual_tab = VisualDuplicatesTab(self.db)

        tabs.addTab(self.scan_tab, "1. Scan")
        tabs.addTab(self.browse_tab, "2. Browse")
        tabs.addTab(self.review_tab, "3. Duplicate Review")
        tabs.addTab(self.visual_tab, "4. Visual Duplicates (Experimental)")

        self.setCentralWidget(tabs)

        # If a previous scan's DB already has data, populate views on launch.
        self.on_scan_complete()

    def on_scan_complete(self):
        self.browse_tab.refresh_folder_list()
        self.review_tab.refresh_all()

    def closeEvent(self, event):
        self.db.close()
        event.accept()
