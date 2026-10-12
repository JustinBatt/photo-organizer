"""
Metadata extraction. Uses Pillow for dimensions + EXIF where possible.
Designed to fail soft: any error just means "no metadata," never a crash
that stops the scan.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from PIL import Image, ExifTags

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:
    HEIF_SUPPORTED = False

_TAG_NAME_TO_ID = {v: k for k, v in ExifTags.TAGS.items()}
_DATE_TAKEN_TAG = _TAG_NAME_TO_ID.get("DateTimeOriginal")
_MAKE_TAG = _TAG_NAME_TO_ID.get("Make")
_MODEL_TAG = _TAG_NAME_TO_ID.get("Model")


@dataclass
class PhotoMetadata:
    width: Optional[int] = None
    height: Optional[int] = None
    date_taken: Optional[str] = None  # ISO 8601 string
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    has_exif: bool = False


def extract(path: str) -> PhotoMetadata:
    meta = PhotoMetadata()
    try:
        with Image.open(path) as img:
            meta.width, meta.height = img.size
            exif = img.getexif()
            if exif:
                raw_date = exif.get(_DATE_TAKEN_TAG) if _DATE_TAKEN_TAG else None
                make = exif.get(_MAKE_TAG) if _MAKE_TAG else None
                model = exif.get(_MODEL_TAG) if _MODEL_TAG else None

                if raw_date:
                    meta.date_taken = _normalize_exif_date(raw_date)
                if make:
                    meta.camera_make = str(make).strip().strip("\x00")
                if model:
                    meta.camera_model = str(model).strip().strip("\x00")

                meta.has_exif = bool(
                    meta.date_taken or meta.camera_make or meta.camera_model
                )
    except Exception:
        # Unreadable / unsupported / corrupt file - treat as "no metadata"
        pass
    return meta


def _normalize_exif_date(raw_date: str) -> Optional[str]:
    # EXIF dates look like "2023:06:14 10:22:31"
    try:
        dt = datetime.strptime(str(raw_date), "%Y:%m:%d %H:%M:%S")
        return dt.isoformat()
    except (ValueError, TypeError):
        return None
