import collections
from datetime import datetime, time, timedelta
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from .util import atomic_write, digest, now, redact, write_json
from .method import choose, is_plan, PLAN, DECISION, PROBLEM, method_metadata, LABELS

LEVELS = {"A": "日志中可观察的工具结果；不自动证明科研结论", "B": "会话中的陈述，未独立核实", "C": "意图、计划或证据不足"}


def claim(event):
    text = event["text"]
    if event["kind"] == "command":
        code = json.loads(event["details"]).get("exit_code")
        prefix = "日志记录命令退出码 {}。".format(code) if code is not None else "日志记录命令状态，退出码未知。"
        text = prefix + " " + text
    elif event["kind"] == "statement":
        text = "会话陈述（待核实）：" + text
    elif event["kind"] == "intent":
        text = "用户提出的工作意图：" + text
    result = {"text": redact(text, 750), "evidence": event["evidence"], "refs": [event["id"]]}
    details = json.loads(event["details"])
    if details.get("commit_hash"):
        result["outcome_key"] = digest([event["project_id"], details["commit_hash"]])
    return result


def unique_claims(claims, limit=16):
    result, seen = [], {}
    for c in claims:
        key = c.get("outcome_key") or digest([c["text"], c["evidence"]])
        if key not in seen:
            result.append(dict(c, refs=list(c["refs"])))
            seen[key] = result[-1]
        else:
            seen[key]["refs"] = list(dict.fromkeys(seen[key]["refs"] + c["refs"]))
    return result[:limit]


def summarize_session(sid, pid, name, events):
    finals = [e for e in events if e["kind"] == "statement" and json.loads(e["details"]).get("phase") in ("final", "final_answer", "unspecified", None)]
    plans = [e for e in finals if is_plan(e)]
    observed = [e for e in events if e["kind"] in ("file_change", "command")]
    errors = [e for e in events if e["kind"] in ("command_error", "tool_error", "file_change_error")]
    # Score the entire day instead of retaining only the final retries.
    completed = [claim(e) for e in choose([e for e in finals if e not in plans], 4)]
    completed += [claim(e) for e in choose(observed, 4)]
    findings = [claim(e) for e in choose(errors, 3)]
    findings += [claim(e) for e in choose([e for e in finals if PROBLEM.search(e['text'])], 3)]
    next_steps = [claim(e) for e in choose([e for e in finals if e in plans or PLAN.search(e['text'])], 3)]
    for c in next_steps:
        c["evidence"] = "C"
    intents = [claim(e) for e in choose([e for e in events if e['kind'] == 'intent'], 3)]
    decisions = [claim(e) for e in choose([e for e in finals if DECISION.search(e['text'])], 2)]
    pools = {'goals': intents[:2], 'progress': unique_claims(completed, 8), 'decisions': decisions,
             'blockers': unique_claims(findings, 6), 'next_steps': unique_claims(next_steps, 4)}
    candidates = []
    for index in range(max((len(value) for value in pools.values()), default=0)):
        for section, rows in pools.items():
            if index < len(rows):
                candidates.append(dict(rows[index], section_hint=section))
    return {"session_id": sid, "project_id": pid, "project_name": name,
            "start": events[0]["timestamp"], "end": events[-1]["timestamp"],
            "cwds": sorted({e["cwd"] for e in events}), "event_count": len(events),
            "evidence_counts": dict(collections.Counter(e["evidence"] for e in events)),
            "intents": intents, "goals": intents[:2], "decisions": decisions,
            "candidates": candidates[:20], "completed": unique_claims(completed, 8),
            "findings": unique_claims(findings, 6), "next_steps": unique_claims(next_steps, 4),
            "selection": "按全天成果、决定与问题信号选材并限制重复；规则选材仍可能遗漏，完整脱敏事件留在本地 SQLite。"}


def build_report(db, date, tzname):
    date = datetime.strptime(date, "%Y-%m-%d").date()
    tz = ZoneInfo(tzname)
    lower = datetime.combine(date, time.min, tzinfo=tz)
    upper = datetime.combine(date + timedelta(days=1), time.min, tzinfo=tz)
    context_lower = lower-timedelta(days=3)
    # Parse timestamps instead of comparing strings with differing offsets.
    events, prior = [], []
    for r in db.execute("SELECT e.* FROM events e JOIN sessions s ON e.session_id=s.id WHERE s.excluded=0 ORDER BY e.timestamp,e.id"):
        stamp = datetime.fromisoformat(r['timestamp'])
        if lower <= stamp < upper:
            events.append(dict(r))
        elif context_lower <= stamp < lower:
            prior.append(dict(r))
    grouped = collections.defaultdict(list)
    for e in events:
        grouped[(e["project_id"], e["session_id"])].append(e)
    sessions = [summarize_session(sid, pid, es[-1]["project_name"], es) for (pid, sid), es in sorted(grouped.items())]
    projects = []
    for pid in sorted({s["project_id"] for s in sessions}):
        ss = [s for s in sessions if s["project_id"] == pid]
        p = {"project_id": pid, "name": ss[0]["project_name"], "sessions": ss,
             "event_count": sum(s["event_count"] for s in ss)}
        for section in ("completed", "findings", "next_steps", "goals", "decisions"):
            p[section] = unique_claims([c for s in ss for c in s[section]])
        projects.append(p)
    context, context_events = [], []
    queues = []
    for pid in sorted({p['project_id'] for p in projects}):
        eligible = [e for e in prior if e['project_id'] == pid and (is_plan(e) or PROBLEM.search(e['text']) or DECISION.search(e['text']))]
        queues.append(choose(eligible, 3))
    while any(queues) and len(context) < 12:
        for queue in queues:
            if not queue or len(context) >= 12:
                continue
            e = queue.pop(0)
            c = claim(e)
            if is_plan(e):
                c['evidence'] = 'C'
            context.append({'project_id': e['project_id'], 'date': datetime.fromisoformat(e['timestamp']).astimezone(tz).date().isoformat(),
                            'claim': c, 'kind': e['kind']})
            context_events.append(e)
    evidence = {}
    for e in events+context_events:
        refs = [dict(r) for r in db.execute("SELECT path,byte_offset,line,record_hash FROM provenance WHERE event_id=? ORDER BY path,byte_offset LIMIT 4", (e["id"],))]
        evidence[e["id"]] = {"session_id": e["session_id"], "timestamp": e["timestamp"],
                              "kind": e["kind"], "evidence": e["evidence"], "sources": refs,
                              "period": "today" if lower <= datetime.fromisoformat(e['timestamp']) < upper else "prior"}
    report = {"version": 1, "date": str(date), "timezone": tzname, "status": "AI草稿",
              "backend": "rules", "generated_at": now(), "evidence_levels": LEVELS,
              "coverage": {"projects": len(projects), "sessions": len({s["session_id"] for s in sessions}),
                           "events": len(events), "evidence_counts": dict(collections.Counter(e["evidence"] for e in events)),
                           "ingest_issues_total": db.execute("SELECT count(*) FROM issues").fetchone()[0]},
              "projects": projects, "evidence_index": evidence,
              "review_method": method_metadata(), "prior_context": {'lookback_days': 3, 'claims': context},
              "daily": {section: unique_claims([c for p in projects for c in p[section]], 24)
                        for section in ("completed", "findings", "next_steps", "goals", "decisions")},
              "suggestions": {"tasks": [], "knowledge": [], "requires_confirmation": True},
              "limitations": ["覆盖本机及已配置的只读 SSH 来源；归档与跨日延续均按事件时间采集。",
                              "不读取科研数据、不运行日志中的命令；退出码 0 和助手自述均不能证明科学结论。",
                              "规则摘要是证据摘录与分层归并；不是人工最终反思。",
                              "自动脱敏为尽力处理；任何外发前仍应检查待发送内容。"]}
    report["coverage"]["events_by_origin"] = dict(collections.Counter(json.loads(e["details"]).get("origin_host", "local") for e in events))
    report["coverage"]["remote_sources"] = [dict(r) for r in db.execute("SELECT source_id,status FROM remote_status ORDER BY source_id")]
    report["coverage"]["events_by_tool"] = dict(collections.Counter(json.loads(e["details"]).get("source_tool", "codex") for e in events))
    report["coverage"]["local_sources"] = [dict(r) for r in db.execute("SELECT source_id,status FROM source_status ORDER BY source_id")]
    if any(r["status"] != "ok" for r in report["coverage"]["local_sources"]):
        report["limitations"].append("部分本地来源不可用或采集不完整；缺失记录不代表没有工作。")
    if any(r["status"] != "ok" for r in report["coverage"]["remote_sources"]):
        report["limitations"].append("部分远程来源当前不可用；已保留历史采集结果，但不能把缺失记录当作没有工作。")
    for p in projects:
        report["suggestions"]["tasks"].extend({"project_id": p["project_id"], **c} for c in p["next_steps"][:2])
        report["suggestions"]["knowledge"].extend({"project_id": p["project_id"], **c} for c in p["findings"][:1])
    return report


def render(report):
    cov = report["coverage"]
    out = [f'# {report["date"]}｜每日复盘（AI草稿）', "",
           f'时区：{report["timezone"]}；生成：{report["generated_at"]}；后端：{report["backend"]}', "",
           "<!-- DAILY_REVIEW_AGENT_BEGIN -->", "## 当日摘要", "",
           f'覆盖 {cov["projects"]} 个项目、{cov["sessions"]} 个会话、{cov["events"]} 条去重事件。', "",
           "证据等级：A=可观察工具记录；B=会话陈述；C=意图/计划。均不自动代表科研验证。", ""]
    origins = cov.get("events_by_origin", {})
    if cov.get("events_by_tool"):
        out += ["工具事件数：" + "；".join(k + "=" + str(v) for k, v in sorted(cov["events_by_tool"].items())) + "。", ""]
    if cov.get("local_sources"):
        out += ["本地来源采集：" + "；".join(r["source_id"] + "=" + r["status"] for r in cov["local_sources"]) + "。", ""]
    if origins:
        out += ["来源事件数：" + "；".join(k + "=" + str(v) for k, v in sorted(origins.items())) + "。", ""]
    if cov.get("remote_sources"):
        out += ["远程采集：" + "；".join(r["source_id"] + "=" + r["status"] for r in cov["remote_sources"]) + "。", ""]
    if report.get("review_method"):
        method = report["review_method"]
        out += [f'复盘方法：{method["name"]} v{method["version"]}；规则摘录与可选模型提炼共用证据边界。', '']
    if report.get("model_summary"):
        out += ["### 结构化提炼（模型草稿，需审核）", ""]
        for section, label in LABELS.items():
            rows = [c for c in report["model_summary"]["daily"]["claims"] if c["section"] == section]
            if rows:
                out += ['#### '+label, '']
                for c in rows:
                    out += [f'- [{c["evidence"]}] {c["text"]} （证据：{", ".join(x[:12] for x in c["refs"])}）']
                    if c["acceptance"]:
                        out += ['  完成标准：'+c["acceptance"]]
                out += ['']
    for section in ('goals', 'decisions'):
        rows = report.get('daily', {}).get(section, [])
        if rows:
            out += ['## '+LABELS[section], '']
            for c in rows[:4]:
                out += [f'- [{c["evidence"]}] {c["text"]} （证据：{", ".join(x[:12] for x in c["refs"])}）']
            out += ['']
    for section, label in (("completed", "今日完成与结果"), ("findings", "问题/发现与决定"), ("next_steps", "下一步")):
        out += ["## " + label, ""]
        found = False
        for p in report["projects"]:
            if not p[section]:
                continue
            found = True
            out += ["### " + p["name"], ""]
            for c in p[section]:
                text = c["text"].replace("\n", "\n  ")
                out += [f'- [{c["evidence"]}] {text} （证据：{", ".join(r[:12] for r in c["refs"])}）']
            out += [""]
        if not found:
            out += ["暂无足够日志证据，留待人工补充。", ""]
    history = report.get('prior_context', {}).get('claims', [])
    if history:
        out += ['## 跨天延续（历史线索，尚未自动确认完成）', '', '以下来自活跃项目近三天的有限记录，不计入今日成果；是否解决需核对当日证据。', '']
        for entry in history[:6]:
            c = entry['claim']
            out += [f'- {entry["date"]} [{c["evidence"]}] {c["text"]} （证据：{", ".join(x[:12] for x in c["refs"])}）']
        out += ['']
    out += ["## 相关笔记/实验与产物链接", "", "以下为本地日志证据定位；日志记录的文件变更不保证产物当前可用。", ""]
    for p in report["projects"]:
        out += [f'### {p["name"]}（{p["project_id"]}）', ""]
        for s in p["sessions"]:
            out += [f'- 会话 `{s["session_id"]}`：{s["event_count"]} 条事件；{s["start"]} → {s["end"]}',
                    '  工作区：' + "; ".join(s["cwds"])]
        out += [""]
    out += ["### 证据索引", "", "完整索引见同名 JSON 与 SQLite；这里列出正文引用。", ""]
    used = {r for p in report["projects"] for sec in ("completed", "findings", "next_steps", "goals", "decisions") for c in p.get(sec, []) for r in c["refs"]}
    used.update(ref for entry in history[:6] for ref in entry['claim']['refs'])
    if report.get("model_summary"):
        used.update(r for c in report["model_summary"]["daily"]["claims"] for r in c["refs"])
    for eid in sorted(used):
        e = report["evidence_index"][eid]
        src = e["sources"][0] if e["sources"] else None
        out += [f'- `{eid[:12]}` [{e["evidence"]}] {src["path"]}:{src["line"]}；byte={src["byte_offset"]}' if src else f'- `{eid[:12]}`：无来源位置']
    out += ["", "## 待确认建议", "", f'任务候选 {len(report["suggestions"]["tasks"])} 条、知识候选 {len(report["suggestions"]["knowledge"])} 条；详见 JSON。仅供确认，不自动建库或建任务。', "",
            "## 覆盖与结论边界", ""] + ["- " + s for s in report["limitations"]]
    out += ["", "<!-- DAILY_REVIEW_AGENT_END -->", "", "## 人工补充与最终反思", "",
            "请在同目录 manual/日期.md 填写；重跑会保留该文件并带入此处。", ""]
    return "\n".join(out)


def save_report(db, config, report):
    private_root = Path(config["state_dir"]) / "reports"
    root = Path(config.get("reports_dir", private_root))
    date = report["date"]
    path = root / (date + ".md")
    manual = root / "manual" / (date + ".md")
    if path.is_symlink() or manual.is_symlink() or manual.parent.is_symlink():
        raise ValueError("refusing a symlinked report or manual reflection")
    if path.exists():
        existing = path.read_text()
        if not all(marker in existing for marker in ("<!-- DAILY_REVIEW_AGENT_BEGIN -->", "<!-- DAILY_REVIEW_AGENT_END -->", "## 人工补充与最终反思\n\n")):
            raise ValueError("existing note is not intact Agent-managed content; preserved without overwrite")
    if not manual.exists():
        atomic_write(manual, "<!-- 人工区域：补充实际投入、个人判断与最终反思。 -->\n")
    # Preserve human edits made directly below the final reflection heading too.
    header = "## 人工补充与最终反思\n\n"
    hint = "请在同目录 manual/日期.md 填写；重跑会保留该文件并带入此处。"
    def reflection_only(text):
        while text.startswith(hint):
            text = text[len(hint):].lstrip("\n")
        return text
    sidecar = reflection_only(manual.read_text())
    if sidecar != manual.read_text():
        atomic_write(manual, sidecar)
    if path.exists() and header in path.read_text():
        inline = reflection_only(path.read_text().rsplit(header, 1)[1])
        if inline.strip() and inline.strip() not in sidecar:
            merged = inline if sidecar.strip() in inline else sidecar.rstrip() + "\n\n" + inline
            atomic_write(manual, merged)
    body = render(report) + manual.read_text()
    # Store human input in the separate stable file, not in the agent-managed JSON.
    write_json(private_root / (date + ".json"), report)
    atomic_write(path, body)
    with db:
        db.execute("INSERT OR REPLACE INTO reports VALUES(?,?,?,?,?)", (
            date, report["timezone"], digest(report), json.dumps(report, ensure_ascii=False), now()))
    return path
