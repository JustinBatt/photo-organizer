"""Small helpers for showing facts about a file on disk."""
import os
from datetime import datetime
from typing import Optional


def human_size(n) -> str:
    size = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def format_dt(value: Optional[datetime]) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else "unknown"


def _to_dt(ts) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(ts)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def file_times(path: str) -> tuple[Optional[datetime], Optional[datetime]]:
    """(created, modified) as reported by the file system, or None each if
    the file can't be read. On Windows 'created' is the true creation time."""
    try:
        st = os.stat(path)
    except OSError:
        return None, None
    created = getattr(st, "st_birthtime", None) or st.st_ctime
    return _to_dt(created), _to_dt(st.st_mtime)


def file_details(path: str) -> dict:
    """Facts read live from the file system (not from the scan database)."""
    try:
        st = os.stat(path)
    except OSError:
        return {"exists": False, "size": None, "created": None, "modified": None}
    created, modified = file_times(path)
    return {"exists": True, "size": st.st_size, "created": created, "modified": modified}


def parse_iso_date(text: Optional[str]) -> Optional[datetime]:
    """Parse the 'date_taken' strings stored by the scanner."""
    if not text:
        return None
    for length, fmt in ((19, "%Y-%m-%dT%H:%M:%S"), (10, "%Y-%m-%d")):
        try:
            return datetime.strptime(str(text)[:length], fmt)
        except ValueError:
            continue
    return None
