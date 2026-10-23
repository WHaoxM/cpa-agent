"""Owner-bound business handoffs on existing conversations; no model or runner."""
from __future__ import annotations
import hashlib
import json
import re
from sqlalchemy import text


class TaskContextConflict(ValueError):
    pass


def decode(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class TaskContextStore:
    def __init__(self, mysql):
        self.mysql = mysql

    def _row(self, conn, session_id, owner, lock=False):
        row = conn.execute(text('SELECT session_id,student_id,task_context_json,task_context_revision FROM conversation WHERE session_id=:session' + (' FOR UPDATE' if lock else '')),
                           {'session': session_id}).mappings().first()
        if not row or row['student_id'] != owner:
            raise PermissionError('任务不存在或不属于当前账号')
        return row

    def read(self, session_id, owner, incarnation=None):
        with self.mysql.engine.connect() as conn:
            row = self._row(conn, session_id, owner)
            context = decode(row['task_context_json'])
            if incarnation is not None and context.get('incarnation') not in (None, incarnation):
                raise TaskContextConflict('原任务会话已失效，请新建任务；没有恢复旧权限')
            result = {'revision': row['task_context_revision'], **context}
            result.pop('incarnation', None)
            result.pop('handoffs', None)
            result.pop('pending', None)
            active = context.get('active_run_id')
            if active:
                run = conn.execute(text('SELECT status FROM agent_run WHERE run_id=:run AND student_id=:owner'), {'run': active, 'owner': owner}).mappings().first()
                result['active_run_status'] = run['status'] if run else 'unavailable'
            from .agent_run_store import AgentRunRepository
            turns = conn.execute(text('SELECT r.*,d.last_error_code FROM agent_run r LEFT JOIN agent_run_dispatch d ON d.run_id=r.run_id WHERE r.session_id=:session AND r.student_id=:owner ORDER BY r.created_at DESC,r.run_id DESC LIMIT 30'),
                                 {'session': session_id, 'owner': owner}).mappings().all()
            result['turns'] = []
            for run in reversed(turns):
                snapshot = AgentRunRepository._verified_input_snapshot(run)
                AgentRunRepository._run_view(run)  # Verify final-result identity/hash before projection.
                if snapshot.get('capability_id') == 'student-task':
                    part = conn.execute(text('SELECT result_json,result_sha256 FROM agent_run_step WHERE run_id=:run AND status=\'completed\' ORDER BY step_index DESC LIMIT 1'), {'run':run['run_id']}).mappings().first()
                    evidence = []
                    if part:
                        value = decode(part['result_json'])
                        if digest(value) != part['result_sha256']:
                            raise ValueError('任务步骤内容校验不一致，未显示为有效依据')
                        evidence = (value.get('student_task') or {}).get('evidence_summary') or []
                    reply = decode(run['final_result_json']).get('reply')
                    if run['status'] == 'failed' and reply:
                        from .run_failure import failure_reply
                        reply = failure_reply(run.get('last_error_code'))
                    result['turns'].append({'run_id': run['run_id'], 'message': snapshot['message'],
                        'status': run['status'], 'reply': reply,
                        'evidence_summary': evidence,
                        'created_at': str(run['created_at'])})
            return result

    def resolve_refs(self, refs, owner):
        if not isinstance(refs, list) or not 1 <= len(refs) <= 8:
            raise ValueError('每次交接需要 1 至 8 项资料')
        from .resource_catalog import read_platform_resource
        resolved = []
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) - {'file_id', 'uri', 'task_id'} or len(ref) != 1:
                raise ValueError('只接受文件或业务资料引用，不接受客户端摘要或 owner')
            if 'file_id' in ref:
                from .media.file_store import get_agent_file_service
                files = get_agent_file_service()
                file = files.get(ref['file_id'])
                if not file or file.get('student_id') != owner:
                    raise PermissionError('附件不可用')
                source = files._load_file(ref['file_id'])
                if not source or source.get('student_id') != owner or not source.get('content_sha256'):
                    raise ValueError('附件版本不可核对，请重新上传')
                resolved.append({'file_id': file['file_id'], 'version': source['content_sha256'],
                                 'name': file['filename'], 'status': file['status']})
            elif 'task_id' in ref:
                row = self.mysql.fetch_one('SELECT student_id,params_json,status FROM pipeline_task WHERE task_id=:id', {'id': ref['task_id']})
                params = decode(row.get('params_json')) if row else {}
                if not row or (row.get('student_id') or params.get('student_id')) != owner:
                    raise PermissionError('简历任务不可用')
                resolved.append({'task_id': ref['task_id'], 'version': digest(params), 'name': '这次简历核对', 'status': row['status']})
            else:
                uri = ref['uri']
                if not isinstance(uri, str) or len(uri) > 160 or not uri.startswith('career://me/'):
                    raise ValueError('业务资料引用无效')
                data = read_platform_resource(self.mysql, student_id=owner, uri=uri)
                if data.get('status') in {'denied', 'unavailable', 'not_found'} or data.get('success') is False:
                    raise PermissionError('资料当前不可读取')
                resolved.append({'uri': uri, 'version': data.get('version') or digest(data),
                                 'name': '这次方向探索' if '/explorations/' in uri else '这份行动计划' if '/action-plans/' in uri else '当前简历' if uri.endswith('/resume') else '这份业务资料',
                                 'status': data.get('status', 'available')})
        return resolved

    def handoff(self, session, owner, payload, key):
        composer = payload.get('purpose') == 'composer'
        if (set(payload) != ({'refs', 'expected_revision', 'purpose'} if composer else {'refs', 'expected_revision'})
                or type(payload['expected_revision']) is not int):
            raise ValueError('交接仅接受 refs、整数 expected_revision 与可选 composer purpose')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', key):
            raise ValueError('交接需要有效的 Idempotency-Key')
        # Validate before acquiring a write lock; no partial association on denial.
        if composer and (not isinstance(payload['refs'], list) or len(payload['refs']) > 8
                or any(not isinstance(r, dict) or set(r) != {'file_id'} for r in payload['refs'])):
            raise ValueError('待发送附件只接受最多 8 个文件引用')
        refs = [] if composer and payload['refs'] == [] else self.resolve_refs(payload['refs'], owner)
        signature = digest(payload)
        with self.mysql.engine.begin() as conn:
            row = self._row(conn, session.session_id, owner, True)
            context = decode(row['task_context_json'])
            if context.get('incarnation') not in (None, session.incarnation_id):
                raise TaskContextConflict('会话已更换，不能继承旧交接')
            history = context.setdefault('handoffs', {})
            prior = history.get(key)
            if prior:
                if prior != signature:
                    raise TaskContextConflict('同一交接标识不能用于不同资料')
            else:
                self._idle(conn, context, owner)
                if row['task_context_revision'] != payload['expected_revision']:
                    raise TaskContextConflict('任务已在另一处更新，请重新核对')
                if len(history) >= 100:
                    raise TaskContextConflict('当前任务交接记录已满，请新建独立任务')
                history[key] = signature
                context['incarnation'] = session.incarnation_id
                if composer:
                    # Durable unsent selection is distinct from the last analysis focus.
                    context['composer_files'] = refs
                else:
                    old = [r for r in context.get('refs', []) if r.get('file_id')]
                    keyed = {r.get('file_id') or r.get('uri') or r.get('task_id'): r for r in old + refs}
                    context['refs'] = list(keyed.values())
                    context['focus'] = refs
                self._save(conn, session.session_id, context)
        return self.read(session.session_id, owner, session.incarnation_id)

    def check_upload(self, session, owner, revision):
        """Preflight before saving bytes; CAS is repeated when associating the receipt."""
        with self.mysql.engine.begin() as conn:
            row = self._row(conn, session.session_id, owner, True)
            context = decode(row['task_context_json'])
            if context.get('incarnation') not in (None, session.incarnation_id):
                raise TaskContextConflict('原任务已失效，请重新打开任务')
            if type(revision) is not int or revision != row['task_context_revision']:
                raise TaskContextConflict('任务资料已更新，请重新读取后上传')
            self._idle(conn, context, owner)
            if len(context.get('composer_files', [])) >= 8:
                raise ValueError('每次最多选择 8 份附件，请先发送或移除部分附件')
        return context

    def attach_upload(self, session, owner, revision, file_id):
        context = self.check_upload(session, owner, revision)
        refs = [{'file_id': r['file_id']} for r in context.get('composer_files', [])]
        if not any(r['file_id'] == file_id for r in refs):
            refs.append({'file_id': file_id})
        return self.handoff(session, owner, {'refs': refs, 'expected_revision': revision,
            'purpose': 'composer'}, 'upload-' + file_id)

    def _idle(self, conn, context, owner):
        active = context.get('active_run_id')
        if active:
            row = conn.execute(text('SELECT status FROM agent_run WHERE run_id=:run AND student_id=:owner'), {'run': active, 'owner': owner}).mappings().first()
            if row and row['status'] not in {'completed', 'failed', 'cancelled'}:
                raise TaskContextConflict('本任务已有一次执行，请先核对进展')

    def _save(self, conn, session_id, context):
        conn.execute(text('UPDATE conversation SET task_context_json=:context,task_context_revision=task_context_revision+1 WHERE session_id=:session'),
                     {'context': json.dumps(context, ensure_ascii=False), 'session': session_id})

    def bind_run(self, conn, session, owner, revision, run_id, message=''):
        row = self._row(conn, session.session_id, owner, True)
        context = decode(row['task_context_json'])
        if type(revision) is not int or row['task_context_revision'] != revision:
            raise TaskContextConflict('任务资料已更新，请重新读取后提交')
        if context.get('incarnation') not in (None, session.incarnation_id):
            raise TaskContextConflict('会话已失效')
        self._idle(conn, context, owner)
        selected = context.get('composer_files', [])
        if selected:
            fresh = self.resolve_refs([{'file_id': r['file_id']} for r in selected], owner)
            if (any(r['status'] != 'ready' for r in fresh)
                    or [(r['file_id'], r['version']) for r in fresh] != [(r['file_id'], r['version']) for r in selected]):
                raise TaskContextConflict('本次附件尚未就绪或版本已变化，请核对附件；没有改读旧简历')
            context['focus'] = fresh
            old = {r.get('file_id') or r.get('uri') or r.get('task_id'): r for r in context.get('refs', [])}
            old.update({r['file_id']: r for r in fresh})
            context['refs'] = list(old.values())
            context['composer_files'] = []
        context.update(incarnation=session.incarnation_id, active_run_id=run_id)
        context.pop('pending', None)
        self._save(conn, session.session_id, context)
        if message:
            conn.execute(text('UPDATE conversation SET title=COALESCE(NULLIF(title,\'\'),:title),updated_at=NOW() WHERE session_id=:session AND student_id=:owner'),
                         {'title':message.strip().replace('\n',' ')[:36], 'session':session.session_id,'owner':owner})

    def stage(self, session_id, owner, run_id, result):
        with self.mysql.engine.begin() as conn:
            row = self._row(conn, session_id, owner, True)
            context = decode(row['task_context_json'])
            if context.get('active_run_id') != run_id:
                raise TaskContextConflict('任务关联已改变，不能保存迟到结果')
            context['pending'] = result
            # Staging does not publish an answer or advance the public revision.
            conn.execute(text('UPDATE conversation SET task_context_json=:context WHERE session_id=:session'),
                         {'context': json.dumps(context, ensure_ascii=False), 'session': session_id})

    def submit_resume(self, session, owner, file_id, revision):
        from pathlib import Path
        from .media.file_store import get_agent_file_service
        from ..pipeline_resume_jobs import persist_resume_intent, enqueue_resume_intent
        if not isinstance(file_id, str) or not re.fullmatch(r'file_[A-Za-z0-9]+', file_id):
            raise ValueError('简历文件引用无效')
        files = get_agent_file_service()
        meta = files.get(file_id)
        if not meta or meta.get('student_id') != owner:
            raise PermissionError('简历文件不可用')
        if meta.get('status') != 'ready' or Path(meta['filename']).suffix.lower() not in {'.pdf', '.docx', '.txt'}:
            raise ValueError('需要已解析就绪的 PDF、DOCX 或文本简历')
        # get() is the public projection, intentionally without storage paths.
        # Resolve original bytes only on the server, after and again with owner checks.
        source = files._load_file(file_id)
        if not source or source.get('student_id') != owner or source.get('file_id') != file_id:
            raise PermissionError('简历原文件不可用')
        source_path = source.get('storage_path')
        if not source_path or not Path(source_path).is_file():
            raise ValueError('简历原文件不可用，请重新上传')
        version = source.get('content_sha256')
        if not version:
            raise ValueError('简历原文件版本不可核对，请重新上传')
        task_id = 'task_' + digest({'owner': owner, 'session': session.session_id,
            'incarnation': session.incarnation_id, 'file': file_id, 'version': version})[:24]
        with self.mysql.engine.begin() as conn:
            row = self._row(conn, session.session_id, owner, True)
            context = decode(row['task_context_json'])
            if context.get('incarnation') != session.incarnation_id:
                raise TaskContextConflict('文件交接会话已经失效')
            if not any(r.get('file_id') == file_id for r in context.get('refs', [])):
                raise PermissionError('本文件没有交接到当前任务')
            prior = conn.execute(text('SELECT student_id,params_json FROM pipeline_task WHERE task_id=:id'), {'id': task_id}).mappings().first()
            if prior:
                if prior['student_id'] != owner or decode(prior['params_json']).get('file_path') != source_path:
                    raise TaskContextConflict('已有解析任务的来源不一致')
            else:
                self._idle(conn, context, owner)
                if type(revision) is not int or revision != row['task_context_revision']:
                    raise TaskContextConflict('任务已更新，请重新核对提交版本')
                persist_resume_intent(self.mysql, student_id=owner, file_path=source_path, task_id=task_id, connection=conn)
                context['resume_submission'] = {'file_id': file_id, 'task_id': task_id, 'version': version}
                self._save(conn, session.session_id, context)
        try:
            delivery = enqueue_resume_intent(self.mysql, task_id)
        except Exception:
            delivery = 'retry_pending'
        return {'task_id': task_id, 'delivery': delivery, 'context': self.read(session.session_id, owner, session.incarnation_id)}

    def settle(self, conn, session_id, owner, run_id):
        row = self._row(conn, session_id, owner, True)
        context = decode(row['task_context_json'])
        if context.get('active_run_id') != run_id or not context.get('pending'):
            raise TaskContextConflict('缺少已验证的任务结果')
        context['latest_result'] = context.pop('pending')
        if 'document_reads' in context['latest_result']:
            context['document_reads'] = context['latest_result']['document_reads']
        context['last_run_id'] = run_id
        context['active_run_id'] = None
        self._save(conn, session_id, context)
