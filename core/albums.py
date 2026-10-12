"""
Album naming rules and suggestions.

Every album name must start with a date prefix, then a space, then a name:
    "23_06 Beach Weekend"   (YY_MM - preferred)
    "2023 Beach Weekend"    (year only)
The prefix is suggested from when the photos were taken (EXIF date taken),
falling back to the file's created/modified time. The name is suggested from
the folders the photos came from.
"""
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

from .fileinfo import file_times, parse_iso_date

SEP = r"[-_.]"
_MONTH = r"(?:0[1-9]|1[0-2])"
_DAY = r"(?:0[1-9]|[12]\d|3[01])"
# A leading date in a folder name: 2023-06-14 / 2023-06 / 23_06 / 2023
_DATE_PREFIX_RE = re.compile(
    rf"^\s*(?:(?:19|20)\d{{2}}{SEP}{_MONTH}(?:{SEP}{_DAY})?|\d{{2}}{SEP}{_MONTH}|(?:19|20)\d{{2}})"
    rf"(?=[\s_\-.]|$)[\s_\-.]*"
)
_VALID_NAME_RE = re.compile(r"^(?:\d{2}_(?:0[1-9]|1[0-2])|(?:19|20)\d{2}) \S.*$")
_ILLEGAL_CHARS = set('<>:"/\\|?*')
MAX_NAME_LENGTH = 100
SAMPLE_LIMIT = 500  # photos examined (on disk) when suggesting a prefix


def strip_date_prefix(name: str) -> str:
    """'2023-06 Beach' -> 'Beach'; '23_06_Beach' -> 'Beach'; '2023' -> ''."""
    return _DATE_PREFIX_RE.sub("", name, count=1).strip()


def validate_album_name(name: str) -> Optional[str]:
    """None if the name is acceptable, otherwise a message saying what's wrong."""
    if not name or not name.strip():
        return "Enter an album name."
    if name != name.strip():
        return "Remove the spaces at the start or end of the name."
    if len(name) > MAX_NAME_LENGTH:
        return f"Keep the name under {MAX_NAME_LENGTH} characters."
    bad = sorted({c for c in name if c in _ILLEGAL_CHARS or ord(c) < 32})
    if bad:
        shown = " ".join(c for c in bad if ord(c) >= 32)
        return f"These characters aren't allowed: {shown}" if shown else "Remove the control characters."
    if name.endswith(".") or name.endswith(" "):
        return "The name can't end with a dot."
    if not _VALID_NAME_RE.match(name):
        return ("Start with a date prefix, then a space and a name — "
                "for example  23_06 Beach Weekend  (YY_MM) or  2023 Beach Weekend.")
    return None


def compose_album_name(prefix: str, name: str) -> str:
    return f"{prefix.strip()} {name.strip()}".strip()


def split_album_name(album_name: str) -> tuple[str, str]:
    """('23_06', 'Beach Weekend') for a valid name; ('', name) otherwise."""
    prefix, _, rest = album_name.partition(" ")
    if _VALID_NAME_RE.match(album_name):
        return prefix, rest
    return "", album_name


def yy_mm(dt: datetime) -> str:
    return dt.strftime("%y_%m")


def year_only(dt: datetime) -> str:
    return dt.strftime("%Y")


@dataclass
class AlbumSuggestion:
    # Most common month among the photos, as 'YY_MM', plus the same as 'YYYY'.
    yy_mm: str
    year: str
    # How the date was found, in plain words, e.g. "date taken of 120 photos".
    date_source: str
    # (suggested name, number of photos from folders with that name), best first.
    names: list = field(default_factory=list)


def photo_date(row) -> tuple[Optional[datetime], str]:
    """Best date for a photo: EXIF date taken, else the earlier of the file's
    created / modified time (read from disk), else the scanned modified time."""
    taken = parse_iso_date(row["date_taken"])
    if taken:
        return taken, "taken"
    created, modified = file_times(row["path"])
    candidates = [d for d in (created, modified) if d]
    if candidates:
        return min(candidates), "file"
    try:
        return datetime.fromtimestamp(row["modified_time"]), "file"
    except (TypeError, ValueError, OSError, OverflowError):
        return None, "none"


def suggest_for_photos(rows: Iterable, today: Optional[datetime] = None) -> AlbumSuggestion:
    rows = list(rows)

    # --- prefix: most common month, over an evenly spaced sample -------------
    if len(rows) > SAMPLE_LIMIT:
        step = len(rows) / SAMPLE_LIMIT
        sample = [rows[int(i * step)] for i in range(SAMPLE_LIMIT)]
    else:
        sample = rows
    months: Counter = Counter()
    kinds: Counter = Counter()
    for row in sample:
        dt, kind = photo_date(row)
        if dt:
            months[yy_mm(dt)] += 1
            kinds[kind] += 1
    if months:
        # most common month; on a tie prefer the earlier one
        best = sorted(months.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        if kinds["taken"] and not kinds["file"]:
            source = "when the photos were taken (EXIF date taken)"
        elif kinds["taken"]:
            source = "date taken where available, otherwise file created/modified date"
        else:
            source = "the files' created/modified date (no date-taken data)"
    else:
        best = yy_mm(today or datetime.now())
        source = "today's date (no dates found)"
    year = ("20" + best[:2]) if best[:2].isdigit() and int(best[:2]) < 70 else ("19" + best[:2])

    # --- names: from the folders the photos live in ---------------------------
    names: Counter = Counter()
    for row in rows:
        base = os.path.basename(str(row["folder"]).rstrip("\\/"))
        name = strip_date_prefix(base)
        if name and not _ILLEGAL_CHARS.intersection(name):
            names[name[:MAX_NAME_LENGTH - 8]] += 1
    ranked = sorted(names.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return AlbumSuggestion(yy_mm=best, year=year, date_source=source, names=ranked)
