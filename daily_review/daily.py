"""A bounded entry point for local drafts, including configured read-only SSH sources."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo
from .remote import collect_all
from .notion import make_plan
from .review import build_report, save_report
from .journal import FORMAT_VERSION
from .model import apply_summary, minimized_input, summarize, validate_output
from .util import digest, now, redact, write_json


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


def run_daily(db, cfg, through="today", days=3, backend="rules", model_timeout=240, progress=None):
    if backend not in ("rules", "codex") or model_timeout <= 0:
        raise ValueError("invalid model backend or timeout")
    dates = dates_for(through, days, cfg["timezone"])
    root = Path(cfg["state_dir"]) / "runs"
    pending = root / "in-progress.json"
    write_json(pending, {"started_at": now(), "dates": dates, "backend": backend})
    def emit(stage, **metadata):
        if progress:
            progress({"stage": stage, **metadata})
    emit("collection_started", days=len(dates))
    stats = collect_all(db, cfg)
    emit("collection_finished", inserted=stats.get("inserted", 0), source_errors=stats.get("source_errors", 0))
    results, model_calls, model_errors = [], 0, 0
    for date in dates:
        emit("day_started", date=date)
        report = build_report(db, date, cfg["timezone"])
        previous = db.execute("SELECT payload FROM reports WHERE date=? AND timezone=?", (date, cfg["timezone"])).fetchone()
        old = json.loads(previous[0]) if previous else None
        if not report["coverage"]["events"] and old is None:
            results.append({"date": date, "status": "no_events", "changed": False})
            emit("day_finished", date=date, status="no_events")
            continue
        model_status = "disabled"
        if backend == "codex" and report["coverage"]["events"]:
            data = minimized_input(report)
            # Include the adapter/prompt contract and configured model in addition
            # to exact selected evidence. Coverage changes invalidate the answer.
            key = digest({"contract": "daily-model-1", "input": data, "timezone": cfg["timezone"],
                          "model": cfg.get("model"), "binary": cfg.get("codex_binary", "codex")})
            cache_path = Path(cfg["state_dir"]) / "model-cache" / (date + ".json")
            cached = None
            try:
                cached = json.loads(cache_path.read_text())
                if cached.get("input_hash") != key:
                    cached = None
                elif cached:
                    validate_output(cached["summary"], data)
                    if not isinstance(cached["metadata"], dict):
                        cached = None
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                cached = None
            if cached:
                report = apply_summary(report, cached["summary"], cached["metadata"])
                model_status = "cached"
                emit("model_cached", date=date)
            else:
                # Save facts before any network operation. A killed/timed-out
                # process cannot leave a changed day without a readable draft.
                report["model_status"] = "pending"
                report["limitations"].append("模型提炼进行中；当前暂存本地规则工作记录。")
                save_report(db, cfg, report)
                report = build_report(db, date, cfg["timezone"])
                model_calls += 1
                emit("model_started", date=date, timeout_seconds=model_timeout)
                try:
                    report = summarize(report, cfg, model_timeout)
                    validate_output(report["model_summary"], data)
                    write_json(cache_path, {"input_hash": key, "summary": report["model_summary"],
                                            "metadata": report["model_input"]})
                    model_status = "generated"
                    emit("model_finished", date=date)
                except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
                    # A mismatched older answer must never describe new evidence.
                    report = build_report(db, date, cfg["timezone"])
                    model_status = "fallback"
                    model_errors += 1
                    report["model_error"] = "模型提炼未完成；保留本地规则工作记录，请稍后重试。"
                    report["limitations"].append(report["model_error"])
                    write_json(Path(cfg["state_dir"]) / "model-failures" / (date + ".json"),
                               {"at": now(), "input_hash": key, "error": redact(str(error), 500, True)})
                    emit("model_failed", date=date)
        report["model_status"] = "ready" if model_status in ("cached", "generated") else model_status
        changed = old is None or material_fingerprint(old) != material_fingerprint(report)
        if not changed:
            report["generated_at"] = old["generated_at"]
        path = save_report(db, cfg, report)
        plan = make_plan(report, cfg["notion"], db)
        plan_path = Path(cfg["state_dir"]) / "notion-plans" / (date + ".dry-run.json")
        write_json(plan_path, plan)
        results.append({"date": date, "status": "draft", "changed": changed, "markdown": str(path),
                        "coverage": report["coverage"], "format": report["format"], "work_records": len(report["work_records"]),
                        "backend": report["backend"], "model_status": model_status,
                        "model_work_records": len(report.get("model_work_records", [])),
                        "notion_plan": str(plan_path),
                        "projects_to_create": len(plan.get("create_projects", [])),
                        "unmatched_projects": len(plan["unmatched_projects"])})
        emit("day_finished", date=date, backend=report["backend"], model_status=model_status, changed=changed,
             markdown=str(path))
    result = {"status": "attention" if stats["issues"] or stats["source_errors"] or model_errors else "ok", "ran_at": now(),
              "timezone": cfg["timezone"], "format": FORMAT_VERSION, "collection": stats, "days": results,
              "changed_days": sum(r["changed"] for r in results), "remote_writes": 0,
              "model_calls": model_calls, "model_errors": model_errors}
    write_json(root / "last-run.json", result)
    pending.unlink()
    return result
