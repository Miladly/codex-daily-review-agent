import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
from . import __version__
from .collector import Collector
from .config import load, initialize, config_path
from .model import doctor, minimized_input, summarize
from .notion import Client, apply_plan, make_plan, remote_snapshot
from .review import build_report, save_report
from .store import connect
from .util import lock, redact, write_json
from .daily import run_daily
from .remote import collect_all


def emit(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def parser():
    p = argparse.ArgumentParser(description="Daily Review Agent: local Codex log drafts; no experiments or automatic Notion writes")
    p.add_argument("--config", help="optional local JSON config")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="inspect local paths and CLI capability without a model call")
    q = sub.add_parser("init", help="create private local configuration; never overwrite or contact external services")
    q.add_argument("--codex-home", help="source Codex directory, defaults to CODEX_HOME or ~/.codex")
    q.add_argument("--timezone", default="Asia/Shanghai")
    q.add_argument("--reports-dir", help="optional Markdown/Obsidian folder; JSON and database stay in private state")
    q = sub.add_parser("migrate-reports", help="preview copying Markdown to another folder; --apply copies and switches config with a backup")
    q.add_argument("--to", required=True, help="destination folder, relative to the configuration file")
    q.add_argument("--apply", action="store_true", help="copy verified notes and switch output; originals are retained")
    q = sub.add_parser("collect", help="read-only incremental ingestion")
    q.add_argument("--replay", action="store_true", help="re-read source files from zero with dedup; retain historical evidence")
    q = sub.add_parser("daily", help="collect local and configured SSH sources, refresh recent drafts; no model or Notion writes")
    q.add_argument("--through", default="today", help="today, yesterday or YYYY-MM-DD in configured timezone")
    q.add_argument("--days", type=int, default=3, help="refresh this many days, default 3; no-event new days are skipped")
    q = sub.add_parser("export-events", help="write versioned local interchange; private data, never upload with source")
    q.add_argument("--date", required=True)
    for cmd in ("review", "run"):
        q = sub.add_parser(cmd, help="render a draft" if cmd == "review" else "collect then render a draft")
        q.add_argument("--date", help="YYYY-MM-DD, default today in configured timezone")
        q.add_argument("--backend", choices=["rules", "codex"], default="rules", help="codex sends only bounded redacted excerpts to the model")
        q.add_argument("--model-timeout", type=int, default=240)
    q = sub.add_parser("model-preview", help="write exact minimized model input locally, no model call")
    q.add_argument("--date", required=True)
    q = sub.add_parser("notion-plan", help="default offline dry-run; --online only reads Notion")
    q.add_argument("--date", required=True)
    q.add_argument("--online", action="store_true")
    q = sub.add_parser("notion-sync", help="write only an explicitly approved online plan")
    q.add_argument("--plan", required=True)
    q.add_argument("--approve", required=True, help="approval_hash from the reviewed online plan")
    return p


def stored_report(db, date, tz):
    row = db.execute("SELECT payload FROM reports WHERE date=? AND timezone=?", (date, tz)).fetchone()
    if not row:
        raise ValueError("generate a local review for this date first")
    return json.loads(row["payload"])


def main(argv=None):
    os.umask(0o077)
    args = parser().parse_args(argv)
    try:
        if os.name != "posix":
            raise RuntimeError("Supported platforms: macOS and Linux. On Windows use WSL2; native Windows locking is unsupported.")
        if args.command == "init":
            path = initialize(args.config or os.environ.get("DAILY_REVIEW_CONFIG") or "config.local.json", args.codex_home, args.timezone, args.reports_dir)
            emit({"config": str(path), "remote_writes": 0, "model_calls": 0})
            return 0
        cfg = load(args.config)
        state = Path(cfg["state_dir"])
        if args.command == "migrate-reports":
            from .migration import migrate_reports
            emit(migrate_reports(config_path(args.config), args.to, apply=args.apply))
            return 0
        if args.command == "doctor":
            home = Path(cfg["codex_home"])
            available = home.is_dir() and any((home / name).is_dir() for name in ("sessions", "archived_sessions"))
            emit({"status": "ok" if available else "attention", "source_available": available,
                  "source_hint": "" if available else "No Codex sessions directory found; set codex_home or create a Codex session first.",
                  "version": __version__, "codex_home_env": os.environ.get("CODEX_HOME"),
                  "resolved_codex_home": str(home), "state_dir": str(state), "reports_dir": cfg["reports_dir"], "timezone": cfg["timezone"],
                  "sessions": len(list((home / "sessions").rglob("*.jsonl"))),
                  "archived_sessions": len(list((home / "archived_sessions").rglob("*.jsonl"))),
                  "excluded_session_ids": cfg["exclude_session_ids"], "excluded_cwds": cfg["exclude_cwds"],
                  "remote_sources": [{"id": s["id"], "host": s["host"], "enabled": s.get("enabled", True)} for s in cfg["remote_sources"]],
                  "codex_cli": doctor(cfg.get("codex_binary", "codex")), "network_calls": 0})
            return 0 if available else 2
        with lock(state / ".agent.lock"):
            db = connect(state)
            try:
                if args.command == "daily":
                    result = run_daily(db, cfg, args.through, args.days)
                    emit(result)
                    return 2 if result["status"] == "attention" else 0
                if args.command == "export-events":
                    from .exchange import export_events
                    path = state / "exchange" / (args.date + ".events.json")
                    result = export_events(db, args.date, cfg["timezone"])
                    write_json(path, result)
                    emit({"path": str(path), "events": len(result["events"]), "schema_version": 1, "remote_writes": 0})
                    return 0
                stats = None
                if args.command in ("collect", "run"):
                    if getattr(args, "replay", False):
                        from .util import digest
                        with db:
                            db.execute("UPDATE files SET offset=0,line=0,prefix_length=0,prefix_hash=?,tail_hash=?,context='{}'", (digest(b""), digest(b"")))
                    stats = collect_all(db, cfg)
                    if args.command == "collect":
                        emit(stats)
                        return 2 if stats["source_errors"] or stats["issues"] else 0
                if args.command in ("review", "run"):
                    date = args.date or datetime.now(ZoneInfo(cfg["timezone"])).date().isoformat()
                    report = build_report(db, date, cfg["timezone"])
                    # Persist the deterministic draft first, even when the optional backend fails.
                    path = save_report(db, cfg, report)
                    error = None
                    if args.backend == "codex":
                        try:
                            report = summarize(report, cfg, args.model_timeout)
                            path = save_report(db, cfg, report)
                        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as e:
                            error = redact(str(e), 500, True)
                            report["model_error"] = error
                            path = save_report(db, cfg, report)
                    emit({"markdown": str(path), "json": str(state / "reports" / (date + ".json")),
                          "sqlite": str(state / "review.sqlite3"), "coverage": report["coverage"],
                          "backend": report["backend"], "collection": stats, "model_error": error})
                    return 2 if error or (stats and (stats["source_errors"] or stats["issues"])) else 0
                if args.command == "model-preview":
                    report = build_report(db, args.date, cfg["timezone"])
                    path = state / "model-input" / (args.date + ".json")
                    write_json(path, minimized_input(report))
                    emit({"preview": str(path), "network_calls": 0})
                    return 0
                if args.command == "notion-plan":
                    report = stored_report(db, args.date, cfg["timezone"])
                    snapshot = remote_snapshot(Client(), cfg["notion"], args.date) if args.online else None
                    plan = make_plan(report, cfg["notion"], db, snapshot)
                    path = state / "notion-plans" / (args.date + (".online.json" if args.online else ".dry-run.json"))
                    write_json(path, plan)
                    emit({"plan": str(path), "action": plan["action"], "reason": plan["reason"],
                          "relation_ids": plan["relation_ids"], "unmatched_projects": plan["unmatched_projects"],
                          "create_projects": plan.get("create_projects", []),
                          "approval_hash": plan["approval_hash"], "remote_writes": 0})
                    return 0
                if args.command == "notion-sync":
                    plan = json.loads(Path(args.plan).read_text())
                    report = stored_report(db, plan["date"], cfg["timezone"])
                    emit(apply_plan(Client(), cfg["notion"], db, report, plan, args.approve))
                    return 0
            finally:
                db.close()
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as e:
        print("daily-review: " + redact(str(e), 600, True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
