"""
Thin SQLite wrapper. Keeps scan results on disk so re-opening the app
or re-filtering doesn't require re-walking the whole file system.
"""
import os
import sqlite3
import threading
from datetime import datetime
from typing import Iterable, Optional, Any


def normalize_path(path) -> str:
    """Canonical form used to compare paths (case-insensitive on Windows)."""
    return os.path.normcase(os.path.normpath(str(path)))


SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roots TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    file_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL,
    path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    folder TEXT NOT NULL,
    extension TEXT NOT NULL,
    size INTEGER NOT NULL,
    modified_time REAL NOT NULL,
    date_taken TEXT,
    camera_make TEXT,
    camera_model TEXT,
    width INTEGER,
    height INTEGER,
    has_exif INTEGER NOT NULL DEFAULT 0,
    is_organized_folder INTEGER NOT NULL DEFAULT 0,
    excluded INTEGER NOT NULL DEFAULT 0,
    exclude_reason TEXT,
    dup_group_key TEXT,
    dup_folder_status TEXT,
    phash TEXT,
    ignored INTEGER NOT NULL DEFAULT 0,
    quarantined INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (scan_id) REFERENCES scans (id)
);

CREATE INDEX IF NOT EXISTS idx_photos_folder ON photos (folder);
CREATE INDEX IF NOT EXISTS idx_photos_date_taken ON photos (date_taken);
CREATE INDEX IF NOT EXISTS idx_photos_dup_group ON photos (dup_group_key);
CREATE INDEX IF NOT EXISTS idx_photos_excluded ON photos (excluded);

CREATE TABLE IF NOT EXISTS actions_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT,
    performed_at TEXT NOT NULL
);

-- Files/folders the user chose to ignore ("by file" / "by folder").
-- A folder rule also covers every sub-folder beneath it.
CREATE TABLE IF NOT EXISTS ignore_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('file', 'folder')),
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (kind, path)
);

-- Virtual albums: nothing on disk is moved or copied.
CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS album_photos (
    album_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (album_id, path),
    FOREIGN KEY (album_id) REFERENCES albums (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_album_photos_path ON album_photos (path);

-- The user's verdict on a duplicate group, so it is not asked about again.
CREATE TABLE IF NOT EXISTS dup_decisions (
    group_key TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'not_duplicate')),
    decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Whole folders moved to the quarantine location (restorable).
CREATE TABLE IF NOT EXISTS quarantine_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_path TEXT NOT NULL,
    quarantine_path TEXT NOT NULL,
    moved_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'quarantined' CHECK (status IN ('quarantined', 'restored')),
    restored_at TEXT
);
"""

# Photos the user can currently see: not junk, not ignored, not quarantined.
_VISIBLE = "excluded = 0 AND ignored = 0 AND quarantined = 0"
_NOT_IN_ANY_ALBUM = (
    "NOT EXISTS (SELECT 1 FROM album_photos ap WHERE ap.path = photos.path)"
)
_CHUNK = 500  # stay well below SQLite's bound-variable limit


class Database:
    """
    SQLite connections can only be used on the thread that created them,
    but the GUI runs scans/hashing on background QThreads. So instead of
    one shared connection, every thread that touches the database gets its
    own connection to the same file, created on demand (see `conn`).
    WAL mode lets the GUI thread keep reading while a worker is writing.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        self._all_conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()

        conn = self.conn  # creates this (the calling) thread's connection
        # Old databases have a photos table without the newest columns; add
        # those first so the CREATE INDEX statements in SCHEMA can't fail.
        self._pre_migrate(conn)
        conn.executescript(SCHEMA)
        self._migrate(conn)
        conn.commit()

    @staticmethod
    def _pre_migrate(conn: sqlite3.Connection):
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='photos'"
        ).fetchone()
        if not exists:
            return
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(photos)")}
        for name in ("ignored", "quarantined"):
            if name not in cols:
                conn.execute(
                    f"ALTER TABLE photos ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0"
                )

    def _migrate(self, conn: sqlite3.Connection):
        """Upgrade a database file created by an older version of the app."""
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(photos)")}
        if "ignored" not in cols:
            conn.execute(
                "ALTER TABLE photos ADD COLUMN ignored INTEGER NOT NULL DEFAULT 0"
            )
        if "quarantined" not in cols:
            conn.execute(
                "ALTER TABLE photos ADD COLUMN quarantined INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_ignored ON photos (ignored)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_quarantined ON photos (quarantined)")

    @property
    def conn(self) -> sqlite3.Connection:
        """The current thread's own connection (created on first use)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass  # e.g. a filesystem without WAL support - fall back silently
            self._local.conn = conn
            with self._conns_lock:
                self._all_conns.append(conn)
        return conn

    def close_thread_connection(self):
        """Call from a worker thread when it is done, to release its connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return
        self._local.conn = None
        with self._conns_lock:
            if conn in self._all_conns:
                self._all_conns.remove(conn)
        try:
            conn.close()
        except sqlite3.Error:
            pass

    def close(self):
        """Close every connection (called once, when the app exits)."""
        with self._conns_lock:
            conns, self._all_conns = self._all_conns, []
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._local.conn = None

    # --- scans -----------------------------------------------------

    def create_scan(self, roots: list[str], started_at: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO scans (roots, started_at) VALUES (?, ?)",
            ("|".join(roots), started_at),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_scan(self, scan_id: int, finished_at: str, file_count: int):
        self.conn.execute(
            "UPDATE scans SET finished_at = ?, file_count = ? WHERE id = ?",
            (finished_at, file_count, scan_id),
        )
        self.conn.commit()

    def clear_scan_data(self, scan_id: Optional[int] = None):
        """Remove previously indexed photos, optionally for one scan only."""
        if scan_id is None:
            self.conn.execute("DELETE FROM photos")
        else:
            self.conn.execute("DELETE FROM photos WHERE scan_id = ?", (scan_id,))
        self.conn.commit()

    # --- ignore rules ------------------------------------------------

    def _apply_rule(self, kind: str, path: str) -> int:
        """Flag matching photos as ignored. Returns how many were newly hidden."""
        if kind == "file":
            cur = self.conn.execute(
                "UPDATE photos SET ignored = 1 WHERE ignored = 0 AND path = ?",
                (path,),
            )
        else:
            # The folder itself, or anything beneath it (prefix match with a
            # separator so "Screenshots" never matches "Screenshots 2").
            prefix = path.rstrip("\\/") + os.sep
            cur = self.conn.execute(
                "UPDATE photos SET ignored = 1 WHERE ignored = 0 AND "
                "(folder = ? COLLATE NOCASE OR substr(folder, 1, ?) = ? COLLATE NOCASE)",
                (path, len(prefix), prefix),
            )
        return cur.rowcount

    def add_ignore_rule(self, kind: str, path: str) -> int:
        """Ignore a file or folder from now on. Returns photos newly hidden."""
        if kind not in ("file", "folder"):
            raise ValueError(f"unknown ignore kind: {kind}")
        self.conn.execute(
            "INSERT OR IGNORE INTO ignore_rules (kind, path, created_at) VALUES (?, ?, ?)",
            (kind, path, datetime.now().isoformat()),
        )
        hidden = self._apply_rule(kind, path)
        self.conn.commit()
        return hidden

    def remove_ignore_rule(self, kind: str, path: str):
        """Stop ignoring. Photos covered by another remaining rule stay hidden."""
        self.conn.execute(
            "DELETE FROM ignore_rules WHERE kind = ? AND path = ?", (kind, path)
        )
        self.conn.execute("UPDATE photos SET ignored = 0")
        for rule in self.conn.execute("SELECT kind, path FROM ignore_rules").fetchall():
            self._apply_rule(rule["kind"], rule["path"])
        self.conn.commit()

    def list_ignore_rules(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT kind, path, created_at FROM ignore_rules ORDER BY id DESC"
        ).fetchall()

    def ignore_sets(self) -> tuple[set[str], list[str]]:
        """(normalized ignored file paths, normalized ignored folder paths)
        for the scanner to skip."""
        files: set[str] = set()
        folders: list[str] = []
        for rule in self.conn.execute("SELECT kind, path FROM ignore_rules"):
            if rule["kind"] == "file":
                files.add(normalize_path(rule["path"]))
            else:
                folders.append(normalize_path(rule["path"]))
        return files, folders

    # --- photos ------------------------------------------------------

    def upsert_photo(self, record: dict[str, Any]):
        cols = (
            "scan_id", "path", "filename", "folder", "extension", "size",
            "modified_time", "date_taken", "camera_make", "camera_model",
            "width", "height", "has_exif", "is_organized_folder",
            "excluded", "exclude_reason",
        )
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        update_clause = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "path")
        sql = (
            f"INSERT INTO photos ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT(path) DO UPDATE SET {update_clause}"
        )
        self.conn.execute(sql, tuple(record.get(c) for c in cols))

    def commit(self):
        self.conn.commit()

    def all_photos(self, include_excluded: bool = False) -> list[sqlite3.Row]:
        if include_excluded:
            return self.conn.execute("SELECT * FROM photos").fetchall()
        return self.conn.execute(
            f"SELECT * FROM photos WHERE {_VISIBLE}"
        ).fetchall()

    def excluded_photos(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM photos WHERE excluded = 1"
        ).fetchall()

    def distinct_folders(self, hide_albumed: bool = True) -> list[str]:
        """Folders that still have at least one photo to show in Browse."""
        query = f"SELECT DISTINCT folder FROM photos WHERE {_VISIBLE}"
        if hide_albumed:
            query += f" AND {_NOT_IN_ANY_ALBUM}"
        rows = self.conn.execute(query + " ORDER BY folder").fetchall()
        return [r["folder"] for r in rows]

    def browse(
        self,
        folder: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        search_text: Optional[str] = None,
        hide_albumed: bool = True,
    ) -> list[sqlite3.Row]:
        query = f"SELECT * FROM photos WHERE {_VISIBLE}"
        params: list[Any] = []
        if hide_albumed:
            query += f" AND {_NOT_IN_ANY_ALBUM}"
        if folder:
            query += " AND folder = ?"
            params.append(folder)
        if date_from:
            query += " AND COALESCE(date_taken, date(modified_time, 'unixepoch')) >= ?"
            params.append(date_from)
        if date_to:
            # date_taken is a full timestamp, so compare against the end of the day.
            query += (
                " AND COALESCE(date_taken, date(modified_time, 'unixepoch')) "
                "< date(?, '+1 day')"
            )
            params.append(date_to)
        if search_text:
            query += " AND (filename LIKE ? OR path LIKE ?)"
            like = f"%{search_text}%"
            params.extend([like, like])
        query += " ORDER BY COALESCE(date_taken, '') DESC, modified_time DESC"
        return self.conn.execute(query, params).fetchall()

    def photos_by_paths(self, paths: Iterable[str]) -> list[sqlite3.Row]:
        paths = list(paths)
        rows: list[sqlite3.Row] = []
        for i in range(0, len(paths), _CHUNK):
            chunk = paths[i:i + _CHUNK]
            marks = ",".join("?" for _ in chunk)
            rows.extend(self.conn.execute(
                f"SELECT * FROM photos WHERE path IN ({marks})", chunk
            ).fetchall())
        return rows

    def get_photo(self, path: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM photos WHERE path = ?", (path,)
        ).fetchone()

    # --- albums (virtual: nothing on disk moves) --------------------------

    def create_album(self, name: str) -> int:
        """Create an album. Raises ValueError if the name is already taken."""
        try:
            cur = self.conn.execute(
                "INSERT INTO albums (name, created_at) VALUES (?, ?)",
                (name, datetime.now().isoformat()),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f'An album named "{name}" already exists.')
        self.conn.commit()
        return cur.lastrowid

    def album_id_by_name(self, name: str) -> Optional[int]:
        row = self.conn.execute(
            "SELECT id FROM albums WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        return row["id"] if row else None

    def list_albums(self) -> list[sqlite3.Row]:
        """All albums with how many visible photos each holds (name order)."""
        return self.conn.execute(
            "SELECT a.id, a.name, a.created_at, COUNT(p.id) AS photo_count "
            "FROM albums a "
            "LEFT JOIN album_photos ap ON ap.album_id = a.id "
            "LEFT JOIN photos p ON p.path = ap.path "
            "  AND p.excluded = 0 AND p.ignored = 0 AND p.quarantined = 0 "
            "GROUP BY a.id ORDER BY a.name COLLATE NOCASE"
        ).fetchall()

    def rename_album(self, album_id: int, new_name: str):
        try:
            self.conn.execute(
                "UPDATE albums SET name = ? WHERE id = ?", (new_name, album_id)
            )
        except sqlite3.IntegrityError:
            raise ValueError(f'An album named "{new_name}" already exists.')
        self.conn.commit()

    def delete_album(self, album_id: int):
        """Delete the album only - its photos are untouched and return to Browse."""
        self.conn.execute("DELETE FROM album_photos WHERE album_id = ?", (album_id,))
        self.conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
        self.conn.commit()

    def add_photos_to_album(self, album_id: int, paths: Iterable[str]) -> int:
        """Returns how many photos were newly added (already-present ones skipped)."""
        now = datetime.now().isoformat()
        before = self.conn.total_changes
        self.conn.executemany(
            "INSERT OR IGNORE INTO album_photos (album_id, path, added_at) VALUES (?, ?, ?)",
            [(album_id, p, now) for p in dict.fromkeys(paths)],
        )
        self.conn.commit()
        return self.conn.total_changes - before

    def remove_photos_from_album(self, album_id: int, paths: Iterable[str]):
        self.conn.executemany(
            "DELETE FROM album_photos WHERE album_id = ? AND path = ?",
            [(album_id, p) for p in paths],
        )
        self.conn.commit()

    def album_photos(self, album_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT p.* FROM album_photos ap JOIN photos p ON p.path = ap.path "
            "WHERE ap.album_id = ? AND p.excluded = 0 AND p.ignored = 0 "
            "AND p.quarantined = 0 "
            "ORDER BY COALESCE(p.date_taken, '') ASC, p.modified_time ASC",
            (album_id,),
        ).fetchall()

    def albumed_paths(self) -> set[str]:
        return {r["path"] for r in self.conn.execute("SELECT DISTINCT path FROM album_photos")}

    def albums_containing(self, path: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT a.name FROM albums a JOIN album_photos ap ON ap.album_id = a.id "
            "WHERE ap.path = ? ORDER BY a.name COLLATE NOCASE", (path,)
        ).fetchall()
        return [r["name"] for r in rows]

    # --- settings ---------------------------------------------------------

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row and row["value"] is not None else default

    def set_setting(self, key: str, value: Optional[str]):
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    # --- duplicate bookkeeping --------------------------------------------

    def set_dup_groups(self, mapping: dict[str, Optional[str]]):
        """Store dup_group_key for many photos at once. Only rows whose value
        actually changed are written (a 65,000-photo table has few changes
        between runs). Does not commit."""
        current = {
            r["path"]: r["dup_group_key"]
            for r in self.conn.execute("SELECT path, dup_group_key FROM photos")
        }
        changes = [
            (key, path) for path, key in mapping.items()
            if path in current and current[path] != key
        ]
        if changes:
            self.conn.executemany(
                "UPDATE photos SET dup_group_key = ? WHERE path = ?", changes
            )

    def dup_decisions(self) -> dict[str, str]:
        return {
            r["group_key"]: r["status"]
            for r in self.conn.execute("SELECT group_key, status FROM dup_decisions")
        }

    def set_dup_decision(self, group_key: str, status: Optional[str]):
        """status: 'confirmed', 'not_duplicate', or None to forget the decision."""
        if status is None:
            self.conn.execute("DELETE FROM dup_decisions WHERE group_key = ?", (group_key,))
        else:
            self.conn.execute(
                "INSERT INTO dup_decisions (group_key, status, decided_at) VALUES (?, ?, ?) "
                "ON CONFLICT(group_key) DO UPDATE SET status = excluded.status, "
                "decided_at = excluded.decided_at",
                (group_key, status, datetime.now().isoformat()),
            )
        self.conn.commit()

    def reset_dup_decisions(self):
        self.conn.execute("DELETE FROM dup_decisions")
        self.conn.commit()

    # --- quarantine -------------------------------------------------------

    def set_quarantined(self, folder: str, flag: bool) -> int:
        """Flag a folder (and everything beneath it) as quarantined / back."""
        prefix = folder.rstrip("\\/") + os.sep
        cur = self.conn.execute(
            "UPDATE photos SET quarantined = ? WHERE "
            "(folder = ? COLLATE NOCASE OR substr(folder, 1, ?) = ? COLLATE NOCASE)",
            (1 if flag else 0, folder, len(prefix), prefix),
        )
        return cur.rowcount

    def count_visible_in_folder(self, folder: str) -> int:
        """Visible photos in a folder and all of its sub-folders."""
        prefix = folder.rstrip("\\/") + os.sep
        row = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM photos WHERE {_VISIBLE} AND "
            "(folder = ? COLLATE NOCASE OR substr(folder, 1, ?) = ? COLLATE NOCASE)",
            (folder, len(prefix), prefix),
        ).fetchone()
        return row["n"]

    def add_quarantine_item(self, original: str, quarantined: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO quarantine_items (original_path, quarantine_path, moved_at) "
            "VALUES (?, ?, ?)",
            (original, quarantined, datetime.now().isoformat()),
        )
        return cur.lastrowid

    def quarantine_items(self, only_active: bool = True) -> list[sqlite3.Row]:
        query = "SELECT * FROM quarantine_items"
        if only_active:
            query += " WHERE status = 'quarantined'"
        return self.conn.execute(query + " ORDER BY id DESC").fetchall()

    def mark_quarantine_restored(self, item_id: int):
        self.conn.execute(
            "UPDATE quarantine_items SET status = 'restored', restored_at = ? WHERE id = ?",
            (datetime.now().isoformat(), item_id),
        )

    def set_dup_folder_status(self, folder: str, status: Optional[str]):
        self.conn.execute(
            "UPDATE photos SET dup_folder_status = ? WHERE folder = ?",
            (status, folder),
        )

    def set_phash(self, path: str, phash: str):
        self.conn.execute(
            "UPDATE photos SET phash = ? WHERE path = ?", (phash, path)
        )

    def duplicate_groups(self) -> dict[str, list[sqlite3.Row]]:
        rows = self.conn.execute(
            f"SELECT * FROM photos WHERE {_VISIBLE} "
            "AND dup_group_key IS NOT NULL ORDER BY dup_group_key"
        ).fetchall()
        groups: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            groups.setdefault(row["dup_group_key"], []).append(row)
        return groups

    def folder_dup_stats(self) -> dict[str, dict]:
        rows = self.conn.execute(
            "SELECT folder, "
            "COUNT(*) AS total, "
            "SUM(CASE WHEN dup_group_key IS NOT NULL THEN 1 ELSE 0 END) AS duped "
            f"FROM photos WHERE {_VISIBLE} GROUP BY folder"
        ).fetchall()
        return {
            r["folder"]: {"total": r["total"], "duped": r["duped"]} for r in rows
        }

    def delete_photo_records(self, paths: Iterable[str]):
        rows = [(p,) for p in paths]
        self.conn.executemany("DELETE FROM photos WHERE path = ?", rows)
        self.conn.executemany("DELETE FROM album_photos WHERE path = ?", rows)
        self.conn.commit()

    def log_action(self, path: str, action: str, reason: str, performed_at: str):
        self.conn.execute(
            "INSERT INTO actions_log (path, action, reason, performed_at) "
            "VALUES (?, ?, ?, ?)",
            (path, action, reason, performed_at),
        )
        self.conn.commit()
