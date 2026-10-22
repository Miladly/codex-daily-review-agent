"""Versioned review instructions and deterministic evidence selection."""
from functools import lru_cache
from importlib.resources import files
import json
import re
from .util import digest

SECTIONS = ('goals', 'progress', 'decisions', 'blockers', 'next_steps', 'continuity')
LABELS = {'goals': '目标与范围', 'progress': '今日进展与验证', 'decisions': '关键决定与依据',
          'blockers': '阻塞、偏差与待证问题', 'next_steps': '下一步与完成标准', 'continuity': '跨天延续'}
PLAN = re.compile(r'计划|建议|下一步|接下来|尚需|仍需|待办|准备|\b(?:plan(?:ned)?|next step|todo|intend|will)\b', re.I)
OUTCOME = re.compile(r'已完成|已交付|已生成|已提交|已保存|已修复|测试通过|完成了|\b(?:delivered|completed|implemented|fixed|saved|passed|finished)\b', re.I)
DECISION = re.compile(r'决定|选择|采用|改为|取消|取舍|\b(?:decided|decision|chose|chosen|switch(?:ed)?)\b', re.I)
PROBLEM = re.compile(r'问题|限制|尚未|未完成|失败|待证|不确定|阻塞|\b(?:fail(?:ed|ure)?|blocked|unresolved|uncertain|limitation)\b', re.I)


@lru_cache(maxsize=1)
def profile():
    folder = files('daily_review').joinpath('skills', 'evidence-daily-review')
    instruction = folder.joinpath('SKILL.md').read_text(encoding='utf-8')
    rubric = folder.joinpath('references', 'quality.md').read_text(encoding='utf-8')
    return {'name': 'evidence-daily-review', 'version': '1.0.0',
            'sha256': digest(instruction+'\n'+rubric), 'instructions': instruction, 'rubric': rubric}


def method_metadata():
    data = profile()
    return {key: data[key] for key in ('name', 'version', 'sha256')}


def is_plan(event):
    return event['kind'] == 'intent' or bool(PLAN.search(event['text']) and not OUTCOME.search(event['text']))


def event_score(event):
    text = event['text']
    details = json.loads(event['details'])
    score = {'intent': 5, 'statement': 3, 'file_change': 4, 'command': 1,
             'command_error': 6, 'tool_error': 6, 'file_change_error': 6}.get(event['kind'], 1)
    if details.get('commit_hash'):
        score += 10
    if OUTCOME.search(text):
        score += 5
    if DECISION.search(text):
        score += 4
    if PROBLEM.search(text):
        score += 2
    if event['kind'] == 'command' and re.search(r'pytest|unittest|tests? passed|\bOK\b|通过|验证', text, re.I):
        score += 3
    return score


def choose(events, limit):
    """Prefer useful outcomes across the whole timeline, with a repeated-noise cap."""
    if limit <= 0:
        return []
    ranked = sorted(enumerate(events), key=lambda row: (event_score(row[1]), row[0]), reverse=True)
    selected, families = [], {}
    for index, event in ranked:
        family = (event['kind'], re.sub(r'\d+', '#', event['text'][:180]))
        if families.get(family, 0) >= 2:
            continue
        families[family] = families.get(family, 0)+1
        selected.append((index, event))
        if len(selected) >= limit:
            break
    return [event for _, event in sorted(selected)]
