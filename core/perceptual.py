"""
STRETCH GOAL (Phase 5): visual duplicate detection using perceptual
hashing. Catches images that are visually the same but don't match on
filename/size (resized copies, re-compressed copies, screenshots, etc).

This is a separate, optional, slower pass - run it after the main
(exact-ish) duplicate pass. Results are similarity scores, not hard
"duplicate" verdicts, since false positives are more likely here.
"""
from typing import Callable, Optional

try:
    import imagehash
    from PIL import Image
    PERCEPTUAL_AVAILABLE = True
except ImportError:
    PERCEPTUAL_AVAILABLE = False

from . import config
from .database import Database

ProgressCallback = Callable[[int, int], None]  # (done, total)


def compute_hashes(
    db: Database,
    progress_cb: Optional[ProgressCallback] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> int:
    """Computes and stores a perceptual hash for every non-excluded photo
    that doesn't already have one. Returns count processed."""
    if not PERCEPTUAL_AVAILABLE:
        raise RuntimeError(
            "Perceptual hashing requires the 'imagehash' package. "
            "Install with: pip install imagehash"
        )

    photos = [p for p in db.all_photos() if not p["phash"]]
    done = 0
    for row in photos:
        if should_stop and should_stop():
            break
        try:
            with Image.open(row["path"]) as img:
                h = imagehash.phash(img)
                db.set_phash(row["path"], str(h))
        except Exception:
            pass  # unreadable/corrupt - just skip, not fatal
        done += 1
        if progress_cb and done % 25 == 0:
            progress_cb(done, len(photos))

    db.commit()
    if progress_cb:
        progress_cb(done, len(photos))
    return done


def find_visual_duplicate_groups(
    db: Database,
    max_distance: int = config.PERCEPTUAL_HASH_MAX_DISTANCE,
) -> list[list[dict]]:
    """
    Naive O(n^2) comparison of all hashed photos. Fine for a few thousand
    photos; for very large libraries this should move to a bucketed/
    approximate-nearest-neighbor approach (e.g. bktree) as a future
    optimization.
    """
    if not PERCEPTUAL_AVAILABLE:
        raise RuntimeError("imagehash not installed")

    rows = [p for p in db.all_photos() if p["phash"]]
    hashes = []
    for r in rows:
        try:
            hashes.append((r, imagehash.hex_to_hash(r["phash"])))
        except Exception:
            continue

    visited = set()
    groups: list[list[dict]] = []

    for i in range(len(hashes)):
        row_i, hash_i = hashes[i]
        if row_i["path"] in visited:
            continue
        cluster = [row_i]
        visited.add(row_i["path"])
        for j in range(i + 1, len(hashes)):
            row_j, hash_j = hashes[j]
            if row_j["path"] in visited:
                continue
            # Skip pairs already caught by exact filename+size matching -
            # this pass is specifically for *visually* similar but not
            # byte/name-identical files.
            if row_i["dup_group_key"] and row_i["dup_group_key"] == row_j["dup_group_key"]:
                continue
            distance = hash_i - hash_j
            if distance <= max_distance:
                cluster.append(row_j)
                visited.add(row_j["path"])
        if len(cluster) > 1:
            groups.append([dict(r) for r in cluster])

    return groups
