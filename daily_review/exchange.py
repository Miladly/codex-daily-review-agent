"""Versioned, redacted local interchange; never a public sharing format."""
import json
from .review import build_report
from .util import redact


def export_events(db, date, timezone):
    report = build_report(db, date, timezone)
    events = []
    for eid in report['evidence_index']:
        event = dict(db.execute('SELECT * FROM events WHERE id=?', (eid,)).fetchone())
        details = json.loads(event['details'])
        events.append({'event_id': eid, 'session_id': event['session_id'], 'event_time': event['timestamp'],
                       'project_id': event['project_id'], 'project_name': event['project_name'],
                       'source': details.get('source_tool', 'codex'), 'origin': details.get('origin_host', 'local'),
                       'kind': event['kind'], 'evidence': event['evidence'], 'summary': redact(event['text']),
                       'commit_hash': details.get('commit_hash'),
                       'evidence_refs': report['evidence_index'][eid]['sources']})
    return {'schema': 'daily-review.events', 'version': 1, 'date': date, 'timezone': timezone,
            'privacy': 'private-local-only; contains paths and excerpts; review before any sharing',
            'events': events, 'projects': [{'project_id': p['project_id'], 'name': p['name']} for p in report['projects']]}
