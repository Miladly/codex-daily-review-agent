import copy
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from daily_review.collector import Collector
from daily_review.config import load
from daily_review.model import minimized_input, validate_output
from daily_review.notion import apply_plan, confirmed, make_plan, plain, relations, rich
from daily_review.projects import Projects
from daily_review.review import build_report, save_report
from daily_review.store import connect
from daily_review.util import digest, lock, redact


def record(typ, payload, stamp="2026-10-20T15:59:00Z"):
    return {"type": typ, "timestamp": stamp, "payload": payload}


def meta(cwd, sid="session-1"):
    return record("session_meta", {"id": sid, "cwd": str(cwd), "source": "cli"})


def message(text, role="assistant", stamp="2026-10-20T15:59:00Z", phase="final"):
    return record("event_msg", {"type": "item_completed", "item": {
        "type": "AgentMessage" if role == "assistant" else "UserMessage", "id": "msg-" + digest(text)[:8],
        "phase": phase, "content": [{"type": "text", "text": text}]}}, stamp)


def command(identifier="cmd-1", code=0):
    return record("event_msg", {"type": "item_completed", "item": {
        "type": "CommandExecution", "id": identifier, "command": "python -m unittest",
        "status": "completed", "exit_code": code, "aggregated_output": "Ran 3 tests\nOK"}})


class AgentFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "codex"
        self.path = self.home / "sessions" / "2026/10/01/rollout.jsonl"
        self.path.parent.mkdir(parents=True)
        self.cwd = self.root / "project"
        self.cwd.mkdir()
        self.cfg = {"codex_home": str(self.home), "state_dir": str(self.root / "state"),
                    "timezone": "Asia/Shanghai", "exclude_session_ids": [], "exclude_cwds": [], "project_overrides": []}
        self.db = connect(self.cfg["state_dir"])

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def write(self, records, path=None, mode="w"):
        path = path or self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open(mode) as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def scan(self):
        return Collector(self.db, self.cfg).scan()

    def count(self):
        return self.db.execute("SELECT count(*) FROM events").fetchone()[0]

    def seed(self):
        self.write([meta(self.cwd), record("turn_context", {"turn_id": "turn-1", "cwd": str(self.cwd)}),
                    message("请完成文档检查", "user"), command(), message("完成本地文档检查；仍需人工确认科研结论。下一步核对来源。")])
        return self.scan()

    def report(self):
        return build_report(self.db, "2026-10-20", "Asia/Shanghai")


class AgentCase(AgentFixture):
    def test_incremental_readonly_and_idempotent(self):
        first = self.seed()
        before = self.path.read_bytes()
        second = self.scan()
        self.assertEqual(first["inserted"], 3)
        self.assertEqual(second["records"], 0)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.count(), 3)

    def test_old_folder_cross_day_continuation(self):
        self.seed()
        self.write([message("次日继续讨论", stamp="2026-10-20T16:00:00Z")], mode="a")
        self.scan()
        self.assertEqual(self.report()["coverage"]["events"], 3)
        self.assertEqual(build_report(self.db, "2026-10-21", "Asia/Shanghai")["coverage"]["events"], 1)

    def test_duplicate_representations_and_repeat_commands(self):
        text = "完成检查但尚未验证"
        self.write([meta(self.cwd), record("turn_context", {"turn_id": "t"}), message(text),
                    record("response_item", {"type": "message", "id": "different-id", "role": "assistant",
                           "content": [{"type": "output_text", "text": text}]}), command("a"), command("b")])
        self.scan()
        self.assertEqual(self.count(), 3)
        self.assertEqual(self.db.execute("SELECT count(*) FROM provenance").fetchone()[0], 4)

    def test_archive_move_and_duplicate_copy(self):
        self.seed()
        archive = self.home / "archived_sessions" / "moved.jsonl"
        archive.parent.mkdir()
        archive.write_bytes(self.path.read_bytes())
        self.scan()
        self.assertEqual(self.count(), 3)
        self.path.unlink()
        self.write([message("归档后新增记录", stamp="2026-10-21T00:00:00Z")], archive, "a")
        self.scan()
        self.assertEqual(self.count(), 4)

    def test_partial_tail_retry_and_bad_line_recovery(self):
        self.seed()
        raw = json.dumps(message("尾行恢复", stamp="2026-10-21T00:01:00Z")).encode()
        with self.path.open("ab") as f:
            f.write(raw[:40])
        self.scan()
        self.assertEqual(self.count(), 3)
        with self.path.open("ab") as f:
            f.write(raw[40:] + b"\nnot-json\n" + json.dumps(command("after-bad")).encode() + b"\n")
        self.scan()
        self.assertEqual(self.count(), 5)
        self.assertEqual(self.db.execute("SELECT count(*) FROM issues WHERE category='invalid_record'").fetchone()[0], 1)

    def test_truncate_and_replace_replay(self):
        self.seed()
        original = self.path.read_bytes()
        self.path.write_bytes(original + json.dumps(message("new")).encode() + b"\n")
        self.scan()
        self.assertEqual(self.count(), 4)
        self.path.write_bytes(original)
        result = self.scan()
        self.assertEqual(result["resets"], 1)
        replacement = self.path.with_suffix(".replacement")
        replacement.write_bytes(original)
        replacement.replace(self.path)
        self.assertEqual(self.scan()["resets"], 1)
        self.assertEqual(self.count(), 4)  # Historical evidence retained, never silently deleted.

    def test_transaction_crash_recovery(self):
        self.write([meta(self.cwd), message("a"), message("b")])
        original = Collector.ingest
        calls = []
        def crash(collector, *args):
            calls.append(1)
            if len(calls) == 3:
                raise RuntimeError("simulated process crash")
            return original(collector, *args)
        with patch.object(Collector, "ingest", crash):
            with self.assertRaises(RuntimeError):
                self.scan()
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM files").fetchone()[0], 0)
        self.scan()
        self.assertEqual(self.count(), 2)

    def test_exclude_self_and_late_exclusion(self):
        self.seed()
        self.cfg["exclude_session_ids"] = ["session-1"]
        self.scan()
        self.assertEqual(self.report()["coverage"]["events"], 0)
        self.cfg["exclude_session_ids"] = []
        p = self.home / "sessions" / "agent.jsonl"
        self.cfg["exclude_cwds"] = [str(self.cwd)]
        self.write([meta(self.cwd, "self"), message("self loop")], p)
        self.scan()
        self.assertEqual(self.db.execute("SELECT count(*) FROM events WHERE session_id='self'").fetchone()[0], 0)

    def test_unknown_schema_reasoning_and_secret_omission(self):
        sample_token = "ntn_" + "veryprivate" + "1234"
        self.write([meta(self.cwd), record("response_item", {"type": "reasoning", "summary": "TOPSECRET_REASONING"}),
                    record("response_item", {"type": "message", "role": "developer", "content": "SYSTEM_SECRET"}),
                    record("future_type", {"unknown": "SECRET_UNKNOWN"}),
                    message("NOTION_TOKEN=" + sample_token + " password=hunter123 alice@example.com")])
        self.scan()
        data = "\n".join(str(tuple(r)) for r in self.db.execute("SELECT * FROM events"))
        for secret in ("TOPSECRET_REASONING", "SYSTEM_SECRET", "SECRET_UNKNOWN", sample_token, "hunter123", "alice@example.com"):
            self.assertNotIn(secret, data)

    def test_legacy_cli_and_missing_time(self):
        self.write([meta(self.cwd), record("event_msg", {"type": "user_message", "message": "请检查"}),
                    record("response_item", {"type": "function_call", "name": "exec_command", "call_id": "c", "arguments": "password=private"}),
                    record("response_item", {"type": "function_call_output", "call_id": "c", "output": "Process exited with code 2\nfailed"}),
                    {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": "missing time"}}])
        self.scan()
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM events WHERE kind='command_error'").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM issues").fetchone()[0], 1)

    def test_cwd_change_and_project_override(self):
        other = self.root / "other"
        other.mkdir()
        self.write([meta(self.cwd), message("first"), record("turn_context", {"cwd": str(other), "turn_id": "t2"}), message("second")])
        self.scan()
        self.assertEqual(self.report()["coverage"]["projects"], 2)
        cfg = dict(self.cfg, project_overrides=[{"cwd": str(self.root), "key": "combined", "name": "Combined"}])
        self.assertEqual(Projects(cfg).resolve(self.cwd), Projects(cfg).resolve(other))

    def test_git_worktree_grouping_and_readonly_probe(self):
        subprocess.run(["git", "init", "-q", str(self.cwd)], check=True)
        subprocess.run(["git", "-C", str(self.cwd), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-qm", "fixture"], check=True)
        wt = self.root / "worktree"
        subprocess.run(["git", "-C", str(self.cwd), "worktree", "add", "-q", "--detach", str(wt)], check=True)
        sub = wt / "sub"
        sub.mkdir()
        resolver = Projects(self.cfg)
        self.assertEqual(resolver.resolve(self.cwd), resolver.resolve(sub))

    def test_reports_and_manual_preserved(self):
        self.seed()
        report = self.report()
        path = save_report(self.db, self.cfg, report)
        manual = path.parent / "manual" / "2026-10-20.md"
        manual.write_text("我的最终反思必须保留")
        save_report(self.db, self.cfg, self.report())
        self.assertIn("我的最终反思必须保留", path.read_text())
        self.assertEqual(report["projects"][0]["sessions"][0]["completed"][0]["evidence"], "A")
        self.assertTrue(all(c["evidence"] == "C" for c in report["projects"][0]["sessions"][0]["next_steps"]))
        self.assertIn("B", {e["evidence"] for e in report["evidence_index"].values()})
        self.assertTrue(report["evidence_index"])
        self.assertTrue(path.with_suffix(".json").exists())
        with path.open("a") as f:
            f.write("\n这段直接写在日报末尾的人工反思也必须保留。")
        save_report(self.db, self.cfg, self.report())
        self.assertIn("直接写在日报末尾", path.read_text())
        self.assertIn("直接写在日报末尾", manual.read_text())

    def test_real_final_answer_phase_and_guardian_exclusion(self):
        self.write([meta(self.cwd), message("真实最终答复", phase="final_answer")])
        guard = meta(self.cwd, "guardian-session")
        guard["payload"]["source"] = {"subagent": {"other": "guardian"}}
        self.write([guard, message("approval metadata is not work")], self.home / "sessions/guard.jsonl")
        self.scan()
        self.assertEqual(self.report()["coverage"]["sessions"], 1)
        self.assertEqual(self.report()["projects"][0]["completed"][0]["evidence"], "B")

    def test_model_failure_keeps_local_draft_and_nonzero_exit(self):
        from daily_review.cli import main
        self.seed()
        config = self.root / "config.json"
        config.write_text(json.dumps(self.cfg))
        with patch("daily_review.cli.summarize", side_effect=RuntimeError("simulated timeout")), patch("daily_review.cli.emit"):
            code = main(["--config", str(config), "review", "--date", "2026-10-20", "--backend", "codex"])
        self.assertEqual(code, 2)
        result = json.loads((Path(self.cfg["state_dir"]) / "reports/2026-10-20.json").read_text())
        self.assertEqual(result["backend"], "rules")
        self.assertIn("simulated timeout", result["model_error"])

    def test_model_adapter_accepts_valid_answer_with_startup_notice(self):
        from daily_review.model import summarize
        self.seed()
        report = self.report()
        data = minimized_input(report)
        result = {"sessions": [{"key": s["key"], "claims": []} for s in data["sessions"]],
                  "projects": [{"key": p["key"], "claims": []} for p in data["projects"]],
                  "daily": {"key": data["date"], "claims": [{k: v for k, v in dict(data["sessions"][0]["claims"][0], section="goals", acceptance="").items() if k != "section_hint"}]}}
        class Proc:
            returncode = 0
            def __init__(self, cmd, **kwargs):
                self.command = cmd
                Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps(result))
                assert "--ephemeral" in cmd and "--ignore-user-config" in cmd
            def communicate(self, *args, **kwargs):
                return '\n'.join([json.dumps({"type": "item.completed", "item": {"type": "error", "message": "tools disabled"}}), json.dumps({"type": "item.completed", "item": {"type": "agent_message"}})]), ""
        with patch("daily_review.model.doctor", return_value={"available": True, "binary": "fake", "version": "fixture"}), patch("daily_review.model.subprocess.Popen", Proc):
            out = summarize(report, self.cfg)
        self.assertEqual(out["backend"], "codex")

    def test_model_adapter_rejects_tool_activity(self):
        from daily_review.model import summarize
        self.seed()
        class Proc:
            returncode = 0
            def __init__(self, *args, **kwargs):
                pass
            def communicate(self, *args, **kwargs):
                return json.dumps({"type": "item.completed", "item": {"type": "command_execution"}}), ""
        with patch("daily_review.model.doctor", return_value={"available": True, "binary": "fake", "version": "fixture"}), patch("daily_review.model.subprocess.Popen", Proc):
            with self.assertRaisesRegex(RuntimeError, "unexpected item activity"):
                summarize(self.report(), self.cfg)

    def test_replay_reassigns_project_without_duplicate_events(self):
        from daily_review.cli import main
        self.seed()
        self.cfg["project_overrides"] = [{"cwd": str(self.cwd), "key": "confirmed", "name": "Confirmed Project"}]
        config = self.root / "override.json"
        config.write_text(json.dumps(self.cfg))
        with patch("daily_review.cli.emit"):
            self.assertEqual(main(["--config", str(config), "collect", "--replay"]), 0)
        self.assertEqual(self.count(), 3)
        self.assertEqual(self.report()["projects"][0]["name"], "Confirmed Project")

    def test_model_refs_levels_and_scope(self):
        self.seed()
        self.write([message("已完成来源检查。")], mode="a")
        self.scan()
        data = minimized_input(self.report())
        c = next(c for c in data["sessions"][0]["claims"] if c["evidence"] == "B")
        c = {k: v for k, v in dict(c, section="progress", acceptance="").items() if k != "section_hint"}
        result = {"sessions": [{"key": s["key"], "claims": []} for s in data["sessions"]],
                  "projects": [{"key": p["key"], "claims": []} for p in data["projects"]],
                  "daily": {"key": data["date"], "claims": [copy.deepcopy(c)]}}
        validate_output(copy.deepcopy(result), data)
        result["daily"]["claims"][0]["evidence"] = "A"
        with self.assertRaises(ValueError):
            validate_output(result, data)
        result["daily"]["claims"][0]["evidence"] = "B"
        result["daily"]["claims"][0]["refs"] = ["invented"]
        with self.assertRaises(ValueError):
            validate_output(result, data)

    def test_minimized_input_never_contains_paths_or_raw_log(self):
        self.seed()
        payload = json.dumps(minimized_input(self.report()))
        self.assertNotIn(str(self.path), payload)
        self.assertNotIn("base_instructions", payload)
        self.assertLess(len(payload), 40000)

    def test_lock_prevents_overlap(self):
        with lock(self.root / "lock"):
            with self.assertRaises(RuntimeError):
                with lock(self.root / "lock"):
                    pass

    def test_oversized_line_and_symlink(self):
        self.write([meta(self.cwd)])
        with self.path.open("ab") as f:
            f.write(b"x" * 110 + b"\n" + json.dumps(message("after oversized")).encode() + b"\n")
        # Test scanner limit without allocating huge fixtures; metadata must fit the limit.
        with patch("daily_review.collector.MAX_LINE", 1000):
            with self.path.open("ab") as f:
                f.write(b"x" * 1200 + b"\n")
            self.scan()
        self.assertEqual(self.count(), 1)
        self.assertTrue(self.db.execute("SELECT 1 FROM issues WHERE category='oversized_record'").fetchone())
        link = self.home / "sessions" / "link.jsonl"
        link.symlink_to(self.path)
        self.assertEqual(self.scan()["files"], 1)

    def test_state_inside_codex_forbidden(self):
        self.cfg["state_dir"] = str(self.home / "agent")
        with self.assertRaises(ValueError):
            self.scan()


class FakeNotion:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "POST" and path == "pages":
            self.snapshot["page"] = {"id": "11111111-1111-4111-8111-111111111111", "properties": body["properties"]}
            self.snapshot["blocks"] = [{"id": "created-" + str(i), **b} for i, b in enumerate(body["children"])]
            return self.snapshot["page"]
        if method == "PATCH" and path.startswith("blocks/"):
            b = self.snapshot["blocks"][0]
            b["code"] = body["code"]
            return b
        if method == "GET" and path.startswith("blocks/"):
            return self.snapshot["blocks"][0]
        if method == "PATCH" and path.startswith("pages/"):
            self.snapshot["page"]["properties"].update(body["properties"])
            return self.snapshot["page"]
        raise AssertionError((method, path))

    def blocks(self, identifier):
        return self.snapshot["blocks"]


class NotionCase(AgentFixture):
    def setUp(self):
        super().setUp()
        self.seed()
        self.ncfg = {"records_data_source": "11111111-1111-4111-8111-111111111111",
                     "projects_data_source": "22222222-2222-4222-8222-222222222222",
                     "project_cache": str(self.root / "absent"), "project_aliases": {}}
        self.snap = {"page": {"id": "11111111-1111-4111-8111-111111111111", "properties": {}},
                     "blocks": [{"id": "b1", "type": "paragraph", "paragraph": {"rich_text": rich("人工笔记")}}], "projects": []}

    def test_offline_plan_has_no_writes(self):
        plan = make_plan(self.report(), self.ncfg, self.db)
        self.assertEqual(plan["action"], "resolve_remote")
        self.assertEqual(plan["schema_changes"], [])
        self.assertEqual(plan["create_tasks"], [])
        self.assertTrue(plan["unmatched_projects"])

    def test_existing_manual_and_confirmed_records_blocked(self):
        self.assertEqual(make_plan(self.report(), self.ncfg, self.db, self.snap)["action"], "blocked")
        self.snap["page"]["properties"]["复盘状态"] = {"select": {"name": "已确认"}}
        self.assertTrue(confirmed(self.snap["page"], []))
        self.assertTrue(confirmed({}, [{"type": "paragraph", "paragraph": {"rich_text": rich("DAILY_REVIEW_CONFIRMED")}}]))
        self.assertFalse(confirmed({}, [{"type": "paragraph", "paragraph": {"rich_text": rich("确认后请添加 DAILY_REVIEW_CONFIRMED，停止更新")}}]))

    def owned(self):
        from daily_review.notion import MARKER
        self.snap["blocks"][0] = {"id": "b1", "type": "code", "code": {"rich_text": rich(MARKER + "\nold"), "language": "markdown"}}
        with self.db:
            self.db.execute("INSERT INTO sync_state VALUES(?,?,?,?,?)", (self.snap["page"]["id"], "b1", digest(plain(self.snap["blocks"][0])), "2026-10-20", "now"))

    def test_owned_update_and_human_edit_detection(self):
        self.owned()
        plan = make_plan(self.report(), self.ncfg, self.db, self.snap)
        self.assertEqual(plan["action"], "update_draft")
        self.snap["blocks"][0]["code"]["rich_text"] = rich("DAILY_REVIEW_AGENT v1\nhuman edited")
        self.assertEqual(make_plan(self.report(), self.ncfg, self.db, self.snap)["action"], "blocked")

    def test_approval_tamper_staleness_and_readback(self):
        self.owned()
        report = self.report()
        client = FakeNotion(self.snap)
        plan = make_plan(report, self.ncfg, self.db, self.snap)
        with self.assertRaises(RuntimeError):
            apply_plan(client, self.ncfg, self.db, report, plan, "wrong")
        changed = copy.deepcopy(plan)
        changed["managed_content"] = "tampered"
        with self.assertRaises(RuntimeError):
            apply_plan(client, self.ncfg, self.db, report, changed, plan["approval_hash"])
        with patch("daily_review.notion.remote_snapshot", return_value=self.snap):
            result = apply_plan(client, self.ncfg, self.db, report, plan, plan["approval_hash"])
        self.assertTrue(result["verified"])
        self.assertEqual(len([x for x in client.calls if x[0] == "PATCH"]), 1)
        self.assertFalse(any(x[1].startswith("data_sources") for x in client.calls))

    def test_exact_existing_relation_no_fuzzy_create(self):
        report = self.report()
        report["projects"][0]["name"] = "Local project"
        projects = [{"name": "Example Research Project", "id": "00000000-0000-4000-8000-000000000009"}]
        ids, missing = relations(report, projects, {})
        self.assertEqual(ids, [])
        ids, missing = relations(report, projects, {"Local project": "Example Research Project"})
        self.assertEqual(ids, [projects[0]["id"]])
        self.assertEqual(missing, [])

    def test_create_draft_then_same_day_updates_instead_of_duplicate(self):
        self.snap["page"], self.snap["blocks"] = None, []
        report = self.report()
        client = FakeNotion(self.snap)
        plan = make_plan(report, self.ncfg, self.db, self.snap)
        self.assertEqual(plan["action"], "create_draft")
        with patch("daily_review.notion.remote_snapshot", return_value=self.snap):
            apply_plan(client, self.ncfg, self.db, report, plan, plan["approval_hash"])
            next_plan = make_plan(report, self.ncfg, self.db, self.snap)
            self.assertEqual(next_plan["action"], "update_draft")
            apply_plan(client, self.ncfg, self.db, report, next_plan, next_plan["approval_hash"])
        self.assertEqual(len([x for x in client.calls if x[:2] == ("POST", "pages")]), 1)
        self.assertIn("人工补充与最终反思", plain(self.snap["blocks"][1]))

    def test_stale_plan_refuses_write(self):
        self.owned()
        report = self.report()
        client = FakeNotion(self.snap)
        plan = make_plan(report, self.ncfg, self.db, self.snap)
        self.snap["page"]["last_edited_time"] = "human-changed"
        with patch("daily_review.notion.remote_snapshot", return_value=self.snap):
            with self.assertRaisesRegex(RuntimeError, "changed since approval"):
                apply_plan(client, self.ncfg, self.db, report, plan, plan["approval_hash"])
        self.assertEqual(client.calls, [])

    def test_relation_merge_preserves_human_link(self):
        self.owned()
        report = self.report()
        name = report["projects"][0]["name"]
        known = "00000000-0000-4000-8000-000000000009"
        human = "22222222-2222-4222-8222-222222222222"
        self.snap["projects"] = [{"id": known, "name": name}]
        self.snap["page"]["properties"]["项目"] = {"relation": [{"id": human}]}
        client = FakeNotion(self.snap)
        plan = make_plan(report, self.ncfg, self.db, self.snap)
        with patch("daily_review.notion.remote_snapshot", return_value=self.snap):
            apply_plan(client, self.ncfg, self.db, report, plan, plan["approval_hash"])
        self.assertEqual({x["id"] for x in self.snap["page"]["properties"]["项目"]["relation"]}, {human, known})

    def test_duplicate_remote_day_rows_refused(self):
        from daily_review.notion import remote_snapshot
        class Duplicate:
            def query(self, *args):
                return [{"id": "a"}, {"id": "b"}]
        with patch("daily_review.notion.validate_schema"):
            with self.assertRaisesRegex(RuntimeError, "duplicate"):
                remote_snapshot(Duplicate(), self.ncfg, "2026-10-20")

    def test_query_pagination_is_readonly(self):
        from daily_review.notion import Client
        client = Client("test-credential-not-real")
        pages = [{"results": [{"id": "1"}], "has_more": True, "next_cursor": "next"},
                 {"results": [{"id": "2"}], "has_more": False}]
        with patch.object(client, "request", side_effect=pages) as request:
            result = client.query("source")
        self.assertEqual(len(result), 2)
        self.assertTrue(all(c.args[1].endswith("/query") for c in request.call_args_list))
        self.assertEqual(request.call_args_list[1].args[2]["start_cursor"], "next")


if __name__ == "__main__":
    unittest.main()
