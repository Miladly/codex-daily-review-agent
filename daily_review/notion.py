"""Optional Notion REST adapter. Offline plans are the default; no schema writes exist."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from .review import render
from .util import digest, now, redact

MARKER = "DAILY_REVIEW_AGENT v1"
API_VERSION = "2025-09-03"


def page_id(value):
    raw = str(value).replace("-", "")
    match = re.search(r"[0-9a-fA-F]{32}", raw)
    if not match:
        raise ValueError("invalid Notion page id")
    return str(uuid.UUID(match[0]))


def token():
    value = os.environ.get("NOTION_TOKEN")
    if value:
        return value
    try:
        result = subprocess.run(["security", "find-generic-password", "-s", "DailyReviewAgent.Notion", "-w"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    raise RuntimeError("Set NOTION_TOKEN or Keychain service DailyReviewAgent.Notion; never put tokens in config")


class Client:
    def __init__(self, credential=None):
        self.credential = credential or token()

    def request(self, method, path, body=None):
        request = urllib.request.Request("https://api.notion.com/v1/" + path,
                    data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                    method=method, headers={"Authorization": "Bearer " + self.credential,
                    "Notion-Version": API_VERSION, "Content-Type": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    return json.load(response)
            except urllib.error.HTTPError as e:
                # Only retry reads and read-only queries. Never blindly retry page creation.
                if e.code == 429 and (method == "GET" or path.endswith("/query")) and attempt < 2:
                    time.sleep(min(float(e.headers.get("Retry-After", "1")), 5))
                    continue
                raise RuntimeError("Notion request failed (HTTP {}); no credential or response body logged".format(e.code))
        raise RuntimeError("Notion retry limit")

    def query(self, source, filter=None):
        rows, cursor = [], None
        while True:
            body = {"page_size": 50}
            if filter:
                body["filter"] = filter
            if cursor:
                body["start_cursor"] = cursor
            page = self.request("POST", f"data_sources/{source}/query", body)
            rows.extend(page["results"])
            if not page.get("has_more"):
                return rows
            cursor = page["next_cursor"]

    def blocks(self, block):
        items, cursor = [], None
        while True:
            url = f"blocks/{block}/children?page_size=50" + ("&start_cursor=" + cursor if cursor else "")
            page = self.request("GET", url)
            items.extend(page["results"])
            if not page.get("has_more"):
                return items
            cursor = page["next_cursor"]


def plain(block):
    data = block.get(block.get("type"), {})
    return "".join(t.get("plain_text", t.get("text", {}).get("content", "")) for t in data.get("rich_text", []))


def confirmed(page, blocks):
    if page.get("is_locked"):
        return True
    for name, prop in page.get("properties", {}).items():
        if name in ("复盘状态", "状态"):
            option = prop.get("select") or prop.get("status") or {}
            if option.get("name") == "已确认":
                return True
    return any(re.search(r"(?:复盘状态|status)\s*[:：]\s*已确认|(?:^|\n)DAILY_REVIEW_CONFIRMED\s*(?:$|\n)", plain(b)) for b in blocks)


def validate_schema(client, cfg):
    if not cfg.get("records_data_source") or not cfg.get("projects_data_source"):
        raise RuntimeError("Configure your own Notion records_data_source and projects_data_source before online access")
    schema = client.request("GET", "data_sources/" + cfg["records_data_source"])["properties"]
    for field, typ in (("记录", "title"), ("日期", "date"), ("记录类型", "select"), ("项目", "relation")):
        if schema.get(field, {}).get("type") != typ:
            raise RuntimeError("Notion schema mismatch: " + field)
    if "每日记录" not in {o["name"] for o in schema["记录类型"]["select"]["options"]}:
        raise RuntimeError("每日记录 select option missing; schema will not be changed")
    relation = schema["项目"]["relation"]
    target = relation.get("data_source_id")
    if not target:
        database = relation.get("database_id")
        if not database:
            raise RuntimeError("cannot verify 项目 relation target")
        related = client.request("GET", "databases/" + database)
        if page_id(cfg["projects_data_source"]) not in {page_id(d["id"]) for d in related.get("data_sources", [])}:
            raise RuntimeError("项目 relation database does not contain expected project data source")
    if target and page_id(target) != page_id(cfg["projects_data_source"]):
        raise RuntimeError("项目 relation targets another data source")


def remote_snapshot(client, cfg, date):
    validate_schema(client, cfg)
    rows = client.query(cfg["records_data_source"], {"and": [
        {"property": "日期", "date": {"equals": date}},
        {"property": "记录类型", "select": {"equals": "每日记录"}}]})
    if len(rows) > 1:
        raise RuntimeError("duplicate 日期+记录类型 rows; manual resolution required")
    projects = []
    for p in client.query(cfg["projects_data_source"]):
        title = p.get("properties", {}).get("项目", {}).get("title", [])
        projects.append({"id": p["id"], "name": "".join(x.get("plain_text", x.get("text", {}).get("content", "")) for x in title)})
    return {"page": rows[0] if rows else None,
            "blocks": client.blocks(rows[0]["id"]) if rows else [], "projects": projects}


def relations(report, projects, aliases):
    result, unmatched = [], []
    for p in report["projects"]:
        target = aliases.get(p["project_id"], aliases.get(p["name"], p["name"]))
        matches = [x for x in projects if x["name"].strip() == target.strip() or
                   str(x.get("id", "")).replace("-", "") == target.replace("-", "")]
        if len(matches) == 1:
            result.append(page_id(matches[0].get("id") or matches[0]["url"]))
        else:
            unmatched.append({"project_id": p["project_id"], "name": p["name"], "reason": "no unique exact match"})
    return sorted(set(result)), unmatched


def project_actions(report, projects, cfg, db):
    aliases = dict(cfg.get("project_aliases", {}))
    for p in report["projects"]:
        if p["project_id"] in aliases or p["name"] in aliases:
            continue
        link = db.execute("SELECT notion_page_id FROM project_links WHERE project_id=?", (p["project_id"],)).fetchone()
        if link:
            aliases[p["project_id"]] = link[0]
    ids, unmatched = relations(report, projects, aliases)
    creations, seen = [], set()
    if cfg.get("create_missing_projects", False):
        for p in unmatched:
            name = p["name"].strip()
            if p["project_id"] in aliases or name in aliases:
                p["reason"] = "explicit or saved project mapping unavailable; do not recreate"
                continue
            same = [x for x in projects if x["name"].strip() == name]
            if same:
                p["reason"] = "ambiguous existing project title; do not create duplicate"
                continue
            if (not name or re.fullmatch(r"g-p-[0-9a-f-]+", name) or name.startswith(("referenced-chatgpt-", "rollout-", "未归属"))
                    or name.rsplit(" · ", 1)[-1].lower() in {"scratch", "workspace", "outputs", ".ssh"}):
                p["reason"] = "generated workspace name needs a meaningful project override"
                continue
            if name not in seen:
                creations.append({"name": name, "project_ids": [q["project_id"] for q in unmatched if q["name"].strip() == name]})
                seen.add(name)
            p["reason"] = "will create missing project during approved synchronization"
    return ids, unmatched, creations, aliases


def ensure_projects(client, cfg, db, report, planned):
    """Only called inside an explicitly approved synchronization; no schema writes."""
    created = []
    for candidate in planned:
        name = candidate["name"]
        # Query again immediately before create, including pagination and exact title filter.
        matches = client.query(cfg["projects_data_source"], {"property": "项目", "title": {"equals": name}})
        if len(matches) > 1:
            raise RuntimeError("duplicate project title; creation stopped: " + name)
        if matches:
            pid = matches[0]["id"]
        else:
            schema = client.request("GET", "data_sources/" + cfg["projects_data_source"])["properties"]
            if schema.get("项目", {}).get("type") != "title":
                raise RuntimeError("project title schema mismatch")
            props = {"项目": {"title": rich(name)}}
            state = schema.get("状态", {})
            if state.get("type") == "select" and "待确认" in {o["name"] for o in state["select"]["options"]}:
                props["状态"] = {"select": {"name": "待确认"}}
            result = client.request("POST", "pages", {
                "parent": {"type": "data_source_id", "data_source_id": cfg["projects_data_source"]},
                "properties": props,
                "children": [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich("由用户授权的 Daily Review Agent 根据工作区建立。用于关联每日工作草稿；目标、阶段和完成标准待人工补充确认。")}}]})
            pid = result["id"]
        readback = client.request("GET", "pages/" + pid)
        title = readback.get("properties", {}).get("项目", {}).get("title", [])
        if "".join(t.get("plain_text", t.get("text", {}).get("content", "")) for t in title) != name:
            raise RuntimeError("project readback title mismatch")
        with db:
            for key in candidate["project_ids"]:
                db.execute("INSERT OR REPLACE INTO project_links VALUES(?,?,?,?)", (key, page_id(pid), name, now()))
        created.append(page_id(pid))
    return created


def managed_text(report):
    # Never include the local human reflection or full source-path evidence index in Notion.
    body = render(report).split("<!-- DAILY_REVIEW_AGENT_BEGIN -->", 1)[1].split("<!-- DAILY_REVIEW_AGENT_END -->", 1)[0]
    body = body.split("### 证据索引", 1)[0]
    body = redact(body, 30000, external=True)
    return MARKER + "\nstatus: AI草稿\ndate: " + report["date"] + "\n\n" + body


def make_plan(report, cfg, db, snapshot=None):
    online = snapshot is not None
    if snapshot is None:
        cache = Path(cfg["project_cache"])
        projects = json.loads(cache.read_text()).get("projects", []) if cache.exists() else []
        snapshot = {"page": None, "blocks": [], "projects": projects}
    ids, unmatched, creations, aliases = project_actions(report, snapshot["projects"], cfg, db)
    page, blocks = snapshot["page"], snapshot["blocks"]
    owned = [b for b in blocks if b.get("type") == "code" and plain(b).startswith(MARKER + "\n")]
    action, reason = "resolve_remote", "offline dry-run; target and confirmed status not yet checked"
    block_id, previous_hash = None, None
    if online:
        if page is None:
            action, reason = "create_draft", "no row with same date and type"
        elif confirmed(page, blocks):
            action, reason = "blocked", "record confirmed or locked"
        elif len(owned) != 1:
            action, reason = "blocked", "existing manual/legacy record or ambiguous managed block; preserve it"
        else:
            block_id = owned[0]["id"]
            previous_hash = digest(plain(owned[0]))
            saved = db.execute("SELECT * FROM sync_state WHERE page_id=?", (page["id"],)).fetchone()
            if not saved:
                receipt = db.execute("SELECT * FROM connector_receipts WHERE page_id=?", (page["id"],)).fetchone()
                if receipt and receipt["date"] == report["date"] and receipt["content_hash"] == previous_hash:
                    saved = {"block_id": block_id, "content_hash": previous_hash}
            if not saved or saved["block_id"] != block_id or saved["content_hash"] != previous_hash:
                action, reason = "blocked", "managed block ownership absent or human edit detected"
            elif page.get("properties", {}).get("项目", {}).get("has_more"):
                action, reason = "blocked", "relation list truncated; refuse to drop unseen human relations"
            else:
                action, reason = "update_draft", "known unchanged agent block"
    content = managed_text(report)
    plan = {"version": 1, "date": report["date"], "record_type": "每日记录", "status": "AI草稿",
            "action": action, "reason": reason, "data_source": cfg["records_data_source"],
            "page_id": page["id"] if page else None, "block_id": block_id, "previous_hash": previous_hash,
            "snapshot_hash": digest(snapshot) if online else None,
            "relation_ids": ids, "unmatched_projects": unmatched, "managed_content": content,
            "create_projects": creations,
            "content_hash": digest(content), "schema_changes": [], "create_tasks": [], "create_knowledge": []}
    plan["approval_hash"] = digest(plan)
    return plan


def rich(text):
    return [{"type": "text", "text": {"content": text[i:i+1900]}} for i in range(0, len(text), 1900)]


def apply_plan(client, cfg, db, report, plan, approval):
    unsigned = {k: v for k, v in plan.items() if k != "approval_hash"}
    if approval != plan.get("approval_hash") or digest(unsigned) != approval:
        raise RuntimeError("explicit approval hash does not match this exact plan")
    if plan["action"] not in ("create_draft", "update_draft"):
        raise RuntimeError("plan is not a writable online draft plan")
    snapshot = remote_snapshot(client, cfg, report["date"])
    current = make_plan(report, cfg, db, snapshot)
    if current["approval_hash"] != approval:
        raise RuntimeError("remote record, mappings or draft changed since approval; generate a new plan")
    added_ids = ensure_projects(client, cfg, db, report, plan.get("create_projects", []))
    relation_ids = sorted(set(plan["relation_ids"]) | set(added_ids))
    code = {"rich_text": rich(plan["managed_content"]), "language": "markdown"}
    if plan["action"] == "create_draft":
        result = client.request("POST", "pages", {
            "parent": {"type": "data_source_id", "data_source_id": cfg["records_data_source"]},
            "properties": {"记录": {"title": rich(report["date"] + "｜每日记录（AI草稿）")},
                           "日期": {"date": {"start": report["date"]}},
                           "记录类型": {"select": {"name": "每日记录"}},
                           "项目": {"relation": [{"id": i} for i in relation_ids]}},
            "children": [{"object": "block", "type": "code", "code": code},
                         {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich("人工补充与最终反思：\n确认后请添加 DAILY_REVIEW_CONFIRMED，Agent 将停止更新此记录。")}}]})
        pid = result["id"]
        blocks = client.blocks(pid)
        managed = [b for b in blocks if b.get("type") == "code" and plain(b).startswith(MARKER + "\n")]
        if len(managed) != 1:
            raise RuntimeError("draft created but managed block readback failed; manual inspection required")
        bid = managed[0]["id"]
    else:
        pid, bid = plan["page_id"], plan["block_id"]
        client.request("PATCH", "blocks/" + bid, {"code": code})
        existing = snapshot["page"].get("properties", {}).get("项目", {}).get("relation", [])
        union = sorted({page_id(x["id"]) for x in existing} | set(relation_ids))
        if len(union) > 100:
            raise RuntimeError("too many relations; managed block updated, relation update stopped")
        if union != sorted(page_id(x["id"]) for x in existing):
            client.request("PATCH", "pages/" + pid, {"properties": {"项目": {"relation": [{"id": i} for i in union]}}})
    block = client.request("GET", "blocks/" + bid)
    if digest(plain(block)) != plan["content_hash"]:
        raise RuntimeError("Notion readback differs from approved content")
    with db:
        db.execute("INSERT OR REPLACE INTO sync_state VALUES(?,?,?,?,?)", (pid, bid, plan["content_hash"], report["date"], now()))
        effective = dict(cfg.get("project_aliases", {}))
        for p in report["projects"]:
            target = effective.get(p["project_id"], effective.get(p["name"], p["name"]))
            hits = [q for q in snapshot["projects"] if q["name"].strip() == target.strip() or page_id(q["id"]) == target]
            if len(hits) == 1:
                db.execute("INSERT OR REPLACE INTO project_links VALUES(?,?,?,?)", (p["project_id"], page_id(hits[0]["id"]), p["name"], now()))
    return {"page_id": pid, "url": "https://www.notion.so/" + pid.replace("-", ""), "verified": True}
