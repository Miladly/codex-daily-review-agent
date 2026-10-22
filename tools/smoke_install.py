#!/usr/bin/env python3
"""Offline installed-wheel acceptance check using synthetic data only."""
import argparse
from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', required=True)
    args = parser.parse_args()
    wheel = Path(args.wheel).resolve()
    with zipfile.ZipFile(wheel) as package:
        metadata = next(name for name in package.namelist() if name.endswith('.dist-info/METADATA'))
        version = Parser().parsestr(package.read(metadata).decode())['Version']
        if any(name.endswith(('.jsonl', '.sqlite3', '.db')) or 'config.local' in name for name in package.namelist()):
            raise RuntimeError('runtime material found in wheel')
    with tempfile.TemporaryDirectory(prefix='review-install-check-') as temp:
        root = Path(temp).resolve()
        env = dict(os.environ)
        for key in ('PYTHONPATH', 'DAILY_REVIEW_CONFIG', 'CODEX_HOME', 'CODEX_THREAD_ID', 'NOTION_TOKEN'):
            env.pop(key, None)
        subprocess.run([sys.executable, '-m', 'venv', str(root/'venv')], check=True, capture_output=True)
        python = root/'venv/bin/python'
        subprocess.run([str(python), '-m', 'pip', 'install', '--no-cache-dir', '--no-index', '--no-deps', str(wheel)], check=True, capture_output=True, env=env)
        source, workspace = root/'codex/sessions/old-date-folder', root/'example-project'
        source.mkdir(parents=True)
        workspace.mkdir()
        marker = root/'must-not-be-created'
        rows = [
            {'type':'session_meta', 'timestamp':'2026-01-15T10:00:00Z', 'payload':{'id':'synthetic-demo', 'cwd':str(workspace), 'source':'cli'}},
            {'type':'event_msg', 'timestamp':'2026-01-15T10:01:00Z', 'payload':{'type':'item_completed','item':{'type':'CommandExecution','id':'synthetic-tool','command':'touch '+str(marker),'exit_code':0,'status':'completed','aggregated_output':'Synthetic tool result'}}},
        ]
        log = source/'synthetic.jsonl'
        log.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        original = hashlib.sha256(log.read_bytes()).hexdigest()
        config = root/'config.local.json'
        def run(*command, expected=0):
            proc = subprocess.run([str(python), '-m', 'daily_review', '--config', str(config), *command], cwd=root, env=env, text=True, capture_output=True)
            if proc.returncode != expected:
                raise RuntimeError('installed command failed: ' + command[0])
            return json.loads(proc.stdout) if proc.stdout.strip() else None
        run('init', '--codex-home', str(root/'codex'), '--timezone', 'UTC')
        data = json.loads(config.read_text())
        data['codex_binary'] = str(root/'no-codex-binary')
        config.write_text(json.dumps(data))
        doctor = run('doctor')
        assert doctor['source_available'] and not doctor['codex_cli']['available']
        first = run('daily', '--through', '2026-01-15', '--days', '3')
        second = run('daily', '--through', '2026-01-15', '--days', '3')
        assert first['collection']['inserted'] == 1
        assert second['collection']['inserted'] == 0 and second['changed_days'] == 0
        export = run('export-events', '--date', '2026-01-15')
        assert export['events'] == 1
        manual = root/'var/reports/manual/2026-01-15.md'
        manual.write_text('Synthetic manual reflection.\n')
        run('daily', '--through', '2026-01-15', '--days', '3')
        report = root/'var/reports/2026-01-15.md'
        assert 'Synthetic manual reflection.' in report.read_text()
        before = report.read_bytes()
        run('daily', '--through', '2026-01-15', '--days', '3')
        assert report.read_bytes() == before and not marker.exists()
        assert hashlib.sha256(log.read_bytes()).hexdigest() == original
        assert config.stat().st_mode & 0o777 == 0o600
        assert (root/'var/review.sqlite3').stat().st_mode & 0o777 == 0o600
        config_before = config.read_bytes()
        preview = run('migrate-reports', '--to', './notes')
        assert preview['status'] == 'preview' and config.read_bytes() == config_before
        assert not (root/'notes').exists()
        migrated = run('migrate-reports', '--to', './notes', '--apply')
        assert migrated['status'] == 'complete' and migrated['hashes_verified']
        assert (Path(migrated['backup'])/'config.before.json').read_bytes() == config_before
        note = root/'notes/2026-01-15.md'
        assert note.read_bytes() == report.read_bytes()
        run('daily', '--through', '2026-01-15', '--days', '3')
        assert 'Synthetic manual reflection.' in note.read_text()
        reviewed = run('review', '--date', '2026-01-15')
        assert Path(reviewed['markdown']) == note
        assert Path(reviewed['json']) == root/'var/reports/2026-01-15.json'
        payload = json.loads(Path(reviewed['json']).read_text())
        assert payload['review_method']['version'] == '1.0.0' and len(payload['review_method']['sha256']) == 64
        assert Path(reviewed['json']).exists() and not list((root/'notes').rglob('*.json'))
        conflict = root/'notes/2026-01-16.md'
        conflict.write_text('An existing personal note.\n')
        run('review', '--date', '2026-01-16', expected=1)
        assert conflict.read_text() == 'An existing personal note.\n'
        separate = root/'fresh/config.local.json'
        subprocess.run([str(python), '-m', 'daily_review', '--config', str(separate), 'init',
                        '--codex-home', str(root/'codex'), '--reports-dir', './vault'],
                       cwd=root, env=env, check=True, capture_output=True)
        assert json.loads(separate.read_text())['reports_dir'] == str(root/'fresh/vault')
        entry = subprocess.run([str(root/'venv/bin/daily-review'), '--version'], cwd=root, env=env, check=True, capture_output=True, text=True)
        assert entry.stdout.strip() == version
    print(json.dumps({'status':'ok','version':version,'installed_outside_checkout':True,
                      'source_unchanged':True,'recorded_command_executed':False,'repeat_inserted':0,
                      'repeat_changed_days':0,'manual_preserved':True,'config_and_db_mode':'0600',
                      'obsidian_output':True,'migration_preview_and_apply':True,'unmanaged_note_preserved':True,
                      'credentials_required':False,'model_calls':0,'external_writes':0}))


if __name__ == '__main__':
    main()
