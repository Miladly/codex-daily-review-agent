#!/usr/bin/env python3
"""Check the Git index/history without displaying candidate secret values."""
import argparse
import ipaddress
import json
from pathlib import PurePosixPath
import re
import subprocess
import sys

MODULES = {'__init__', '__main__', 'cli', 'collector', 'config', 'daily', 'exchange', 'model',
           'notion', 'projects', 'remote', 'remote_worker', 'review', 'store', 'util', 'migration', 'method', 'journal'}
ROOT_FILES = {'.gitignore', 'README.md', 'PRIVACY.md', 'LICENSE', 'pyproject.toml', 'config.example.json', 'MANIFEST.in', 'CONTRIBUTING.md', 'SECURITY.md', 'CHANGELOG.md', 'THIRD_PARTY_NOTICES.md'}
DOCS = {'docs/architecture.md', 'docs/configuration.md', 'examples/sample-review.md', 'docs/release-readiness.md',
        'docs/obsidian.md', 'docs/usage.md', 'docs/scheduling.md', 'docs/notes-design.md', 'docs/review-method-sources.md', 'docs/journal-format.md',
        'daily_review/skills/evidence-daily-review/SKILL.md',
        'daily_review/skills/evidence-daily-review/references/quality.md',
        '.agents/skills/evidence-daily-review/SKILL.md'}
TESTS = {'tests/test_agent.py', 'tests/test_daily_and_projects.py', 'tests/test_remote.py', 'tests/test_release.py', 'tests/test_onboarding.py', 'tests/test_notes.py', 'tests/test_review_quality.py', 'tests/test_journal.py'}


def allowed(path):
    if path in ROOT_FILES | DOCS | TESTS | {'tools/check_release.py', 'tools/smoke_install.py', '.github/workflows/tests.yml'}:
        return True
    p = PurePosixPath(path)
    return len(p.parts) == 2 and p.parts[0] == 'daily_review' and p.suffix == '.py' and p.stem in MODULES


PATTERNS = {
    'private-key': r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
    'credential-token': r'\b(?:sk-[A-Za-z0-9_-]{16,}|ntn_[A-Za-z0-9_-]{16,}|secret_[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b',
    'jwt': r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b',
    'home-path': r'(?:/Users|/home)/[A-Za-z0-9_.-]+',
    'url-credential': r'https?://[^/\s:@]+:[^/\s@]+@',
}


def findings(path, data):
    if not allowed(path):
        return [(path, 0, 'non-allowlisted-path')]
    if len(data) > 512 * 1024:
        return [(path, 0, 'unexpected-large-file')]
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return [(path, 0, 'binary-file')]
    result = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for category, pattern in PATTERNS.items():
            if re.search(pattern, line):
                result.append((path, lineno, category))
        for value in re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', line):
            try:
                ip = ipaddress.ip_address(value)
            except ValueError:
                continue
            # Loopback and documentation addresses are safe synthetic examples.
            if ip.is_private and not ip.is_loopback and not any(ip in ipaddress.ip_network(n) for n in ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24')):
                result.append((path, lineno, 'private-network-address'))
        for email in re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', line):
            if not email.lower().endswith(('@example.com', '@example.org', '@example.net', '@users.noreply.github.com')):
                result.append((path, lineno, 'email-address'))
    # UUID-like workspace IDs are private unless an explicit synthetic fixture.
    for lineno, line in enumerate(text.splitlines(), 1):
        for value in re.findall(r'(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', line):
            compact = value.replace('-', '')
            synthetic = len(set(compact)) == 1 or value.startswith('00000000-0000-4000-8000-') or value in {'11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'}
            if not synthetic:
                result.append((path, lineno, 'workspace-identifier'))
        if re.search(r'(?i)(?:collection://|(?:notion\.so|app\.notion\.com)/[^\s]*)[0-9a-f]{32}', line):
            result.append((path, lineno, 'workspace-url'))
    return result


def git(*args):
    return subprocess.check_output(['git', *args])


def scan_index():
    issues, count = [], 0
    for row in git('ls-files', '--stage', '-z').split(b'\0'):
        if not row:
            continue
        metadata, rawpath = row.split(b'\t', 1)
        mode, sha, stage = metadata.decode().split()
        path = rawpath.decode()
        if mode != '100644' or stage != '0':
            issues.append((path, 0, 'unexpected-mode-or-stage'))
            continue
        issues.extend(findings(path, git('cat-file', 'blob', sha)))
        count += 1
    return count, issues


def scan_history():
    seen, issues, count = set(), [], 0
    for commit in git('rev-list', '--all').decode().splitlines():
        metadata = git('show', '-s', '--format=%an%n%ae%n%cn%n%ce%n%B', commit)
        issues.extend(('commit:' + commit, line, category) for _, line, category in findings('README.md', metadata))
        for row in git('ls-tree', '-r', '-z', commit).split(b'\0'):
            if not row:
                continue
            meta, rawpath = row.split(b'\t', 1)
            mode, kind, sha = meta.decode().split()
            path = rawpath.decode()
            key = (path, sha)
            if key in seen:
                continue
            seen.add(key)
            if mode != '100644' or kind != 'blob':
                issues.append((path, 0, 'unexpected-history-object'))
                continue
            issues.extend(findings(path, git('cat-file', 'blob', sha)))
            count += 1
    return count, issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true')
    args = parser.parse_args()
    try:
        count, issues = scan_index()
        history_count = 0
        if args.history:
            history_count, prior = scan_history()
            issues.extend(prior)
    except subprocess.CalledProcessError:
        print(json.dumps({'status': 'error', 'reason': 'run in a Git repository with staged source'}))
        return 2
    issues = sorted(set(issues))
    print(json.dumps({'status': 'blocked' if issues or not count else 'ok', 'indexed_files': count,
                      'historical_path_blobs': history_count,
                      'findings': [{'path': p, 'line': n, 'category': k} for p, n, k in issues]}))
    return int(bool(issues) or not count)


if __name__ == '__main__':
    raise SystemExit(main())
