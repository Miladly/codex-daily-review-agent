import collections
from datetime import datetime, time, timedelta
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from .util import atomic_write, digest, now, redact, write_json

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
    plans = [e for e in finals if re.search(r"将|计划|建议|暂按", e["text"][:160])
             and not re.search(r"已完成|已按|已生成|已保存|已提交|已检查|测试通过|已修复", e["text"][:160])]
    observed = [e for e in events if e["kind"] in ("file_change", "command")]
    errors = [e for e in events if e["kind"] in ("command_error", "tool_error", "file_change_error")]
    # The latest final statements usually describe the outcome, but remain B evidence.
    completed = [claim(e) for e in finals if e not in plans][-4:] + [claim(e) for e in observed[-4:]]
    findings = [claim(e) for e in errors[-3:]]
    findings += [claim(e) for e in finals if re.search(r"发现|问题|限制|尚未|未完成|失败|待证|不确定", e["text"])][-3:]
    next_steps = [claim(e) for e in finals if e in plans or re.search(r"下一步|接下来|建议|待办|仍需", e["text"])][-3:]
    for c in next_steps:
        c["evidence"] = "C"
    intents = [claim(e) for e in events if e["kind"] == "intent"]
    return {"session_id": sid, "project_id": pid, "project_name": name,
            "start": events[0]["timestamp"], "end": events[-1]["timestamp"],
            "cwds": sorted({e["cwd"] for e in events}), "event_count": len(events),
            "evidence_counts": dict(collections.Counter(e["evidence"] for e in events)),
            "intents": intents[:3], "completed": unique_claims(completed, 8),
            "findings": unique_claims(findings, 6), "next_steps": unique_claims(next_steps, 4),
            "selection": "规则选取末段陈述及工具事件；完整脱敏事件保存在本地 SQLite，可追溯。"}


def build_report(db, date, tzname):
    date = datetime.strptime(date, "%Y-%m-%d").date()
    tz = ZoneInfo(tzname)
    lower = datetime.combine(date, time.min, tzinfo=tz)
    upper = datetime.combine(date + timedelta(days=1), time.min, tzinfo=tz)
    # Parse timestamps instead of comparing strings with differing offsets.
    events = []
    for r in db.execute("SELECT e.* FROM events e JOIN sessions s ON e.session_id=s.id WHERE s.excluded=0 ORDER BY e.timestamp,e.id"):
        if lower <= datetime.fromisoformat(r["timestamp"]) < upper:
            events.append(dict(r))
    grouped = collections.defaultdict(list)
    for e in events:
        grouped[(e["project_id"], e["session_id"])].append(e)
    sessions = [summarize_session(sid, pid, es[-1]["project_name"], es) for (pid, sid), es in sorted(grouped.items())]
    projects = []
    for pid in sorted({s["project_id"] for s in sessions}):
        ss = [s for s in sessions if s["project_id"] == pid]
        p = {"project_id": pid, "name": ss[0]["project_name"], "sessions": ss,
             "event_count": sum(s["event_count"] for s in ss)}
        for section in ("completed", "findings", "next_steps"):
            p[section] = unique_claims([c for s in ss for c in s[section]])
        projects.append(p)
    evidence = {}
    for e in events:
        refs = [dict(r) for r in db.execute("SELECT path,byte_offset,line,record_hash FROM provenance WHERE event_id=? ORDER BY path,byte_offset LIMIT 4", (e["id"],))]
        evidence[e["id"]] = {"session_id": e["session_id"], "timestamp": e["timestamp"],
                              "kind": e["kind"], "evidence": e["evidence"], "sources": refs}
    report = {"version": 1, "date": str(date), "timezone": tzname, "status": "AI草稿",
              "backend": "rules", "generated_at": now(), "evidence_levels": LEVELS,
              "coverage": {"projects": len(projects), "sessions": len({s["session_id"] for s in sessions}),
                           "events": len(events), "evidence_counts": dict(collections.Counter(e["evidence"] for e in events)),
                           "ingest_issues_total": db.execute("SELECT count(*) FROM issues").fetchone()[0]},
              "projects": projects, "evidence_index": evidence,
              "daily": {section: unique_claims([c for p in projects for c in p[section]], 24)
                        for section in ("completed", "findings", "next_steps")},
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
    if report.get("model_summary"):
        out += ["### 结构化提炼", ""]
        for c in report["model_summary"]["daily"]["claims"]:
            out += [f'- [{c["evidence"]}] {c["text"]} （证据：{", ".join(x[:12] for x in c["refs"])}）']
        out += [""]
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
    out += ["## 相关笔记/实验与产物链接", "", "以下为本地日志证据定位；日志记录的文件变更不保证产物当前可用。", ""]
    for p in report["projects"]:
        out += [f'### {p["name"]}（{p["project_id"]}）', ""]
        for s in p["sessions"]:
            out += [f'- 会话 `{s["session_id"]}`：{s["event_count"]} 条事件；{s["start"]} → {s["end"]}',
                    '  工作区：' + "; ".join(s["cwds"])]
        out += [""]
    out += ["### 证据索引", "", "完整索引见同名 JSON 与 SQLite；这里列出正文引用。", ""]
    used = {r for p in report["projects"] for sec in ("completed", "findings", "next_steps") for c in p[sec] for r in c["refs"]}
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
    root = Path(config["state_dir"]) / "reports"
    date = report["date"]
    manual = root / "manual" / (date + ".md")
    if not manual.exists():
        atomic_write(manual, "<!-- 人工区域：补充实际投入、个人判断与最终反思。 -->\n")
    path = root / (date + ".md")
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
    write_json(root / (date + ".json"), report)
    atomic_write(path, body)
    with db:
        db.execute("INSERT OR REPLACE INTO reports VALUES(?,?,?,?,?)", (
            date, report["timezone"], digest(report), json.dumps(report, ensure_ascii=False), now()))
    return path
