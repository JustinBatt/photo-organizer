"""
Thin SQLite wrapper. Keeps scan results on disk so re-opening the app
or re-filtering doesn't require re-walking the whole file system.
"""
import sqlite3
from pathlib import Path
from typing import Iterable, Optional, Any


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
"""


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

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
            "SELECT * FROM photos WHERE excluded = 0"
        ).fetchall()

    def excluded_photos(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM photos WHERE excluded = 1"
        ).fetchall()

    def distinct_folders(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT folder FROM photos WHERE excluded = 0 ORDER BY folder"
        ).fetchall()
        return [r["folder"] for r in rows]

    def browse(
        self,
        folder: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        search_text: Optional[str] = None,
    ) -> list[sqlite3.Row]:
        query = "SELECT * FROM photos WHERE excluded = 0"
        params: list[Any] = []
        if folder:
            query += " AND folder = ?"
            params.append(folder)
        if date_from:
            query += " AND COALESCE(date_taken, date(modified_time, 'unixepoch')) >= ?"
            params.append(date_from)
        if date_to:
            query += " AND COALESCE(date_taken, date(modified_time, 'unixepoch')) <= ?"
            params.append(date_to)
        if search_text:
            query += " AND (filename LIKE ? OR path LIKE ?)"
            like = f"%{search_text}%"
            params.extend([like, like])
        query += " ORDER BY COALESCE(date_taken, '') DESC, modified_time DESC"
        return self.conn.execute(query, params).fetchall()

    def set_dup_group(self, path: str, dup_group_key: Optional[str]):
        self.conn.execute(
            "UPDATE photos SET dup_group_key = ? WHERE path = ?",
            (dup_group_key, path),
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
            "SELECT * FROM photos WHERE excluded = 0 AND dup_group_key IS NOT NULL "
            "ORDER BY dup_group_key"
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
            "FROM photos WHERE excluded = 0 GROUP BY folder"
        ).fetchall()
        return {
            r["folder"]: {"total": r["total"], "duped": r["duped"]} for r in rows
        }

    def delete_photo_records(self, paths: Iterable[str]):
        self.conn.executemany(
            "DELETE FROM photos WHERE path = ?", [(p,) for p in paths]
        )
        self.conn.commit()

    def log_action(self, path: str, action: str, reason: str, performed_at: str):
        self.conn.execute(
            "INSERT INTO actions_log (path, action, reason, performed_at) "
            "VALUES (?, ?, ?, ?)",
            (path, action, reason, performed_at),
        )
        self.conn.commit()
