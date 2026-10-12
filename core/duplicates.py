"""
Exact-ish duplicate detection (same filename + size, different folder)
plus folder-level grouping/survivorship recommendations.

Folder-level rules (per the agreed spec):
  - 100% of a folder's photos are duplicated elsewhere  -> SURVIVE
    (shown for awareness; NOT recommended for deletion by default -
    it might be an intentional backup).
  - Some-but-not-all are duplicated (>= MOSTLY_DUPLICATE_THRESHOLD)
    -> DELETE the duplicated subset by default, and surface the
    remaining unique photos as separate "exceptions" to review.
  - Below the threshold -> no folder-level grouping; duplicates in that
    folder stay as ordinary individual duplicate groups.
"""
from dataclasses import dataclass, field
from typing import Optional

from . import config
from .database import Database

FOLDER_STATUS_FULL_DUP = "full_duplicate"      # 100% - recommend SURVIVE
FOLDER_STATUS_MOSTLY_DUP = "mostly_duplicate"  # partial - recommend DELETE subset


@dataclass
class DupGroupRecommendation:
    group_key: str
    photos: list  # sqlite3.Row
    keep_path: str
    reasons: dict  # path -> human-readable reason string


def _score_photo(row, folder_status_map: dict[str, str]) -> int:
    score = 0
    if row["has_exif"]:
        score += config.SCORE_HAS_EXIF
    if row["is_organized_folder"]:
        score += config.SCORE_ORGANIZED_FOLDER
    return score


def build_duplicate_groups(db: Database) -> list[DupGroupRecommendation]:
    """
    Groups non-excluded photos by (lowercased filename, size). Any group
    with 2+ members is a duplicate group. Writes dup_group_key back to
    the DB and returns scored recommendations for the GUI.
    """
    photos = db.all_photos(include_excluded=False)

    buckets: dict[tuple[str, int], list] = {}
    for row in photos:
        key = (row["filename"].lower(), row["size"])
        buckets.setdefault(key, []).append(row)

    recommendations: list[DupGroupRecommendation] = []

    for (fname, size), rows in buckets.items():
        if len(rows) < 2:
            db.set_dup_group(rows[0]["path"], None)
            continue

        group_key = f"{fname}::{size}"
        for row in rows:
            db.set_dup_group(row["path"], group_key)

        # Score each candidate; higher wins. Tie-break: larger file size,
        # then older modified_time (earlier = more likely original).
        def sort_key(r):
            return (
                -_score_photo(r, {}),
                -r["size"],
                r["modified_time"],
            )

        ranked = sorted(rows, key=sort_key)
        keeper = ranked[0]

        reasons = {}
        for row in rows:
            bits = []
            if row["path"] == keeper["path"]:
                bits.append("Recommended keep")
            if row["has_exif"]:
                bits.append("has camera metadata")
            if row["is_organized_folder"]:
                bits.append("in an organized folder")
            if not row["has_exif"] and not row["is_organized_folder"]:
                bits.append("no metadata, unorganized folder")
            reasons[row["path"]] = ", ".join(bits) if bits else "—"

        recommendations.append(
            DupGroupRecommendation(
                group_key=group_key,
                photos=rows,
                keep_path=keeper["path"],
                reasons=reasons,
            )
        )

    db.commit()
    return recommendations


@dataclass
class FolderGroupRecommendation:
    folder: str
    total: int
    duplicated: int
    status: str  # FOLDER_STATUS_FULL_DUP or FOLDER_STATUS_MOSTLY_DUP
    recommendation: str  # human-readable
    duplicated_photo_paths: list[str] = field(default_factory=list)
    unique_photo_paths: list[str] = field(default_factory=list)


def build_folder_groups(db: Database) -> list[FolderGroupRecommendation]:
    """
    Must be called AFTER build_duplicate_groups (relies on dup_group_key
    already being set). Computes per-folder duplication ratio and
    classifies folders as full/mostly duplicate.
    """
    stats = db.folder_dup_stats()
    photos_by_folder: dict[str, list] = {}
    for row in db.all_photos(include_excluded=False):
        photos_by_folder.setdefault(row["folder"], []).append(row)

    results: list[FolderGroupRecommendation] = []

    for folder, s in stats.items():
        total, duped = s["total"], s["duped"] or 0
        if total == 0:
            continue
        ratio = duped / total

        if duped == total:
            status = FOLDER_STATUS_FULL_DUP
            recommendation = (
                f"All {total} photos already exist elsewhere. Default: "
                f"SURVIVE (not recommended for deletion - could be an "
                f"intentional backup)."
            )
        elif ratio >= config.MOSTLY_DUPLICATE_THRESHOLD:
            status = FOLDER_STATUS_MOSTLY_DUP
            recommendation = (
                f"{duped}/{total} photos already exist elsewhere. "
                f"Default: DELETE the {duped} duplicated photos; "
                f"{total - duped} unique photo(s) held out for separate review."
            )
        else:
            continue  # below threshold -> leave as individual groups

        folder_rows = photos_by_folder[folder]
        dup_paths = [r["path"] for r in folder_rows if r["dup_group_key"]]
        unique_paths = [r["path"] for r in folder_rows if not r["dup_group_key"]]

        db.set_dup_folder_status(folder, status)

        results.append(
            FolderGroupRecommendation(
                folder=folder,
                total=total,
                duplicated=duped,
                status=status,
                recommendation=recommendation,
                duplicated_photo_paths=dup_paths,
                unique_photo_paths=unique_paths,
            )
        )

    db.commit()
    return results


def individual_only_groups(
    dup_groups: list[DupGroupRecommendation],
    folder_groups: list[FolderGroupRecommendation],
) -> list[DupGroupRecommendation]:
    """
    Filters dup_groups down to ones NOT already covered by a folder-level
    recommendation, so the GUI doesn't show the same photos twice.
    """
    folders_covered = {fg.folder for fg in folder_groups}
    result = []
    for g in dup_groups:
        # A group is "covered" only if ALL its photos live in folders that
        # are themselves fully covered by a folder-level group (their
        # duplicated members are already accounted for there).
        if all(_photo_folder(p) in folders_covered for p in g.photos):
            continue
        result.append(g)
    return result


def _photo_folder(row) -> str:
    return row["folder"]
