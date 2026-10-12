"""
The only place files are actually removed from disk. Always goes through
send2trash (Windows Recycle Bin) rather than permanent deletion, so every
action taken here is reversible by the user if the app got something
wrong.
"""
from datetime import datetime
from typing import Iterable

from send2trash import send2trash

from .database import Database


def send_to_recycle_bin(db: Database, paths: Iterable[str], reason: str) -> tuple[list[str], list[str]]:
    """
    Attempts to move each path to the Recycle Bin. Returns (succeeded,
    failed) path lists. Successful deletions are removed from the DB and
    logged; failures are left in the DB untouched so nothing is silently
    lost from view.
    """
    succeeded, failed = [], []
    now = datetime.now().isoformat()

    for path in paths:
        try:
            send2trash(path)
            db.log_action(path, "recycle", reason, now)
            succeeded.append(path)
        except Exception as e:
            db.log_action(path, "recycle_failed", f"{reason} | error: {e}", now)
            failed.append(path)

    if succeeded:
        db.delete_photo_records(succeeded)

    return succeeded, failed
