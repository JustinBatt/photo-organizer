"""
Thumbnails, loaded in the background.

Thumbnails are generated with Pillow (not QPixmap's native loader) so we
get consistent results across formats Qt may not handle well out of the
box (WEBP, HEIC/HEIF via pillow-heif, some TIFF variants), and so EXIF
orientation is respected.

`ThumbnailLoader` decodes images on a small thread pool and keeps a bounded
in-memory cache. Views ask for a thumbnail with `get()` / `request()` and
repaint when the `loaded` signal fires - so a list of 65,000 photos only
ever decodes the handful currently on screen.
"""
import os
from collections import OrderedDict

from PIL import Image, ImageOps
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

try:  # HEIC/HEIF support (same optional dependency as core.metadata)
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass


def render_thumbnail_image(path: str, size: int) -> QImage | None:
    """Decode `path` to a QImage no larger than size x size, or None on failure.
    Safe to call from any thread."""
    try:
        with Image.open(path) as img:
            try:
                img.draft("RGB", (size * 2, size * 2))  # fast JPEG downscale; no-op otherwise
            except Exception:
                pass
            img = ImageOps.exif_transpose(img)  # respect rotation
            img = img.convert("RGB")
            img.thumbnail((size, size))
            data = img.tobytes("raw", "RGB")
            qimg = QImage(data, img.width, img.height, img.width * 3,
                          QImage.Format.Format_RGB888)
            return qimg.copy()  # detach from `data` before it's freed
    except Exception:
        return None


class _State:
    """Shared between the loader and its tasks; bumping `generation`
    cancels every task that has not started yet."""
    generation = 0


class _Signals(QObject):
    done = Signal(str, QImage, int)  # path, image (null on failure), generation


class _Task(QRunnable):
    def __init__(self, path: str, size: int, generation: int, state: _State, signals: _Signals):
        super().__init__()
        self.setAutoDelete(True)
        self._path, self._size, self._gen = path, size, generation
        self._state, self._signals = state, signals

    def run(self):
        if self._state.generation != self._gen:
            return  # request was cancelled before it started
        img = render_thumbnail_image(self._path, self._size)
        self._signals.done.emit(self._path, img if img is not None else QImage(), self._gen)


class ThumbnailLoader(QObject):
    """Background thumbnail loader with an LRU cache. Use from the GUI thread."""

    loaded = Signal(str)  # emitted (GUI thread) when a thumbnail becomes available

    def __init__(self, size: int = 150, cache_size: int = 4000, parent=None):
        super().__init__(parent)
        self.size = size
        self._cache_size = cache_size
        self._cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._failed: set[str] = set()
        self._pending: set[str] = set()
        self._priority = 0
        self._state = _State()
        self._signals = _Signals()
        self._signals.done.connect(self._on_done)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(2, min(6, os.cpu_count() or 2)))
        self.placeholder = self._make_pixmap("…")
        self.failed_pixmap = self._make_pixmap("×")

    def _make_pixmap(self, glyph: str) -> QPixmap:
        pm = QPixmap(self.size, self.size)
        pm.fill(QColor(236, 236, 236))
        painter = QPainter(pm)
        painter.setPen(QColor(160, 160, 160))
        painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
        painter.end()
        return pm

    def get(self, path: str) -> QPixmap | None:
        """The thumbnail if it is ready (or the 'failed' picture if it could
        not be decoded); None if it is not loaded yet."""
        pm = self._cache.get(path)
        if pm is not None:
            self._cache.move_to_end(path)
            return pm
        if path in self._failed:
            return self.failed_pixmap
        return None

    def request(self, path: str):
        """Start loading `path` in the background if it isn't loaded already.
        Newest requests run first, so what is on screen now wins while scrolling."""
        if path in self._pending or path in self._cache or path in self._failed:
            return
        self._pending.add(path)
        self._priority += 1
        self._pool.start(
            _Task(path, self.size, self._state.generation, self._state, self._signals),
            self._priority,
        )

    def pixmap_or_request(self, path: str) -> QPixmap:
        pm = self.get(path)
        if pm is None:
            self.request(path)
            return self.placeholder
        return pm

    def cancel_pending(self):
        """Forget requests that haven't started (e.g. the list was replaced)."""
        self._state.generation += 1
        self._pending.clear()
        self._pool.clear()

    def shutdown(self):
        self.cancel_pending()
        self._pool.waitForDone(3000)

    def _on_done(self, path: str, image: QImage, generation: int):
        if generation != self._state.generation:
            return  # stale result from before a cancel
        self._pending.discard(path)
        if image.isNull():
            self._failed.add(path)
        else:
            self._cache[path] = QPixmap.fromImage(image)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        self.loaded.emit(path)
