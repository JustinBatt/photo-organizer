"""
Quarantine = move a whole folder somewhere safe instead of deleting it.
Nothing is deleted; "Restore" moves it back to where it came from.
"""
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime

from .database import Database, normalize_path

QUARANTINE_SETTING = "quarantine_root"


class QuarantineError(Exception):
    """Raised with a message that is fine to show to the user."""


@dataclass
class QuarantineResult:
    item_id: int
    original: str
    destination: str
    photo_count: int


def _is_inside(child: str, parent: str) -> bool:
    c, p = normalize_path(child), normalize_path(parent)
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def _safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "folder"


def quarantine_folder(db: Database, folder: str, root: str) -> QuarantineResult:
    if not os.path.isdir(folder):
        raise QuarantineError(f"The folder no longer exists:\n{folder}")
    base = os.path.basename(os.path.normpath(folder))
    if not base or os.path.dirname(os.path.normpath(folder)) == os.path.normpath(folder):
        raise QuarantineError("A whole drive can't be quarantined.")
    if _is_inside(root, folder):
        raise QuarantineError("The quarantine location can't be inside the folder being quarantined.")
    if _is_inside(folder, root):
        raise QuarantineError("This folder is already inside the quarantine location.")
    try:
        os.makedirs(root, exist_ok=True)
    except OSError as e:
        raise QuarantineError(f"Can't use the quarantine location:\n{root}\n\n{e}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(root, f"{stamp}_{_safe_name(base)}")
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(root, f"{stamp}_{_safe_name(base)}_{n}")

    try:
        shutil.move(folder, dest)
    except Exception as e:  # PermissionError, files in use, disk full...
        if os.path.isdir(folder) and os.path.isdir(dest):
            raise QuarantineError(
                "The folder was copied to the quarantine location but the original "
                "couldn't be fully removed (a file may be open in another program). "
                "Nothing was deleted, and the app still treats the folder as active.\n\n"
                f"Copy: {dest}\n{e}"
            )
        raise QuarantineError(f"Couldn't move the folder:\n{e}")

    count = db.set_quarantined(folder, True)
    item_id = db.add_quarantine_item(folder, dest)
    db.log_action(folder, "quarantine", f"moved to {dest}", datetime.now().isoformat())
    db.commit()
    return QuarantineResult(item_id, folder, dest, count)


def restore_item(db: Database, item_id: int) -> str:
    """Move a quarantined folder back. Returns the restored path."""
    rows = [r for r in db.quarantine_items() if r["id"] == item_id]
    if not rows:
        raise QuarantineError("That item isn't in quarantine any more.")
    item = rows[0]
    original, held = item["original_path"], item["quarantine_path"]
    if not os.path.isdir(held):
        raise QuarantineError(f"The quarantined folder can't be found:\n{held}")
    if os.path.exists(original):
        raise QuarantineError(
            f"Something already exists at the original location:\n{original}\n\n"
            "Rename or move it, then try again."
        )
    try:
        os.makedirs(os.path.dirname(original), exist_ok=True)
        shutil.move(held, original)
    except Exception as e:
        raise QuarantineError(f"Couldn't restore the folder:\n{e}")
    db.set_quarantined(original, False)
    db.mark_quarantine_restored(item_id)
    db.log_action(original, "restore", f"restored from {held}", datetime.now().isoformat())
    db.commit()
    return original
