import importlib.util
from pathlib import Path
import tempfile
import unittest
from daily_review.config import load
from daily_review.notion import validate_schema
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('release_guard', Path(__file__).resolve().parents[1]/'tools/check_release.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class ReleaseCase(unittest.TestCase):
    def test_runtime_and_unexpected_sources_blocked(self):
        for path in ('var/reports/day.md', 'config.local.json', '.env', 'data/export.json', 'daily_review/extra_adapter.py'):
            self.assertFalse(guard.allowed(path))
        self.assertTrue(guard.allowed('daily_review/collector.py'))

    def test_secrets_paths_networks_blocked_without_echoing_values(self):
        values = ['sk-' + 'syntheticfixturevalue'*3, '/Users' + '/private-user/project', '10.' + '12.34.56', 'person'+'@private.invalid', '-----BEGIN '+'OPENSSH PRIVATE KEY-----']
        for value in values:
            matches = guard.findings('README.md', value.encode())
            self.assertTrue(matches)
            self.assertNotIn(value, str(matches))
        self.assertFalse(guard.findings('README.md', b'127.0.0.1 and contact@example.com'))

    def test_no_private_notion_defaults_and_no_api_without_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)/'config.json'
            config.write_text('{}')
            cfg = load(config)
        self.assertEqual(cfg['notion']['records_data_source'], '')
        self.assertEqual(cfg['notion']['projects_data_source'], '')
        client = Mock()
        with self.assertRaises(RuntimeError):
            validate_schema(client, cfg['notion'])
        client.request.assert_not_called()

    def test_observed_commit_requires_real_output_and_no_hash_fabrication(self):
        from daily_review.projects import observed_commit
        self.assertIsNone(observed_commit('命令记录：git commit -m sample\n返回：nothing committed', '/does-not-exist'))
        value = 'a'*40
        self.assertEqual(observed_commit('返回：[main '+value+'] synthetic', '/does-not-exist'), value)

    def test_commit_claim_refs_combine_and_distinct_edits_remain(self):
        from daily_review.review import unique_claims
        combined = unique_claims([{'text':'First observation','evidence':'A','refs':['first'],'outcome_key':'same-commit'}, {'text':'Second observation','evidence':'A','refs':['second'],'outcome_key':'same-commit'}])
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0]['refs'], ['first','second'])
        distinct = unique_claims([{'text':'Edit file: A','evidence':'A','refs':['a']}, {'text':'Edit file: B','evidence':'A','refs':['b']}])
        self.assertEqual(len(distinct), 2)

    def test_unknown_workspace_names_never_auto_create_projects(self):
        import sqlite3
        from daily_review.store import SCHEMA
        from daily_review.notion import project_actions
        db = sqlite3.connect(':memory:')
        db.executescript(SCHEMA)
        report={'projects':[{'project_id':'x','name':'Research host · workspace'}]}
        _,unmatched,created,_ = project_actions(report, [], {'create_missing_projects':True}, db)
        self.assertFalse(created)
        self.assertEqual(len(unmatched), 1)
        db.close()

    def test_workspace_identifiers_are_not_publishable(self):
        value = '12345678-' + 'abcd-4abc-8abc-' + '123456789abc'
        matches = guard.findings('README.md', value.encode())
        self.assertTrue(any(row[2] == 'workspace-identifier' for row in matches))
        self.assertNotIn(value, str(matches))

    def test_commit_metadata_is_checked_without_echoing_private_values(self):
        def fake_git(*args):
            if args[0] == 'rev-list':
                return b'fixture-commit\n'
            if args[0] == 'show':
                return b'Example author\n' + b'person' + b'@private.invalid\nmessage\n'
            return b''
        with patch.object(guard, 'git', side_effect=fake_git):
            count, matches = guard.scan_history()
        self.assertEqual(count, 0)
        self.assertTrue(any(row[2] == 'email-address' for row in matches))
        self.assertNotIn('person' + '@private.invalid', str(matches))
