import json
from pathlib import Path
from unittest.mock import patch
from test_agent import AgentFixture
from daily_review.cli import main
from daily_review.config import initialize, load
from daily_review.daily import run_daily
from daily_review.migration import copy_new, migrate_reports
from daily_review.review import save_report
from daily_review.util import lock


class NotesCase(AgentFixture):
    def setUp(self):
        super().setUp()
        self.config = self.root/'config.json'
        self.vault = (self.root/'vault').resolve()
        self.source = Path(self.cfg['state_dir'])/'reports'
        self.config.write_text(json.dumps(self.cfg))

    def seed_report(self):
        self.seed()
        return save_report(self.db, self.cfg, self.report())

    def test_init_external_relative_folder_and_mandatory_exclusion(self):
        other = self.root/'setup/config.json'
        initialize(other, str(self.home), reports_dir='../vault')
        cfg = load(other)
        self.assertEqual(cfg['reports_dir'], str(self.vault))
        self.assertIn(str(self.vault), cfg['exclude_cwds'])
        self.assertFalse(self.vault.exists())
        self.assertEqual(other.stat().st_mode & 0o777, 0o600)

    def test_invalid_destinations_rejected_before_init_writes(self):
        for destination in ('', str(self.home/'notes'), str(self.home), str(self.root), str(self.root/'setup/var')):
            config = self.root/'setup/config.json'
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                initialize(config, str(self.home), reports_dir=destination)
            self.assertFalse(config.exists())

    def test_nonstring_and_empty_config_paths_rejected(self):
        for destination in ('', None, 42):
            self.config.write_text(json.dumps(dict(self.cfg, reports_dir=destination)))
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                load(self.config)

    def test_default_path_is_backwards_compatible(self):
        cfg = load(self.config)
        self.assertEqual(Path(cfg['reports_dir']), self.source.resolve())

    def test_notes_cannot_expose_config_with_separate_state(self):
        folder = self.root/'config-folder'
        folder.mkdir()
        config = folder/'config.json'
        config.write_text(json.dumps(dict(self.cfg, reports_dir=str(folder))))
        with self.assertRaisesRegex(ValueError, 'private configuration'):
            load(config)

    def test_external_markdown_keeps_json_and_sqlite_private(self):
        self.seed()
        cfg = dict(self.cfg, reports_dir=str(self.vault))
        report = self.report()
        path = save_report(self.db, cfg, report)
        self.assertEqual(path.parent, self.vault)
        self.assertTrue((self.source/path.with_suffix('.json').name).exists())
        self.assertFalse(list(self.vault.rglob('*.json')))
        self.assertFalse(list(self.vault.rglob('*.sqlite3')))
        before = path.read_bytes()
        save_report(self.db, cfg, report)
        self.assertEqual(path.read_bytes(), before)

    def test_manual_and_inline_edits_survive_external_regeneration(self):
        self.seed()
        cfg = dict(self.cfg, reports_dir=str(self.vault))
        report = self.report()
        path = save_report(self.db, cfg, report)
        manual = path.parent/'manual'/path.name
        manual.write_text('Synthetic personal reflection.\n')
        save_report(self.db, cfg, report)
        with path.open('a') as stream:
            stream.write('\nEdited in a Markdown editor.\n')
        save_report(self.db, cfg, report)
        save_report(self.db, cfg, report)
        self.assertEqual(path.read_text().count('Edited in a Markdown editor.'), 1)
        self.assertIn('Edited in a Markdown editor.', manual.read_text())
        self.assertIn('Synthetic personal reflection.', path.read_text())

    def test_unmanaged_or_damaged_notes_are_never_overwritten(self):
        self.seed()
        for content in ('Existing personal note\n', '<!-- DAILY_REVIEW_AGENT_BEGIN -->\n'):
            path = self.source/'2026-10-20.md'
            path.parent.mkdir(exist_ok=True)
            path.write_text(content)
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, 'preserved'):
                save_report(self.db, self.cfg, self.report())
            self.assertEqual(path.read_text(), content)

    def test_report_and_manual_symlinks_are_rejected(self):
        self.seed()
        self.vault.mkdir()
        outside = self.root/'outside.md'
        outside.write_text('Do not change\n')
        report = self.vault/'2026-10-20.md'
        report.symlink_to(outside)
        cfg = dict(self.cfg, reports_dir=str(self.vault))
        with self.assertRaises(ValueError):
            save_report(self.db, cfg, self.report())
        report.unlink()
        (self.vault/'manual').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            save_report(self.db, cfg, self.report())
        self.assertEqual(outside.read_text(), 'Do not change\n')

    def test_cli_returns_existing_json_path(self):
        self.seed()
        self.config.write_text(json.dumps(dict(self.cfg, reports_dir=str(self.vault))))
        with patch('daily_review.cli.emit') as emit:
            result = main(['--config', str(self.config), 'review', '--date', '2026-10-20'])
        self.assertEqual(result, 0)
        output = emit.call_args.args[0]
        self.assertTrue(Path(output['json']).exists())
        self.assertTrue(Path(output['markdown']).exists())
        self.assertNotEqual(Path(output['json']).parent, Path(output['markdown']).parent)

    def test_daily_updates_external_notes_and_keeps_plans_private(self):
        self.seed()
        self.config.write_text(json.dumps(dict(self.cfg, reports_dir='./vault')))
        cfg = load(self.config)
        with patch('daily_review.daily.collect_all', return_value={'issues': 0, 'source_errors': 0}):
            first = run_daily(self.db, cfg, '2026-10-20', 1)
            second = run_daily(self.db, cfg, '2026-10-20', 1)
        self.assertEqual(Path(first['days'][0]['markdown']).parent, self.vault)
        self.assertEqual(Path(first['days'][0]['notion_plan']).parent, Path(cfg['state_dir'])/'notion-plans')
        self.assertEqual(second['changed_days'], 0)
        self.assertEqual(second['remote_writes'], 0)

    def test_migration_preview_has_no_writes(self):
        path = self.seed_report()
        before = self.config.read_bytes(), path.read_bytes()
        result = migrate_reports(self.config, './vault')
        self.assertEqual(result['status'], 'preview')
        self.assertEqual(result['note_files'], 1)
        self.assertEqual(result['manual_files'], 1)
        self.assertEqual((self.config.read_bytes(), path.read_bytes()), before)
        self.assertFalse(self.vault.exists())
        self.assertFalse((self.root/'state/migrations').exists())

    def test_migration_verified_copy_switch_backup_and_originals(self):
        old = self.seed_report()
        old_bytes = old.read_bytes()
        manual = self.source/'manual'/old.name
        manual.write_text('Synthetic manual text\n')
        (self.source/'private-extra.json').write_text('{}')
        (self.source/'other-document.md').write_text('Keep this extra document here')
        before_config = self.config.read_bytes()
        result = migrate_reports(self.config, './vault', apply=True)
        self.assertEqual(result['status'], 'complete')
        self.assertTrue(result['hashes_verified'])
        self.assertEqual((self.vault/old.name).read_bytes(), old_bytes)
        self.assertEqual(old.read_bytes(), old_bytes)
        self.assertEqual((self.vault/'manual'/old.name).read_text(), 'Synthetic manual text\n')
        self.assertEqual((Path(result['backup'])/'config.before.json').read_bytes(), before_config)
        self.assertEqual(load(self.config)['reports_dir'], str(self.vault))
        self.assertFalse(list(self.vault.rglob('*.json')))
        self.assertFalse((self.vault/'other-document.md').exists())
        self.assertEqual((self.vault/old.name).stat().st_mode & 0o777, 0o600)
        save_report(self.db, load(self.config), self.report())
        self.assertIn('Synthetic manual text', (self.vault/old.name).read_text())
        self.assertEqual(old.read_bytes(), old_bytes)

    def test_destination_conflict_never_switches_or_overwrites(self):
        old = self.seed_report()
        self.vault.mkdir()
        conflict = self.vault/old.name
        conflict.write_text('Existing different personal note\n')
        before = self.config.read_bytes()
        for apply in (False, True):
            with self.assertRaisesRegex(ValueError, 'conflict'):
                migrate_reports(self.config, './vault', apply=apply)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(conflict.read_text(), 'Existing different personal note\n')

    def test_identical_preexisting_copy_is_reused(self):
        old = self.seed_report()
        self.vault.mkdir()
        (self.vault/old.name).write_bytes(old.read_bytes())
        result = migrate_reports(self.config, './vault', apply=True)
        self.assertEqual(result['status'], 'complete')

    def test_failed_copy_keeps_config_and_can_resume(self):
        old = self.seed_report()
        before = self.config.read_bytes()
        calls = []
        def fail_second(source, target, expected):
            calls.append(source)
            if len(calls) == 2:
                raise OSError('synthetic interruption')
            return copy_new(source, target, expected)
        with patch('daily_review.migration.copy_new', side_effect=fail_second), self.assertRaises(OSError):
            migrate_reports(self.config, './vault', apply=True)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertTrue(old.exists())
        self.assertTrue((self.vault/old.name).exists())
        self.assertEqual(migrate_reports(self.config, './vault', apply=True)['status'], 'complete')

    def test_concurrent_source_edit_stops_switch(self):
        old = self.seed_report()
        before = self.config.read_bytes()
        def edit_after_copy(source, target, expected):
            copy_new(source, target, expected)
            if source.resolve() == old.resolve():
                source.write_text(source.read_text()+'\nHuman edit during migration\n')
        with patch('daily_review.migration.copy_new', side_effect=edit_after_copy), self.assertRaisesRegex(ValueError, 'changed'):
            migrate_reports(self.config, './vault', apply=True)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertIn('Human edit during migration', old.read_text())

    def test_concurrent_config_edit_is_not_overwritten(self):
        self.seed_report()
        def edit_after_copy(source, target, expected):
            copy_new(source, target, expected)
            self.config.write_text(self.config.read_text()+'\n')
        before = self.config.read_text()
        with patch('daily_review.migration.copy_new', side_effect=edit_after_copy), self.assertRaisesRegex(ValueError, 'configuration changed'):
            migrate_reports(self.config, './vault', apply=True)
        self.assertTrue(self.config.read_text().startswith(before))
        self.assertNotIn('reports_dir', json.loads(self.config.read_text()))

    def test_migration_respects_active_agent_lock(self):
        self.seed_report()
        with lock(Path(self.cfg['state_dir'])/'.agent.lock'), self.assertRaisesRegex(RuntimeError, 'holds'):
            migrate_reports(self.config, './vault', apply=True)
        self.assertFalse(self.vault.exists())

    def test_new_note_during_migration_stops_switch(self):
        self.seed_report()
        before = self.config.read_bytes()
        def add_note(source, target, expected):
            copy_new(source, target, expected)
            new_note = self.source/'manual/2026-10-21.md'
            new_note.write_text('New synthetic reflection\n')
        with patch('daily_review.migration.copy_new', side_effect=add_note), self.assertRaisesRegex(ValueError, 'inventory changed'):
            migrate_reports(self.config, './vault', apply=True)
        self.assertEqual(self.config.read_bytes(), before)

    def test_cli_migration_never_opens_database_or_network(self):
        self.seed_report()
        with patch('daily_review.cli.connect', side_effect=AssertionError('database opened')), patch('daily_review.cli.collect_all', side_effect=AssertionError('collection attempted')), patch('daily_review.cli.emit') as emit:
            result = main(['--config', str(self.config), 'migrate-reports', '--to', './vault'])
        self.assertEqual(result, 0)
        self.assertEqual(emit.call_args.args[0]['status'], 'preview')
        self.assertFalse(self.vault.exists())

    def test_same_destination_is_a_noop(self):
        self.seed_report()
        before = self.config.read_bytes()
        self.assertEqual(migrate_reports(self.config, str(self.source), apply=True)['status'], 'already_configured')
        self.assertEqual(self.config.read_bytes(), before)

    def test_migration_requires_config_and_rejects_overlapping_folders(self):
        self.seed_report()
        with self.assertRaises(ValueError):
            migrate_reports(None, str(self.vault))
        with self.assertRaises(ValueError):
            migrate_reports(self.config, str(self.source/'nested'), apply=True)

    def test_unmanaged_source_and_destination_symlinks_are_rejected(self):
        old = self.seed_report()
        self.vault.mkdir()
        outside = self.root/'outside.md'
        outside.write_bytes(old.read_bytes())
        (self.vault/old.name).symlink_to(outside)
        with self.assertRaises(ValueError):
            migrate_reports(self.config, './vault', apply=True)
        (self.vault/old.name).unlink()
        old.write_text('Unmanaged source note\n')
        with self.assertRaises(ValueError):
            migrate_reports(self.config, './vault')
