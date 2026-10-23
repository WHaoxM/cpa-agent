"""Owner-scoped, resume-only evidence report over the confirmed L0 snapshot.

This is a domain projection, not another extractor or a substitute for the
job-specific L4 report. It never manufactures match scores or market claims.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, create_model

from config.prompt_loader import load_prompt

from .job_publication import canonical_json, content_sha256
from .pipeline import require_confirmed_profile
from .career_positioning import _Anchor as EvidenceAnchor, check_plain_interpretation


_GROUPS = (
    ("overview", "基本资料与求职意向", ("name", "phone", "email", "target_role", "grade")),
    ("education", "教育背景", ("school", "major", "degree", "gpa", "rank_percent")),
    ("experience", "项目与实践经历", ("projects", "internship_months", "leadership_roles")),
    ("capabilities", "技能与资质", ("skills", "certificates")),
    ("achievements", "竞赛与研究", ("competitions", "research")),
    ("semantic_evidence", "已提取的行为与能力证据", ("semantic_facts",)),
)
_DISPLAY = {
    "name": "姓名", "phone": "电话", "email": "邮箱", "target_role": "目标方向",
    "grade": "年级", "school": "学校", "major": "专业", "degree": "学历",
    "gpa": "绩点", "rank_percent": "排名百分位", "projects": "项目",
    "internship_months": "实习时长（月）", "leadership_roles": "任职经历",
    "skills": "技能", "certificates": "证书", "competitions": "竞赛",
    "research": "研究", "semantic_facts": "语义事实",
    "team_project": "团队项目经历", "school_tier": "学校层次线索", "major_category": "专业类别",
    "communication": "沟通表达线索", "teamwork": "团队协作线索", "stress_tolerance": "压力应对线索",
    "responsibility": "责任意识线索", "internship": "实习经历", "leader": "带队经历",
    "student_officer": "学生工作经历",
}
_META = {"_resume_confirmation", "_user_self_report", "_skill_exploration", "source", "source_sha256", "raw_text_chars",
         "structure_path", "chunk_count", "error", "failed_offsets", "next_action",
         "semantic_fact_issues", "semantic_fact_conflicts", "semantic_fact_next_action",
         "skill_mentions", "entity_linking", "structure_calls", "entity_extraction_call",
         "entity_extraction_status", "entity_extraction_issues", "source_field_issues"}
_ROLE_PROMPT_VERSION = 4
_SYNTHESIS_VERSION = 20


class _Interpretation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=500, description='summary没有解释过的另一项经历的含义。不是问题清单或改进建议，不假设技术实现、使用者或效果，不举未提供的例子。')
    evidence_ids: list[str] = Field(min_length=1)
    anchors: list[EvidenceAnchor] = Field(min_length=1, max_length=2)


class _ReadingQuestion(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    choice: str = Field(description='只能选择本次事实提供的question_choices.key；不自造问题、分工或技术选项')


class _ReadingSummary(_Interpretation):
    text: str = Field(min_length=1,max_length=500,description='只说明这段明确的本人经历已经向读者交代了哪些有用信息，以及为何比仅列工具名称更具体。不是技术评审，不提出或罗列待补问题；问题另有questions，不在此重复。')


class _Synthesis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: _ReadingSummary = Field(validation_alias=AliasChoices('what_is_clear','summary'))
    # Ask for reading notes, not personality praise. Public/old data stays readable.
    strengths: list[_Interpretation] = Field(max_length=3,
        validation_alias=AliasChoices('reading_notes','strengths'),
        description='summary以外确有独有内容的任务解读；不是优势评定。没有新增内容时为空，不为填满栏目而写。')
    questions: list[_ReadingQuestion] = Field(max_length=1,description='只选最影响当前理解的一处，不凑问卷；也可以没有问题')


class _RoleDirection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=2, max_length=100)
    evidence_ids: list[str] = Field(min_length=1)
    questions_to_verify: list[str]


class _RoleDirections(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    directions: list[_RoleDirection] = Field(min_length=1, max_length=4)


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def _present(value):
    return value is not None and value != "" and value != [] and value != {}


def _quote_candidates(value):
    if isinstance(value, dict):
        preferred = [value.get(k) for k in ("evidence", "name", "title", "desc", "description", "role")]
        return [str(v).strip() for v in preferred if isinstance(v, str) and v.strip()]
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return []


def _evidence_refs(value, blocks):
    # Exact substring only. Semantic/page inference belongs to the extractor;
    # no page number is invented when a normalized value cannot be located.
    for quote in _quote_candidates(value):
        for block in blocks:
            body = block.get("text") or ""
            if quote in body and isinstance(block.get("page"), int):
                return [{"page": block["page"], "block_id": block.get("block_id"),
                         "quote": quote[:180]}]
    return []


def _item(key, value, index, blocks):
    fields = value if isinstance(value, dict) else {"value": value}
    label = _DISPLAY.get(key, key)
    if isinstance(value, dict):
        label = str(value.get("name") or value.get("title") or label)
    refs = _evidence_refs(value, blocks)
    return {"id": f"{key}_{index}", "label": label, "fields": fields,
            "evidence_refs": refs,
            "evidence_status": "page_located" if refs else "confirmed_profile_only"}


def _chapter(chapter_id, title, keys, profile, blocks):
    items = []
    for key in keys:
        value = profile.get(key)
        if not _present(value):
            continue
        if isinstance(value, list):
            items.extend(_item(key, entry, index, blocks) for index, entry in enumerate(value)
                         if _present(entry))
        elif key == "semantic_facts" and isinstance(value, dict):
            unmapped = {issue.get("field") for issue in (profile.get("semantic_fact_issues") or [])
                        if isinstance(issue, dict) and issue.get("reason") == "value_not_in_scoring_category"}
            for index, (fact, entry) in enumerate(value.items()):
                if not _present(entry):
                    continue
                item = _item(fact, entry, index, blocks)
                if fact in unmapped:
                    item["scoring_status"] = "unmapped_category"
                items.append(item)
        else:
            items.append(_item(key, value, 0, blocks))
    return {"id": chapter_id, "title": title, "items": items}


def _entity_review(profile, blocks):
    linking = _object(profile.get("entity_linking"))
    mentions = linking.get("mentions") if isinstance(linking.get("mentions"), list) else []
    candidates = linking.get("candidates") if isinstance(linking.get("candidates"), list) else []
    decisions = {d.get("mention_index"): d for d in linking.get("decisions", [])
                 if isinstance(d, dict) and isinstance(d.get("mention_index"), int)}
    items = []
    for index, mention in enumerate(mentions):
        if not isinstance(mention, dict):
            continue
        decision = decisions.get(index) or {}
        ci = decision.get("candidate_index")
        candidate = candidates[ci] if isinstance(ci, int) and 0 <= ci < len(candidates) else None
        fields = {"original_mention": mention.get("name"),
                  "decision": decision.get("decision") or "unresolved",
                  "candidate": candidate.get("name") if isinstance(candidate, dict) else None,
                  "rationale": decision.get("rationale"),
                  "review_status": "proposed_not_confirmed" if decision else "unresolved"}
        refs = _evidence_refs(mention.get("evidence") or mention.get("name"), blocks)
        items.append({"id": f"entity_link_{index}", "label": mention.get("name") or "技能原文",
                      "fields": fields, "evidence_refs": refs,
                      "evidence_status": "page_located" if refs else "confirmed_profile_only"})
    return {"id": "entity_review", "title": "技能实体归一化审阅", "items": items,
            "status": linking.get("status") or "not_available",
            "reason": linking.get("reason"),
            "notice": "归一化仅为候选建议，未自动改写已确认简历或公共图谱。"}


def build_resume_diagnostic_report(profile: dict, *, student_id: str) -> dict:
    """Build a complete report *within resume scope* from confirmed facts only."""
    confirmation = profile["_resume_confirmation"]
    evidence = _object(confirmation.get("source_evidence"))
    blocks = [block for block in evidence.get("blocks", []) if isinstance(block, dict)]
    covered = set()
    chapters = []
    for chapter_id, title, keys in _GROUPS:
        covered.update(keys)
        chapters.append(_chapter(chapter_id, title, keys, profile, blocks))
    chapters.append(_entity_review(profile, blocks))
    chapters.append({"id": "role_directions", "title": "候选岗位方向", "items": [],
                     "status": "pending_model_interpretation", "basis": "confirmed_resume_only",
                     "notice": "方向建议不是实时招聘岗位、正式匹配结果或胜任力评分。"})
    other = sorted(key for key, value in profile.items()
                   if key not in covered | _META and not key.startswith("_") and _present(value))
    chapters.append(_chapter("other_facts", "其他已提取事实", other, profile, blocks))
    missing_pages = evidence.get("missing_pages") or []
    limitations = ["岗位方向是基于已确认简历的待核验探索建议；不是实时招聘岗位、正式匹配分或市场需求判断。"]
    if any(isinstance(issue, dict) and issue.get("field") == "school_tier"
           and issue.get("reason") == "value_not_in_scoring_category"
           for issue in (profile.get("semantic_fact_issues") or [])):
        limitations.append("学校层级原文候选有可引用文字，但其类别不在当前学校评分表内；不可评分，也不推断一本或二本。")
    if missing_pages:
        limitations.append("原文件有未提取页面，本报告仅覆盖用户明确确认的已提取内容。")
    report_id = "resume_" + hashlib.sha256(
        f"{student_id}\x00{confirmation['task_id']}".encode("utf-8")).hexdigest()[:32]
    return {
        "schema_version": "resume-diagnostic-v1", "report_type": "resume_diagnostic",
        "report_id": report_id, "student_id": student_id,
        "status": "partial",
        "source_confirmation_task_id": confirmation["task_id"],
        "source_file_id": evidence.get("file_id"),
        "coverage": {key: evidence.get(key) for key in
                     ("page_count", "observed_pages", "missing_pages", "accuracy_verified")},
        "chapters": chapters, "analysis": None, "analysis_status": "not_generated",
        "quality_status": "not_assessed",
        "limitations": limitations,
        "missing_sections": (["unparsed_source_pages"] if missing_pages else []) +
                            ["evidence_grounded_semantic_analysis", "resume_based_role_directions"],
    }


def synthesis_facts(report):
    facts = [{"id": item["id"], "label": item["label"], "fields": item["fields"],
              "evidence_refs": item["evidence_refs"]}
             for chapter in report["chapters"] if chapter["id"] not in {"overview", "entity_review", "role_directions"}
             for item in chapter["items"]]
    for fact in facts:
        fragments = []
        # Keep exact wording and punctuation scope; never paraphrase a team
        # function into a personal action or combine it with another clause.
        for key, value in fact['fields'].items():
            if isinstance(value, str) and value.strip():
                for index, text in enumerate(re.split(r'[，,；;。！？\n]', value)):
                    if text.strip():
                        fragment={'id':fact['id']+'.'+key+'/f'+str(index),'text':text.strip()}
                        if re.search(r'核对|校验|比对|检查',text):
                            fragment['reading_boundary']='任务词只说明检查了什么；逐项不说明人工、自动或独立流程。具体做法仅按原文明确文字，不从动作推断实现方法、性格或主动性。此为阅读约束，不是另一项学生事实。'
                        fragments.append(fragment)
        fact['fragments'] = fragments
    choices=_question_choices(facts)
    for fact in facts:
        fact['question_choices']=[c for c in choices.values() if c['evidence_id']==fact['id']]
    return facts


def _question_choices(facts):
    """Finite report-domain choices from the same original fragments.

    These are the existing conservative eligibility rules, applied BEFORE the
    model chooses, not a second model or a generic source/actor classifier.
    All source fields/fragments remain in the input, even if no question fits.
    """
    choices={}
    experience='\n'.join(canonical_json(f['fields']) for f in facts if not f['id'].startswith('skills_'))
    has_project=any(f['id'].startswith('projects_') and any(f['fields'].get(k) for k in ('desc','description','content')) for f in facts)
    actions=('查询','核对','展示','编写','整理','统计','测试','设计','分析','沟通')
    for fact in facts:
        ordinal=0
        role=str(fact['fields'].get('role') or '')
        role_actions={a for a in actions if a in role} if re.search(r'负责|承担',role) else set()
        known_use=any(re.search(r'用于|用来|交给|供',f['text']) for f in fact['fragments'])
        for fragment in fact['fragments']:
            text=fragment['text'];focuses=[]
            key=fragment['id'].rsplit('.',1)[-1].split('/')[0]
            if fact['id'].startswith('skills_'):
                if key=='value' and not has_project and text not in experience:focuses=['actual_task']
            elif fact['id'].startswith('projects_') and key in {'desc','description','content'}:
                if re.search(r'用于|用来|交给|供',text):continue
                fragment_actions={a for a in actions if a in text}
                covered_by_role=bool(fragment_actions) and fragment_actions<=role_actions
                if not covered_by_role and not re.search(r'我|本人|同伴|他人|同学|负责',text):focuses.append('contribution')
                if re.search(r'核对|判断|识别|检查|校验',text) and not re.search(r'依据|根据|组合|规则|按',text):focuses.append('rule')
                if not known_use:focuses.append('use')
            for focus in focuses:
                choice=fact['id']+'@q'+str(ordinal);ordinal+=1
                choices[choice]={'key':choice,'focus':focus,'evidence_id':fact['id'],'fragment_id':fragment['id']}
    return choices


def synthesis_model(facts):
    keys=tuple(_question_choices(facts))
    question=create_model('SourceBoundReadingQuestion',__base__=_ReadingQuestion,
                          choice=(Literal[keys],...)) if keys else _ReadingQuestion
    experiences=[f for f in facts if f['id'].startswith(('projects_','work_experience_',
        'internships_','research_','competitions_','leadership_roles_'))]
    def recorded_fragments(actor):
        return tuple(fragment['id'] for fact in experiences for fragment in fact['fragments']
            if re.search(actor+r'.{0,30}(?:写|编写|核对|统计|查询|整理|测试|设计|分析|沟通)',fragment['text']))
    personal_fragments=recorded_fragments(r'(?:我|本人)') or recorded_fragments(r'负责')
    summary=_ReadingSummary
    if personal_fragments:
        anchor=create_model('RecordedPersonalTaskAnchor',__base__=EvidenceAnchor,
            fragment_id=(Literal[personal_fragments],...))
        summary=create_model('RecordedPersonalTaskReading',__base__=_ReadingSummary,
            anchors=(list[anchor],Field(min_length=1,max_length=min(2,len(personal_fragments)),
                description='总览先从原文明确的本人任务出发；其他项目功能和未说明的做法仍在完整资料及问题中，不当成此人的已知行动。')))
    # A single experience receives one coherent interpretation, not several
    # headings that invite rephrasing the same task into invented strengths.
    return create_model('SourceBoundResumeSynthesis',__base__=_Synthesis,
        summary=(summary,Field(validation_alias=AliasChoices('what_is_clear','summary'))),
        strengths=(list[_Interpretation],Field(max_length=min(3,max(0,len(experiences)-1)),
            validation_alias=AliasChoices('reading_notes','strengths'),
            description='只解释summary未覆盖的另一项实际经历；只有一项经历时留空，全部原始内容仍在章节中。')),
        questions=(list[question],Field(max_length=1 if keys else 0)))


def _technical_identifiers(text):
    return {s.casefold() for s in re.findall(r'[A-Za-z][A-Za-z0-9_+#.]*',text)}


def _supported_task_guard_view(text, anchors):
    """Prove a narrow task clause; never edit the published narrative.

    Explicit first-person source, same verb/object prefix, only whitespace,
    pronoun, grammatical 了 and tool/verb order normalization. No completion,
    independence, ability, negative, prospective or cross-action inference.
    """
    def fold(body):
        body=re.sub(r'[\s“”‘’「」]','',body).casefold()
        body=re.sub(r'^用([a-z][a-z0-9_+#.]*)编写了?(.+)$',r'编写\1\2',body)
        return re.sub(r'^(编写|写|统计|查询|核对|整理)了',r'\1',body)
    sources=[]
    for a in anchors:
        source=re.sub(r'^(?:我|本人)','',a['quote'],count=1)
        if source==a['quote'] or re.match(r'^(?:没|未|不|计划|准备|希望|拟|尝试|正在)',source) or re.search(r'未完成|失败',source):continue
        sources.append(fold(source))
    def prove(match):
        clause=match.group()
        body=re.sub(r'^(?:学生|你|本人)','',clause.strip(),count=1)
        if body==clause.strip():return clause
        normalized=fold(body)
        if len(normalized)>=6 and re.match(r'^(?:编写|写|统计|查询|核对|整理)',normalized) and any(s.startswith(normalized) for s in sources):
            return '原文明示的具体任务'
        return clause
    return re.sub(r'[^，,。；;\n]+',prove,text)


def validate_synthesis(value, facts):
    parsed = synthesis_model(facts).model_validate(value).model_dump()
    bank = [{**fact, 'kind':'resume_skill' if fact['id'].startswith('skills_') else 'resume_experience',
             'quote':canonical_json(fact['fields'])} for fact in facts]
    output = {'summary':None,'strengths':[],'questions':[]}
    failures = []
    seen_interpretations = set()
    for field, points in [('summary',[parsed['summary']]),('strengths',parsed['strengths'])]:
        for index, point in enumerate(points):
            try:
                entries = _resolve_synthesis_anchors(point['anchors'], bank, point['evidence_ids'])
                guard_text=point['text']
                unread=_technical_identifiers(guard_text)-_technical_identifiers('\n'.join(a['quote'] for a in point['anchors']))
                if unread:
                    raise ValueError('本项原文没有这些技术名称或语法：'+', '.join(sorted(unread))+'；不要从任务名补写实现细节')
                if re.search(r'角色字段|锚定|功能点|可能是[^。]{0,80}(?:或|、)',guard_text):
                    raise ValueError('正文直接解释学生下一步要理解的事，不展示内部术语或猜测参与方式的选项')
                if re.search(r'颗粒度|全链路|(?:您).{0,8}(?:完成了|负责|实现了)|具备|精通',guard_text):
                    raise ValueError('解释不能新增个人职责、能力或抽象评价；事实请逐字引用，解释请说清任务用途')
                selected_bank=[{**entry,'quote':anchor['quote']} for entry,anchor in zip(entries,point['anchors'])]
                check_plain_interpretation(_supported_task_guard_view(guard_text,point['anchors']), bank=selected_bank)
                text = point['text']
                if field == 'strengths' and all(e['kind']=='resume_skill' for e in entries):
                    raise ValueError('只有技能名称不能生成个人优势，请保留为简历列出的工具')
                if field != 'questions':
                    normalized=re.sub(r'[\W_]+','',text).removeprefix('我')
                    if normalized in seen_interpretations or any(normalized==re.sub(r'[\W_]+','',a['quote']).removeprefix('我') for a in point['anchors']):
                        raise ValueError('正文应解释这段任务的用途，不重复原文或另一条解读')
                    seen_interpretations.add(normalized)
                result = {'text':text,'evidence_ids':point['evidence_ids'],
                          'source_quotes':[{'evidence_id':a['evidence_id'],'text':a['quote']} for a in point['anchors']]}
                if field == 'summary': output[field]=result
                else: output[field].append(result)
            except ValueError as exc:
                failures.append('analysis.'+field+'.'+str(index)+': '+str(exc))
    try:
        output['questions']=project_reading_questions(parsed['questions'],facts)
    except ValueError as exc:
        failures.append(str(exc))
    if failures:
        raise ValueError('\n'.join(failures))
    return output


def project_reading_questions(questions, facts):
    """An independently bound question survives a rejected narrative section.

    The caller first validates the entire wire shape against synthesis_model;
    no text from an invalid summary/strength is used to form this question.
    """
    from .resume_improvements import reading_question
    bank=[{**fact,'kind':'resume_skill' if fact['id'].startswith('skills_') else 'resume_experience',
           'quote':canonical_json(fact['fields'])} for fact in facts]
    output=[];failures=[]
    seen_questions=set()
    for index,point in enumerate(questions):
        try:
            selected=_question_choices(facts)[point['choice']]
            anchor={'evidence_id':selected['evidence_id'],'fragment_id':selected['fragment_id'],'quote':None}
            entry=_resolve_synthesis_anchors([anchor],bank,[selected['evidence_id']])[0]
            quote=anchor['quote'];focus=selected['focus']
            if entry['id'] in seen_questions:
                raise ValueError('同一事实只选一个最需要补清的问题')
            seen_questions.add(entry['id'])
            if focus=='contribution' and re.search(r'我|本人|同伴|他人|同学|负责',quote):
                raise ValueError('该片段的参与者已经明确，不重新考问分工')
            if focus=='actual_task':
                if entry['kind']!='resume_skill' or any(quote in e['quote'] for e in bank if e['kind']=='resume_experience'):
                    raise ValueError('工具已有经历对应，不重新要求证明使用过；请选择真正未知的部分')
            elif entry['kind']=='resume_skill':
                raise ValueError('工具名称不能当作已经发生的具体任务')
            output.append({**reading_question(focus,quote),'evidence_ids':[entry['id']],
                'source_quotes':[{'evidence_id':entry['id'],'text':quote}]})
        except ValueError as exc:
            failures.append('analysis.questions.'+str(index)+': '+str(exc))
    if failures:
        raise ValueError('\n'.join(failures))
    return output


def _resolve_synthesis_anchors(anchors, bank, ids):
    """Thin report adapter for the existing exact-fragment anchor protocol."""
    by_id={entry['id']:entry for entry in bank}
    if set(ids)!={anchor['evidence_id'] for anchor in anchors}:
        raise ValueError('本项引用与原文锚点不一致')
    entries=[]
    for anchor in anchors:
        entry=by_id.get(anchor['evidence_id'])
        if entry is None:
            raise ValueError('引用的原文未读取')
        fragments=entry['fragments']
        if anchor['fragment_id']:
            fragment=next((item for item in fragments if item['id']==anchor['fragment_id']),None)
            if fragment is None or (anchor['quote'] is not None and anchor['quote']!=fragment['text']):
                raise ValueError('原文片段不属于本项依据或引用内容不一致')
            anchor['quote']=fragment['text']
        elif anchor['quote'] not in [item['text'] for item in fragments]:
            raise ValueError('引用必须逐字来自本项原文')
        entries.append(entry)
    return entries


def _synthesize(report: dict, *, llm_client=None) -> dict:
    from .resume_improvements import synthesize, VERSION, _unique_object
    report.update(improvements=[],improvements_version=VERSION,improvements_status='not_generated')
    facts = synthesis_facts(report)
    if not facts:
        report["analysis_status"] = "insufficient_evidence"
        return report
    payload = canonical_json(facts)
    if len(payload) > 24000:
        report["analysis_status"] = "input_budget_exceeded"
        return report
    try:
        from ..utils.llm_client import get_llm_client
        client = llm_client or get_llm_client()
        response = client.chat_with_metadata(
            messages=[
                {"role": "system", "content": load_prompt(
                    "report.resume_diagnostic.user", evidence_json="见下一条消息中的 JSON 数据")},
                {"role": "user", "content": payload},
            ],
            temperature=0.1, max_tokens=1500,
            response_format={"type": "json_schema", "json_schema": {
                "name": "resume_diagnostic_synthesis", "strict": True,
                "schema": synthesis_model(facts).model_json_schema()}},
        )
        if response.finish_reason == "length" or response.context_status.fits is False:
            report["analysis_status"] = "model_output_truncated"
            return report
        candidate = json.loads(response.text,object_pairs_hook=_unique_object)
        wire=synthesis_model(facts).model_validate(candidate).model_dump()
        questions=project_reading_questions(wire['questions'],facts)
        candidates=[{'evidence_id':q['evidence_ids'][0],'questions':[q['text']],
                     **{k:q[k] for k in ('kind','problem','why')}} for q in questions]
        draft=synthesize(deepcopy(report),candidates=candidates)
        if draft['improvements_status']!='ready':
            raise ValueError('问题未通过事实与表达边界')
        report.update(draft)
        report.update(analysis_version=_SYNTHESIS_VERSION,model_id=response.model,model_usage=response.usage)
        valid_ids = {fact["id"] for fact in facts}
        claims = [wire["summary"], *wire["strengths"]]
        if any(not set(claim["evidence_ids"]) <= valid_ids for claim in claims):
            report["analysis_status"] = "invalid_evidence_references"
            return report
        if any(any(word in claim['text'] for word in ('掌握','熟练','胜任','精通')) for claim in claims):
            report['analysis_status']='unsupported_ability_claim'
            return report
        usage=response.usage
        try:
            analysis = validate_synthesis(candidate, facts)
        except ValueError as invalid:
            # One field-specific correction of a well-formed draft, with the
            # identical complete source and already-validated question. Never
            # resubmit the user journey or select among generated answers.
            schema=synthesis_model(facts).model_json_schema()
            source_bank=[{**f,'quote':canonical_json(f['fields'])} for f in facts]
            unread=set()
            for claim in deepcopy([wire['summary'],*wire['strengths']]):
                try:_resolve_synthesis_anchors(claim['anchors'],source_bank,claim['evidence_ids'])
                except ValueError:continue  # The original reference error still requires correction.
                unread|=_technical_identifiers(claim['text'])-_technical_identifiers('\n'.join(a['quote'] for a in claim['anchors']))
            literals=[''.join('['+c.lower()+c.upper()+']' if c.isascii() and c.isalpha() else re.escape(c) for c in name) for name in sorted(unread)]
            excluded='[\\s\\S]*(例如|比如|譬如|[（(]如|不是简单查表|并非简单查表|不只是简单查表|[0-9]+\\s*(人|条|次|%|％)'+('|'+'|'.join(literals) if literals else '')+')[\\s\\S]*'
            for prop in (schema['properties']['what_is_clear'],schema['properties']['reading_notes']['items']):
                definition=prop['$ref'].rsplit('/',1)[1]
                schema['$defs'][definition]['properties']['text']['not']={'pattern':excluded}
            selected=[q['choice'] for q in wire['questions']]
            schema['properties']['questions']={'type':'array','minItems':len(selected),'maxItems':len(selected),
                'items':{'type':'object','additionalProperties':False,'required':['choice'],
                    'properties':{'choice':{'type':'string',**({'enum':selected} if selected else {})}}}}
            report['model_usage']=None  # A dispatched correction must not be counted as only one call.
            corrected=client.chat_with_metadata(messages=[
                {'role':'system','content':load_prompt('report.resume_diagnostic.user',evidence_json='见下一条消息中的 JSON 数据')+
                    '\n【正文纠正反馈】\n'+str(invalid)+
                    '\n仅重新组织正文，不复用初稿句子。只解释现有描述让读者明白什么，不重写谁做过什么，不增设技术细节或问题。'
                    'questions保持本次已经验证的选择；已知资料与所有事实限制不变。'},
                {'role':'user','content':payload}],temperature=0.1,max_tokens=1500,
                response_format={'type':'json_schema','json_schema':{'name':'resume_narrative_correction','strict':True,'schema':schema}})
            if isinstance(usage,dict) and isinstance(corrected.usage,dict) and all(
                    type(u.get(k)) is int for u in (usage,corrected.usage) for k in ('prompt_tokens','completion_tokens','total_tokens')):
                usage={k:usage[k]+corrected.usage[k] for k in ('prompt_tokens','completion_tokens','total_tokens')}
            else:usage=None
            report.update(model_usage=usage,model_id=corrected.model)
            if corrected.finish_reason=='length' or corrected.context_status.fits is False:
                report['analysis_status']='model_output_truncated';return report
            try:
                fixed=json.loads(corrected.text,object_pairs_hook=_unique_object)
                fixed_wire=synthesis_model(facts).model_validate(fixed).model_dump()
                if fixed_wire['questions']!=wire['questions']:
                    raise ValueError('纠正正文不能更换已验证的问题')
                analysis=validate_synthesis(fixed,facts)
            except ValueError:
                report['analysis_status']='invalid_grounded_interpretation';return report
        report["analysis"] = analysis
        report["analysis_version"] = _SYNTHESIS_VERSION
        report["analysis_status"] = "model_interpretation_with_references"
        report["quality_status"] = "model_interpretation_unverified"
        report["model_usage"] = usage
        report.setdefault("model_id",response.model)
        report["missing_sections"].remove("evidence_grounded_semantic_analysis")
    except Exception:
        report["analysis_status"] = "model_unavailable_or_invalid"
    return report


def _synthesize_role_directions(report: dict, *, llm_client=None) -> dict:
    """Interpret confirmed resume facts; never turn model text into catalog authority."""
    chapter = next(ch for ch in report["chapters"] if ch["id"] == "role_directions")
    facts = [{"id": item["id"], "label": item["label"], "fields": item["fields"]}
             for ch in report["chapters"] if ch["id"] not in {"entity_review", "role_directions"}
             for item in ch["items"] if item["id"] not in {"name_0", "phone_0", "email_0"}]
    if not facts:
        chapter["status"] = "insufficient_resume_evidence"
        return report
    payload = canonical_json(facts)
    if len(payload) > 24000:
        chapter["status"] = "input_budget_exceeded"
        return report
    try:
        from ..utils.llm_client import get_llm_client
        client = llm_client or get_llm_client()
        response = client.chat_with_metadata(
            messages=[
                {"role": "system", "content": load_prompt(
                    "report.role_directions.user", evidence_json="见下一条消息中的 JSON 数据")},
                {"role": "user", "content": payload},
            ], temperature=0.1, max_tokens=1400,
            response_format={"type": "json_schema", "json_schema": {
                "name": "resume_role_directions", "strict": True,
                "schema": _RoleDirections.model_json_schema()}},
        )
        if response.finish_reason == "length" or response.context_status.fits is False:
            chapter["status"] = "model_output_truncated"
            return report
        directions = _RoleDirections.model_validate_json(response.text).directions
        valid_ids = {fact["id"] for fact in facts}
        if any(not set(direction.evidence_ids) <= valid_ids for direction in directions):
            chapter["status"] = "invalid_evidence_references"
            return report
        fact_by_id = {fact["id"]: fact for fact in facts}
        evidence_by_id = {
            item["id"]: item.get("evidence_refs", [])
            for source_chapter in report["chapters"]
            for item in source_chapter["items"]
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        def fact_label(fact_id: str) -> str:
            fact = fact_by_id[fact_id]
            fields = fact["fields"]
            value = fields.get("name") or fields.get("title") or fields.get("value")
            return str(value if isinstance(value, str) and value.strip() else fact["label"])[:70]
        chapter["items"] = [{
            "id": f"role_direction_{index}", "label": direction.title,
            "fields": {"title": direction.title,
                       "rationale": "依据简历记录的「" + "」「".join(
                           fact_label(fact_id) for fact_id in direction.evidence_ids[:3]
                       ) + "」列为探索方向；具体岗位要求与适合程度仍需核验。",
                       "evidence_ids": direction.evidence_ids,
                       "questions_to_verify": direction.questions_to_verify,
                       "source_type": "resume_model_inference",
                       "source_confirmation_task_id": report["source_confirmation_task_id"]},
            # Match the rationale's fact-ID order.  The UI shows the first
            # three citations, so chapter iteration order would otherwise
            # hide the evidence actually named in that rationale.
            "evidence_refs": [ref for fact_id in direction.evidence_ids
                              for ref in evidence_by_id.get(fact_id, [])],
            "evidence_status": "model_interpretation_with_fact_references",
        } for index, direction in enumerate(directions)]
        chapter["status"] = "model_interpretation_unverified"
        chapter["prompt_version"] = _ROLE_PROMPT_VERSION
        chapter["model_usage"] = response.usage
        chapter["model_id"] = response.model
        report["missing_sections"].remove("resume_based_role_directions")
    except Exception:
        chapter["status"] = "model_unavailable_or_invalid"
    if not report["missing_sections"]:
        report["status"] = "completed"
    return report


def _require_current(mysql, student_id):
    try:
        return require_confirmed_profile(mysql, student_id)
    except ValueError as error:
        raise ResumeReportNeedsInput(str(error)) from error


class ResumeReportNeedsInput(Exception):
    pass


class ResumeReportStale(Exception):
    pass


def create_or_read_resume_diagnostic(mysql, *, student_id: str, llm_client=None,
                                     expected_confirmation_task_id: str | None = None,
                                     writer_claim: dict | None = None) -> dict:
    from .resume_improvements import VERSION as improvements_version
    profile = _require_current(mysql, student_id)
    if (expected_confirmation_task_id is not None
            and profile["_resume_confirmation"]["task_id"] != expected_confirmation_task_id):
        raise ResumeReportStale("报告任务源于旧版已确认简历")
    report = build_resume_diagnostic_report(profile, student_id=student_id)
    existing = mysql.fetch_all(
        "SELECT report_id, student_id, source_confirmation_task_id, status, report_json, content_sha256 "
        "FROM resume_diagnostic_report WHERE student_id=:sid AND source_confirmation_task_id=:tid LIMIT 1",
        {"sid": student_id, "tid": report["source_confirmation_task_id"]})
    existing_report = _object(existing[0].get("report_json")) if existing else {}
    existing_roles = next((chapter for chapter in existing_report.get("chapters", [])
                           if isinstance(chapter, dict) and chapter.get("id") == "role_directions"), {})
    if (existing and existing[0].get("status") == "completed"
            and existing_report.get("quality_status") == "model_interpretation_unverified"
            and existing_report.get("analysis_version") == _SYNTHESIS_VERSION
            and existing_roles.get("status") == "model_interpretation_unverified"
            and existing_roles.get("prompt_version") == _ROLE_PROMPT_VERSION
            and existing_report.get('improvements_version') == improvements_version
            and existing_report.get('improvements_status') == 'ready'):
        return read_resume_diagnostic(mysql, student_id=student_id, report_id=report["report_id"])
    report = _synthesize(report, llm_client=llm_client)
    report = _synthesize_role_directions(report, llm_client=llm_client)
    statement = (
        "INSERT INTO resume_diagnostic_report "
        "(report_id,student_id,source_confirmation_task_id,status,report_json,content_sha256) "
        "VALUES (:rid,:sid,:tid,:status,:payload,:sha) "
        "ON DUPLICATE KEY UPDATE status=VALUES(status),report_json=VALUES(report_json),"
        "content_sha256=VALUES(content_sha256)")
    values = {"rid": report["report_id"], "sid": student_id,
              "tid": report["source_confirmation_task_id"], "status": report["status"],
              "payload": canonical_json(report), "sha": content_sha256(report)}
    if writer_claim is None:
        mysql.execute(statement, values, commit=True)
    else:
        from sqlalchemy import text
        from .pipeline_events import ConfirmationTransaction
        with mysql.engine.begin() as conn:
            claimed = conn.execute(text(
                "SELECT d.claim_token,t.status,t.student_id FROM resume_diagnostic_dispatch d "
                "JOIN pipeline_task t ON t.task_id=d.task_id "
                "WHERE d.task_id=:task_id FOR UPDATE"),
                {"task_id": writer_claim["task_id"]}).mappings().first()
            if (not claimed or claimed["student_id"] != student_id
                    or claimed["status"] != "running"
                    or claimed["claim_token"] != writer_claim["claim_token"]):
                raise ResumeReportStale("报告任务执行权已转移，请重新读取")
            conn.execute(text("SELECT student_id FROM student_profile "
                              "WHERE student_id=:sid FOR UPDATE"), {"sid": student_id}).first()
            current = require_confirmed_profile(ConfirmationTransaction(conn), student_id)
            if current["_resume_confirmation"]["task_id"] != report["source_confirmation_task_id"]:
                raise ResumeReportStale("报告任务源于旧版已确认简历")
            conn.execute(text(statement), values)
    return read_resume_diagnostic(mysql, student_id=student_id, report_id=report["report_id"])


def read_resume_diagnostic(mysql, *, student_id: str, report_id: str | None = None) -> dict:
    if report_id:
        rows = mysql.fetch_all(
            "SELECT report_id,student_id,source_confirmation_task_id,status,report_json,content_sha256 "
            "FROM resume_diagnostic_report WHERE student_id=:sid AND report_id=:rid LIMIT 1",
            {"sid": student_id, "rid": report_id})
        if not rows:
            raise LookupError("当前简历报告不存在")
    profile = _require_current(mysql, student_id)
    confirmation_id = profile["_resume_confirmation"]["task_id"]
    if not report_id:
        rows = mysql.fetch_all(
            "SELECT report_id,student_id,source_confirmation_task_id,status,report_json,content_sha256 "
            "FROM resume_diagnostic_report WHERE student_id=:sid AND source_confirmation_task_id=:tid LIMIT 1",
            {"sid": student_id, "tid": confirmation_id})
    if not rows:
        raise LookupError("当前简历报告不存在")
    row = rows[0]
    report = _object(row.get("report_json"))
    if row.get("source_confirmation_task_id") != confirmation_id:
        raise ResumeReportStale("报告源于旧版已确认简历，请重新生成")
    expected = build_resume_diagnostic_report(profile, student_id=student_id)
    invariant_keys = ("schema_version", "report_type", "report_id", "student_id",
                      "source_confirmation_task_id", "source_file_id", "coverage")
    factual_chapters = [chapter for chapter in report.get("chapters", [])
                        if isinstance(chapter, dict) and chapter.get("id") != "role_directions"]
    expected_factual_chapters = [chapter for chapter in expected["chapters"]
                                 if chapter["id"] != "role_directions"]
    role_chapters = [chapter for chapter in report.get("chapters", [])
                     if isinstance(chapter, dict) and chapter.get("id") == "role_directions"]
    legacy_without_directions = not role_chapters and report.get("schema_version") == "resume-diagnostic-v1"
    if (row.get("student_id") != student_id or row.get("report_id") != expected["report_id"]
            or row.get("status") != report.get("status")
            or any(report.get(key) != expected.get(key) for key in invariant_keys)
            or factual_chapters != expected_factual_chapters
            or (len(role_chapters) != 1 and not legacy_without_directions)
            or row.get("content_sha256") != content_sha256(report)):
        raise ResumeReportStale("报告内容或来源已变化，请重新核对简历")
    # The published-job overlay is read afresh and never persisted into the
    # resume report hash: job updates must not invalidate confirmed resume facts.
    result = deepcopy(report)
    result['analysis_content_status'] = 'current' if report.get('analysis_version') == _SYNTHESIS_VERSION else 'legacy'
    result.setdefault('improvements', [])
    result.setdefault('improvements_status', 'pending_upgrade')
    result["resume_confirmed_at"] = profile["_resume_confirmation"].get("confirmed_at")
    if legacy_without_directions:
        result["chapters"].append({"id": "role_directions", "title": "候选岗位方向",
                                   "items": [], "status": "pending_upgrade",
                                   "basis": "confirmed_resume_only",
                                   "notice": "当前是旧版简历事实报告；请发起生成以补充基于简历的岗位方向。"})
        result["status"] = "partial"
        result["missing_sections"] = list(result.get("missing_sections") or []) + [
            "resume_based_role_directions"]
    try:
        from .job_catalog_listing import list_published_job_candidates
        result["job_catalog"] = list_published_job_candidates(mysql, limit=20, offset=0)
    except Exception:
        result["job_catalog"] = {"status": "unavailable", "total": 0, "items": [],
                                 "catalog_version": None, "catalog_latest_published_at": None,
                                 "has_more": False}
    return result
