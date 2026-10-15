"""
Metadata-based duplicate detection plus folder-level grouping/survivorship
recommendations.

How two photos are judged to be the same picture (metadata only, never pixels):
  - Normal case: same DATE TAKEN + same CAMERA (make/model) + same FILE SIZE.
    This still matches when a copy was renamed.
  - Fallback for photos with no usable camera data (screenshots, downloads,
    scans...): same FILE NAME + same FILE SIZE.

The user can overrule any group: "Confirm" marks it as a verified duplicate,
"Not duplicates" removes it from the list for good (stored in dup_decisions).

Folder-level rules (per the agreed spec):
  - 100% of a folder's photos are duplicated elsewhere  -> SURVIVE
    (shown for awareness; NOT recommended for deletion by default -
    it might be an intentional backup).
  - Some-but-not-all are duplicated (>= MOSTLY_DUPLICATE_THRESHOLD)
    -> DELETE the duplicated subset by default, and surface the
    remaining unique photos as separate "exceptions" to review.
  - Below the threshold -> no folder-level grouping; duplicates in that
    folder stay as ordinary individual duplicate groups.

Safety rule (always enforced): the app never pre-selects, and never allows
applying, a selection that would remove EVERY copy of a photo.
"""
from dataclasses import dataclass, field
from typing import Iterable, Optional

from . import config
from .database import Database

FOLDER_STATUS_FULL_DUP = "full_duplicate"      # 100% - recommend SURVIVE
FOLDER_STATUS_MOSTLY_DUP = "mostly_duplicate"  # partial - recommend DELETE subset

BASIS_METADATA = "metadata"
BASIS_NAME_SIZE = "name+size"

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
DECISION_CONFIRMED = "confirmed"
DECISION_NOT_DUPLICATE = "not_duplicate"


@dataclass
class DupGroupRecommendation:
    group_key: str
    photos: list  # sqlite3.Row
    keep_path: str
    reasons: dict  # path -> human-readable reason string
    status: str = STATUS_PENDING       # STATUS_PENDING or STATUS_CONFIRMED
    basis: str = BASIS_NAME_SIZE       # BASIS_METADATA or BASIS_NAME_SIZE


def _score_photo(row) -> int:
    score = 0
    if row["has_exif"]:
        score += config.SCORE_HAS_EXIF
    if row["is_organized_folder"]:
        score += config.SCORE_ORGANIZED_FOLDER
    return score


def match_key(row) -> tuple[str, str]:
    """(group key, basis) describing what makes this photo 'the same' as others."""
    make = (row["camera_make"] or "").strip().lower()
    model = (row["camera_model"] or "").strip().lower()
    date_taken = row["date_taken"]
    if date_taken and (make or model):
        return f"meta::{date_taken}|{make}|{model}|{row['size']}", BASIS_METADATA
    return f"name::{row['filename'].lower()}|{row['size']}", BASIS_NAME_SIZE


def _folder_key(row) -> str:
    return row["folder"].lower()


def build_duplicate_groups(db: Database) -> list[DupGroupRecommendation]:
    """
    Groups visible photos by metadata (see module doc). Any group with 2+
    members is a duplicate group, unless the user marked it "not duplicates".
    Writes dup_group_key back to the DB (only rows that changed) and returns
    scored recommendations for the GUI, biggest groups first.
    """
    photos = db.all_photos(include_excluded=False)
    decisions = db.dup_decisions()
    albumed = db.albumed_paths()  # a copy already filed in an album is the one to keep

    buckets: dict[str, list] = {}
    basis_of: dict[str, str] = {}
    for row in photos:
        key, basis = match_key(row)
        buckets.setdefault(key, []).append(row)
        basis_of[key] = basis

    recommendations: list[DupGroupRecommendation] = []
    stored_keys: dict[str, Optional[str]] = {}

    for key, rows in buckets.items():
        if len(rows) < 2 or decisions.get(key) == DECISION_NOT_DUPLICATE:
            for row in rows:
                stored_keys[row["path"]] = None
            continue
        for row in rows:
            stored_keys[row["path"]] = key

        # Higher score wins. Tie-break: larger file, then older modified
        # time (earlier = more likely the original), then path for stability.
        ranked = sorted(
            rows,
            key=lambda r: (
                r["path"] not in albumed, -r["rating"], -_score_photo(r), -r["size"],
                r["modified_time"], r["path"],
            ),
        )
        keeper = ranked[0]

        reasons = {}
        for row in rows:
            bits = []
            if row["path"] == keeper["path"]:
                bits.append("Recommended keep")
            if row["has_exif"]:
                bits.append("has camera metadata")
            if row["path"] in albumed:
                bits.append("already in an album")
            if row["rating"]:
                bits.append("rated " + "★" * row["rating"])
            if row["is_organized_folder"]:
                bits.append("in an organized folder")
            if not row["has_exif"] and not row["is_organized_folder"] and row["path"] not in albumed and not row["rating"]:
                bits.append("no metadata, unorganized folder")
            reasons[row["path"]] = ", ".join(bits) if bits else "—"

        recommendations.append(
            DupGroupRecommendation(
                group_key=key,
                photos=ranked,
                keep_path=keeper["path"],
                reasons=reasons,
                status=STATUS_CONFIRMED if decisions.get(key) == DECISION_CONFIRMED
                else STATUS_PENDING,
                basis=basis_of[key],
            )
        )

    db.set_dup_groups(stored_keys)
    db.commit()
    recommendations.sort(key=lambda g: (-len(g.photos), g.group_key))
    return recommendations


def groups_spanning_folders(
    dup_groups: Iterable[DupGroupRecommendation],
) -> list[DupGroupRecommendation]:
    """Groups whose copies live in at least two different folders - the only
    kind that makes a folder 'duplicated elsewhere'."""
    return [g for g in dup_groups if len({_folder_key(r) for r in g.photos}) > 1]


@dataclass
class FolderGroupRecommendation:
    folder: str
    total: int
    duplicated: int
    status: str  # FOLDER_STATUS_FULL_DUP or FOLDER_STATUS_MOSTLY_DUP
    recommendation: str  # human-readable
    duplicated_photo_paths: list[str] = field(default_factory=list)
    unique_photo_paths: list[str] = field(default_factory=list)
    # Sub-folders (anywhere beneath this folder) that hold indexed photos, and how
    # many photos those hold. A quarantine moves these along with the folder.
    subfolder_count: int = 0
    subfolder_photos: int = 0


def folder_descendants(photo_counts: dict[str, int]) -> dict[str, tuple[set, int]]:
    """For each folder (lower-cased): (the sub-folders beneath it that hold photos,
    total photos in them). `photo_counts` maps folder -> photos directly in it."""
    import os
    stats: dict[str, tuple[set, int]] = {}
    for folder, count in photo_counts.items():
        current = folder
        while True:
            parent = os.path.dirname(current)
            if not parent or parent == current:
                break
            subs, total = stats.get(parent.lower(), (set(), 0))
            subs.add(folder.lower())
            stats[parent.lower()] = (subs, total + count)
            current = parent
    return stats


def build_folder_groups(
    db: Database, dup_groups: list[DupGroupRecommendation]
) -> list[FolderGroupRecommendation]:
    """
    Classifies folders as full/mostly duplicate. A photo counts as "duplicated
    elsewhere" only if one of its copies lives in a DIFFERENT folder.
    """
    elsewhere: set[str] = set()
    for g in groups_spanning_folders(dup_groups):
        elsewhere.update(r["path"] for r in g.photos)

    photos_by_folder: dict[str, list] = {}
    for row in db.all_photos(include_excluded=False):
        photos_by_folder.setdefault(row["folder"], []).append(row)

    sub_stats = folder_descendants(
        {folder: len(rows) for folder, rows in photos_by_folder.items()}
    )
    results: list[FolderGroupRecommendation] = []

    for folder, folder_rows in photos_by_folder.items():
        total = len(folder_rows)
        dup_paths = [r["path"] for r in folder_rows if r["path"] in elsewhere]
        unique_paths = [r["path"] for r in folder_rows if r["path"] not in elsewhere]
        duped = len(dup_paths)
        if total == 0 or duped == 0:
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

        results.append(
            FolderGroupRecommendation(
                folder=folder,
                total=total,
                duplicated=duped,
                status=status,
                recommendation=recommendation,
                duplicated_photo_paths=dup_paths,
                unique_photo_paths=unique_paths,
                subfolder_count=len(sub_stats.get(folder.lower(), (set(), 0))[0]),
                subfolder_photos=sub_stats.get(folder.lower(), (set(), 0))[1],
            )
        )

    results.sort(key=lambda f: f.folder.lower())
    return results


def individual_only_groups(
    dup_groups: list[DupGroupRecommendation],
    folder_groups: list[FolderGroupRecommendation],
) -> list[DupGroupRecommendation]:
    """
    Filters dup_groups down to ones NOT already shown in the folder-level
    section, so the GUI doesn't list the same photos twice. A group is hidden
    only if EVERY one of its copies is already listed there.
    """
    covered = {p for fg in folder_groups for p in fg.duplicated_photo_paths}
    return [g for g in dup_groups if not all(r["path"] in covered for r in g.photos)]


def default_selection(
    folder_groups: list[FolderGroupRecommendation],
    individual_groups: list[DupGroupRecommendation],
    all_groups: list[DupGroupRecommendation],
) -> set[str]:
    """
    Paths to pre-check for removal:
      - the duplicated photos of every 'mostly duplicate' folder,
      - every non-recommended copy in the individual groups.
    Folders that are 100% duplicated are never pre-checked.
    Then the safety rule is applied: if a group would lose every copy, its
    recommended keeper is taken back out of the selection.
    """
    selected: set[str] = set()
    for fg in folder_groups:
        if fg.status == FOLDER_STATUS_MOSTLY_DUP:
            selected.update(fg.duplicated_photo_paths)
    for g in individual_groups:
        selected.update(r["path"] for r in g.photos if r["path"] != g.keep_path)
    for g in all_groups:
        if all(r["path"] in selected for r in g.photos):
            selected.discard(g.keep_path)
    return selected


def groups_losing_every_copy(
    selected_paths: Iterable[str], all_groups: list[DupGroupRecommendation]
) -> list[DupGroupRecommendation]:
    """Groups for which the selection would remove every single copy."""
    selected = set(selected_paths)
    return [g for g in all_groups if all(r["path"] in selected for r in g.photos)]
