"""Synthetic acceptance cases for factual journals with a separate review layer."""
import copy
from test_agent import AgentFixture, meta, message, command
from test_review_quality import empty_answer, output_claim
from daily_review.journal import artifact_links
from daily_review.model import minimized_input, validate_output
from daily_review.review import render, save_report


class JournalCase(AgentFixture):
    def test_record_and_later_repair_survive_noise_with_original_identity(self):
        rows = [meta(self.cwd), message('已交付合成草稿；尚未最终验收。', stamp='2026-10-20T01:00:00Z'),
                message('字段校验失败，尚未修复。', stamp='2026-10-20T02:00:00Z')]
        rows += [message('重试连接 {}，等待响应。'.format(i), stamp='2026-10-20T03:00:00Z') for i in range(12)]
        rows += [message('已修正字段校验并启动独立接续，最终效果仍待评测。', stamp='2026-10-20T14:00:00Z')]
        self.write(rows); self.scan(); report=self.report()
        records=report['work_records']
        self.assertTrue(any('已交付' in r['work'] for r in records))
        self.assertTrue(any('已修正字段' in r['work'] for r in records))
        self.assertTrue(all(r['date']=='2026-10-20' and r['project_id']==report['projects'][0]['project_id'] for r in records))
        self.assertEqual(records[-1]['local_time'],'22:00')
        data=minimized_input(report)
        self.assertIn('已修正字段',data['sessions'][0]['claims'][0]['text'])

    def test_factual_records_precede_review_and_survive_json_storage(self):
        self.seed(); report=self.report(); body=render(report)
        self.assertLess(body.index('## 分项目工作记录'),body.index('## 复盘思考'))
        self.assertIn('## 后续事项与完成标准',body)
        self.assertIn('人工补充与最终反思',body)
        save_report(self.db,self.cfg,report)
        import json
        saved=json.loads(self.db.execute('select payload from reports').fetchone()[0])
        self.assertEqual(saved['format'],'record-review-1')
        self.assertTrue(saved['work_records'])
        self.assertTrue(saved['projects'][0]['sessions'][0]['work_records'])

    def test_intent_only_session_has_no_completed_journal_item(self):
        self.write([meta(self.cwd),message('请制作完整报告。','user')]);self.scan()
        report=self.report()
        self.assertEqual(report['work_records'],[])
        self.assertIn('暂无足够结果记录',render(report))

    def test_artifact_navigation_rejects_active_remote_and_credential_urls(self):
        text='[报告](docs/synthetic-report.md) [危险](javascript:run.pdf) [网络](https://example.com/private.pdf) [带参](docs/file.pdf?token=value)'
        result=artifact_links(text)
        self.assertEqual([a['target'] for a in result],['docs/synthetic-report.md'])
        self.assertEqual(result[0]['availability'],'not_checked')

    def test_model_can_enrich_record_without_erasing_rule_archive(self):
        self.seed();report=self.report();data=minimized_input(report)
        candidate=next(c for c in data['sessions'][0]['claims'] if c['section_hint']=='work_records')
        result=empty_answer(data)
        result['projects'][0]['claims']=[output_claim(candidate,'work_records')]
        report['model_summary']=validate_output(result,data)
        old=copy.deepcopy(report['work_records']);body=render(report)
        self.assertIn(candidate['text'],body)
        self.assertEqual(report['work_records'],old)

    def test_past_evidence_and_intent_cannot_be_model_work_records(self):
        self.write([meta(self.cwd),message('计划制作报告。','user',stamp='2026-10-19T10:00:00Z'),
                    message('已保存合成草稿。')]);self.scan();data=minimized_input(self.report())
        result=empty_answer(data)
        result['daily']['claims']=[output_claim(data['prior_context'][0]['claim'],'work_records')]
        with self.assertRaisesRegex(ValueError,'prior-only'):
            validate_output(result,data)
        intent=next(e for e in data['prior_context'] if e['claim']['evidence']=='C')
        self.assertEqual(intent['date'],'2026-10-19')

    def test_later_provisional_approval_is_retained_as_report_not_final_acceptance(self):
        self.write([meta(self.cwd),message('初稿仍需修改范围。',stamp='2026-10-20T01:00:00Z'),
                    message('核对最新提交后，三处问题已落实，可作为暂定稿继续下一节。',stamp='2026-10-20T13:00:00Z')]);self.scan()
        rows=self.report()['work_records']
        self.assertIn('暂定稿',rows[-1]['work'])
        self.assertEqual(rows[-1]['status'],'reported')
        self.assertEqual(rows[-1]['evidence'],'B')

    def test_reflection_limit_does_not_limit_project_work_records(self):
        self.seed();report=self.report();data=minimized_input(report)
        source=next(c for c in data['sessions'][0]['claims'] if c['section_hint']=='work_records')
        answer=empty_answer(data)
        answer['projects'][0]['claims']=[dict(output_claim(source,'work_records'),text='合成工作事项'+str(i)) for i in range(7)]
        report['model_summary']=validate_output(answer,data)
        body=render(report)
        self.assertTrue(all('合成工作事项'+str(i) in body for i in range(7)))

    def test_old_persisted_reports_keep_records_when_planned(self):
        self.seed();report=self.report()
        report.pop('work_records')
        for p in report['projects']:p.pop('work_records')
        self.assertIn('命令退出码',render(report))

    def test_recorded_file_uri_and_typed_artifact_are_navigation_only(self):
        items=artifact_links('[样稿](file:///tmp/example%20draft.md) /tmp/other.svg')
        self.assertEqual({a['target'] for a in items},{'/tmp/example draft.md','/tmp/other.svg'})
        self.assertTrue(all(a['availability']=='not_checked' for a in items))

    def test_failed_attempt_is_work_history_without_claiming_completion(self):
        self.write([meta(self.cwd),command(code=1)]);self.scan();rows=self.report()['work_records']
        self.assertTrue(rows)
        self.assertEqual(rows[0]['source_kind'],'command_error')
        self.assertEqual(rows[0]['status'],'observed')
        self.assertIn('result',rows[0])

    def test_model_preview_uses_current_records_without_external_call(self):
        import json
        from unittest.mock import patch
        from daily_review.cli import main
        self.seed();config=self.root/'config.json';config.write_text(json.dumps(self.cfg))
        with patch('daily_review.cli.summarize') as external,patch('daily_review.cli.emit'):
            self.assertEqual(main(['--config',str(config),'model-preview','--date','2026-10-20']),0)
            external.assert_not_called()
        from pathlib import Path
        data=json.loads((Path(self.cfg['state_dir'])/'model-input/2026-10-20.json').read_text())
        self.assertTrue(any(c['section_hint']=='work_records' for s in data['sessions'] for c in s['claims']))


    def test_auto_records_escape_html_images_and_note_embeds(self):
        attack = '<img src="https://example.com/synthetic-pixel"> ![](https://example.com/pixel) ![[embed]]'
        self.write([meta(self.cwd), message('已完成合成工作。'+attack)]); self.scan()
        report = self.report()
        report['projects'][0]['name'] = attack
        body = render(report)
        self.assertNotIn('<img', body)
        self.assertNotIn('![](', body)
        self.assertNotIn('![[embed]]', body)
        self.assertIn('&lt;img', body)
        self.assertIn('已完成合成工作', body)
        self.assertIn('<!-- DAILY_REVIEW_AGENT_BEGIN -->', body)
        self.assertIn('<!-- DAILY_REVIEW_AGENT_END -->', body)

    def test_model_text_and_acceptance_cannot_activate_html_or_images(self):
        self.seed(); report=self.report(); data=minimized_input(report)
        source=next(c for c in data['sessions'][0]['claims'] if c['section_hint']=='work_records')
        attack='<iframe src="https://example.com/synthetic"></iframe> ![](https://example.com/pixel)'
        answer=empty_answer(data)
        answer['projects'][0]['claims']=[dict(output_claim(source,'work_records'),text=attack)]
        answer['daily']['claims']=[dict(output_claim(source,'next_steps',evidence='C',acceptance=attack),text='合成后续建议')]
        report['model_summary']=validate_output(answer,data)
        body=render(report)
        self.assertNotIn('<iframe',body)
        self.assertNotIn('![](',body)
        self.assertIn('&lt;iframe',body)
        self.assertIn('完成标准',body)

    def test_decoded_artifact_schemes_and_controls_are_rejected(self):
        for target in ('javascript%3Arun.pdf','https%3A//example.com/file.md',
                       'docs/file%3Ftoken%3Dsynthetic.md','docs/line%0Abreak.md',
                       'docs/line%0Dbreak.md','%2F%2Fexample.com/file.md'):
            self.assertEqual(artifact_links('[合成产物]('+target+')'),[],target)
        self.assertEqual(artifact_links('[合成产物](docs/example%20draft.md)')[0]['target'],'docs/example draft.md')

    def test_persisted_artifacts_are_revalidated_and_safe_paths_are_encoded(self):
        self.seed(); report=self.report()
        record=report['projects'][0]['work_records'][0]
        record['artifacts']=[{'label':'unsafe','target':'javascript:run.md','origin':'local'},
                             {'label':'safe','target':'docs/example draft(1).md','origin':'local'}]
        body=render(report)
        self.assertNotIn('javascript:',body)
        self.assertIn('docs/example%20draft%281%29.md',body)

    def test_security_escaping_does_not_rewrite_human_reflection(self):
        from pathlib import Path
        self.seed(); report=self.report()
        manual=Path(self.cfg['state_dir'])/'reports/manual/2026-10-20.md'
        manual.parent.mkdir(parents=True,exist_ok=True)
        human='人工自选格式：<span>保留原文</span> ![[my-note]]'
        manual.write_text(human)
        path=save_report(self.db,self.cfg,report)
        self.assertTrue(path.read_text().endswith(human))
        self.assertEqual(manual.read_text(),human)
        save_report(self.db,self.cfg,report)
        self.assertEqual(manual.read_text(),human)
