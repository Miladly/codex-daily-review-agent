"""A factual work journal followed by separate, bounded review notes.

No source commands, artifact files or remote destinations are opened here.
Structured records remain private and can be grouped by date/project later.
"""
from datetime import datetime
import json
import re
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

from .method import choose, is_plan
from .util import digest, redact

FORMAT_VERSION = "record-review-1"
STATUS_LABELS = {"observed": "工具记录", "reported": "会话记录", "discussed": "讨论／计划"}


def excerpt(text, limit=300):
    text = re.sub(r"```.*?(?:```|$)", " [代码略] ", text, flags=re.S)
    text = re.sub(r":::writing[^\n]*|^:::$", "", text, flags=re.M)
    text = re.sub(r"\[([^\]\n]+)\]\(<[^>\n]+>\)", r"\1", text)
    text = re.sub(r"\[([^\]\n]+)\]\([^\n]+?\)(?=\s|$|[，。；·])", r"\1", text)
    text = re.sub(r"[#*]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return redact(text, limit)


def artifact_links(text, origin="local"):
    """Only reuse recorded links; reject executable/credential-bearing URLs."""
    result, seen = [], set()
    candidates = re.findall(r"\[([^\]\n]+)\]\(<?([^\n>]+?)>?\)(?=\s|$|[，。；·])", text)
    candidates += [("文件产物", raw) for raw in re.findall(r"(?<![:/\w])(?:/|~/)[^,\n<>]*?\.(?:md|docx|pdf|png|svg|pptx|xlsx)(?=[\s,;>)]|$)", text, re.I)]
    for label, raw in candidates:
        target = raw.strip("<>")
        parsed = urlparse(target)
        if parsed.scheme not in ("", "file") or parsed.netloc or parsed.query or parsed.fragment:
            continue
        if not re.search(r"\.(?:md|docx|pdf|png|svg|pptx|xlsx)$", target, re.I):
            continue
        target = unquote(target)
        if parsed.scheme == "file":
            target = unquote(parsed.path)
        if len(target)>500:
            continue
        if label == "文件产物":
            label = target.rsplit("/",1)[-1]
        if target in seen:
            continue
        seen.add(target)
        result.append({"label": excerpt(label, 100), "target": redact(target, 500),
                       "origin": origin, "availability": "not_checked"})
        if len(result) == 4:
            break
    return result


def records_for_session(events, date, tzname):
    finals = [e for e in events if e["kind"] == "statement" and
              json.loads(e["details"]).get("phase") in ("final", "final_answer", "unspecified", None)]
    # Keep actual activity/discussion, never present an intent-only request as done.
    eligible = [e for e in finals if not is_plan(e)]
    selected = choose(eligible, 7)
    if eligible and eligible[-1] not in selected:
        selected.append(eligible[-1])
    observed = [e for e in events if e["kind"] in ("file_change", "command")]
    selected += choose(observed, 4)
    errors = [e for e in events if e["kind"] in ("command_error", "tool_error", "file_change_error")]
    selected += choose(errors, 2)
    selected.sort(key=lambda e: (datetime.fromisoformat(e["timestamp"]), e["id"]))
    result = []
    for event in selected:
        details = json.loads(event["details"])
        origin = details.get("origin_host", "local")
        text = event["text"]
        if event["kind"] in ("command", "command_error"):
            # The returned status is more useful than reproducing shell syntax.
            parts = text.split("返回：", 1)
            text = ("检查返回："+parts[1]) if len(parts) == 2 else "命令状态记录："+text
        status = "reported" if event["kind"] == "statement" else "observed"
        summary = excerpt(text, 360)
        work = re.split(r"[。；]", summary, maxsplit=1)[0][:140] or summary
        remainder = summary[len(work):].lstrip("。； ")
        result.append({"id": digest([date, event["project_id"], event["id"]])[:24],
                       "date": date, "project_id": event["project_id"], "project_name": event["project_name"],
                       "session_id": event["session_id"], "timestamp": event["timestamp"],
                       "local_time": datetime.fromisoformat(event["timestamp"]).astimezone(ZoneInfo(tzname)).strftime("%H:%M"),
                       "work": work, "result": remainder or summary, "description": summary, "status": status, "evidence": event["evidence"],
                       "refs": [event["id"]], "artifacts": artifact_links(event["text"], origin),
                       "source_kind": event["kind"], "source_origin": origin})
    return result


def merge_records(sessions, limit=24):
    # Rotate sessions before applying the bound, retaining the last state in each.
    queues = [list(s.get("work_records", [])) for s in sessions]
    selected, seen = [], set()
    while any(queues) and len(selected) < limit:
        for queue in queues:
            if not queue or len(selected) >= limit:
                continue
            row = queue.pop(-1)
            if row["id"] not in seen:
                selected.append(row)
                seen.add(row["id"])
    return sorted(selected, key=lambda row: (datetime.fromisoformat(row["timestamp"]), row["id"]))


def model_records(report, group):
    records = []
    for claim in group.get("claims", []):
        if claim["section"] != "work_records":
            continue
        source = [r for r in report.get("work_records", []) if set(r["refs"]) & set(claim["refs"])]
        records.append({"work": claim["text"], "refs": claim["refs"], "evidence": claim["evidence"],
                        "status": "reported", "local_time": "", "artifacts": [a for r in source for a in r["artifacts"]]})
    return records


def project_records(project):
    if "work_records" in project:
        return project["work_records"]
    # Old persisted reports can still be previewed or planned without silently
    # presenting them as an empty workday after a software upgrade.
    return [{"work": c["text"], "description": c["text"], "refs": c["refs"], "evidence": c["evidence"],
             "local_time": "", "status": "reported", "artifacts": []}
            for c in project.get("completed", []) if c["evidence"] in ("A", "B")]


def render_hybrid(report):
    cov, used = report["coverage"], set()
    model = report.get("model_summary", {})
    by_project = {g["key"]: g for g in model.get("projects", [])}
    def refs(rows):
        for ref in rows:
            used.add(ref)
        return " （证据："+", ".join(ref[:12] for ref in rows)+"）"
    def claim_line(c):
        return "- ["+c["evidence"]+"] "+excerpt(c["text"], 700)+refs(c["refs"])
    out = [f'# {report["date"]}｜每日工作记录与复盘（AI草稿）', "",
           f'时区：{report["timezone"]}；生成：{report["generated_at"]}；后端：{report["backend"]}', "",
           "<!-- DAILY_REVIEW_AGENT_BEGIN -->", "## 今日工作概览", "",
           f'覆盖 {cov["projects"]} 个项目、{cov["sessions"]} 个独立会话、{cov["events"]} 条去重事件。', "",
           "| 项目 | 本日工作记录概览 |", "|---|---|"]
    for p in report["projects"]:
        rows = model_records(report, by_project.get(p["project_id"], {})) or project_records(p)
        overview = "；".join(excerpt(r["work"], 90) for r in ([rows[0], rows[-1]] if len(rows)>1 else rows))
        out += ["| "+p["name"].replace("|", "／")+" | "+(overview or "仅有意图或证据不足，未列为已完成工作").replace("|", "／")+" |"]
    out += ["", "## 分项目工作记录", "",
            "记录优先保留做过的事项、处理结果和产物。A=工具记录；B=会话陈述；C=意图／建议；均不自动代表科研有效或最终验收。", ""]
    for p in report["projects"]:
        out += ["### "+p["name"], ""]
        rows = model_records(report, by_project.get(p["project_id"], {})) or project_records(p)
        if not rows:
            out += ["本日只有工作意图或计划，暂无足够结果记录；请人工补充实际工作。", ""]
        # A readable page plus a private structured archive, not a log transcript.
        visible = rows if len(rows)<=8 else rows[:3]+rows[-5:]
        for row in visible:
            stamp = (row.get("local_time", "")+" ") if row.get("local_time") else ""
            out += ["- "+stamp+"["+row["evidence"]+"] "+row.get("description", row["work"])+refs(row["refs"])]
        if len(visible)<len(rows):
            out += [f'另有 {len(rows)-len(visible)} 条已选工作事项保存在私有JSON；中段记录未全部展示。']
        links, seen = [], set()
        for row in rows:
            for artifact in row["artifacts"]:
                if artifact["target"] not in seen:
                    links.append(artifact); seen.add(artifact["target"])
        if links:
            out += ["", "**相关产物（按记录定位，未重新检查文件）**", ""]
            for a in links[:8]:
                target = a["target"]
                if a["origin"] != "local":
                    out += ["- 远程记录：`"+a["label"]+"`"]
                else:
                    # Leave file paths quoted; never turn source text into code.
                    out += ["- ["+a["label"].replace("]", "")+"](<"+target.replace(">", "%3E")+">)"]
        out += [""]
    out += ["## 复盘思考", ""]
    daily = model.get("daily", {}).get("claims", [])
    review = [c for c in daily if c["section"] in ("progress", "decisions", "blockers")]
    if review:
        out += [claim_line(c) for c in review[:3]]+[""]
    else:
        out += ["以下是日志中的决定与问题线索，供人工复盘；规则后端不自动编写因果解释或个人感受。", ""]
        candidates = report.get("daily", {}).get("decisions", [])+report.get("daily", {}).get("findings", [])
        out += [claim_line(c) for c in candidates[:3]] or ["暂无足够复盘线索，可在人工区补充。"]
        out += [""]
    out += ["## 后续事项与完成标准", ""]
    actions = [c for c in daily if c["section"] == "next_steps"]
    if actions:
        for c in actions[:3]:
            out += [claim_line(c), "  完成标准："+c["acceptance"]]
    else:
        out += ["以下为日志中记录的后续建议，尚未自动核对是否已在后续处理；请先检查工作记录，避免重复待办。", ""]
        out += [claim_line(c) for c in report.get("daily", {}).get("next_steps", [])[:3]] or ["暂无明确后续建议。"]
    history = report.get("prior_context", {}).get("claims", [])
    if history:
        out += ["", "### 跨天延续", "", "历史线索不计入今日成果；没有当日对应证据时不自动认定关闭。", ""]
        for e in history[:3]:
            out += ["- "+e["date"]+" "+excerpt(e["claim"]["text"], 200)+refs(e["claim"]["refs"])]
    out += ["", "## 来源与留存", "",
            "工作记录按日期及原项目标识保存在私有JSON的work_records中，复盘判断与建议分开；供后续周报、月报按项目归并。同一产物或实验的多次进度不能重复累计为独立成果。", ""]
    for field, label in (("events_by_tool", "工具事件"), ("events_by_origin", "来源事件")):
        if cov.get(field): out += [label+"："+"；".join(k+"="+str(v) for k,v in sorted(cov[field].items()))+"。"]
    for field, label in (("local_sources", "本地采集"), ("remote_sources", "远程采集")):
        if cov.get(field): out += [label+"："+"；".join(r["source_id"]+"="+r["status"] for r in cov[field])+"。"]
    if report.get("review_method"):
        out += ["复盘方法："+report["review_method"]["name"]+" v"+report["review_method"]["version"]+"。"]
    out += [""]+["- "+s for s in report["limitations"]]
    out += ["", "### 证据索引", "", "完整事件及会话层结构保留在本地SQLite与同日JSON；此处只列正文引用。", ""]
    for ref in sorted(used):
        e = report["evidence_index"][ref]
        src = e.get("sources", [])
        location = (src[0]["path"]+":"+str(src[0]["line"])) if src else "无来源位置"
        out += ["- `"+ref[:12]+"`｜"+e["timestamp"]+"｜"+location]
    out += ["", "<!-- DAILY_REVIEW_AGENT_END -->", "", "## 人工补充与最终反思", "",
            "请在同目录 manual/日期.md 填写；重跑会保留该文件并带入此处。", ""]
    return "\n".join(out)
