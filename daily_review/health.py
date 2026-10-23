"""Read-only run health. No logs, event text, model calls or scheduler changes."""
import json
from pathlib import Path
from .daily import dates_for


def run_status(cfg, through="yesterday", backend="rules", at=None):
    expected = dates_for(through, 1, cfg["timezone"], at)[0]
    path = Path(cfg["state_dir"]) / "runs/last-run.json"
    note = Path(cfg["reports_dir"]) / (expected + ".md")
    result = {"status": "attention", "expected_date": expected, "expected_backend": backend,
              "timezone": cfg["timezone"], "last_run": str(path), "ran_at": None,
              "markdown": str(note), "note_exists": note.is_file(), "day": None, "reasons": [],
              "network_calls": 0, "model_calls": 0, "scheduler_checked": False}
    result["unfinished_run"] = (path.parent / "in-progress.json").exists()
    if result["unfinished_run"]:
        result["reasons"].append("run_unfinished")
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("days"), list):
            raise ValueError("invalid run metadata")
    except FileNotFoundError:
        result["reasons"].append("no_completed_run")
        return result
    except (OSError, ValueError):
        result["reasons"].append("run_metadata_unreadable")
        return result
    result["ran_at"] = data.get("ran_at")
    if data.get("timezone") != cfg["timezone"]:
        result["reasons"].append("timezone_changed")
    if data.get("status") != "ok":
        result["reasons"].append("last_run_incomplete")
    day = next((d for d in data["days"] if isinstance(d, dict) and d.get("date") == expected), None)
    if day is None:
        result["reasons"].append("expected_date_not_processed")
    else:
        result["day"] = {key: day.get(key) for key in ("date", "status", "backend", "model_status")}
        if day.get("status") not in ("draft", "no_events"):
            result["reasons"].append("day_incomplete")
        if day.get("status") == "draft":
            if not result["note_exists"]:
                result["reasons"].append("note_missing")
            if backend == "codex" and (day.get("backend") != "codex" or
                                       day.get("model_status") not in ("generated", "cached")):
                result["reasons"].append("model_review_incomplete")
    result["status"] = "attention" if result["reasons"] else "ok"
    return result
