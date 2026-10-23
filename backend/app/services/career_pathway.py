"""Public role facts in MySQL, with separately rebuildable graph/vector views.

Imports are trusted CLI actions; HTTP is read-only. No account, resume, learning
progress or match-score writes occur here. Unknown qualification is not a pass.
"""
import hashlib
import json
import math
import re
from copy import deepcopy
from contextlib import contextmanager
from urllib.parse import urlparse
from sqlalchemy import text

SCHEMA = 'career-pathway/v1'
ID = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')
BUSINESS_FIELDS = {'schema_version','role_id','role_name','revision','jobs','salary_references','skills','courses','prerequisites'}


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def content_digest(payload):
    return hashlib.sha256(canonical_json({k: v for k, v in payload.items()
        if k not in {'content_sha256', 'projection_status', 'published_at'}}).encode('utf-8')).hexdigest()


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _url(value):
    parsed = urlparse(value if isinstance(value, str) else '')
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Public evidence requires an HTTPS URL')


def _evidence(value):
    if not isinstance(value, dict) or set(value) != {'url','note'} or not isinstance(value['note'], str) or not value['note'].strip():
        raise ValueError('Evidence note is required')
    _url(value.get('url'))


def validate_payload(p):
    if set(p) - BUSINESS_FIELDS - {'content_sha256','projection_status','published_at'} or BUSINESS_FIELDS - set(p):
        raise ValueError('Unexpected or missing business fields')
    if p.get('schema_version') != SCHEMA:
        raise ValueError('Unsupported schema')
    for key in ('role_id', 'revision'):
        if not isinstance(p.get(key), str) or not ID.fullmatch(p[key]):
            raise ValueError('Invalid ' + key)
    if not p.get('role_name'):
        raise ValueError('role_name required')
    for key in ('jobs', 'salary_references', 'skills', 'courses', 'prerequisites'):
        if not isinstance(p.get(key), list) or len(p[key]) > 100:
            raise ValueError('Invalid bounded list: ' + key)
    if not p['jobs'] or not p['skills'] or not p['courses']:
        raise ValueError('Jobs, skills and courses required')
    ids = [s.get('skill_id') for s in p['skills']]
    if any(type(i) is not int or i <= 0 for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('Invalid or duplicate taxonomy identity')
    skills = set(ids)
    for s in p['skills']:
        if s.get('tier') not in {'foundation', 'application', 'engineering'} or not s.get('name'):
            raise ValueError('Invalid skill')
        if s.get('requirement_kind') != 'recommended':
            raise ValueError('Learning recommendations are not employer eligibility gates')
        _evidence(s.get('evidence'))
    courses = set()
    for c in p['courses']:
        if not ID.fullmatch(str(c.get('course_id', ''))) or c['course_id'] in courses:
            raise ValueError('Invalid or duplicate course identity')
        courses.add(c['course_id'])
        if not c.get('skill_ids') or not set(c['skill_ids']) <= skills:
            raise ValueError('Course references unknown role skill')
        if not c.get('chapter_ids') or any(type(i) is not int for i in c['chapter_ids']):
            raise ValueError('Real chapter identities required')
        _url(c.get('source_url'))
    edges, visited, active = set(), set(), set()
    graph = {i: [] for i in skills}
    for e in p['prerequisites']:
        a, b = e.get('from_skill_id'), e.get('to_skill_id')
        if a not in skills or b not in skills or a == b or (a, b) in edges:
            raise ValueError('Invalid prerequisite')
        edges.add((a, b)); graph[a].append(b)
        _evidence(e.get('evidence'))

    def visit(n):
        if n in active:
            raise ValueError('Prerequisite cycle')
        if n in visited:
            return
        active.add(n)
        for nxt in graph[n]:
            visit(nxt)
        active.remove(n); visited.add(n)
    for n in skills:
        visit(n)
    facts = set()
    for j in p['jobs']:
        if not ID.fullmatch(str(j.get('fact_id', ''))) or j['fact_id'] in facts:
            raise ValueError('Invalid job identity')
        facts.add(j['fact_id'])
        for key in ('title', 'employer', 'summary', 'source_fetched_at'):
            if not j.get(key):
                raise ValueError('Missing job fact: ' + key)
        _url(j.get('source_uri'))
        if not re.fullmatch(r'[a-f0-9]{64}', j.get('source_sha256', '')):
            raise ValueError('Source hash required')
        if j.get('usage') != 'reference_only' or j.get('scoring_ready') is not False:
            raise ValueError('Facts do not authorize scoring')
        if j.get('vacancy_validity') not in {'unknown', 'open', 'closed'}:
            raise ValueError('Invalid vacancy state')
        if not isinstance(j.get('locations'), list) or not isinstance(j.get('requirements'), list):
            raise ValueError('Invalid job facts')
        for clause in j['requirements']:
            if clause.get('kind') not in {'all_of', 'any_of', 'preferred', 'not_stated'}:
                raise ValueError('Invalid qualification clause')
            if not clause.get('label') or not isinstance(clause.get('values'), list):
                raise ValueError('Invalid clause values')
            if (clause['kind'] == 'not_stated') != (not clause['values']):
                raise ValueError('Unknown requirements cannot invent values')
        if j.get('salary_quote') is not None:
            raise ValueError('This importer accepts undisclosed quotes, not unevidenced salaries')
    references = set()
    for s in p['salary_references']:
        if not ID.fullmatch(str(s.get('reference_id', ''))) or s['reference_id'] in references:
            raise ValueError('Invalid salary identity')
        references.add(s['reference_id'])
        low, high = s.get('lower'), s.get('upper')
        if (type(low) not in (int, float) or type(high) not in (int, float)
                or not math.isfinite(low) or not math.isfinite(high) or not 0 <= low <= high):
            raise ValueError('Invalid salary range')
        if s.get('kind') != 'estimate' or s.get('currency') != 'CNY' or s.get('period') != 'month':
            raise ValueError('Explicit estimate, CNY/month required')
        for key in ('city', 'experience_level', 'method', 'as_of', 'uncertainty', 'disclaimer'):
            if not s.get(key):
                raise ValueError('Missing estimate context: ' + key)
        if not s.get('basis'):
            raise ValueError('Estimate basis required')
        for basis in s['basis']:
            if not isinstance(basis, dict) or set(basis) != {'title','url','note'} or not basis['title']:
                raise ValueError('Estimate basis title, URL and note required')
            _evidence({'url': basis['url'], 'note': basis['note']})
    canonical_json(p)
    return p


def _insert(conn, table, row):
    # Callers supply only fixed internal table/column names, never HTTP input.
    cols = list(row)
    conn.execute(text(f'INSERT INTO {table} (' + ','.join('`'+k+'`' for k in cols)
        + ') VALUES (' + ','.join(':'+k for k in cols) + ')'), row)


@contextmanager
def _import_transaction(mysql, apply):
    with mysql.engine.connect() as conn:
        locked = False
        if apply:
            locked = conn.execute(text("SELECT GET_LOCK('cpa.career_pathway.import',10)")).scalar() == 1
            conn.commit()
            if not locked:
                raise RuntimeError('Pathway importer busy')
        try:
            with conn.begin():
                yield conn
        finally:
            # Release only AFTER the business transaction commits or rolls back.
            if locked:
                conn.execute(text("SELECT RELEASE_LOCK('cpa.career_pathway.import')"))
                conn.commit()


def apply_manifest(mysql, manifest, *, apply=False):
    p = deepcopy(manifest)
    additions = p.pop('taxonomy_additions', [])
    with _import_transaction(mysql, apply) as conn:
        names = {r['skill_name']: int(r['id']) for r in conn.execute(text(
            'SELECT id,skill_name FROM skill_taxonomy')).mappings()}
        absent = [s for s in additions if s['name'] not in names]
        used_names = {s['skill_name'] for s in p['skills']}
        if any(s['name'] not in used_names for s in additions):
            raise ValueError('Taxonomy additions must be used by this role revision')
        existing_revision = conn.execute(text('SELECT content_sha256 FROM career_pathway_revision '
            'WHERE revision=:revision'), p).mappings().first()
        if existing_revision and absent:
            raise ValueError('Existing revision cannot append new taxonomy identities')
        if absent and not apply:
            return dict(status='preview', role_id=p['role_id'], revision=p['revision'],
                taxonomy_to_append=absent, jobs=len(p['jobs']), courses=len(p['courses']), writes_performed=False)
        for s in absent:
            result = conn.execute(text('INSERT INTO skill_taxonomy '
                '(skill_name,category,tier,difficulty,heat_score,dimension,description) '
                "VALUES (:name,'后端',2,'中',NULL,'D2',:description)"), s)
            names[s['name']] = int(result.lastrowid)
        for s in p['skills']:
            s['skill_id'] = names[s.pop('skill_name')]
        for c in p['courses']:
            c['skill_ids'] = [names[n] for n in c.pop('skill_names')]
            row = conn.execute(text('SELECT title FROM course WHERE course_id=:id'),
                {'id': c['course_id']}).mappings().first()
            if not row:
                raise ValueError('Missing course: ' + c['course_id'])
            chapters = list(conn.execute(text('SELECT id,video_url FROM chapter '
                'WHERE course_id=:id ORDER BY sequence,id'), {'id': c['course_id']}).mappings())
            c.update(name=row['title'], chapter_ids=[int(r['id']) for r in chapters])
            if not chapters or c['source_url'] not in [r['video_url'] for r in chapters]:
                raise ValueError('Source URL does not match stored chapter')
        for e in p['prerequisites']:
            e['from_skill_id'] = names[e.pop('from_skill_name')]
            e['to_skill_id'] = names[e.pop('to_skill_name')]
        validate_payload(p)
        digest = content_digest(p)
        result = dict(status='preview', revision=p['revision'], content_sha256=digest,
                      payload=p, writes_performed=False)
        if not apply:
            return result
        old = conn.execute(text('SELECT content_sha256 FROM career_pathway_revision '
            'WHERE revision=:revision FOR UPDATE'), p).mappings().first()
        if old:
            if old['content_sha256'] != digest:
                raise ValueError('Immutable revision collision')
            return {**result, 'status': 'unchanged'}
        rev = p['revision']
        conn.execute(text("UPDATE career_pathway_revision SET status='retired' "
            "WHERE role_id=:role_id AND status='active'"), p)
        _insert(conn, 'career_pathway_revision', dict(revision=rev, role_id=p['role_id'],
            role_name=p['role_name'], schema_version=SCHEMA, content_sha256=digest,
            snapshot_json=canonical_json(p), status='active', curation_method='operator_cli_public_curation'))
        for j in p['jobs']:
            _insert(conn, 'career_job_fact', {k: j[k] for k in
                ('fact_id','employer','title','source_uri','source_sha256','source_fetched_at','vacancy_validity','usage')} |
                dict(revision=rev, locations_json=canonical_json(j['locations']),
                     requirements_json=canonical_json(j['requirements']), salary_quote_json=None, fact_json=canonical_json(j)))
        for s in p['salary_references']:
            _insert(conn, 'career_salary_reference', {k: s[k] for k in
                ('reference_id','kind','city','experience_level','currency','period','as_of','method','uncertainty')} |
                dict(revision=rev, lower_amount=s['lower'], upper_amount=s['upper'],
                     basis_json=canonical_json(s['basis']), reference_json=canonical_json(s)))
        for s in p['skills']:
            _insert(conn, 'career_role_skill', dict(revision=rev, skill_id=s['skill_id'], tier=s['tier'],
                requirement_kind=s['requirement_kind'], evidence_json=canonical_json(s['evidence'])))
        for c in p['courses']:
            for sid in c['skill_ids']:
                _insert(conn, 'career_skill_course', dict(revision=rev, skill_id=sid, course_id=c['course_id'],
                    evidence_json=canonical_json({'url': c['source_url'], 'note': c['name']})))
        for e in p['prerequisites']:
            _insert(conn, 'career_skill_prerequisite', dict(revision=rev, from_skill_id=e['from_skill_id'],
                to_skill_id=e['to_skill_id'], evidence_json=canonical_json(e['evidence'])))
        for target in ('graph', 'vector'):
            _insert(conn, 'career_pathway_projection', dict(revision=rev, target=target,
                status='pending', content_sha256=digest, details_json='{}'))
        return {**result, 'status': 'applied', 'writes_performed': True}


def read_pathway(mysql, role_id):
    row = mysql.execute_single('SELECT * FROM career_pathway_revision '
        "WHERE role_id=:role_id AND status='active' ORDER BY created_at DESC,revision DESC LIMIT 1", {'role_id': role_id})
    if row is None:
        return None
    p = deepcopy(_json(row['snapshot_json']))
    if content_digest(p) != row['content_sha256']:
        raise RuntimeError('Pathway integrity failed')
    if (p['role_id'] != role_id or any(p[k] != row[k] for k in ('role_id','revision','schema_version'))):
        raise RuntimeError('Pathway identity mismatch')
    validate_payload(p)
    states = mysql.execute('SELECT target,status,content_sha256,details_json FROM career_pathway_projection '
        'WHERE revision=:revision', {'revision': row['revision']})
    p.update(content_sha256=row['content_sha256'], published_at=str(row['created_at']),
        projection_status={s['target']: dict(status=s['status'] if s['content_sha256'] == row['content_sha256'] else 'stale',
            details=_json(s['details_json'])) for s in states})
    return p


def record_projection(mysql, payload, target, result):
    if target not in {'graph', 'vector'} or result.get('status') not in {'ready','partial','failed','pending','unavailable'}:
        raise ValueError('Invalid projection result')
    if result['status'] == 'ready' and any(result.get(k) != payload[k] for k in ('role_id','revision','content_sha256')):
        raise ValueError('Projection identity mismatch')
    mysql.execute_write('UPDATE career_pathway_projection SET status=:status,details_json=:details,'
        'content_sha256=:digest,updated_at=CURRENT_TIMESTAMP WHERE revision=:revision AND target=:target AND content_sha256=:digest',
        dict(revision=payload['revision'], target=target, status=result['status'],
             details=canonical_json(result), digest=payload['content_sha256']))
