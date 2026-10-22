"""Synthetic failure cases for review selection and the model evidence contract."""
import copy
import json
from unittest.mock import patch

from test_agent import AgentFixture, meta, message
from daily_review.method import method_metadata, profile
from daily_review.model import minimized_input, summarize, validate_output
from daily_review.review import render


def empty_answer(data):
    return {"sessions": [{"key": s["key"], "claims": []} for s in data["sessions"]],
            "projects": [{"key": p["key"], "claims": []} for p in data["projects"]],
            "daily": {"key": data["date"], "claims": []}}


def output_claim(c, section="progress", evidence=None, acceptance=""):
    return {"text": c["text"], "refs": list(c["refs"]), "evidence": evidence or c["evidence"],
            "section": section, "acceptance": acceptance}


class ReviewQualityCase(AgentFixture):
    def test_early_delivery_survives_late_retry_noise(self):
        rows = [meta(self.cwd), message("已交付合成检查报告，尚未进行外部验收。")]
        rows += [message("连接重试 {}，等待网络响应。".format(i)) for i in range(10)]
        # This fixture exposes the old final-four selection's omission.
        self.assertFalse(any("已交付" in r["payload"]["item"]["content"][0]["text"] for r in rows[-4:]))
        self.write(rows)
        self.scan()
        session = self.report()["projects"][0]["sessions"][0]
        self.assertTrue(any("已交付" in c["text"] for c in session["completed"]))
        self.assertLessEqual(sum("连接重试" in c["text"] for c in session["completed"]), 2)

    def test_future_plan_is_not_selected_as_completed_work(self):
        self.write([meta(self.cwd), message("下一步计划进行完整验证；当前尚未完成。")])
        self.scan()
        session = self.report()["projects"][0]["sessions"][0]
        self.assertEqual(session["completed"], [])
        self.assertTrue(session["next_steps"])
        self.assertTrue(all(c["evidence"] == "C" for c in session["next_steps"]))

    def test_prior_context_retains_date_and_is_not_today_coverage(self):
        self.write([meta(self.cwd), message("计划核对合成数据来源。", "user", stamp="2026-10-19T10:00:00Z"),
                    message("已保存合成笔记；来源仍待核对。")])
        self.scan()
        report = self.report()
        self.assertEqual(report["coverage"]["events"], 1)
        prior = report["prior_context"]["claims"]
        self.assertEqual(prior[0]["date"], "2026-10-19")
        ref = prior[0]["claim"]["refs"][0]
        self.assertEqual(report["evidence_index"][ref]["period"], "prior")
        self.assertNotIn(ref, {r for c in report["daily"]["completed"] for r in c["refs"]})
        self.assertIn("不计入今日成果", render(report))

    def test_prior_evidence_cannot_be_today_progress(self):
        self.write([meta(self.cwd), message("计划核对合成数据来源。", "user", stamp="2026-10-19T10:00:00Z"),
                    message("已保存合成笔记。")])
        self.scan()
        data = minimized_input(self.report())
        c = data["prior_context"][0]["claim"]
        result = empty_answer(data)
        result["daily"]["claims"] = [output_claim(c)]
        with self.assertRaisesRegex(ValueError, "prior-only"):
            validate_output(copy.deepcopy(result), data)
        result["daily"]["claims"][0]["section"] = "continuity"
        validate_output(result, data)

    def test_intent_only_reference_cannot_claim_progress(self):
        self.write([meta(self.cwd), message("请执行来源检查。", "user")])
        self.scan()
        data = minimized_input(self.report())
        result = empty_answer(data)
        result["daily"]["claims"] = [output_claim(data["sessions"][0]["claims"][0])]
        with self.assertRaisesRegex(ValueError, "planned work"):
            validate_output(result, data)

    def test_next_action_requires_criterion_and_c_level(self):
        self.seed()
        data = minimized_input(self.report())
        result = empty_answer(data)
        result["daily"]["claims"] = [output_claim(data["sessions"][0]["claims"][0], "next_steps", "C")]
        with self.assertRaisesRegex(ValueError, "acceptance criterion"):
            validate_output(copy.deepcopy(result), data)
        result["daily"]["claims"][0]["acceptance"] = "形成逐条来源核对表，并标记未验证条目。"
        validate_output(copy.deepcopy(result), data)
        result["daily"]["claims"] *= 4
        with self.assertRaisesRegex(ValueError, "exceed three"):
            validate_output(result, data)

    def test_model_budget_rotation_includes_quiet_project(self):
        report = {"date": "2026-01-15", "projects": [], "evidence_index": {}}
        for pid, session_count in (("busy", 40), ("quiet", 1)):
            sessions = []
            for index in range(session_count):
                claims = []
                for j in range(20):
                    ref = "{}-{}-{}".format(pid, index, j)
                    report["evidence_index"][ref] = {"evidence": "B", "kind": "statement"}
                    claims.append({"text": "合成成果说明。"*80, "evidence": "B", "refs": [ref], "section_hint": "progress"})
                sessions.append({"session_id": str(index), "project_id": pid, "candidates": claims})
            report["projects"].append({"project_id": pid, "name": pid, "sessions": sessions})
        data = minimized_input(report)
        quiet = next(s for s in data["sessions"] if s["project_key"] == "quiet")
        self.assertTrue(quiet["claims"])
        self.assertLessEqual(len(data["sessions"]), 32)
        self.assertLessEqual(data["selection"]["selected_candidates"], 72)
        self.assertLessEqual(data["excerpt_characters"], 22000)
        self.assertGreater(data["selection"]["omitted_candidates"], 0)

    def test_project_cannot_cite_other_project(self):
        self.seed()
        report = self.report()
        other = copy.deepcopy(report["projects"][0])
        other["project_id"] = "other"
        for session in other["sessions"]:
            session["project_id"] = "other"
            for c in session["candidates"]:
                c["refs"] = [r+"-other" for r in c["refs"]]
                for ref in c["refs"]:
                    report["evidence_index"][ref] = {"evidence": "C", "kind": "intent"}
        report["projects"].append(other)
        data = minimized_input(report)
        result = empty_answer(data)
        foreign = next(s for s in data["sessions"] if s["project_key"] == "other")["claims"][0]
        result["projects"][0]["claims"] = [output_claim(foreign, "goals", "C")]
        with self.assertRaisesRegex(ValueError, "out-of-scope"):
            validate_output(result, data)

    def test_packaged_method_is_explicitly_loaded_in_model_prompt(self):
        self.seed()
        report = self.report()
        result = empty_answer(minimized_input(report))
        prompts = []
        class Proc:
            returncode = 0
            def __init__(self, cmd, **kwargs):
                from pathlib import Path
                Path(cmd[cmd.index("-o")+1]).write_text(json.dumps(result))
            def communicate(self, prompt, **kwargs):
                prompts.append(prompt)
                return "", ""
        with patch("daily_review.model.doctor", return_value={"available": True, "binary": "fake", "version": "fixture"}), patch("daily_review.model.subprocess.Popen", Proc):
            summarize(report, self.cfg)
        self.assertIn(profile()["instructions"], prompts[0])
        self.assertIn(profile()["rubric"], prompts[0])
        self.assertEqual(method_metadata()["version"], "1.1.0")
        self.assertEqual(len(method_metadata()["sha256"]), 64)

    def test_model_render_includes_observable_acceptance(self):
        self.seed()
        report = self.report()
        data = minimized_input(report)
        result = empty_answer(data)
        result["daily"]["claims"] = [output_claim(data["sessions"][0]["claims"][0], "next_steps", "C", "保存逐条核对表。")]
        report["model_summary"] = validate_output(result, data)
        self.assertIn("完成标准：保存逐条核对表。", render(report))

    def test_merged_claim_references_are_bounded(self):
        self.seed()
        report = self.report()
        first = report["projects"][0]["sessions"][0]["candidates"][0]
        refs = ["synthetic-merged-"+str(i) for i in range(1000)]
        first["refs"] = refs
        for ref in refs:
            report["evidence_index"][ref] = {"evidence": "C", "kind": "intent"}
        data = minimized_input(report)
        self.assertLessEqual(len(data["sessions"][0]["claims"][0]["refs"]), 4)
        self.assertLessEqual(len(set(data["selected_evidence"]) & set(refs)), 4)
