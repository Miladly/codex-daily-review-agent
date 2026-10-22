"""SSH read-only extraction. Only bounded redacted events reach the Mac."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from .util import digest, now, redact

PROTOCOL = "DAILY_REVIEW_REMOTE_V1:"


def ssh_command(source, worker):
    host = source["host"]
    if not re.fullmatch(r"[A-Za-z0-9_.@:-]+", host) or host.startswith("-"):
        raise ValueError("invalid SSH host")
    cmd = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
           "-o", "ClearAllForwardings=yes", "-o", "PermitLocalCommand=no", "-o", "RemoteCommand=none"]
    if sys.platform == "darwin":
        cmd += ["-o", "UseKeychain=yes"]
    cmd += [host, "python3 -I -B -c " + shlex.quote(worker)]
    return cmd


def agent_environment():
    env = dict(os.environ)
    if sys.platform == "darwin" and not env.get("SSH_AUTH_SOCK"):
        try:
            found = subprocess.run(["/bin/launchctl", "getenv", "SSH_AUTH_SOCK"], capture_output=True, text=True, timeout=3).stdout.strip()
            if found and Path(found).exists():
                env["SSH_AUTH_SOCK"] = found
        except (OSError, subprocess.TimeoutExpired):
            pass
    return env


def request_for(db, cfg, source):
    sid = source["id"]
    prefix = "ssh://" + sid + "/"
    files = []
    for row in db.execute("SELECT * FROM files WHERE path LIKE ?", (prefix + "%",)):
        row = dict(row)
        row["path"] = "/" + row["path"][len(prefix):]
        files.append(row)
    sessions = [dict(r) for r in db.execute("SELECT s.* FROM sessions s JOIN remote_sessions r ON s.id=r.session_id WHERE r.source_id=?", (sid,))]
    module_root = Path(__file__).parent
    return {"codex_home": source.get("codex_home"), "files": files, "sessions": sessions,
            "max_scan_bytes": source.get("max_scan_bytes", 8 * 1024 * 1024),
            "exclude_session_ids": cfg.get("exclude_session_ids", []),
            "exclude_cwds": source.get("exclude_cwds", []), "project_overrides": source.get("project_overrides", []),
            "modules": {n: (module_root / (n + ".py")).read_text() for n in ("util", "projects", "collector", "store")}}


def fetch(db, cfg, source):
    worker = (Path(__file__).parent / "remote_worker.py").read_text()
    proc = subprocess.run(ssh_command(source, worker), input=json.dumps(request_for(db, cfg, source), ensure_ascii=False),
                          capture_output=True, text=True, timeout=source.get("timeout", 90), env=agent_environment())
    if proc.returncode:
        raise RuntimeError("SSH exit {}: {}".format(proc.returncode, redact(proc.stderr[-350:], 350, True)))
    records = [line[len(PROTOCOL):] for line in proc.stdout.splitlines() if line.startswith(PROTOCOL)]
    if len(records) != 1:
        raise RuntimeError("remote response missing or ambiguous; no cursor advanced")
    response = json.loads(records[0])
    if not response.get("ok") or response.get("version") != 1:
        raise RuntimeError("remote extraction failed: " + str(response.get("error", "session directory missing")))
    for field in ("files", "sessions", "events", "provenance", "issues"):
        if not isinstance(response.get(field), list):
            raise RuntimeError("invalid remote response; no cursor advanced")
    return response


def fetch_reliable(db, cfg, source):
    try:
        return fetch(db, cfg, source)
    except (OSError, subprocess.TimeoutExpired):
        pass
    except RuntimeError as exc:
        msg = str(exc)
        if not msg.startswith(("SSH exit", "remote response")) or any(x in msg for x in ("Permission denied", "Host key verification failed")):
            raise
    time.sleep(0.5)
    return fetch(db, cfg, source)


def merge(db, source, response):
    sid = source["id"]
    def uri(path):
        return "ssh://" + sid + "/" + path.lstrip("/")
    inserted = 0
    with db:
        for s in response["sessions"]:
            db.execute("INSERT INTO sessions VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET excluded=sessions.excluded OR excluded.excluded", tuple(s[k] for k in ("id", "initial_cwd", "started_at", "source", "excluded")))
            db.execute("INSERT OR IGNORE INTO remote_sessions VALUES(?,?)", (sid, s["id"]))
        for e in response["events"]:
            details = json.loads(e["details"])
            details["origin_host"] = sid
            details["origin_label"] = source.get("label", sid)
            project = digest(["remote", sid, e["project_id"]])[:20]
            name = source.get("label", sid) + " · " + e["project_name"]
            old = db.execute("SELECT details FROM events WHERE id=?", (e["id"],)).fetchone()
            # A handed-off/mirrored event keeps a single event ID and all source locations.
            if old and json.loads(old[0]).get("origin_host") != sid:
                continue
            db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET cwd=excluded.cwd,project_id=excluded.project_id,project_name=excluded.project_name,details=excluded.details", (
                e["id"], e["session_id"], e["turn_id"], e["timestamp"], e["kind"], e["evidence"],
                redact(e["text"]), e["cwd"], project, name, json.dumps(details, ensure_ascii=False), e["priority"]))
            inserted += int(old is None)
        for p in response["provenance"]:
            db.execute("INSERT OR IGNORE INTO provenance VALUES(?,?,?,?,?)", (p["event_id"], uri(p["path"]), p["byte_offset"], p["line"], p["record_hash"]))
        for f in response["files"]:
            db.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?,?)", (uri(f["path"]),) + tuple(f[k] for k in (
                "device", "inode", "offset", "line", "prefix_length", "prefix_hash", "tail_hash", "context", "updated_at")))
        for issue in response["issues"]:
            db.execute("INSERT OR REPLACE INTO issues VALUES(?,?,?,?,?,?)", (digest([sid, issue["id"]]), uri(issue["path"]),
                        issue["byte_offset"], issue["category"], redact(issue["message"], 200), issue["observed_at"]))
        db.execute("INSERT OR REPLACE INTO remote_status VALUES(?,?,?,?,?)", (sid, "ok", now(), response["codex_home"], ""))
    return inserted


def collect_remote(db, cfg):
    results = []
    for source in cfg.get("remote_sources", []):
        if not source.get("enabled", True):
            continue
        try:
            inserted, batches, totals = 0, 0, {}
            for _ in range(source.get("max_batches", 128)):
                response = fetch_reliable(db, cfg, source)
                inserted += merge(db, source, response)
                batches += 1
                for key, value in response["stats"].items():
                    totals[key] = totals.get(key, 0) + value
                if not response.get("more"):
                    break
            else:
                raise RuntimeError("remote initial backfill reached batch limit; completed batches retained, rerun to continue")
            results.append({"id": source["id"], "host": source["host"], "status": "ok", "stats": response["stats"],
                            "batch_stats": totals, "batches": batches, "inserted": inserted,
                            "codex_home": response["codex_home"], "raw_logs_downloaded": False})
        except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            error = "remote extraction timed out; no cursor advanced" if isinstance(exc, subprocess.TimeoutExpired) else redact(str(exc), 400, True)
            with db:
                db.execute("INSERT OR REPLACE INTO remote_status VALUES(?,?,?,?,?)", (source["id"], "unavailable", now(), source.get("codex_home", ""), error))
            results.append({"id": source["id"], "host": source["host"], "status": "unavailable", "error": error})
    return results


def collect_all(db, cfg):
    from .collector import Collector
    local = Collector(db, cfg).scan()
    remote = collect_remote(db, cfg)
    local["local_inserted"] = local["inserted"]
    local["remote_sources"] = remote
    local["inserted"] += sum(r.get("inserted", 0) for r in remote)
    local["issues"] += sum(r.get("batch_stats", r.get("stats", {})).get("issues", 0) for r in remote)
    local["source_errors"] = local.get("source_errors", 0) + sum(r["status"] != "ok" for r in remote)
    return local
