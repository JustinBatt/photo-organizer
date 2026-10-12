"""
Decides whether a discovered image file is a real photo or should be
excluded (app asset, icon, system file, etc). Also decides whether a
folder "looks organized" for informational tagging.

Every exclusion carries a human-readable reason so users can review and
override in the Excluded list.
"""
from pathlib import Path
from typing import Optional

from . import config
from .metadata import PhotoMetadata


def path_is_excluded_dir(dir_path: Path) -> bool:
    """True if this directory (by name) should never be descended into."""
    name = dir_path.name.lower()
    return name in config.EXCLUDED_PATH_SEGMENTS


def any_excluded_segment(full_path: Path) -> Optional[str]:
    """If any ancestor segment of full_path matches an excluded keyword,
    return that keyword (for logging); else None."""
    lowered_parts = [p.lower() for p in full_path.parts]
    for seg in config.EXCLUDED_PATH_SEGMENTS:
        if seg in lowered_parts:
            return seg
    return None


def classify_file(path: Path, meta: PhotoMetadata) -> tuple[bool, Optional[str]]:
    """
    Returns (excluded: bool, reason: Optional[str]).
    Assumes the file has already passed the extension check.
    """
    ext = path.suffix.lower()

    if ext in config.ICON_EXTENSIONS:
        return True, f"Icon/cursor format ({ext})"

    excluded_seg = any_excluded_segment(path)
    if excluded_seg:
        return True, f"System/app path ('{excluded_seg}')"

    if meta.width and meta.height:
        if meta.width < config.MIN_PHOTO_DIMENSION_PX or meta.height < config.MIN_PHOTO_DIMENSION_PX:
            return True, (
                f"Very small image ({meta.width}x{meta.height}px) - "
                f"likely an icon or UI sprite"
            )

    if not meta.has_exif:
        # Match whole path SEGMENTS only (never a raw substring check) so
        # a real photo like "resized_copy.jpg" or a folder like
        # "Resort Trip" can never false-match on a short keyword like "res".
        segments = {p.lower() for p in path.parts}
        matched = segments & config.APP_LIKE_PATH_KEYWORDS
        if matched:
            kw = next(iter(matched))
            return True, f"No camera metadata + app-like path ('{kw}')"

    return False, None


def folder_looks_organized(folder_name: str) -> bool:
    for pattern in config.ORGANIZED_FOLDER_PATTERNS:
        if pattern.search(folder_name):
            return True
    return False
