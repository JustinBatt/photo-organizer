from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget

from core.database import Database
from core import config
from .scan_tab import ScanTab
from .browse_tab import BrowseTab
from .albums_tab import AlbumsTab
from .duplicate_review_tab import DuplicateReviewTab
from .visual_duplicates_tab import VisualDuplicatesTab


class MainWindow(QMainWindow):
    def __init__(self, db_path: str = config.DEFAULT_DB_FILENAME):
        super().__init__()
        self.setWindowTitle("Photo Organizer")
        self.resize(1100, 750)

        self.db = Database(db_path)

        self.tabs = QTabWidget()
        self.scan_tab = ScanTab(self.db, on_scan_complete=self.on_scan_complete)
        self.browse_tab = BrowseTab(self.db)
        self.albums_tab = AlbumsTab(self.db)
        self.review_tab = DuplicateReviewTab(self.db)
        self.visual_tab = VisualDuplicatesTab(self.db)

        self.tabs.addTab(self.scan_tab, "1. Scan")
        self.tabs.addTab(self.browse_tab, "2. Browse")
        self.tabs.addTab(self.albums_tab, "3. Albums")
        self.tabs.addTab(self.review_tab, "4. Duplicate Review")
        self.tabs.addTab(self.visual_tab, "5. Visual Duplicates (Experimental)")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.setCentralWidget(self.tabs)

        # The duplicate view is rebuilt lazily - only when it is opened after
        # something changed - so launching with a large collection stays fast.
        self._review_stale = True
        self._browse_stale = False
        self._albums_stale = False

        # Keep the tabs consistent with each other.
        self.browse_tab.ignores_changed.connect(self._mark_review_stale)
        self.review_tab.files_deleted.connect(self._mark_browse_stale)
        self.review_tab.files_deleted.connect(self._mark_albums_stale)
        # Photos put into an album leave Browse; photos taken out of one return to it.
        self.browse_tab.albums_changed.connect(self._mark_albums_stale)
        self.browse_tab.albums_changed.connect(self._mark_review_stale)
        self.albums_tab.albums_changed.connect(self._mark_browse_stale)
        self.visual_tab.files_deleted.connect(self._mark_browse_stale)
        self.visual_tab.files_deleted.connect(self._mark_review_stale)
        self.visual_tab.files_deleted.connect(self._mark_albums_stale)

        # If a previous scan's DB already has data, populate the browse view on launch.
        self.browse_tab.refresh_folder_list()

    def on_scan_complete(self):
        self.browse_tab.refresh_folder_list()
        self._mark_review_stale()
        self._mark_albums_stale()

    # --- keeping tabs in sync ---------------------------------------------

    def _mark_review_stale(self):
        self._review_stale = True
        if self.tabs.currentWidget() is self.review_tab:
            self._refresh_review()

    def _mark_albums_stale(self):
        self._albums_stale = True
        if self.tabs.currentWidget() is self.albums_tab:
            self._refresh_albums()

    def _mark_browse_stale(self):
        self._browse_stale = True
        if self.tabs.currentWidget() is self.browse_tab:
            self._refresh_browse()

    def _refresh_review(self):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.review_tab.refresh_all()
        finally:
            QApplication.restoreOverrideCursor()
        self._review_stale = False

    def _refresh_albums(self):
        self.albums_tab.refresh()
        self._albums_stale = False

    def _refresh_browse(self):
        self.browse_tab.refresh_folder_list()
        self._browse_stale = False

    def _on_tab_changed(self, _index: int):
        current = self.tabs.currentWidget()
        if current is self.review_tab and self._review_stale:
            self._refresh_review()
        elif current is self.browse_tab and self._browse_stale:
            self._refresh_browse()
        elif current is self.albums_tab and self._albums_stale:
            self._refresh_albums()

    def closeEvent(self, event):
        self.browse_tab.shutdown()
        self.albums_tab.shutdown()
        self.review_tab.shutdown()
        self.visual_tab.shutdown()
        self.db.close()
        event.accept()
