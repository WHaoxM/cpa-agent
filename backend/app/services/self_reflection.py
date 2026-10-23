"""One bounded self-report workflow; no scoring, model or career inference."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import uuid

from sqlalchemy.exc import IntegrityError

PACK_PATH = Path(__file__).resolve().parents[2] / 'config' / 'self_reflection_questionnaire.json'


class ReflectionError(ValueError):
    def __init__(self, message, status=400, reason='invalid_reflection'):
        super().__init__(message)
        self.status, self.reason = status, reason


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def questionnaire(direction_id=None):
    pack = json.loads(PACK_PATH.read_text(encoding='utf-8'))
    version = 'reflection-v1-' + _digest(pack)[:16]
    directions = [{k: d[k] for k in ('id', 'label', 'group')} for d in pack['directions']]
    if direction_id is None:
        return {'version': version, 'directions': directions}
    direction = next((d for d in pack['directions'] if d['id'] == direction_id), None)
    if not direction:
        raise ReflectionError('未找到这个自评方向，请重新选择', 404, 'direction_unavailable')
    return {'version': version, 'direction': {k: direction[k] for k in ('id', 'label', 'group')},
            'questions': deepcopy(direction['questions']), 'options': deepcopy(pack['options'])}


def _request(body):
    allowed = {'assessment_kind', 'submission_id', 'direction_id', 'questionnaire_version', 'answers', 'student_id', 'user_id'}
    if set(body) - allowed:
        raise ReflectionError('只需提交方向、题包与选项引用，不接受题目文案或计算结果')
    sid, direction, version, answers = (body.get(k) for k in ('submission_id', 'direction_id', 'questionnaire_version', 'answers'))
    if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', sid):
        raise ReflectionError('提交标识无效，请重新读取后再试')
    if not isinstance(direction, str) or not direction or len(direction) > 64 or not isinstance(version, str) or not version or len(version) > 80:
        raise ReflectionError('方向与题包版本必填')
    if not isinstance(answers, dict) or not 1 <= len(answers) <= 100 or any(
        not isinstance(k, str) or len(k) > 128 or not isinstance(v, str) or len(v) > 64 for k, v in answers.items()
    ):
        raise ReflectionError('请为每个问题明确选择一个选项，不确定时可选择暂时无法判断')
    return sid, {'direction_id': direction, 'questionnaire_version': version, 'answers': answers}


def make_snapshot(request_data):
    pack = questionnaire(request_data['direction_id'])
    if pack['version'] != request_data['questionnaire_version']:
        raise ReflectionError('自评题目已更新，请重新读取并核对回答后提交', 409, 'questionnaire_changed')
    answers = request_data['answers']
    question_ids = {q['id'] for q in pack['questions']}
    if set(answers) != question_ids or any(choice not in {o['id'] for o in pack['options']} for choice in answers.values()):
        raise ReflectionError('问题或选项不属于当前题包，请重新读取并核对')
    return {'schema_version': 1, 'direction': pack['direction'], 'questionnaire_version': pack['version'],
            'questions': pack['questions'], 'options': pack['options'], 'answers': deepcopy(answers),
            'request_sha256': _digest(request_data)}


def project_record(row):
    snapshot = row.get('reflection_snapshot')
    snapshot = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
    # Validate the saved shape/hash, never reconstruct history with today's pack.
    try:
        valid = (
            isinstance(snapshot, dict) and snapshot['schema_version'] == 1
            and all(isinstance(snapshot['direction'][k], str) for k in ('id', 'label', 'group'))
            and isinstance(snapshot['questions'], list) and bool(snapshot['questions'])
            and len({q['id'] for q in snapshot['questions']}) == len(snapshot['questions'])
            and set(snapshot['answers']) == {q['id'] for q in snapshot['questions']}
            and all(isinstance(q['label'], str) for q in snapshot['questions'])
            and all(isinstance(o['label'], str) and o['group'] in ('not_started','concept','practice','unknown') for o in snapshot['options'])
            and all(a in {o['id'] for o in snapshot['options']} for a in snapshot['answers'].values())
            and _digest({'direction_id': snapshot['direction']['id'], 'questionnaire_version': snapshot['questionnaire_version'],
                         'answers': snapshot['answers']}) == snapshot['request_sha256'])
        if not valid:
            raise ValueError('Invalid snapshot')
    except (ValueError, KeyError, TypeError):
        raise ReflectionError('保存的自评记录不完整，暂时无法完整回看，请重试读取', 409, 'snapshot_unavailable')
    return {'success': True, 'status': 'saved', 'assessment_kind': 'self_reflection',
            'data': {'assessment_id': row['assessment_id'], 'submission_id': row['submission_id'],
                     'created_at': row['created_at'], 'reflection': snapshot}}


def save_reflection(mysql, owner, body):
    sid, request_data = _request(body)
    digest = _digest(request_data)
    sql = ('SELECT assessment_id,student_id,submission_id,reflection_snapshot,created_at '
           'FROM student_self_assessment WHERE student_id=:owner AND submission_id=:submission LIMIT 1')
    params = {'owner': owner, 'submission': sid}
    rows = mysql.execute(sql, params)
    if not rows:
        snapshot = make_snapshot(request_data)
        try:
            mysql.execute_write(
                'INSERT INTO student_self_assessment '
                '(assessment_id,student_id,assessed_skills,assessed_abilities,weak_skills,recommended_roles,submission_id,reflection_snapshot) '
                "VALUES (:id,:owner,'[]',NULL,'[]','[]',:submission,:snapshot)",
                {**params, 'id': 'asm_' + uuid.uuid4().hex, 'snapshot': json.dumps(snapshot, ensure_ascii=False)})
        except IntegrityError as error:
            # A concurrent identical submission may have won the UNIQUE fence.
            if getattr(error.orig, 'args', [None])[0] != 1062:
                raise
        rows = mysql.execute(sql, params)
    if not rows or str(rows[0].get('student_id')) != owner:
        raise ReflectionError('自评写入后暂时无法核验，请重新读取', 503, 'save_unconfirmed')
    result = project_record(rows[0])
    if result['data']['reflection']['request_sha256'] != digest:
        raise ReflectionError('这次提交的内容已变化，请核对后作为新一次自评提交', 409, 'submission_conflict')
    return result
