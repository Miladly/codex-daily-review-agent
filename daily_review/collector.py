"""Incremental, read-only JSONL ingestion. Source bytes are never copied into SQLite."""
import json
from pathlib import Path
import re
from .projects import Projects
from .util import digest, now, parse_time, redact, under, workspace_path

MAX_LINE = 16 * 1024 * 1024


def text_content(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(x.get("text", "") for x in content if isinstance(x, dict)
                     and x.get("type") in ("text", "input_text", "output_text"))


def normalize(record, ctx):
    """Normalize desktop UI events and older CLI records; never run logged commands."""
    typ, p = record.get("type"), record.get("payload", {})
    if not isinstance(p, dict):
        return []
    item, priority = None, 1
    if typ == "event_msg":
        if p.get("type") == "item_completed":
            item, priority = p.get("item", {}), 3
        elif p.get("type") in ("user_message", "agent_message"):
            role = "user" if p["type"] == "user_message" else "assistant"
            item = {"type": "message", "role": role, "content": p.get("message", "")}
    elif typ == "response_item":
        item = p
    if not isinstance(item, dict):
        return []
    t = item.get("type")
    details, evidence, kind, text = {}, "C", "", ""
    if t in ("UserMessage", "AgentMessage", "message"):
        role = item.get("role") or ("user" if t == "UserMessage" else "assistant")
        if role not in ("user", "assistant"):
            return []
        text = text_content(item.get("content", []))
        # Injected environment/policy wrappers are not the person's work request.
        if role == "user" and text.lstrip().startswith(("# AGENTS.md", "<environment_context>", "<permissions", "<INSTRUCTIONS>")):
            return []
        kind = "intent" if role == "user" else "statement"
        evidence = "C" if role == "user" else "B"
        details["phase"] = item.get("phase", "unspecified")
    elif t == "CommandExecution":
        code, status = item.get("exit_code"), item.get("status")
        kind = "command_error" if status in ("failed", "declined") or (code is not None and code != 0) else "command"
        evidence = "A" if code is not None else "C"
        details = {"exit_code": code, "status": status}
        command = item.get("command", "")
        if isinstance(command, list):
            command = " ".join(str(x) for x in command)
        text = "命令记录：" + redact(command, 280) + "\n返回：" + redact(item.get("aggregated_output") or item.get("stdout") or item.get("formatted_output") or item.get("stderr"), 1100)
        if item.get("cwd"):
            details["event_cwd"] = str(item["cwd"])
    elif t == "FileChange":
        changes = item.get("changes", [])
        if isinstance(changes, dict):
            paths = list(changes)
        else:
            paths = [x.get("path", "") for x in changes if isinstance(x, dict)]
        details = {"artifacts": [redact(p, 400) for p in paths if p], "status": item.get("status")}
        kind, evidence = "file_change", "A" if item.get("status") == "completed" else "C"
        text = "工具报告文件变更（不等于当前文件仍存在）：" + ", ".join(details["artifacts"])
        if item.get("status") in ("failed", "declined"):
            kind, evidence = "file_change_error", "A"
            text = "文件变更失败或未获执行：" + ", ".join(details["artifacts"])
    elif t == "McpToolCall":
        status = item.get("status")
        kind, evidence = ("tool_error" if status in ("failed", "cancelled", "canceled") else "tool"), "A"
        details = {"status": status, "tool": item.get("tool"), "server": item.get("server")}
        text = "工具调用：" + str(item.get("tool", "")) + "；状态：" + str(status)
    elif t in ("function_call", "custom_tool_call"):
        # Retain only the tool name for pairing. Arguments may contain secrets or scripts.
        cid = item.get("call_id")
        if cid:
            pending = ctx.setdefault("pending", {})
            pending[cid] = redact(item.get("name", "tool"), 150)
            while len(pending) > 100:
                pending.pop(next(iter(pending)))
        return []
    elif t in ("function_call_output", "custom_tool_call_output"):
        tool = ctx.setdefault("pending", {}).pop(item.get("call_id"), "unknown")
        output = item.get("output", "")
        if not isinstance(output, str):
            output = json.dumps(output, ensure_ascii=False)
        # Legacy output is evidence only of what the tool reported.
        code = re.search(r"(?:Process exited with code|exit_code[\"']?\s*[:=])\s*(-?\d+)", output)
        kind, evidence = ("command_error" if code and int(code[1]) != 0 else "tool_result"), "A"
        details = {"tool": tool, "exit_code": int(code[1]) if code else None,
                   "call_id": item.get("call_id")}
        text = "旧版工具返回 " + tool + "：" + redact(output, 1100)
    else:
        return []  # Reasoning, world_state, compacted history, images, encrypted content.
    identity = digest(text)
    text = redact(text)
    if not text:
        return []
    return [{"kind": kind, "evidence": evidence, "text": text, "details": details, "priority": priority,
             "identity": identity, "item_id": item.get("id")}]


class Collector:
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.projects = Projects(config)
        self.stats = {k: 0 for k in ("files", "unchanged", "records", "inserted", "excluded", "issues", "resets")}

    def issue(self, path, offset, category, message):
        key = digest([str(path), offset, category])
        self.db.execute("INSERT OR REPLACE INTO issues VALUES(?,?,?,?,?,?)",
                        (key, str(path), offset, category, redact(message, 200), now()))
        self.stats["issues"] += 1

    def excluded(self, ctx):
        source = ctx.get("source")
        if isinstance(source, dict):
            sub = source.get("subagent", {})
            if isinstance(sub, dict):
                if sub.get("other") == "guardian":
                    return True
                parent = sub.get("thread_spawn", {}).get("parent_thread_id")
                if parent:
                    row = self.db.execute("SELECT excluded FROM sessions WHERE id=?", (parent,)).fetchone()
                    if parent in self.config.get("exclude_session_ids", []) or (row and row[0]):
                        return True
        return (ctx.get("sid") in self.config.get("exclude_session_ids", []) or
                str(ctx.get("source", "")).startswith("daily-review") or
                str(ctx.get("thread_source", "")).startswith("daily-review") or
                any(under(ctx.get("cwd", "/"), p) for p in self.config.get("exclude_cwds", [])))

    def scan(self):
        root = Path(self.config["codex_home"])
        # Do not accidentally place agent-owned files inside Codex's state tree.
        if under(self.config["state_dir"], root):
            raise ValueError("state_dir must be outside CODEX_HOME")
        available = root.is_dir() and any((root / name).is_dir() for name in ("sessions", "archived_sessions"))
        if not available:
            with self.db:
                self.issue(root, None, "source_unavailable", "Codex sessions directory missing; set codex_home or create a session first")
            self.stats["source_errors"] = 1
        for folder in ("sessions", "archived_sessions"):
            for path in sorted((root / folder).rglob("*.jsonl")):
                if path.is_symlink() or not under(path, root / folder):
                    continue
                self.stats["files"] += 1
                try:
                    with self.db:
                        self.scan_file(path)
                except (OSError, ValueError, TypeError, KeyError) as e:
                    with self.db:
                        self.issue(path, None, "file_error", type(e).__name__)
        # Enforce newly added exclusions for previously collected sessions as well.
        with self.db:
            status = "unavailable" if not available else "incomplete" if self.stats["issues"] else "ok"
            self.db.execute("INSERT OR REPLACE INTO source_status VALUES(?,?,?,?)", ("codex", status, now(), "" if status == "ok" else "local source missing or incomplete"))
            self.db.execute("UPDATE sessions SET excluded=1 WHERE source LIKE '%guardian%'")
            for sid in self.config.get("exclude_session_ids", []):
                self.db.execute("UPDATE sessions SET excluded=1 WHERE id=?", (sid,))
        return self.stats

    def scan_file(self, path):
        stat = path.stat()
        old = self.db.execute("SELECT * FROM files WHERE path=?", (str(path),)).fetchone()
        offset, line, ctx = 0, 0, {}
        with path.open("rb") as f:
            if old:
                plen = old["prefix_length"]
                f.seek(0)
                prefix = digest(f.read(plen))
                f.seek(max(0, old["offset"] - 256))
                tail = digest(f.read(min(old["offset"], 256)))
                if (stat.st_dev == old["device"] and stat.st_ino == old["inode"] and
                    stat.st_size >= old["offset"] and prefix == old["prefix_hash"] and tail == old["tail_hash"]):
                    offset, line, ctx = old["offset"], old["line"], json.loads(old["context"])
                else:
                    self.stats["resets"] += 1
                    self.issue(path, 0, "cursor_reset", "file replaced, truncated or checkpoint bytes changed; replay with dedup")
            if old and offset == stat.st_size:
                self.stats["unchanged"] += 1
                return
            f.seek(offset)
            scan_start = offset
            # A size snapshot makes a live, growing source finite for this run.
            while f.tell() < stat.st_size:
                budget = self.config.get("_read_byte_budget")
                if budget and f.tell() - scan_start >= budget:
                    self.stats["budget_yields"] = self.stats.get("budget_yields", 0) + 1
                    break
                start = f.tell()
                raw = f.readline(min(MAX_LINE + 1, stat.st_size - start))
                oversized = len(raw) > MAX_LINE
                if oversized:
                    while raw and not raw.endswith(b"\n") and f.tell() < stat.st_size:
                        raw = f.readline(min(MAX_LINE + 1, stat.st_size - f.tell()))
                if not raw.endswith(b"\n"):
                    # A torn or not-yet-flushed tail is retried next time, never consumed.
                    break
                line += 1
                offset = f.tell()
                self.stats["records"] += 1
                if oversized:
                    self.issue(path, start, "oversized_record", "record exceeds 16 MiB; skipped without copying")
                    continue
                try:
                    record = json.loads(raw)
                    if not isinstance(record, dict):
                        raise ValueError("not an object")
                    self.ingest(record, ctx, path, start, line, digest(raw))
                except (ValueError, TypeError, AttributeError) as e:
                    self.issue(path, start, "invalid_record", type(e).__name__)
            f.seek(0)
            plen = min(offset, 4096)
            prefix = digest(f.read(plen))
            f.seek(max(0, offset - 256))
            tail = digest(f.read(min(offset, 256)))
            self.db.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?,?)", (
                str(path), stat.st_dev, stat.st_ino, offset, line, plen, prefix, tail,
                json.dumps(ctx, ensure_ascii=False), now()))

    def ingest(self, record, ctx, path, start, line, raw_hash):
        p, typ = record.get("payload", {}), record.get("type")
        if not isinstance(p, dict):
            return
        if typ == "session_meta":
            sid = p.get("id") or p.get("session_id")
            if not sid:
                raise ValueError("session id missing")
            ctx.update(sid=sid, cwd=p.get("cwd", ""), source=p.get("source"),
                       thread_source=p.get("thread_source"), turn="", pending={})
            self.db.execute("INSERT INTO sessions VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET excluded=sessions.excluded OR excluded.excluded", (
                sid, p.get("cwd"), record.get("timestamp"), str(p.get("source", "")), int(self.excluded(ctx))))
            return
        if typ == "turn_context":
            ctx["cwd"] = p.get("cwd") or ctx.get("cwd", "")
            ctx["turn"] = p.get("turn_id") or ctx.get("turn", "")
        if typ == "event_msg" and p.get("type") == "task_started":
            ctx["turn"] = p.get("turn_id") or p.get("root_turn_id") or ""
        if not ctx.get("sid"):
            self.issue(path, start, "missing_metadata", "no session_meta before record; event not attributed")
            return
        if self.excluded(ctx):
            self.stats["excluded"] += 1
            self.db.execute("UPDATE sessions SET excluded=1 WHERE id=?", (ctx["sid"],))
            return
        events = normalize(record, ctx)
        if not events:
            return
        stamp = parse_time(record.get("timestamp")).isoformat()
        for event in events:
            cwd = event["details"].pop("event_cwd", None) or ctx.get("cwd")
            if not cwd:
                self.issue(path, start, "missing_cwd", "event has no workspace")
                continue
            cwd = workspace_path(cwd)
            if any(under(cwd, x) for x in self.config.get("exclude_cwds", [])):
                continue
            project, name = self.projects.resolve(cwd)
            if event["kind"] == "command" and event["details"].get("exit_code") == 0:
                from .projects import observed_commit
                commit = observed_commit(event["text"], cwd)
                if commit:
                    event["details"]["commit_hash"] = commit
            # Canonical UI and response_item representations share this content key.
            # Without a turn ID include the timestamp to retain repeated old messages.
            turn = ctx.get("turn") or stamp
            eid = digest([ctx["sid"], turn, event["kind"], event["identity"],
                          event["item_id"] if event["kind"] not in ("intent", "statement") else None])
            args = (eid, ctx["sid"], turn, stamp, event["kind"], event["evidence"], event["text"],
                    cwd, project, name, json.dumps(event["details"], ensure_ascii=False), event["priority"])
            exists = self.db.execute("SELECT 1 FROM events WHERE id=?", (eid,)).fetchone()
            self.db.execute("""INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                cwd=excluded.cwd,project_id=excluded.project_id,project_name=excluded.project_name,
                timestamp=CASE WHEN excluded.priority>events.priority THEN excluded.timestamp ELSE events.timestamp END,
                details=CASE WHEN excluded.priority>events.priority THEN excluded.details ELSE events.details END,
                priority=MAX(events.priority,excluded.priority)""", args)
            self.stats["inserted"] += int(not exists)
            self.db.execute("INSERT OR IGNORE INTO provenance VALUES(?,?,?,?,?)", (eid, str(path), start, line, raw_hash))
