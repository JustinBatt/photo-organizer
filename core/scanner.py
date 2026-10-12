"""
Walks the file system from one or more root folders, indexing every
image file it finds into the database. Designed to be driven from a
background thread in the GUI, so progress is reported via a callback
rather than print statements.
"""
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import config, filters
from .database import Database, normalize_path
from .metadata import extract as extract_metadata

ProgressCallback = Callable[[int, str], None]  # (files_found_so_far, current_path)


def scan_roots(
    roots: list[str],
    db: Database,
    progress_cb: Optional[ProgressCallback] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> int:
    """
    Walk `roots`, index every image found into `db`. Returns total files
    indexed. `should_stop` lets the GUI cancel a long-running scan.

    Files and folders the user chose to ignore (see Database.add_ignore_rule)
    are skipped entirely; an ignored folder is never entered, so everything
    beneath it is skipped too.
    """
    started_at = datetime.now().isoformat()
    scan_id = db.create_scan(roots, started_at)

    ignored_files, ignored_folders = db.ignore_sets()

    def under_ignored_folder(norm_path: str) -> bool:
        return any(
            norm_path == f or norm_path.startswith(f + os.sep)
            for f in ignored_folders
        )

    count = 0
    for root in roots:
        root_path = Path(root)
        if not root_path.exists():
            continue

        for dirpath, dirnames, filenames in os.walk(root_path):
            if should_stop and should_stop():
                break

            if ignored_folders and under_ignored_folder(normalize_path(dirpath)):
                dirnames[:] = []  # don't descend any further
                continue

            # Prune excluded and ignored directories in-place so os.walk
            # never descends into them (big perf win on Windows/ProgramData/etc).
            dirnames[:] = [
                d for d in dirnames
                if not filters.path_is_excluded_dir(Path(dirpath) / d)
                and not (
                    ignored_folders
                    and under_ignored_folder(normalize_path(os.path.join(dirpath, d)))
                )
            ]

            current_dir = Path(dirpath)
            folder_organized = filters.folder_looks_organized(current_dir.name)

            for fname in filenames:
                ext = Path(fname).suffix.lower()
                if ext not in config.ALL_SCANNED_EXTENSIONS:
                    continue

                full_path = current_dir / fname
                if ignored_files and normalize_path(full_path) in ignored_files:
                    continue

                try:
                    stat = full_path.stat()
                except OSError:
                    continue

                meta = extract_metadata(str(full_path))
                excluded, reason = filters.classify_file(full_path, meta)

                record = {
                    "scan_id": scan_id,
                    "path": str(full_path),
                    "filename": fname,
                    "folder": str(current_dir),
                    "extension": ext,
                    "size": stat.st_size,
                    "modified_time": stat.st_mtime,
                    "date_taken": meta.date_taken,
                    "camera_make": meta.camera_make,
                    "camera_model": meta.camera_model,
                    "width": meta.width,
                    "height": meta.height,
                    "has_exif": int(meta.has_exif),
                    "is_organized_folder": int(folder_organized),
                    "excluded": int(excluded),
                    "exclude_reason": reason,
                }
                db.upsert_photo(record)
                count += 1

                if progress_cb and count % 25 == 0:
                    progress_cb(count, str(full_path))

            if should_stop and should_stop():
                break

    db.commit()
    db.finish_scan(scan_id, datetime.now().isoformat(), count)
    if progress_cb:
        progress_cb(count, "Done")
    return count
