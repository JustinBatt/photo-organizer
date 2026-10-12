"""
Central configuration: default exclusion rules, thresholds, and heuristics.
All of these are meant to be user-editable later (e.g. via a Settings tab);
for v1 they live here as clearly-named constants so they're easy to find
and tweak by hand.
"""

# --- File types --------------------------------------------------------

# Real photo formats we index and show in Browse.
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".webp", ".heic", ".heif",
}

# Formats that are almost always UI assets, not photos. We still record
# these (so they show up in the "Excluded" review list) but they default
# to excluded.
ICON_EXTENSIONS = {".ico", ".cur"}

ALL_SCANNED_EXTENSIONS = IMAGE_EXTENSIONS | ICON_EXTENSIONS

# --- Path-based exclusion ------------------------------------------------
# If any of these (case-insensitive) appear as a path *segment*, we skip
# the whole directory during the walk (never descend into it) for both
# correctness and speed.
EXCLUDED_PATH_SEGMENTS = {
    "program files",
    "program files (x86)",
    "windows",
    "programdata",
    "appdata",
    "node_modules",
    ".git",
    "$recycle.bin",
    "system volume information",
    "google\\chrome\\user data",
    "microsoft\\edge\\user data",
    "cache",
    "caches",
    "temp",
    "tmp",
}

# --- Heuristic exclusion (applied per-file, after EXIF is read) --------

# Images smaller than this in *either* dimension are treated as probable
# icons/sprites rather than photos.
MIN_PHOTO_DIMENSION_PX = 100

# If a file has no camera/phone EXIF data at all AND one of its path
# SEGMENTS (a whole folder name, not a substring) exactly matches one of
# these, we treat it as an app asset rather than a photo. Matched as
# whole segments (like EXCLUDED_PATH_SEGMENTS) so a real photo folder or
# filename that merely contains one of these as a substring - e.g.
# "resized_copy.jpg" or "Resort Trip" - is never a false match.
APP_LIKE_PATH_KEYWORDS = {
    "assets", "resources", "res", "drawable", "icons",
    "sprites", "textures", "ui",
}

# --- "Already organized" folder detection -------------------------------
# A folder name matching any of these patterns is tagged as already
# organized. This is informational only in v1 (see spec: Phase 1) --
# it doesn't change what's shown, just how it's labeled and how it scores
# in duplicate-survivorship recommendations.
import re

ORGANIZED_FOLDER_PATTERNS = [
    re.compile(r"^(19|20)\d{2}$"),                      # "2023"
    re.compile(r"^(19|20)\d{2}[-_ ](0[1-9]|1[0-2])$"),   # "2023-06", "2023_06"
    re.compile(r"^(19|20)\d{2}[-_ ].+"),                 # "2023 Summer Trip"
    re.compile(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[-_ ]?"
        r"(19|20)\d{2}",
        re.IGNORECASE,
    ),                                                    # "June 2023"
]

# --- Duplicate detection -------------------------------------------------

# A folder is "mostly duplicated" if the fraction of its photos that have
# an exact match (same filename + size) elsewhere is >= this threshold,
# but not 100%. Below this threshold, duplicates in that folder are left
# as individual (not folder-level) groups.
MOSTLY_DUPLICATE_THRESHOLD = 0.5

# Recommendation scoring weights (higher score = more likely to "win" and
# be recommended as the keeper in a duplicate group).
SCORE_HAS_EXIF = 3
SCORE_ORGANIZED_FOLDER = 2
SCORE_LARGER_FILE = 1
SCORE_OLDER_FILE = 1

# --- Perceptual (visual) duplicate detection - stretch goal --------------

# Hamming distance threshold below which two images are considered
# visually similar/duplicate. 0 = identical hash. Typical useful range
# is 0-10 for an 8x8 phash (64 bits).
PERCEPTUAL_HASH_MAX_DISTANCE = 8

# Database location (relative to wherever the app is run from, or pass
# an absolute path via CLI/GUI settings later).
DEFAULT_DB_FILENAME = "photo_organizer.db"
