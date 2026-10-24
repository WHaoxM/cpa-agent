"""Three bounded business adapters inside the existing QA run, not another Agent OS."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4
from sqlalchemy import text
from .task_context import TaskContextStore, TaskContextConflict, decode, digest


def release_contract():
    return {'version': 1, 'module': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'task_context': hashlib.sha256(Path(__file__).with_name('task_context.py').read_bytes()).hexdigest(),
            'resume_message_transport': {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                         for name in ('task_packet.py','scoped_invoke.py')},
            'delegation_helpers': hashlib.sha256((Path(__file__).parent.parent/'career_exploration.py').read_bytes()).hexdigest(),
            'evidence_projection': hashlib.sha256(Path(__file__).with_name('task_evidence_projection.py').read_bytes()).hexdigest()}


def _lead_sources(mysql, owner):
    """Only routing metadata; do not run another domain's personal analysis."""
    from ..career_exploration import project_candidate
    row = mysql.fetch_one('SELECT profile_json FROM student_profile WHERE student_id=:owner', {'owner':owner})
    confirmation = (decode(row['profile_json']).get('_resume_confirmation') or {}) if row else {}
    rows = mysql.fetch_all('SELECT role_name,category,core_skills FROM career_insights ORDER BY role_name LIMIT 61')
    return {'facts': {'resume_version':confirmation.get('task_id'),
                       'resume_status':'confirmed' if confirmation.get('task_id') else 'missing'},
            'candidates':[candidate for row in rows[:60] if (candidate := project_candidate(row, []))]}


def _exploration_release():
    from ..career_exploration_analysis import release_contract as current
    return current()


def _assert_exploration_release(expected):
    if _exploration_release() != expected:
        raise RuntimeError('runtime_release_changed_during_delegation')


def _bind_analysis(mysql, session_id, owner, run_id, exploration, message, round_id):
    from ..career_exploration_analysis import AnalysisService
    service = AnalysisService(mysql)
    eid = exploration['exploration_id']
    binding = service.binding(eid, owner, round_id)
    with mysql.engine.begin() as conn:
        row = service.domain._owner(eid, owner, conn, True)
        if row['session_id'] != session_id:
            raise TaskContextConflict('探索属于另一任务，不能合并其对话')
        prior = conn.execute(text('SELECT run_id FROM career_exploration_analysis_turn WHERE run_id=:run'), {'run': run_id}).first()
        if prior:
            return
        active = conn.execute(text("SELECT t.run_id FROM career_exploration_analysis_turn t JOIN agent_run r ON r.run_id=t.run_id WHERE t.exploration_id=:eid AND r.status NOT IN ('completed','failed','cancelled')"), {'eid': eid}).first()
        if active:
            raise TaskContextConflict('探索已有分析正在执行')
        ordinal = conn.execute(text('SELECT COALESCE(MAX(ordinal),0)+1 FROM career_exploration_analysis_turn WHERE exploration_id=:eid'), {'eid': eid}).scalar_one()
        conn.execute(text('''INSERT INTO career_exploration_analysis_turn
          (turn_id,exploration_id,run_id,request_key,request_hash,ordinal,round_id,message,context_json)
          VALUES (:turn,:eid,:run,:key,:hash,:ordinal,:round,:message,:context)'''),
          {'turn': 'turn_'+uuid4().hex[:24], 'eid': eid, 'run': run_id, 'key': run_id,
           'hash': digest({'message': message, 'round': round_id}), 'ordinal': ordinal,
           'round': round_id, 'message': message, 'context': json.dumps(binding, ensure_ascii=False)})
        conn.execute(text('UPDATE career_exploration SET revision=revision+1 WHERE exploration_id=:eid'), {'eid': eid})


def run_step(*, mysql, snapshot, run_id, owner, session_id, repository, claim_token, invoke, packet, board):
    from .experts_lead import task_lead_decide
    from ..career_exploration import ExplorationService
    from .modes import InteractionMode
    store = TaskContextStore(mysql)
    context = store.read(session_id, owner)
    if context.get('active_run_id') != run_id or context['revision'] != snapshot['task_context_version'] + 1:
        raise TaskContextConflict('当前任务已改变，未读取旧任务资料')
    focus = context.get('focus') or []
    files = [r for r in focus if r.get('file_id')]
    sources = _lead_sources(mysql, owner)
    catalogue = [c['role_name'] for c in sources['candidates']]
    previous = context.get('latest_result') or {}
    # Reading checkpoints belong to the task, not to whichever business agent
    # happened to answer last. Older tasks still recover their last receipt.
    previous = {**previous, 'document_reads': context.get('document_reads') or previous.get('document_reads') or []}
    recent_discussion = _recent(mysql, session_id, owner)
    decision = task_lead_decide(snapshot['message'], {'catalogue': catalogue,
        'materials': [{'name': r['name'], 'status': r['status']} for r in focus],
        'previous_agent': previous.get('agent'), 'current_question': previous.get('question'),
        'compared_roles': previous.get('compared_roles', []),
        'has_confirmed_resume': bool(sources.get('facts', {}).get('resume_version')),
        'has_plan': bool(previous.get('round_id')),
        'has_unread_material': any(not r.get('complete') for r in previous['document_reads']) or bool(previous.get('continuation')),
        'recent_discussion': recent_discussion})
    result = dict(decision)
    repository.record_business_delegation(run_id, student_id=owner, claim_token=claim_token, agent=decision['agent'])
    if decision['agent'] == 'clarify':
        result.update(reply=decision['reply'] or '你想先看简历，还是继续比较职业方向？', state='needs_input')
        output = {'reply': result['reply'], 'tool_calls': [], 'skill_versions': [], 'blackboard_updates': {}}
    elif decision['agent'] == 'conversation_recall':
        history_messages = recent_discussion.get('messages') or []
        if not history_messages:
            result.update(
                reply='当前可用的本会话记录里没有这条信息。请把原话再发一次，或补充你希望我回忆的内容。',
                state='needs_input',
                history_coverage=recent_discussion.get('coverage', 'unavailable'),
            )
            output = {'reply': result['reply'], 'tool_calls': [], 'skill_versions': [],
                      'blackboard_updates': {}}
        else:
            from .modes import InteractionMode
            from .task_packet import build_standard_packet
            packet = build_standard_packet(
                'qa',
                goal=('仅依据服务端提供的同一会话近期对话回答本轮回忆问题。'
                      '按角色区分用户原话与助手旧答；用户原话可作为“你之前提到”的来源，'
                      '不代表客观核验；后来的用户纠正优先。若记录缺少所需信息或被截断，先澄清。'),
                user_utterance=snapshot['message'],
                read_keys=[], write_keys=[], capability_id='student-task-conversation-recall',
            )
            output = invoke(
                packet, board, session_id, allow_tools=False,
                interaction_mode=InteractionMode.ASK, student_id=owner,
                allow_artifact_commit=False,
                ask_context_messages=history_messages,
                ask_context_append_current=True,
                ask_context_appendix=_conversation_recall_appendix(history_messages),
            )
            reply = str(output.get('reply') or '').strip()
            if output.get('incomplete'):
                raise RuntimeError(str(output.get('stop_reason') or 'conversation_recall_incomplete'))
            if not reply:
                raise ValueError('conversation_recall_answer_empty')
            result.update(
                reply=reply,
                state='answered_from_conversation',
                history_coverage=recent_discussion.get('coverage', 'recent_discussion_only'),
                history_message_count=len(history_messages),
                resource_access='conversation_only',
            )
    elif decision['agent'] == 'resume_preparation':
        # A file reference never grants cross-owner or cross-session access.
        ids = [r['file_id'] for r in files] or snapshot.get('attachment_ids', [])
        if not ids:
            ids = [r['file_id'] for r in context.get('refs', []) if r.get('file_id')][-1:]
        if not ids and sources.get('facts', {}).get('resume_status') == 'confirmed':
            from .mode_runtime import ModeRuntime
            repository.record_resource_read(run_id, student_id=owner, claim_token=claim_token, label='本人经历与自述', phase='started')
            output = ModeRuntime()._chat_impl(snapshot['message'], session_id=session_id, student_id=owner,
                interaction_mode='ask', content_parts=[{'type':'resource_ref','uri':'career://me/resume'}], session_context={})
            repository.record_resource_read(run_id, student_id=owner, claim_token=claim_token, label='本人经历与自述', phase='completed')
            material = {'state':'partial' if output.get('incomplete') else 'discussing','files':[],
                        'evidence_summary':[{'name':'当前已确认简历','status':'partial' if output.get('incomplete') else 'read'}]}
        else:
            output, material = _resume_read(mysql, ids, owner, session_id, previous, snapshot['message'],
                                             run_id, repository, claim_token, invoke, board,
                                             read_action=decision.get('document_action', 'discuss'))
        result.update(material, reply=output['reply'])
    else:
        # Pin the selected domain before reading/executing it. A resume read
        # must not fail because an unused positioning module is being edited.
        delegation_release = _exploration_release()
        from ..career_exploration_analysis import run_analysis_step
        from .task_evidence_projection import exploration_invoke
        from .task_packet import build_standard_packet
        eid = previous.get('exploration_id')
        round_id = previous.get('round_id')
        for ref in focus:
            uri = ref.get('uri', '')
            if '/explorations/' in uri:
                pieces = uri.split('/explorations/')[1].split('/rounds/')
                eid, round_id = pieces[0], pieces[1] if len(pieces) > 1 else None
            elif '/action-plans/' in uri:
                linked = mysql.fetch_one('SELECT r.exploration_id,r.round_id FROM career_exploration_round r JOIN career_exploration e ON e.exploration_id=r.exploration_id WHERE r.plan_id=:plan AND e.student_id=:owner AND e.session_id=:session',
                    {'plan': uri.rsplit('/', 1)[-1], 'owner': owner, 'session': session_id})
                if not linked:
                    raise TaskContextConflict('这份计划不属于当前探索任务，请从原任务继续')
                eid, round_id = linked['exploration_id'], linked['round_id']
        domain = ExplorationService(mysql)
        exploration = domain.get(eid, owner) if eid else None
        roles = decision['compared_roles'] or (exploration or {}).get('draft', {}).get('compared_roles') or previous.get('compared_roles') or []
        if not roles:
            result.update(reply='先选一至两个你想比较的目录方向，我们再结合经历讨论。', state='needs_input')
            if eid:
                result['exploration_id'] = eid
            output = {'reply': result['reply'], 'tool_calls': [], 'skill_versions': [], 'blackboard_updates': {}}
        else:
            if exploration is None:
                exploration = domain.create(owner, {'request_id': 'task_'+session_id[-40:], 'session_id': session_id})
                eid = exploration['exploration_id']
            current_roles = exploration['draft']['compared_roles']
            if roles != current_roles and round_id is None:
                exploration = domain.patch(eid, owner, {'expected_revision': exploration['revision'], 'compared_roles': roles})
            if round_id is None and exploration.get('rounds'):
                round_id = exploration['rounds'][-1]['round_id']
            _bind_analysis(mysql, session_id, owner, run_id, exploration, snapshot['message'], round_id)
            child_packet = build_standard_packet('qa', goal=snapshot['message'], user_utterance=snapshot['message'], write_keys=[], capability_id='career-exploration')
            scoped = exploration_invoke(invoke)
            output = run_analysis_step(mysql=mysql, run_id=run_id, sid=owner, repository=repository,
                claim_token=claim_token, invoke=scoped, packet=child_packet, board=board,
                session_id=session_id, mode=InteractionMode.EXPERTS, context_projector=scoped.prepare_context)
            analysis = output['exploration_analysis']
            result.update(analysis, exploration_id=eid, round_id=round_id, compared_roles=roles,
                          state='needs_input' if analysis.get('question') else 'proposal' if analysis.get('proposal') else 'discussing')
            # Deliver the SAME validated review in this task, not a summary
            # that forces the student to leave the conversation to find it.
            result['reply'] = _discussion_reply(analysis)
            output = {**output, 'reply': result['reply']}
            for key in ('task_context_projection','task_context_recovery'):
                if output.get(key):result[key]=output[key]
        _assert_exploration_release(delegation_release)
        result['delegation_release'] = delegation_release
    store.stage(session_id, owner, run_id, result)
    return {**output, 'student_task': result}


def _discussion_reply(analysis):
    reply = analysis['reply']
    review = analysis.get('review')
    if review:
        for key, label in (('difficulties', '对困难与感受的理解'),
                           ('uncertain', '目前仍不能判断'),
                           ('next_options', '接下来可以选择')):
            value = review[key]
            if value not in reply:
                reply += '\n\n' + label + '：' + value
    return reply


def _validate_submitted_reply(reply):
    """Reject a trust-level upgrade; never silently rewrite model facts as truth.

    This is a narrow source-boundary check, not a general answer-quality score.
    Negative explanations remain legal; a rejected answer cannot be committed.
    """
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError('task_empty_answer')
    if re.search(r'(?:接下来|稍后|马上|将会)[\s\S]{0,150}(?:读取|分析|整理)[\s\S]{0,60}请稍等', reply):
        raise ValueError('task_terminal_answer_pending')
    clean = re.sub(r'[*_`#]', '', reply)
    claims = r'已(?:确认|核验|验证)(?:的)?(?:实践|项目|个人|工作|教育|经历|事实|能力)|内容真实(?:可靠)?|已经核验|验证属实'
    for clause in re.split(r'[。！？；\n]', clean):
        for match in re.finditer(claims, clause):
            prefix = clause[max(0,match.start()-16):match.start()]
            if not re.search(r'不是|并非|不属于|不能|不得|不等于|不代表|尚未|未经|未被|没有|不应', prefix):
                raise ValueError('unconfirmed_resume_claim: 学生提交材料不能表述为已确认或已核验事实')
    return reply


def _resume_read(mysql, ids, owner, session_id, previous, message, run_id, repository, claim_token, invoke, board, *, read_action='continue'):
    from .media.file_store import get_agent_file_service
    from .media.session_resources import mount_resources
    from .media.document_access import read_window, _full_text
    from .task_packet import build_standard_packet
    from .modes import InteractionMode
    if not ids:
        return ({'reply': '先上传你希望一起查看的简历，或从已有确认简历入口继续。', 'tool_calls': [], 'skill_versions': []},
                {'state': 'needs_input', 'files': [], 'evidence_summary': [], 'continuation': []})
    if len(ids) > 8:
        raise ValueError('本次最多读取八份材料，请分开任务处理')
    store = get_agent_file_service()
    files = []
    for fid in ids:
        meta = store.get(fid)
        if not meta or meta.get('student_id') != owner:
            raise PermissionError('附件不属于当前学生')
        if meta.get('status') != 'ready':
            raise ValueError('附件尚未解析就绪，未进行内容分析；请等待或重试状态读取')
        files.append(meta)
    mount_resources(session_id, [{'file_id': m['file_id']} for m in files], student_id=owner)
    discussion = _student_discussion(mysql, session_id, owner) if mysql is not None else {
        'messages': [], 'coverage': 'all_prior_student_messages', 'boundary': 'student_statement_not_verified'}
    if read_action not in {'discuss', 'continue', 'restart'}:
        raise ValueError('未知的文档读取操作')
    previous_positions = {r['file_id']: r for r in (previous.get('document_reads') or previous.get('continuation') or [])}
    chunks, receipts, positions, checkpoints = [], [], [], []
    for meta in files:
        repository.record_resource_read(run_id, student_id=owner, claim_token=claim_token, label='上传的简历', phase='started')
        full_text, _ = _full_text(meta['file_id'])
        text_version = digest({'bytes': meta.get('content_sha256'), 'text': hashlib.sha256(full_text.encode()).hexdigest()})
        prior = previous_positions.get(meta['file_id'])
        if prior and prior['version'] != text_version:
            raise TaskContextConflict('原文件版本已变化，请重新读取，不沿用旧位置')
        # A normal follow-up reuses the current window. Only the Lead's typed
        # continuation intent advances it; an EOF checkpoint never restarts.
        start = (prior.get('window_start', 0) if read_action == 'discuss' or prior.get('complete')
                 else prior['next_offset']) if prior and read_action != 'restart' else 0
        window = read_window(meta['file_id'], offset=start, max_chars=max(400, 4000 // len(files)))
        chunks.append('【学生提交的材料：'+meta['filename']+'】\n'
                      + ('本次从开头读取；' if start == 0 else '本次读取后续正文片段；')
                      + ('后面仍有未读内容。' if window.has_more else '本段已经到达文末，但不代表本次同时读到了前面全部正文。')
                      + '\n'+window.text)
        # A tail window is not by itself proof that the model saw every prior page.
        complete = window.complete and start == 0
        receipts.append({'name': meta['filename'], 'status': 'read' if complete else 'partial',
                         'coverage': '本次全文' if complete else '本次读取的正文片段',
                         'provenance': 'student_submitted_unverified'})
        if window.has_more:
            positions.append({'file_id': meta['file_id'], 'version': text_version,
                              'next_offset': start + len(window.text)})
        checkpoints.append({'file_id': meta['file_id'], 'version': text_version,
                            'window_start': start, 'next_offset': start + len(window.text),
                            'complete': not window.has_more})
        repository.record_resource_read(run_id, student_id=owner, claim_token=claim_token, label='上传的简历', phase='completed')
    goal = '以下是本轮实际读取的简历旧稿，属于学生提交，不是已核验经历；如与此前学生纠正冲突，以后来的明确纠正为准。只输出面向学生的最终分析，不输出内部过程、元数据、文件编号或完整性标记。缺失资料保持未知。'
    goal += '\n全篇称为“你提交的材料／你写的经历”，包括标题。不得使用“已确认的实践经历”“已经核验”“内容真实可靠”等结论；读取不等于确认，列出技能不等于掌握。不因语言鼓励而提升证据等级。'
    if not all(r['status'] == 'read' for r in receipts):
        goal += '\n本次只读取到片段，必须说明范围限制；不得声称全文分析或所有经历均已核对。'
    goal += '\n<SERVER_DOCUMENT>\n' + '\n\n'.join(chunks) + '\n</SERVER_DOCUMENT>'
    packet = build_standard_packet('qa', goal=goal, user_utterance=message, write_keys=[], capability_id='student-task-resume')
    packet.lean = True  # The current question occurs once, after the original source.
    packet.student_discussion = [m['text'] for m in discussion['messages']]
    packet.validate()
    from dataclasses import replace
    for attempt in range(2):
        output = invoke(packet, board, session_id, allow_tools=False, interaction_mode=InteractionMode.ASK,
                        student_id=owner, allow_artifact_commit=False)
        if output.get('incomplete'):
            reason=output.get('stop_reason')
            raise RuntimeError(reason if reason in {'turn_token_cap_exceeded','turn_token_usage_unverified','estimated_input_over_window'} else 'tool_failed')
        try:
            _validate_submitted_reply(output.get('reply', ''))
            break
        except ValueError:
            if attempt:
                raise
            # Repair only a concrete delivery/trust contract violation. A
            # second model must not invent semantic reasons to veto students.
            packet=replace(packet,goal=goal+'\n上一份候选未通过交付边界：直接交付本次实际读取的答复，不能让学生等候一个没有运行的后续过程；不得称学生提交的材料已核验。只依据原文及学生后来的纠正，保留未知。')
    return output, {'state': 'partial' if positions or any(r['status'] != 'read' for r in receipts) else 'discussing',
                    'files': [{'file_id': m['file_id'], 'name': m['filename']} for m in files],
                    'evidence_summary': receipts, 'continuation': positions, 'document_reads': checkpoints,
                    'discussion_context': {'coverage': discussion['coverage'], 'message_count': len(discussion['messages']),
                                           'source_digest': digest(discussion['messages']), 'answer_validation_retries': attempt}}


def _student_discussion(mysql, session_id, owner):
    """Replay immutable student inputs, not assistant assertions or file bodies.

    This is bounded source-preserving replay, NOT an accepted long-term
    compactor. Refuse over-capacity history rather than silently losing an old
    correction. Provider/window/turn preflight still runs in the existing invoke.
    """
    from .agent_run_store import AgentRunRepository
    rows = mysql.fetch_all("SELECT * FROM agent_run WHERE session_id=:session AND student_id=:owner "
        "AND status IN ('completed','failed','cancelled') ORDER BY created_at,run_id LIMIT 257",
        {'session': session_id, 'owner': owner})
    messages = []
    for row in rows:
        snapshot = AgentRunRepository._verified_input_snapshot(row)
        if snapshot.get('capability_id') == 'student-task':
            messages.append({'run_id': row['run_id'], 'text': snapshot['message']})
    if len(rows) > 256:
        raise ValueError('task_discussion_capacity')
    return {'messages': messages, 'coverage': 'all_prior_student_messages',
            'boundary': 'student_statement_not_verified'}


def _history(mysql, session_id, owner):
    """Return a bounded, owner-scoped same-session chat transcript.

    Conversation messages are the canonical source for Ask dialogue. Redis is
    written before its SQL projection, so reconcile the two with stable IDs;
    never collapse repeated text or guess chronology from mixed local/UTC
    timestamps. Do not include tool payloads, thinking, or attachment bodies.
    """
    from ..session_history import merge_history_sources

    redis_messages = []
    try:
        from ..session_manager import get_session_manager
        manager = get_session_manager()
        state = manager.refresh_session(session_id, require_authoritative=True)
        if state is not None:
            if state.student_id != owner:
                return []
            redis_messages = [
                {'message_id': item.get('message_id'), 'role': item.get('role'),
                 'content': item.get('content', '')}
                for item in state.messages
                if isinstance(item, dict) and item.get('role') in {'user', 'assistant'}
            ]
    except Exception:
        # The owner-bound SQL projection remains a safe fallback. An unavailable
        # or invalid Redis snapshot is never used as proof of complete history.
        redis_messages = []

    durable_rows = mysql.fetch_all(
        """SELECT m.message_id, m.role, m.content
           FROM conversation_message AS m
           INNER JOIN conversation AS c
             ON c.session_id=m.session_id AND c.student_id=:owner
           WHERE m.session_id=:session AND m.role IN ('user','assistant')
           ORDER BY m.id DESC LIMIT :limit""",
        {'session': session_id, 'owner': owner, 'limit': 32},
    ) or []
    durable_messages = [
        {'message_id': row.get('message_id'), 'role': row.get('role'),
         'content': row.get('content', '')}
        for row in reversed(durable_rows)
    ]
    history = merge_history_sources(redis_messages, durable_messages)
    return [
        {'role': item['role'], 'content': item['content']}
        for item in history[-16:]
        if item.get('role') in {'user', 'assistant'} and isinstance(item.get('content'), str)
    ]


def _recent(mysql, session_id, owner):
    history = _history(mysql, session_id, owner)
    remaining, messages = 1800, []
    for item in reversed(history):
        if remaining <= 0:
            break
        content = item['content'][:min(remaining, 600)]
        messages.append({'role':item['role'],'content':content,'truncated':len(content)<len(item['content'])})
        remaining -= len(content)
    return {'messages':list(reversed(messages)), 'coverage':'recent_discussion_only',
            'boundary':('仅包含当前会话近期 user/assistant 正文；user 消息可以归因为“你之前说过”，'
                        '但不是客观核验，assistant 消息只是历史对话，不能当作事实来源。'
                        '后来的用户纠正优先；未注入或被截断的旧内容不可假称记得。附件正文、工具日志和思考内容不在此上下文中。')}


def _conversation_recall_appendix(messages):
    """Describe the bounded transcript without making server metadata user text."""
    appendix = (
        '同一会话回忆边界：下面的 user/assistant 消息由服务端从当前会话投影，'
        '不是附件正文或个人档案。用户消息只能归因为“用户此前这样说过”，'
        '不能升级为外部核实事实；assistant 消息只说明当时对话内容，不能作为独立证据。'
        '同一事项以后出现的明确用户纠正优先。未出现在所给近期窗口的信息不得假称记得；'
        '信息不足时问一个澄清问题。不要调用工具或声称读取过附件。'
    )
    truncated_positions = []
    visible_position = 0
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        if message.get('role') not in {'user', 'assistant'}:
            continue
        content = message.get('content')
        if not isinstance(content, str) or not content.strip():
            continue
        visible_position += 1
        if message.get('truncated') is True:
            truncated_positions.append(visible_position)
    if truncated_positions:
        positions = '、'.join(str(position) for position in truncated_positions)
        appendix += (
            f'服务端传入的近期历史第{positions}条消息只包含原消息开头，后续内容已省略；'
            '不得依据省略部分推断，也不要把截断片段当作完整原话。'
        )
    return appendix


def settle(mysql, conn, session_id, owner, run_id):
    linked = conn.execute(text('SELECT run_id FROM career_exploration_analysis_turn WHERE run_id=:run'), {'run': run_id}).first()
    if linked:
        from ..career_exploration_analysis import settle_analysis
        settle_analysis(conn, run_id, owner)
    TaskContextStore(mysql).settle(conn, session_id, owner, run_id)
