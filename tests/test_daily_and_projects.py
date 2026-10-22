from datetime import datetime, timezone
import copy
import json
from pathlib import Path
from unittest.mock import patch
from test_agent import AgentFixture, message
from daily_review.daily import dates_for, run_daily
from daily_review.notion import ensure_projects, make_plan, project_actions, rich
from daily_review.util import redact


class DailyBatchCase(AgentFixture):
    def setUp(self):
        super().setUp()
        self.cfg["notion"] = {"records_data_source": "11111111-1111-4111-8111-111111111111",
                             "projects_data_source": "22222222-2222-4222-8222-222222222222",
                             "project_cache": str(self.root / "missing-cache"), "project_aliases": {}}

    def test_dates_follow_shanghai_midnight_and_yesterday(self):
        at = datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)
        self.assertEqual(dates_for("today", 3, "Asia/Shanghai", at), ["2026-10-19", "2026-10-20", "2026-10-21"])
        self.assertEqual(dates_for("yesterday", 1, "Asia/Shanghai", at), ["2026-10-20"])
        with self.assertRaises(ValueError):
            dates_for("today", 0, "Asia/Shanghai", at)

    def test_repeat_batch_is_stable_no_network_and_preserves_manual(self):
        self.seed()
        with patch("daily_review.notion.Client.request", side_effect=AssertionError("network forbidden")):
            first = run_daily(self.db, self.cfg, "2026-10-21", 3)
            path = Path(self.cfg["state_dir"]) / "reports/2026-10-20.md"
            before = path.read_bytes()
            second = run_daily(self.db, self.cfg, "2026-10-21", 3)
        self.assertEqual(first["changed_days"], 1)
        self.assertEqual(first["format"], "record-review-1")
        self.assertTrue(next(d for d in first["days"] if d["status"]=="draft")["work_records"])
        self.assertEqual(second["changed_days"], 0)
        self.assertEqual(second["remote_writes"], 0)
        self.assertEqual(second["model_calls"], 0)
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(first["days"][0]["status"], "no_events")
        self.assertTrue((Path(self.cfg["state_dir"]) / "runs/last-run.json").exists())

    def test_late_event_updates_previous_day_only(self):
        self.seed()
        run_daily(self.db, self.cfg, "2026-10-21", 3)
        self.write([message("迟到写入的昨日结果", stamp="2026-10-20T15:59:30Z")], mode="a")
        result = run_daily(self.db, self.cfg, "2026-10-21", 3)
        self.assertEqual(result["changed_days"], 1)
        day = next(r for r in result["days"] if r["date"] == "2026-10-20")
        self.assertEqual(day["coverage"]["events"], 4)

    def test_exports_strip_internal_metadata_and_truncated_paths(self):
        text = '结果<oai-mem-citation><citation_entries>MEMORY.md:1</citation_entries></oai-mem-citation> :codex-file-citation{path="/private/a.md"} ' + '/Users' + '/demo'
        cleaned = redact(text, 2000, external=True)
        self.assertNotIn("MEMORY.md", cleaned)
        self.assertNotIn("codex-file-citation", cleaned)
        self.assertNotIn("/Users" + "/demo", cleaned)


class DailyModelCase(AgentFixture):
    def setUp(self):
        super().setUp()
        self.cfg["notion"] = {"records_data_source": "11111111-1111-4111-8111-111111111111",
                             "projects_data_source": "22222222-2222-4222-8222-222222222222",
                             "project_cache": str(self.root / "missing-cache"), "project_aliases": {}}

    def fake_summary(self, report, cfg, timeout):
        from daily_review.model import apply_summary, minimized_input, validate_output
        data = minimized_input(report)
        answer = {"sessions": [{"key": s["key"], "claims": []} for s in data["sessions"]],
                  "projects": [{"key": p["key"], "claims": []} for p in data["projects"]],
                  "daily": {"key": data["date"], "claims": []}}
        for group in answer["projects"]:
            candidate = next(c for s in data["sessions"] if s["project_key"] == group["key"]
                             for c in s["claims"] if c["section_hint"] == "work_records")
            group["claims"] = [dict(text=candidate["text"], evidence=candidate["evidence"],
                                    refs=candidate["refs"], section="work_records", acceptance="")]
        validate_output(answer, data)
        return apply_summary(report, answer, {"cli": "synthetic", "excerpt_characters": data["excerpt_characters"]})

    def run_model(self, days=3):
        return run_daily(self.db, self.cfg, "2026-10-21", days, backend="codex")

    def test_opt_in_calls_model_once_then_reuses_exact_input_and_manual(self):
        self.seed()
        with patch("daily_review.daily.summarize", side_effect=self.fake_summary) as model:
            first = self.run_model()
            path = Path(first["days"][1]["markdown"])
            manual = path.parent / "manual/2026-10-20.md"
            manual.write_text("保留我的判断。")
            second = self.run_model()
            before = path.read_bytes()
            third = self.run_model()
        self.assertEqual(model.call_count, 1)
        self.assertEqual(first["model_calls"], 1)
        self.assertEqual(second["model_calls"], 0)
        self.assertEqual(second["changed_days"], 0)
        self.assertEqual(second["days"][1]["model_status"], "cached")
        self.assertEqual(second["days"][1]["backend"], "codex")
        self.assertGreater(second["days"][1]["model_work_records"], 0)
        self.assertIn("保留我的判断。", path.read_text())
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(third["changed_days"], 0)

    def test_changed_previous_day_gets_fresh_model_answer(self):
        self.seed()
        with patch("daily_review.daily.summarize", side_effect=self.fake_summary) as model:
            self.run_model()
            self.write([message("已修正昨天的问题并交付第二版。", stamp="2026-10-20T15:59:30Z")], mode="a")
            result = self.run_model()
        self.assertEqual(model.call_count, 2)
        self.assertEqual(result["changed_days"], 1)
        self.assertEqual(result["model_calls"], 1)

    def test_model_failure_retains_facts_and_retries_without_using_stale_answer(self):
        self.seed()
        with patch("daily_review.daily.summarize", side_effect=self.fake_summary):
            self.run_model()
        self.write([message("完成新增文档。", stamp="2026-10-20T15:59:30Z")], mode="a")
        with patch("daily_review.daily.summarize", side_effect=RuntimeError("synthetic timeout")):
            failed = self.run_model()
        day = failed["days"][1]
        self.assertEqual(failed["status"], "attention")
        self.assertEqual(failed["model_errors"], 1)
        self.assertEqual(day["backend"], "rules")
        self.assertEqual(day["model_status"], "fallback")
        self.assertIn("模型提炼未完成", Path(day["markdown"]).read_text())
        stored = json.loads((Path(self.cfg["state_dir"])/"reports/2026-10-20.json").read_text())
        self.assertNotIn("model_summary", stored)
        self.assertTrue(stored["work_records"])
        self.assertNotIn("synthetic timeout", json.dumps(failed))
        with patch("daily_review.daily.summarize", side_effect=self.fake_summary):
            recovered = self.run_model()
        self.assertEqual(recovered["status"], "ok")
        self.assertEqual(recovered["model_calls"], 1)

    def test_model_failure_one_day_does_not_stop_later_days(self):
        self.seed()
        self.write([message("完成当日文档检查。", stamp="2026-10-21T10:00:00Z")], mode="a")
        def mixed(report, cfg, timeout):
            if report["date"] == "2026-10-20":
                raise RuntimeError("synthetic failure")
            return self.fake_summary(report, cfg, timeout)
        with patch("daily_review.daily.summarize", side_effect=mixed):
            result = self.run_model()
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(result["model_errors"], 1)
        self.assertEqual(result["days"][2]["model_status"], "generated")

    def test_cache_revalidated_and_model_setting_invalidates_it(self):
        self.seed()
        cache = Path(self.cfg["state_dir"])/"model-cache/2026-10-20.json"
        with patch("daily_review.daily.summarize", side_effect=self.fake_summary) as model:
            self.run_model()
            value = json.loads(cache.read_text())
            value["summary"]["projects"][0]["claims"][0]["refs"] = ["invented-reference"]
            cache.write_text(json.dumps(value))
            self.assertEqual(self.run_model()["model_calls"], 1)
            self.cfg["model"] = "synthetic-model-b"
            self.assertEqual(self.run_model()["model_calls"], 1)
        self.assertEqual(model.call_count, 3)

    def test_default_rules_never_calls_model_and_invalid_backend_fails_before_collection(self):
        self.seed()
        with patch("daily_review.daily.summarize", side_effect=AssertionError("not authorized")):
            self.assertEqual(run_daily(self.db, self.cfg, "2026-10-21", 3)["model_calls"], 0)
        with patch("daily_review.daily.collect_all", side_effect=AssertionError("invalid input")):
            with self.assertRaises(ValueError):
                run_daily(self.db, self.cfg, backend="unknown")
            with self.assertRaises(ValueError):
                run_daily(self.db, self.cfg, backend="codex", model_timeout=0)

    def test_coverage_change_rebuilds_model_but_partial_sources_remain_attention(self):
        self.seed()
        with patch("daily_review.daily.summarize", side_effect=self.fake_summary):
            self.run_model()
            original = __import__('daily_review.daily', fromlist=['build_report']).build_report
            def partial(*args):
                report = original(*args)
                report["coverage"]["remote_sources"] = [{"source_id": "synthetic", "status": "error"}]
                return report
            from daily_review.remote import collect_all
            stats = collect_all(self.db, self.cfg)
            stats["source_errors"] = 1
            with patch("daily_review.daily.build_report", side_effect=partial), patch("daily_review.daily.collect_all", return_value=stats):
                result = self.run_model()
        self.assertEqual(result["status"], "attention")
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(result["days"][1]["backend"], "codex")


class ProjectSyncCase(AgentFixture):
    def setUp(self):
        super().setUp()
        self.seed()
        self.ncfg = {"records_data_source": "11111111-1111-4111-8111-111111111111",
                     "projects_data_source": "22222222-2222-4222-8222-222222222222",
                     "project_cache": str(self.root / "absent"), "create_missing_projects": True,
                     "project_aliases": {}}

    def test_missing_project_only_planned_not_created_in_dryrun(self):
        with patch("daily_review.notion.Client.request", side_effect=AssertionError("network forbidden")):
            plan = make_plan(self.report(), self.ncfg, self.db)
        self.assertEqual(len(plan["create_projects"]), 1)
        self.assertEqual(plan["action"], "resolve_remote")

    def test_duplicates_and_generated_workspace_never_auto_created(self):
        report = self.report()
        name = report["projects"][0]["name"]
        projects = [{"id": "11111111-1111-4111-8111-111111111111", "name": name},
                    {"id": "22222222-2222-4222-8222-222222222222", "name": name}]
        ids, missing, creates, aliases = project_actions(report, projects, self.ncfg, self.db)
        self.assertFalse(creates)
        self.assertIn("ambiguous", missing[0]["reason"])
        report["projects"][0]["name"] = "g-p-00000000000000000000000000000000"
        self.assertFalse(project_actions(report, [], self.ncfg, self.db)[2])

    def test_saved_mapping_survives_project_rename(self):
        report = self.report()
        key = report["projects"][0]["project_id"]
        pid = "11111111-1111-4111-8111-111111111111"
        with self.db:
            self.db.execute("INSERT INTO project_links VALUES(?,?,?,?)", (key, pid, "original", "now"))
        ids, missing, creates, aliases = project_actions(report, [{"id": pid, "name": "Renamed"}], self.ncfg, self.db)
        self.assertEqual(ids, [pid])
        self.assertFalse(missing)
        self.assertFalse(creates)

    def test_stale_explicit_alias_is_not_a_new_project(self):
        report = self.report()
        self.ncfg["project_aliases"][report["projects"][0]["project_id"]] = "11111111-1111-4111-8111-111111111111"
        ids, missing, creates, aliases = project_actions(report, [], self.ncfg, self.db)
        self.assertFalse(creates)
        self.assertIn("mapping unavailable", missing[0]["reason"])

    def test_verified_connector_receipt_can_continue_through_rest(self):
        from daily_review.notion import managed_text
        from daily_review.util import digest
        report = self.report()
        pid = "11111111-1111-4111-8111-111111111111"
        text = managed_text(report)
        snapshot = {"page": {"id": pid, "properties": {}}, "projects": [],
                    "blocks": [{"id": "block1", "type": "code", "code": {"rich_text": rich(text)}}]}
        self.assertEqual(make_plan(report, self.ncfg, self.db, snapshot)["action"], "blocked")
        with self.db:
            self.db.execute("INSERT INTO connector_receipts VALUES(?,?,?,?)", (pid, report["date"], digest(text), "verified"))
        self.assertEqual(make_plan(report, self.ncfg, self.db, snapshot)["action"], "update_draft")
        snapshot["blocks"][0]["code"]["rich_text"] = rich(text + "\nmanual edit")
        self.assertEqual(make_plan(report, self.ncfg, self.db, snapshot)["action"], "blocked")

    def test_create_readback_and_repeat_reuses_existing_project(self):
        pid = "11111111-1111-4111-8111-111111111111"
        report = self.report()
        planned = make_plan(report, self.ncfg, self.db)["create_projects"]
        class Projects:
            def __init__(self):
                self.pages = []
                self.writes = 0
            def query(self, *args):
                return self.pages
            def request(self, method, path, body=None):
                if method == "GET" and path.startswith("data_sources/"):
                    return {"properties": {"项目": {"type": "title"}, "状态": {"type": "select", "select": {"options": [{"name": "待确认"}]}}}}
                if method == "POST":
                    self.writes += 1
                    page = {"id": pid, "properties": body["properties"]}
                    self.pages.append(page)
                    return page
                if method == "GET" and path.startswith("pages/"):
                    return self.pages[0]
                raise AssertionError(path)
        client = Projects()
        self.assertEqual(ensure_projects(client, self.ncfg, self.db, report, planned), [pid])
        ensure_projects(client, self.ncfg, self.db, report, planned)
        self.assertEqual(client.writes, 1)
        self.assertEqual(self.db.execute("SELECT notion_page_id FROM project_links").fetchone()[0], pid)
