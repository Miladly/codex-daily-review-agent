import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from daily_review.config import ROOT, load
from daily_review.cli import main
from daily_review.collector import Collector
from daily_review.store import connect


class OnboardingCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.config = self.root/'config.local.json'

    def tearDown(self):
        self.temp.cleanup()

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(['--config', str(self.config), *args])
        return code, out.getvalue(), err.getvalue()

    def test_init_private_config_and_no_overwrite(self):
        code, out, err = self.call('init', '--codex-home', str(self.root/'codex'), '--timezone', 'UTC')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        before = self.config.read_bytes()
        self.assertEqual(self.call('init')[0], 1)
        self.assertEqual(self.config.read_bytes(), before)
        cfg = load(self.config)
        self.assertEqual(cfg['state_dir'], str(self.root/'var'))
        self.assertEqual(cfg['timezone'], 'UTC')
        self.assertEqual(json.loads(out)['remote_writes'], 0)

    def test_example_empty_exclusions_cannot_remove_default(self):
        self.config.write_text('{"exclude_cwds": []}')
        cfg = load(self.config)
        self.assertIn(str(ROOT), cfg['exclude_cwds'])
        self.assertIn(cfg['state_dir'], cfg['exclude_cwds'])

    def test_relative_paths_resolve_against_configuration(self):
        self.config.write_text(json.dumps({'codex_home':'./codex','state_dir':'./state', 'project_overrides':[{'cwd':'./project','key':'demo','name':'Demo'}]}))
        cfg = load(self.config)
        self.assertEqual(cfg['codex_home'], str(self.root/'codex'))
        self.assertEqual(cfg['state_dir'], str(self.root/'state'))
        self.assertEqual(cfg['project_overrides'][0]['cwd'], str(self.root/'project'))
        with patch.dict(os.environ, {'DAILY_REVIEW_CONFIG':str(self.config)}):
            self.assertEqual(load()['state_dir'], cfg['state_dir'])

    def test_invalid_configurations_fail_before_state_creation(self):
        fixtures = [[], {'remote_sources':'bad'}, {'remote_sources':[{'id':'x','host':'-unsafe'}]}, {'remote_sources':[{'id':'x','host':'safe','timeout':0}]}, {'timezone':'Missing/Timezone'}, {'notion':{'token':'SYNTHETIC_NOT_A_REAL_SECRET'}}, {'exclude_cwds':[None]}, {'remote_sources':[{'id':'x','host':'safe','enabled':'false'}]}, {'remote_sources':[{'id':'x','host':'safe','password':'SYNTHETIC_NOT_A_REAL_SECRET'}]}]
        for data in fixtures:
            self.config.write_text(json.dumps(data))
            code, out, err = self.call('collect')
            self.assertEqual(code, 1, (data,out,err))
            self.assertNotIn('Traceback', err)
            self.assertNotIn('SYNTHETIC_NOT_A_REAL_SECRET',err)
            self.assertFalse((self.root/'var').exists())

    def test_missing_source_attention_and_later_recovery(self):
        self.config.write_text(json.dumps({'codex_home':str(self.root/'missing')}))
        code, out, err = self.call('doctor')
        self.assertEqual(code, 2, err)
        self.assertFalse(json.loads(out)['source_available'])
        code, out, err = self.call('daily','--through','2026-01-15')
        self.assertEqual(code, 2, err)
        self.assertEqual(json.loads(out)['collection']['source_errors'], 1)
        (self.root/'missing/sessions').mkdir(parents=True)
        code, out, err = self.call('daily','--through','2026-01-15')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['collection']['source_errors'], 0)

    def test_archive_only_source_is_available(self):
        (self.root/'codex/archived_sessions').mkdir(parents=True)
        self.config.write_text(json.dumps({'codex_home':str(self.root/'codex')}))
        cfg=load(self.config)
        db=connect(cfg['state_dir'])
        try:
            stats=Collector(db,cfg).scan()
            self.assertEqual(stats['issues'],0)
            self.assertEqual(db.execute("SELECT status FROM source_status WHERE source_id='codex'").fetchone()[0],'ok')
        finally:
            db.close()
