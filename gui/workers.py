"""
QThread workers so long-running scans / hashing never freeze the GUI.
"""
from PySide6.QtCore import QThread, Signal

from core.scanner import scan_roots
from core.perceptual import compute_hashes


class ScanWorker(QThread):
    progress = Signal(int, str)
    finished_scan = Signal(int)
    error = Signal(str)

    def __init__(self, roots: list[str], db):
        super().__init__()
        self.roots = roots
        self.db = db
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            count = scan_roots(
                self.roots,
                self.db,
                progress_cb=lambda n, p: self.progress.emit(n, p),
                should_stop=lambda: self._stop,
            )
            self.finished_scan.emit(count)
        except Exception as e:
            self.error.emit(str(e))


class HashWorker(QThread):
    progress = Signal(int, int)
    finished_hashing = Signal(int)
    error = Signal(str)

    def __init__(self, db):
        super().__init__()
        self.db = db
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            count = compute_hashes(
                self.db,
                progress_cb=lambda d, t: self.progress.emit(d, t),
                should_stop=lambda: self._stop,
            )
            self.finished_hashing.emit(count)
        except Exception as e:
            self.error.emit(str(e))
