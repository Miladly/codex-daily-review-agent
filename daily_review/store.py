from pathlib import Path
import os
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
 path TEXT PRIMARY KEY, device INTEGER, inode INTEGER, offset INTEGER NOT NULL,
 line INTEGER NOT NULL, prefix_length INTEGER NOT NULL, prefix_hash TEXT NOT NULL,
 tail_hash TEXT NOT NULL, context TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, initial_cwd TEXT, started_at TEXT, source TEXT, excluded INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
 timestamp TEXT NOT NULL, kind TEXT NOT NULL, evidence TEXT NOT NULL,
 text TEXT NOT NULL, cwd TEXT NOT NULL, project_id TEXT NOT NULL,
 project_name TEXT NOT NULL, details TEXT NOT NULL, priority INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS event_time ON events(timestamp);
CREATE INDEX IF NOT EXISTS event_session ON events(session_id);
CREATE TABLE IF NOT EXISTS provenance (
 event_id TEXT NOT NULL, path TEXT NOT NULL, byte_offset INTEGER NOT NULL, line INTEGER NOT NULL,
 record_hash TEXT NOT NULL, PRIMARY KEY(event_id,path,byte_offset,record_hash)
);
CREATE TABLE IF NOT EXISTS issues (
 id TEXT PRIMARY KEY, path TEXT NOT NULL, byte_offset INTEGER, category TEXT NOT NULL,
 message TEXT NOT NULL, observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
 date TEXT NOT NULL, timezone TEXT NOT NULL, content_hash TEXT NOT NULL,
 payload TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(date,timezone)
);
CREATE TABLE IF NOT EXISTS sync_state (
 page_id TEXT PRIMARY KEY, block_id TEXT NOT NULL, content_hash TEXT NOT NULL,
 date TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_links (
 project_id TEXT PRIMARY KEY, notion_page_id TEXT NOT NULL, name TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connector_receipts (
 page_id TEXT PRIMARY KEY, date TEXT NOT NULL, content_hash TEXT NOT NULL,
 verified_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS remote_sessions (
 source_id TEXT NOT NULL, session_id TEXT NOT NULL, PRIMARY KEY(source_id,session_id)
);
CREATE TABLE IF NOT EXISTS remote_status (
 source_id TEXT PRIMARY KEY, status TEXT NOT NULL, attempted_at TEXT NOT NULL,
 codex_home TEXT NOT NULL, error TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_status (
 source_id TEXT PRIMARY KEY, status TEXT NOT NULL, attempted_at TEXT NOT NULL,
 error TEXT NOT NULL
);
PRAGMA user_version=1;
"""


def connect(state_dir):
    root = Path(state_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / "review.sqlite3"
    db = sqlite3.connect(path, timeout=15)
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise RuntimeError("local database uses a newer schema; use a compatible Agent version and preserve private backups")
        os.chmod(path, 0o600)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(SCHEMA)
    except BaseException:
        db.close()
        raise
    return db
