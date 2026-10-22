"""Executed in an isolated remote Python process; all database state is in RAM."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import types
import time

PROTOCOL = "DAILY_REVIEW_REMOTE_V1:"


def execute(request):
    # Sources are the trusted local collector implementation, never log-provided code.
    package = types.ModuleType("daily_review")
    package.__path__ = []
    sys.modules["daily_review"] = package
    for name in ("util", "projects", "collector", "store"):
        module = types.ModuleType("daily_review." + name)
        module.__package__ = "daily_review"
        sys.modules[module.__name__] = module
        exec(compile(request["modules"][name], "<daily-review-" + name + ">", "exec"), module.__dict__)
    from daily_review.collector import Collector
    from daily_review.store import SCHEMA
    root = Path(request.get("codex_home") or os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve()
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA temp_store=MEMORY")
    db.executescript(SCHEMA)
    for f in request.get("files", []):
        db.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?)", tuple(f[k] for k in (
            "path", "device", "inode", "offset", "line", "prefix_length", "prefix_hash", "tail_hash", "context", "updated_at")))
    for s in request.get("sessions", []):
        db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", tuple(s[k] for k in ("id", "initial_cwd", "started_at", "source", "excluded")))
    db.commit()
    cfg = {"codex_home": str(root), "state_dir": "/__daily_review_in_memory_only__",
           "exclude_session_ids": request.get("exclude_session_ids", []),
           "exclude_cwds": request.get("exclude_cwds", []),
           "project_overrides": request.get("project_overrides", [])}
    byte_budget = request.get("max_scan_bytes", 8 * 1024 * 1024)
    used, deferred = 0, 0
    original_scan = Collector.scan_file
    def bounded_scan(collector, path):
        nonlocal used, deferred
        stat = path.stat()
        old = db.execute("SELECT offset,device,inode FROM files WHERE path=?", (str(path),)).fetchone()
        cost = max(0, stat.st_size - old["offset"]) if old and old["device"] == stat.st_dev and old["inode"] == stat.st_ino and stat.st_size >= old["offset"] else stat.st_size
        if cost and used and used + cost > byte_budget:
            deferred += 1
            return
        collector.config["_read_byte_budget"] = max(1, byte_budget - used)
        prior_resets = collector.stats["resets"]
        original_scan(collector, path)
        checkpoint = db.execute("SELECT offset FROM files WHERE path=?", (str(path),)).fetchone()
        base = old["offset"] if old and collector.stats["resets"] == prior_resets else 0
        used += max(0, checkpoint[0] - base) if checkpoint else 0
    Collector.scan_file = bounded_scan
    stats = Collector(db, cfg).scan()
    def rows(table):
        return [dict(r) for r in db.execute("SELECT * FROM " + table)]
    result = {"version": 1, "ok": root.is_dir() and any((root / name).is_dir() for name in ("sessions", "archived_sessions")),
              "codex_home": str(root), "python": sys.version.split()[0], "stats": stats,
              "more": bool(deferred or stats.get("budget_yields")), "source_bytes_scanned": used,
              "files": rows("files"), "sessions": rows("sessions"), "events": rows("events"),
              "provenance": rows("provenance"), "issues": rows("issues")}
    db.close()
    return result


def main():
    try:
        request = json.load(sys.stdin)
        # No source output is allowed to mix with the protocol response.
        with contextlib.redirect_stdout(io.StringIO()):
            response = execute(request)
    except Exception as exc:
        response = {"version": 1, "ok": False, "error": type(exc).__name__}
    print(PROTOCOL + json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    main()
