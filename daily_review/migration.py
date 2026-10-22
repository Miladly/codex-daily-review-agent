"""Offline Markdown migration: preview first, copy without overwrite, switch last."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from .config import load, resolve_reports_dir
from .util import atomic_write, lock, under, write_json

MARKERS = ('<!-- DAILY_REVIEW_AGENT_BEGIN -->', '<!-- DAILY_REVIEW_AGENT_END -->',
           '## 人工补充与最终反思\n\n')
DATE_NOTE = re.compile(r'\d{4}-\d{2}-\d{2}\.md')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(source, destination):
    if not source.is_dir():
        raise ValueError('configured reports directory is missing; generate a draft first')
    if destination.exists() and not destination.is_dir():
        raise ValueError('destination must be a directory')
    for root in (source, destination):
        manual = root/'manual'
        if manual.is_symlink() or (manual.exists() and not manual.is_dir()):
            raise ValueError('manual reflection folder must be a real directory')
    rows = []
    for folder in (source, source/'manual'):
        for path in sorted(folder.glob('*.md')):
            if not DATE_NOTE.fullmatch(path.name):
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError('symlinked or non-file source note; migration stopped')
            if folder == source and not all(marker in path.read_text() for marker in MARKERS):
                raise ValueError('source has an unmanaged date note; original preserved')
            relative = path.relative_to(source).as_posix()
            target = destination/relative
            expected = sha(path)
            if target.is_symlink() or (target.exists() and (not target.is_file() or sha(target) != expected)):
                raise ValueError('destination note conflict; existing files preserved')
            rows.append({'file': relative, 'sha256': expected, 'bytes': path.stat().st_size})
    return rows


def copy_new(source, target, expected):
    """Install a complete private file without replacing an existing target."""
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('source changed during migration; configuration unchanged')
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError('destination symlink; migration stopped')
    if target.exists():
        if not target.is_file() or sha(target) != expected:
            raise ValueError('destination changed during migration; configuration unchanged')
        return
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.review-copy-', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or not target.is_file() or sha(target) != expected:
                raise ValueError('destination changed during migration; configuration unchanged') from None
    finally:
        Path(temporary).unlink(missing_ok=True)


def migrate_reports(config_file, destination, apply=False):
    if config_file is None:
        raise ValueError('migration requires an existing private configuration; run init first')
    config_file = Path(config_file).resolve()
    cfg = load(config_file)
    source = Path(cfg['reports_dir'])
    target = Path(resolve_reports_dir(destination, cfg, config_file.parent, config_file))
    result = {'status': 'preview', 'source': str(source), 'destination': str(target),
              'applied': False, 'originals_retained': True, 'model_calls': 0, 'external_writes': 0}
    if source == target:
        return dict(result, status='already_configured', note_files=0, manual_files=0)
    if under(source, target) or under(target, source):
        raise ValueError('source and destination report folders must not overlap')

    def plan():
        rows = inventory(source, target)
        result.update(note_files=sum('/' not in row['file'] for row in rows),
                      manual_files=sum(row['file'].startswith('manual/') for row in rows))
        return rows

    if not apply:
        plan()
        return result
    with lock(Path(cfg['state_dir'])/'.agent.lock'):
        # Recheck under the same lock used by daily/review before copying.
        if load(config_file) != cfg:
            raise ValueError('output configuration changed; preview migration again')
        rows = plan()
        before = config_file.read_text()
        raw = json.loads(before)
        raw['reports_dir'] = str(target)
        backup_root = Path(cfg['state_dir'])/'migrations'
        backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = Path(tempfile.mkdtemp(prefix='reports-', dir=backup_root))
        atomic_write(backup/'config.before.json', before)
        write_json(backup/'manifest.json', dict(result, status='copying', files=rows))
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        for row in rows:
            copy_new(source/row['file'], target/row['file'], row['sha256'])
        for row in rows:
            if sha(source/row['file']) != row['sha256'] or sha(target/row['file']) != row['sha256']:
                raise ValueError('note changed during migration; originals and configuration retained')
        if inventory(source, target) != rows:
            raise ValueError('note inventory changed during migration; configuration retained')
        if config_file.read_text() != before:
            raise ValueError('configuration changed during migration; refusing to overwrite it')
        # Only verified Markdown and manual reflections move to the active vault.
        # JSON/SQLite/cursors stay private; original Markdown remains as a snapshot.
        write_json(backup/'manifest.json', dict(result, status='verified', files=rows))
        atomic_write(config_file, json.dumps(raw, ensure_ascii=False, indent=2)+'\n')
        result.update(status='complete', applied=True, backup=str(backup), hashes_verified=True)
        write_json(backup/'manifest.json', dict(result, files=rows))
        return result
