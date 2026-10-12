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

# EXIF tag numbers. Make/Model/DateTime live in the main block (IFD0), but
# the real "date taken" tags live in the Exif sub-block that IFD0 points to.
_MAKE_TAG = 0x010F            # 271
_MODEL_TAG = 0x0110           # 272
_DATETIME_TAG = 0x0132        # 306  (IFD0: last modified, a weak fallback)
_EXIF_IFD_POINTER = 0x8769    # 34665
_DATE_TIME_ORIGINAL = 0x9003  # 36867
_DATE_TIME_DIGITIZED = 0x9004 # 36868
_SUBSEC_TIME_ORIGINAL = 0x9291   # 37521
_SUBSEC_TIME_DIGITIZED = 0x9292  # 37522


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
                make = exif.get(_MAKE_TAG)
                model = exif.get(_MODEL_TAG)
                meta.date_taken = _read_date_taken(exif)
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


def _read_date_taken(exif) -> Optional[str]:
    """
    The moment the photo was taken, as "YYYY-MM-DDTHH:MM:SS" (plus ".NN" when
    the camera recorded sub-seconds, which keeps burst shots apart).
    Prefers DateTimeOriginal, then DateTimeDigitized, then the weaker IFD0
    DateTime.
    """
    try:
        sub = exif.get_ifd(_EXIF_IFD_POINTER)
    except Exception:
        sub = {}
    candidates = (
        (sub.get(_DATE_TIME_ORIGINAL), sub.get(_SUBSEC_TIME_ORIGINAL)),
        (sub.get(_DATE_TIME_DIGITIZED), sub.get(_SUBSEC_TIME_DIGITIZED)),
        (exif.get(_DATE_TIME_ORIGINAL), None),  # some writers put it in IFD0
        (exif.get(_DATETIME_TAG), None),
    )
    for raw, subsec in candidates:
        if not raw:
            continue
        iso = _normalize_exif_date(raw)
        if iso:
            digits = "".join(ch for ch in str(subsec or "") if ch.isdigit())
            return f"{iso}.{digits}" if digits else iso
    return None


def _normalize_exif_date(raw_date) -> Optional[str]:
    # EXIF dates look like "2023:06:14 10:22:31"
    if isinstance(raw_date, bytes):
        raw_date = raw_date.decode("ascii", "ignore")
    text = str(raw_date).strip().strip("\x00")
    try:
        dt = datetime.strptime(text, "%Y:%m:%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    if dt.year < 1900:
        return None
    return dt.isoformat()
