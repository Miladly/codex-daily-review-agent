"""Portable, private configuration. No credentials are accepted in JSON."""
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .util import atomic_write, under

ROOT = Path(__file__).resolve().parent.parent


def config_path(path=None):
    if path:
        return Path(path).expanduser().resolve()
    if os.environ.get('DAILY_REVIEW_CONFIG'):
        return Path(os.environ['DAILY_REVIEW_CONFIG']).expanduser().resolve()
    for candidate in (Path.cwd()/'config.local.json', ROOT/'config.local.json'):
        if candidate.is_file():
            return candidate.resolve()
    return None


def load(path=None):
    path = config_path(path)
    checkout = (ROOT/'pyproject.toml').is_file()
    state = (path.parent/'var') if path else ROOT/'var' if checkout else Path.home()/'.local/share/codex-daily-review'
    cfg = {
        'codex_home': os.environ.get('CODEX_HOME', str(Path.home()/'.codex')),
        'state_dir': str(state), 'timezone': 'Asia/Shanghai',
        'exclude_session_ids': [], 'exclude_cwds': [], 'project_overrides': [], 'remote_sources': [],
        'notion': {'records_data_source': '', 'projects_data_source': '',
                   'project_cache': str((path.parent if path else ROOT)/'notion-projects.json'),
                   'project_aliases': {}, 'create_missing_projects': False},
    }
    if path:
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict):
            raise ValueError('configuration must be a JSON object')
        allowed = set(cfg) | {'codex_binary', 'model'}
        if set(raw) - allowed:
            raise ValueError('unknown configuration field; credentials belong in env/Keychain, not JSON')
        notion = raw.pop('notion', {})
        if not isinstance(notion, dict) or set(notion) - set(cfg['notion']):
            raise ValueError('invalid Notion configuration; credentials belong in env/Keychain')
        cfg.update(raw)
        cfg['notion'].update(notion)
    base = path.parent if path else Path.cwd()
    def absolute(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('configuration paths must be nonempty strings')
        target = Path(value).expanduser()
        return str((target if target.is_absolute() else base/target).resolve())
    for key in ('codex_home', 'state_dir'):
        cfg[key] = absolute(cfg[key])
    if under(cfg['state_dir'], cfg['codex_home']):
        raise ValueError('state_dir must be outside CODEX_HOME, before creating any local state')
    try:
        ZoneInfo(cfg['timezone'])
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise ValueError('timezone must name an installed IANA timezone, such as UTC') from None
    for key in ('exclude_session_ids', 'exclude_cwds', 'project_overrides', 'remote_sources'):
        if not isinstance(cfg[key], list):
            raise ValueError(key + ' must be a list')
    if not all(isinstance(x, str) for x in cfg['exclude_session_ids']):
        raise ValueError('exclude_session_ids must contain strings')
    # Required exclusions are additive: an empty example list cannot remove them.
    cfg['exclude_cwds'] = list(dict.fromkeys([str(ROOT), cfg['state_dir']] + [absolute(x) for x in cfg['exclude_cwds']]))
    for rule in cfg['project_overrides']:
        if not isinstance(rule, dict) or not all(isinstance(rule.get(k), str) and rule[k] for k in ('cwd', 'key', 'name')):
            raise ValueError('project_overrides require cwd, key and name strings')
        rule['cwd'] = absolute(rule['cwd'])
    ids = set()
    for source in cfg['remote_sources']:
        if not isinstance(source, dict):
            raise ValueError('remote_sources must contain objects')
        allowed_source = {'id', 'host', 'label', 'codex_home', 'enabled', 'exclude_cwds', 'project_overrides', 'timeout', 'max_batches', 'max_scan_bytes'}
        if set(source) - allowed_source or not isinstance(source.get('enabled', True), bool):
            raise ValueError('invalid remote configuration; no credentials or unknown fields allowed')
        for key in ('exclude_cwds', 'project_overrides'):
            if not isinstance(source.get(key, []), list):
                raise ValueError('remote exclusions and project overrides must be lists')
        if not all(isinstance(x, str) for x in source.get('exclude_cwds', [])):
            raise ValueError('remote exclusions must contain paths')
        for rule in source.get('project_overrides', []):
            if not isinstance(rule, dict) or not all(isinstance(rule.get(k), str) and rule[k] for k in ('cwd', 'key', 'name')):
                raise ValueError('remote project overrides require cwd, key and name')
        if any(not isinstance(source[k], str) for k in ('label', 'codex_home') if k in source):
            raise ValueError('remote label and codex_home must be strings')
        sid, host = source.get('id', ''), source.get('host', '')
        if not isinstance(sid, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,48}', sid) or sid in ids:
            raise ValueError('remote source IDs must be unique lowercase names')
        if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9_.@:-]+', host) or host.startswith('-'):
            raise ValueError('remote source must specify a valid SSH host/alias')
        for key, default in (('timeout', 90), ('max_batches', 128), ('max_scan_bytes', 8*1024*1024)):
            value = source.get(key, default)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError('remote limits and timeout must be positive integers')
        ids.add(sid)
    if not isinstance(cfg['notion']['project_aliases'], dict) or not isinstance(cfg['notion']['create_missing_projects'], bool):
        raise ValueError('Notion project_aliases must be an object and create_missing_projects a boolean')
    cfg['notion']['project_cache'] = absolute(cfg['notion']['project_cache'])
    current = os.environ.get('CODEX_THREAD_ID')
    if current:
        cfg['exclude_session_ids'] = list(dict.fromkeys(cfg['exclude_session_ids'] + [current]))
    return cfg


def initialize(path, codex_home=None, timezone='Asia/Shanghai'):
    target = Path(path).expanduser().resolve()
    if target.exists():
        raise ValueError('configuration already exists; edit it explicitly, init never overwrites')
    data = {'codex_home': codex_home or os.environ.get('CODEX_HOME', str(Path.home()/'.codex')),
            'state_dir': './var', 'timezone': timezone, 'exclude_session_ids': [],
            'exclude_cwds': [str(ROOT)], 'project_overrides': [], 'remote_sources': [],
            'notion': {'records_data_source': '', 'projects_data_source': '',
                       'project_aliases': {}, 'create_missing_projects': False}}
    # Validate before writing. No source data or credentials are read.
    if not isinstance(timezone, str):
        raise ValueError('invalid timezone')
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('timezone must name an installed IANA timezone') from None
    source = Path(data['codex_home']).expanduser().resolve()
    data['codex_home'] = str(source)
    if under(target.parent/'var', source):
        raise ValueError('state_dir must be outside CODEX_HOME')
    atomic_write(target, json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    return target
