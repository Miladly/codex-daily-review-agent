"""A bounded entry point for local drafts, including configured read-only SSH sources."""
from datetime import datetime, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from .remote import collect_all
from .notion import make_plan
from .review import build_report, save_report
from .journal import FORMAT_VERSION
from .util import digest, now, write_json


def dates_for(through, days, tzname, at=None):
    if not 1 <= days <= 31:
        raise ValueError("days must be between 1 and 31")
    today = (at or datetime.now(ZoneInfo(tzname))).astimezone(ZoneInfo(tzname)).date()
    if through in ("today", "yesterday"):
        end = today - timedelta(days=int(through == "yesterday"))
    else:
        end = datetime.strptime(through, "%Y-%m-%d").date()
    return [(end - timedelta(days=n)).isoformat() for n in reversed(range(days))]


def material_fingerprint(report):
    data = {k: v for k, v in report.items() if k != "generated_at"}
    return digest(data)


def run_daily(db, cfg, through="today", days=3):
    dates = dates_for(through, days, cfg["timezone"])
    stats = collect_all(db, cfg)
    results = []
    for date in dates:
        report = build_report(db, date, cfg["timezone"])
        previous = db.execute("SELECT payload FROM reports WHERE date=? AND timezone=?", (date, cfg["timezone"])).fetchone()
        old = json.loads(previous[0]) if previous else None
        if not report["coverage"]["events"] and old is None:
            results.append({"date": date, "status": "no_events", "changed": False})
            continue
        changed = old is None or material_fingerprint(old) != material_fingerprint(report)
        if not changed:
            report["generated_at"] = old["generated_at"]
        path = save_report(db, cfg, report)
        plan = make_plan(report, cfg["notion"], db)
        plan_path = Path(cfg["state_dir"]) / "notion-plans" / (date + ".dry-run.json")
        write_json(plan_path, plan)
        results.append({"date": date, "status": "draft", "changed": changed, "markdown": str(path),
                        "coverage": report["coverage"], "format": report["format"], "work_records": len(report["work_records"]),
                        "notion_plan": str(plan_path),
                        "projects_to_create": len(plan.get("create_projects", [])),
                        "unmatched_projects": len(plan["unmatched_projects"])})
    result = {"status": "attention" if stats["issues"] or stats["source_errors"] else "ok", "ran_at": now(),
              "timezone": cfg["timezone"], "format": FORMAT_VERSION, "collection": stats, "days": results,
              "changed_days": sum(r["changed"] for r in results), "remote_writes": 0, "model_calls": 0}
    root = Path(cfg["state_dir"]) / "runs"
    write_json(root / "last-run.json", result)
    return result
