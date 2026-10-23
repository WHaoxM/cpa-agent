"""Thin list/read adapter over the exploration domain, with existing cursors."""
import re
from .career_exploration import ExplorationService, fingerprint, decoded

URI = re.compile(r'^career://me/explorations/(exp_[a-f0-9]{24})(?:/rounds/(round_[a-f0-9]{24}))?$')


def list_exploration_resources(mysql, *, student_id):
    return [{'uri': 'career://me/explorations/' + row['exploration_id'],
             'title': '我的职业方向探索', 'status': 'user_submitted',
             'sections': ['overview', 'facts', 'directions', 'rounds']}
            for row in ExplorationService(mysql).list(student_id)]


def read_exploration_resource(mysql, *, student_id, uri, section='overview', offset=0, limit=3):
    match = URI.fullmatch(uri or '')
    if not match or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError('探索资料或分页无效')
    service = ExplorationService(mysql)
    record = service.get(match[1], student_id)
    rounds = record['rounds']
    if match[2]:
        selected = service.round_for_owner(match[2], student_id)
        if selected['exploration_id'] != match[1]:
            raise LookupError('轮次不属于这次探索')
        rounds = [r for r in rounds if r['round_id'] == match[2]]
        record['sources'] = decoded(selected['sources_json'])
        record['source_status'] = rounds[0]['source_status']
        record['draft'] = {'compared_roles': [c['role_name'] for c in record['sources']['candidates']]}
    versions = [service.evidence_version(service._plan(service.round_for_owner(r['round_id'], student_id), student_id), student_id)
                for r in rounds if r['plan']]
    base = {'success': True, 'uri': uri, 'title': '我的职业方向探索',
            'version': fingerprint([record, versions]), 'status': 'user_submitted',
            'source_note': record['sources']['boundary'], 'section': section}
    if section == 'overview':
        if offset:
            raise ValueError('概览不支持偏移')
        latest = rounds[-1] if rounds else None
        return {**base, 'coverage': 'overview_only', 'data': {
            'interest': record['draft'].get('interest'),
            'compared_roles': record['draft'].get('compared_roles'),
            'latest_selection': latest['selection'] if latest else None,
            'source_status': record['source_status'],
            'plan_uri': 'career://me/action-plans/' + latest['plan']['plan_id'] if latest and latest['plan'] else None,
            'boundary': '只能辅助探索；最终决定由学生本人提交，不是已核验的能力或市场结论'}}
    if section == 'facts':
        facts = record['sources']['facts']
        from .career_positioning import current_starting_sources
        reading = current_starting_sources(mysql, student_id, facts.get('resume_version'))
        items = [{'source_status': record['source_status'], 'source': 'confirmed_resume' if facts['resume_status'] == 'confirmed' else 'unavailable',
                  'skills': facts['skills'], 'projects': facts['projects'], 'experiences': facts.get('experiences', [])},
                 {'source': 'student_self_report', 'self_report': facts['self_report'],
                  'skill_exploration': facts['skill_exploration'], 'self_reflection': facts['self_reflection']},
                 {'source': 'portrait_and_resume_interpretation', 'reading': reading,
                  'boundary': '画像未知不等于零；简历语义解读是模型解释，不是新的个人事实。'}]
    elif section == 'directions':
        items = [c for c in record['sources']['candidates'] if c['role_name'] in record['draft']['compared_roles']]
    elif section == 'rounds':
        items = [{'ordinal': r['ordinal'], 'selection': r['selection'], 'source_status': r['source_status'],
                  'review': r['review'], 'student_decision': r['decision']} for r in rounds]
    else:
        raise ValueError('探索资料仅支持 overview/facts/directions/rounds')
    page = items[offset:offset + limit]
    end = offset + len(page)
    return {**base, 'data': page, 'coverage': {'total': len(items), 'offset': offset,
            'returned': len(page), 'complete': end >= len(items), 'has_more': end < len(items),
            'next_offset': end if end < len(items) else None}}
