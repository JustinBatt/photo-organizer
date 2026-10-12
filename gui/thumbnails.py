"""
Thumbnail generation via Pillow (not QPixmap's native loader) so we get
consistent, correct results across formats Qt may not handle well out of
the box (WEBP, HEIC/HEIF via pillow-heif, some TIFF variants), and so
orientation from EXIF is respected.
"""
from PIL import Image, ImageOps
from PySide6.QtGui import QImage, QPixmap, QIcon

_CACHE_SIZE = 150


def load_thumbnail_icon(path: str, size: int = _CACHE_SIZE) -> QIcon | None:
    pixmap = load_thumbnail_pixmap(path, size)
    if pixmap is None:
        return None
    return QIcon(pixmap)


def load_thumbnail_pixmap(path: str, size: int = _CACHE_SIZE) -> QPixmap | None:
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)  # respect rotation
            img = img.convert("RGB")
            img.thumbnail((size, size))
            data = img.tobytes("raw", "RGB")
            qimg = QImage(data, img.width, img.height, img.width * 3,
                           QImage.Format.Format_RGB888)
            qimg = qimg.copy()  # detach from `data` before it's freed
            return QPixmap.fromImage(qimg)
    except Exception:
        return None
