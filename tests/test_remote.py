import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
from test_agent import AgentFixture, meta, message, record
from daily_review.remote import PROTOCOL, collect_remote, merge, request_for, ssh_command
from daily_review.review import build_report


class RemoteCase(AgentFixture):
    def setUp(self):
        super().setUp()
        self.source = {"id": "test-server", "host": "test-server", "label": "Test Server", "codex_home": str(self.home)}
        self.cfg["remote_sources"] = [self.source]
        self.worker = Path(__file__).resolve().parents[1] / "daily_review/remote_worker.py"

    def execute_worker(self, request=None):
        request = request or request_for(self.db, self.cfg, self.source)
        proc = subprocess.run([sys.executable, "-I", "-B", "-c", self.worker.read_text()],
                              input=json.dumps(request), capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.startswith(PROTOCOL))
        result = json.loads(proc.stdout[len(PROTOCOL):])
        self.assertTrue(result["ok"], result)
        return result

    def remote_seed(self):
        self.write([meta(self.cwd), record("turn_context", {"turn_id": "t1", "cwd": str(self.cwd)}),
                    message("Remote parser checked; password=private-secret"),
                    record("response_item", {"type": "reasoning", "summary": "RAW_PRIVATE_REASONING"})])

    def test_worker_reads_sources_without_writes_and_sends_no_raw_logs(self):
        self.remote_seed()
        before = {str(p): p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
        result = self.execute_worker()
        after = {str(p): p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        output = json.dumps(result)
        self.assertNotIn("RAW_PRIVATE_REASONING", output)
        self.assertNotIn("private-secret", output)
        self.assertNotIn("base_instructions", output)
        self.assertEqual(len(result["events"]), 1)

    def test_remote_cursor_roundtrip_and_append(self):
        self.remote_seed()
        result = self.execute_worker()
        self.assertEqual(merge(self.db, self.source, result), 1)
        repeated = self.execute_worker()
        self.assertEqual(repeated["stats"]["records"], 0)
        self.assertEqual(merge(self.db, self.source, repeated), 0)
        self.write([message("continued tomorrow", stamp="2026-10-20T16:00:00Z")], mode="a")
        fresh = self.execute_worker()
        self.assertEqual(merge(self.db, self.source, fresh), 1)
        self.assertEqual(build_report(self.db, "2026-10-21", "Asia/Shanghai")["coverage"]["events_by_origin"], {"test-server": 1})

    def test_remote_archive_copy_deduplicates(self):
        self.remote_seed()
        merge(self.db, self.source, self.execute_worker())
        archive = self.home / "archived_sessions/moved.jsonl"
        archive.parent.mkdir()
        archive.write_bytes(self.path.read_bytes())
        merge(self.db, self.source, self.execute_worker())
        self.assertEqual(self.count(), 1)
        paths = [r[0] for r in self.db.execute("SELECT path FROM provenance")]
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(p.startswith("ssh://test-server/") for p in paths))

    def test_remote_partial_tail_and_failed_transfer_dont_advance_cursor(self):
        self.remote_seed()
        merge(self.db, self.source, self.execute_worker())
        old = self.db.execute("SELECT offset FROM files").fetchone()[0]
        raw = json.dumps(message("partial line")).encode()
        with self.path.open("ab") as f:
            f.write(raw[:30])
        merge(self.db, self.source, self.execute_worker())
        self.assertEqual(self.db.execute("SELECT offset FROM files").fetchone()[0], old)
        with patch("daily_review.remote.fetch", side_effect=RuntimeError("disconnected")):
            status = collect_remote(self.db, self.cfg)
        self.assertEqual(status[0]["status"], "unavailable")
        self.assertEqual(self.db.execute("SELECT offset FROM files").fetchone()[0], old)
        report = self.report()
        self.assertEqual(report["coverage"]["remote_sources"][0]["status"], "unavailable")
        self.assertEqual(report["coverage"]["events"], 1)
        self.assertTrue(any("不能把缺失记录" in s for s in report["limitations"]))
        with self.path.open("ab") as f:
            f.write(raw[30:] + b"\n")
        self.assertEqual(merge(self.db, self.source, self.execute_worker()), 1)

    def test_mirrored_session_and_multiple_origins_do_not_double_count(self):
        self.remote_seed()
        result = self.execute_worker()
        merge(self.db, self.source, result)
        other = {**self.source, "id": "other-server"}
        self.assertEqual(merge(self.db, other, result), 0)
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM provenance").fetchone()[0], 2)

    def test_ssh_transport_rejects_host_injection_and_uses_readonly_options(self):
        cmd = ssh_command(self.source, "print('safe')")
        self.assertIn("BatchMode=yes", cmd)
        self.assertIn("StrictHostKeyChecking=yes", cmd)
        self.assertIn("ClearAllForwardings=yes", cmd)
        self.assertIn("python3 -I -B -c", cmd[-1])
        if sys.platform == "darwin":
            self.assertIn("UseKeychain=yes", cmd)
        with self.assertRaises(ValueError):
            ssh_command({"host": "server; touch /tmp/injected"}, "")

    def test_remote_logged_commands_are_never_executed(self):
        marker = self.root / "must-not-exist"
        self.write([meta(self.cwd), record("event_msg", {"type": "item_completed", "item": {
            "type": "CommandExecution", "id": "bad", "command": "touch " + str(marker),
            "exit_code": 0, "status": "completed", "aggregated_output": "recorded output only"}})])
        self.execute_worker()
        self.assertFalse(marker.exists())

    def test_bounded_batches_resume_within_large_file(self):
        self.write([meta(self.cwd), record("turn_context", {"turn_id": "big", "cwd": str(self.cwd)})] + [message("chunk-" + str(i) + " x" * 300) for i in range(6)])
        self.source["max_scan_bytes"] = 1200
        batches = 0
        while True:
            response = self.execute_worker()
            merge(self.db, self.source, response)
            batches += 1
            if not response.get("more"):
                break
            self.assertLess(batches, 12)
        self.assertGreater(batches, 1)
        self.assertEqual(self.count(), 6)
        self.assertEqual(self.db.execute("SELECT offset FROM files").fetchone()[0], self.path.stat().st_size)

    def test_file_uri_and_normal_path_group_together(self):
        self.write([meta(self.cwd), message("ordinary workspace"), record("event_msg", {"type": "item_completed", "item": {
            "type": "CommandExecution", "id": "uri", "command": "echo recorded", "cwd": self.cwd.as_uri(),
            "exit_code": 0, "status": "completed", "aggregated_output": "recorded"}})])
        merge(self.db, self.source, self.execute_worker())
        report = self.report()
        self.assertEqual(report["coverage"]["projects"], 1)
        self.assertEqual(report["projects"][0]["sessions"][0]["cwds"], [str(self.cwd.resolve())])

    def test_transient_ssh_failure_retries_once(self):
        self.remote_seed()
        response = self.execute_worker()
        with patch("daily_review.remote.fetch", side_effect=[RuntimeError("SSH exit 255: Timeout"), response]) as mocked, patch("daily_review.remote.time.sleep"):
            result = collect_remote(self.db, self.cfg)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(result[0]["status"], "ok")
        self.assertEqual(self.count(), 1)
