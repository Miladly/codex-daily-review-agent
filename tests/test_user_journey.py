"""First-install and unattended-run failures, using only synthetic local data."""
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import unittest
import tempfile
from unittest.mock import patch
from test_agent import AgentFixture, message
from daily_review.cli import main
from daily_review.config import initialize, load
from daily_review.daily import run_daily
from daily_review.health import run_status
from daily_review.model import doctor, resolve_binary
from daily_review.store import connect
from daily_review.util import write_json


class InstallJourneyCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.config = self.root/"config.local.json"

    def tearDown(self):
        self.temp.cleanup()

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--config", str(self.config), *args])
        return code, out.getvalue(), err.getvalue()

    def test_init_persists_agent_thread_exclusion_for_later_scheduler(self):
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "synthetic-review-thread"}):
            initialize(self.config, str(self.root/"codex"))
        with patch.dict(os.environ, {}, clear=True):
            self.assertIn("synthetic-review-thread", load(self.config)["exclude_session_ids"])

    def test_config_can_follow_command_without_losing_global_argument(self):
        initialize(self.config, str(self.root/"codex"))
        (self.root/"codex/sessions").mkdir(parents=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["doctor", "--config", str(self.config)])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.getvalue())["source_available"])

    def test_bad_dates_and_timeout_fail_before_state_or_collection(self):
        initialize(self.config, str(self.root/"codex"))
        cases = [("run", "--date", "bad-date"), ("review", "--date", "2026-02-30"),
                 ("review", "--date", "2026-1-1"), ("daily", "--days", "0"),
                 ("daily", "--model-timeout", "0"), ("run", "--model-timeout", "-1")]
        with patch("daily_review.cli.collect_all", side_effect=AssertionError("must not collect")), \
             patch("daily_review.daily.collect_all", side_effect=AssertionError("must not collect")):
            for args in cases:
                self.assertEqual(self.call(*args)[0], 1, args)
                self.assertFalse((self.root/"var").exists(), args)

    def test_relative_model_binary_resolves_from_config_directory(self):
        self.config.write_text(json.dumps({"codex_binary": "./bin/codex"}))
        self.assertEqual(load(self.config)["codex_binary"], str(self.root/"bin/codex"))
        for value in (None, 9, {}, ""):
            self.config.write_text(json.dumps({"codex_binary": value}))
            self.assertEqual(self.call("doctor")[0], 1)

    def test_doctor_checks_requested_backend_without_model_or_source_reads(self):
        initialize(self.config, str(self.root/"codex"))
        (self.root/"codex/sessions").mkdir(parents=True)
        with patch("daily_review.cli.doctor", return_value={"available": False}), \
             patch("daily_review.cli.summarize", side_effect=AssertionError("no model")):
            self.assertEqual(self.call("doctor")[0], 0)
            code, out, err = self.call("doctor", "--backend", "codex")
        self.assertEqual(code, 2, err)
        self.assertEqual(json.loads(out)["status"], "attention")
        self.assertFalse((self.root/"var").exists())

    def test_status_before_first_run_is_readonly(self):
        initialize(self.config, str(self.root/"codex"))
        code, out, err = self.call("status", "--through", "2026-01-15")
        self.assertEqual(code, 2, err)
        self.assertIn("no_completed_run", json.loads(out)["reasons"])
        self.assertFalse((self.root/"var").exists())

    def test_corrupt_db_reports_recovery_hint_without_traceback_or_reset(self):
        initialize(self.config, str(self.root/"codex"))
        state = self.root/"var"
        state.mkdir()
        db = state/"review.sqlite3"
        db.write_bytes(b"synthetic damaged SQLite")
        before = db.read_bytes()
        code, out, err = self.call("collect")
        self.assertEqual(code, 1)
        self.assertIn("private backups", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(before, db.read_bytes())

    def test_newer_db_schema_is_not_silently_downgraded(self):
        state = self.root/"state"
        state.mkdir()
        path = state/"review.sqlite3"
        db = sqlite3.connect(path)
        db.execute("PRAGMA user_version=99")
        db.close()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(RuntimeError, "newer schema"):
            connect(state)
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
        db = sqlite3.connect(path)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 99)
        db.close()

    def test_cli_inspection_timeout_is_an_unavailable_diagnostic(self):
        with patch("daily_review.model.resolve_binary", return_value="synthetic-codex"), \
             patch("daily_review.model.subprocess.run", side_effect=subprocess.TimeoutExpired("synthetic-codex", 10)):
            info = doctor()
        self.assertFalse(info["available"])
        self.assertIn("timed out", info["error"])

    def test_missing_explicit_executable_never_silently_uses_the_desktop_default(self):
        with patch("daily_review.model.shutil.which", return_value=None), \
             patch("daily_review.model.Path.is_file", return_value=True), \
             patch("daily_review.model.os.access", return_value=True):
            self.assertIn("CodexCLI.app", resolve_binary("codex"))
            self.assertIsNone(resolve_binary("explicit-missing-codex"))


class RuntimeJourneyCase(AgentFixture):
    def setUp(self):
        super().setUp()
        self.cfg["notion"] = {"records_data_source": "", "projects_data_source": "",
                              "project_cache": str(self.root/"absent"), "project_aliases": {}}
        self.cfg["reports_dir"] = str(self.root/"notes")

    def test_progress_contains_phase_and_date_but_no_work_text(self):
        self.seed()
        events = []
        result = run_daily(self.db, self.cfg, "2026-10-20", 1, progress=events.append)
        self.assertEqual(result["status"], "ok")
        stages = [row["stage"] for row in events]
        self.assertEqual(stages[0], "collection_started")
        self.assertIn("collection_finished", stages)
        self.assertEqual(stages[-1], "day_finished")
        self.assertNotIn("文档检查", json.dumps(events, ensure_ascii=False))
        self.assertTrue(all(set(e) <= {"stage", "days", "inserted", "source_errors", "date", "status",
                                      "backend", "model_status", "changed", "markdown"} for e in events))

    def test_cli_progress_keeps_stdout_parseable_and_can_be_suppressed(self):
        self.seed()
        cfg = self.root/"config.json"
        cfg.write_text(json.dumps(self.cfg))
        for quiet in (False, True):
            out, err = io.StringIO(), io.StringIO()
            args = ["--config", str(cfg), "daily", "--through", "2026-10-20", "--days", "1"]
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(args + (["--quiet"] if quiet else []))
            self.assertEqual(code, 0, err.getvalue())
            self.assertEqual(json.loads(out.getvalue())["status"], "ok")
            self.assertEqual(bool(err.getvalue()), not quiet)

    def test_status_detects_missed_day_model_fallback_and_missing_note(self):
        self.seed()
        run_daily(self.db, self.cfg, "2026-10-20", 1)
        self.assertEqual(run_status(self.cfg, "2026-10-20")["status"], "ok")
        self.assertIn("expected_date_not_processed", run_status(self.cfg, "2026-10-21")["reasons"])
        self.assertIn("model_review_incomplete", run_status(self.cfg, "2026-10-20", "codex")["reasons"])
        note = Path(self.cfg["reports_dir"])/"2026-10-20.md"
        note.rename(note.with_suffix(".preserved"))
        self.assertIn("note_missing", run_status(self.cfg, "2026-10-20")["reasons"])

    def test_status_handles_corrupt_metadata_and_timezone_without_event_access(self):
        path = Path(self.cfg["state_dir"])/"runs/last-run.json"
        write_json(path, [])
        self.assertIn("run_metadata_unreadable", run_status(self.cfg)["reasons"])
        write_json(path, {"status": "ok", "timezone": "UTC", "days": []})
        self.assertIn("timezone_changed", run_status(self.cfg)["reasons"])
        write_json(path, {"status": "ok", "timezone": "Asia/Shanghai", "days": [{"date": "2026-01-15", "status": "no_events"}]})
        at = datetime(2026, 1, 15, 16, 1, tzinfo=timezone.utc)
        result = run_status(self.cfg, at=at)
        self.assertEqual(result["expected_date"], "2026-01-15")
        self.assertEqual(result["status"], "ok")
        path.write_text("{")
        self.assertEqual(run_status(self.cfg)["status"], "attention")

    def test_single_review_timeout_is_visible_in_markdown_and_keeps_facts(self):
        self.seed()
        cfg = self.root/"config.json"
        cfg.write_text(json.dumps(self.cfg))
        with patch("daily_review.cli.summarize", side_effect=subprocess.TimeoutExpired("synthetic", 10)), \
             contextlib.redirect_stdout(io.StringIO()):
            code = main(["--config", str(cfg), "review", "--date", "2026-10-20", "--backend", "codex"])
        self.assertEqual(code, 2)
        note = Path(self.cfg["reports_dir"])/"2026-10-20.md"
        self.assertIn("模型提炼未完成", note.read_text())
        self.assertIn("## 分项目工作记录", note.read_text())

    def test_empty_day_is_not_a_missing_day_or_a_failed_model(self):
        run_daily(self.db, self.cfg, "2026-10-20", 1)
        self.assertEqual(run_status(self.cfg, "2026-10-20", "codex")["status"], "ok")

    def test_interrupted_run_is_not_hidden_by_an_older_success(self):
        self.seed()
        run_daily(self.db, self.cfg, "2026-10-20", 1)
        self.write([message("新增合成工作结果。")], mode="a")
        def interrupted(event):
            if event["stage"] == "day_started":
                raise RuntimeError("synthetic interruption")
        with self.assertRaisesRegex(RuntimeError, "interruption"):
            run_daily(self.db, self.cfg, "2026-10-20", 1, progress=interrupted)
        status = run_status(self.cfg, "2026-10-20")
        self.assertEqual(status["status"], "attention")
        self.assertIn("run_unfinished", status["reasons"])
        run_daily(self.db, self.cfg, "2026-10-20", 1)
        self.assertEqual(run_status(self.cfg, "2026-10-20")["status"], "ok")
