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
