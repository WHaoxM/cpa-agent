"""Domain-only grounded interpretation; reuses the exploration QA runner/storage.

References are selected from server-owned quotes, never supplied by the model.
Their validity proves attribution, not the semantic correctness of an inference.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Literal
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

CONTENT_VERSION = 4



def experience_items(profile):
    result = []
    for key, label in (('projects', '项目经历'), ('work_experience', '工作经历'),
                       ('internships', '实习经历'), ('research', '研究经历'),
                       ('competitions', '竞赛经历'), ('leadership_roles', '任职经历')):
        values = profile.get(key) or []
        for index, item in enumerate(values if isinstance(values, list) else []):
            if isinstance(item, str):
                item = {'name': item}
            if not isinstance(item, dict):
                continue
            name = item.get('name') or item.get('title') or label
            description = item.get('desc') or item.get('description') or item.get('evidence') or ''
            role = item.get('role') or ''
            if all(isinstance(v, str) for v in (name, description, role)) and any((description, role, name)):
                result.append({'id': f'resume.{key}.{index}', 'label': label,
                               'name': name, 'role': role, 'description': description})
    return result


def current_starting_sources(mysql, sid, confirmation_id):
    """Read verified owner artifacts; never pair a historical resume with new results."""
    from .student_workbench import read_student_workbench
    from .student_portrait_snapshot import read_portrait_snapshot
    from .resume_diagnostic_report import read_resume_diagnostic
    state = read_student_workbench(mysql, student_id=sid)
    if state['source_confirmation_task_id'] != confirmation_id:
        return {'status': 'unavailable', 'reason': 'resume_version_changed'}
    result = {'status': 'current', 'portrait': None, 'resume_interpretation': None}
    snapshot_id = state['portrait'].get('snapshot_id')
    if snapshot_id:
        snapshot = read_portrait_snapshot(mysql, student_id=sid, snapshot_id=snapshot_id)
        if snapshot['source_confirmation_task_id'] != confirmation_id:
            raise ValueError('画像与简历来源不一致')
        result['portrait'] = {'snapshot_id': snapshot_id, 'status': snapshot['status'],
                              'portrait': snapshot['portrait']}
    if state['resume_diagnostic'].get('report_id'):
        report = read_resume_diagnostic(mysql, student_id=sid)
        if report['source_confirmation_task_id'] != confirmation_id:
            raise ValueError('简历解读来源不一致')
        result['resume_interpretation'] = {'report_id': report['report_id'],
            'analysis': report.get('analysis'), 'quality_status': report.get('quality_status'),
            'analysis_content_status':report.get('analysis_content_status'),
            'coverage': report.get('coverage'), 'missing_sections': report.get('missing_sections')}
    return result


def evidence_for_positioning(sources, message, history=None):
    facts = sources['facts']
    bank = []
    def add(key, label, quote, kind):
        if quote:
            item = {'id': key, 'label': label, 'quote': quote, 'kind': kind}
            if kind == 'student_statement':
                item['declares_not_done'] = bool(re.search(r'(?:我|本人)[^。！？\n]{0,30}(?:没有|还没|尚未|还未|从未|未曾|没做|没用|没试)', quote))
            pieces = list(dict.fromkeys(s.strip() for s in re.split(r'[，,；;。！？\n]', _public_quote(item)) if len(s.strip()) >= 2))
            if kind == 'resume_experience':
                pieces = [s for s in pieces if s != label]
            item['fragments'] = [{'id': key + '/f' + str(i), 'text': s} for i, s in enumerate(pieces)]
            if kind=='resume_experience' and '报名' in quote and '人数' in quote:
                item['interpretation_scope']='原文的统计对象是报名；可以解释按活动计数或分布。计数本身不能列出或判定重复报名，也不说明喜好、受欢迎程度、异常、资源安排合理性、真实意愿或实际到场。只读描述不证明数值正确，也不证明检查采用何种实现方式。'
            bank.append(item)
    for i, item in enumerate(facts.get('projects') or []):
        quote = '；'.join(str(item[k]) for k in ('name', 'role', 'desc', 'description') if item.get(k))
        add(f'resume.project.{i}', item.get('name') or '项目经历', quote, 'resume_experience')
    for item in facts.get('experiences') or []:
        # Projects are already represented above with stable legacy references.
        if item['id'].startswith('resume.projects.'):
            continue
        add(item['id'], item['label'], '；'.join(filter(None, (item['name'], item['role'], item['description']))), 'resume_experience')
    for i, skill in enumerate(facts.get('skills') or []):
        add(f'resume.skill.{i}', '简历列出的技能', skill, 'resume_skill')
    for key, label in (('self_report', '本人经历自述'), ('skill_exploration', '本人技能自述'), ('self_reflection', '本人技能自评')):
        value = facts.get(key)
        if value:
            add('student.' + key, label, json.dumps(value, ensure_ascii=False), 'student_statement')
    for i, text in enumerate(history or []):
        add(f'student.previous.{i}', '之前的本人补充', text, 'student_statement')
    add('student.message', '本次本人补充', message, 'student_statement')
    for i, candidate in enumerate(sources['candidates']):
        for j, skill in enumerate(candidate.get('skills') or []):
            add(f'direction.{i}.{j}', candidate['role_name'] + ' · 目录技能线索', skill, 'direction_catalogue')
    return bank


def reading_question_options(bank):
    return {q['key']:q for e in bank for q in e.get('reading_question_options',[])}


def bind_current_reading_questions(bank, reading):
    """Reuse the current owner's already-validated report question, not prose repair.

    The caller has read owner/version-bound artifacts and only enables this for
    the initial personal-reading turn. Exact source fragments must still exist
    in this turn's bank. No question is invented from a client reference.
    """
    if answered_duplicate_check_question(bank):return
    report=reading.get('resume_interpretation') or {}
    if reading.get('status')!='current' or report.get('analysis_content_status')!='current':return
    analysis=report.get('analysis') or {}
    for source in (analysis.get('summary') or {}).get('source_quotes',[]):
        fact=source.get('evidence_id','')
        if re.fullmatch(r'projects_\d+',fact):key='resume.project.'+fact.rsplit('_',1)[1]
        elif re.fullmatch(r'skills_\d+',fact):key='resume.skill.'+fact.rsplit('_',1)[1]
        else:continue
        entry=next((e for e in bank if e['id']==key),None)
        fragment=next((f for f in (entry or {}).get('fragments',[]) if f['text']==source.get('text')),None)
        if fragment:
            entry.setdefault('reading_known_fragments',[]).append({'fragment_id':fragment['id'],'quote':fragment['text']})
    for i,q in enumerate((report.get('analysis') or {}).get('questions',[])):
        if len(q.get('evidence_ids',[]))!=1 or len(q.get('source_quotes',[]))!=1:continue
        fact=q['evidence_ids'][0]
        if re.fullmatch(r'projects_\d+',fact):key='resume.project.'+fact.rsplit('_',1)[1]
        elif re.fullmatch(r'skills_\d+',fact):key='resume.skill.'+fact.rsplit('_',1)[1]
        else:continue
        entry=next((e for e in bank if e['id']==key),None)
        quote=q['source_quotes'][0]['text']
        fragment=next((f for f in (entry or {}).get('fragments',[]) if f['text']==quote),None)
        if not fragment or not all(isinstance(q.get(k),str) and q[k] for k in ('text','why','problem')):continue
        entry.setdefault('reading_question_options',[]).append({'key':'resume-reading-'+str(i),
            'text':q['text'],'reason':q['why'],'problem':q['problem'],
            'evidence_id':key,'fragment_id':fragment['id']})


def _prepare_reading_choice(value, bank):
    options=reading_question_options(bank)
    trusted={};errors=[]
    if not options or not isinstance(value,dict):return value,trusted,errors
    next_action=value.get('next_action') or {}
    raw_uncertainties=value.get('uncertainties',[])
    if not isinstance(next_action,dict) or not isinstance(raw_uncertainties,list) or any(not isinstance(p,dict) for p in raw_uncertainties):
        return value,trusted,['positioning: 下一步须为对象，未知项须为对象数组。']
    q=options.get(next_action.get('choice')) if next_action.get('kind') in {'clarify_experience','clarify_question'} else None
    def same_source_question(point):
        # A literal second copy carries no new claim. Canonicalize it into
        # the SAME source question below, rather than spending a model repair
        # on presentation duplication. Any changed words/anchor/extra field
        # still fails; this is not permission to delete an invalid paragraph.
        normalize=lambda s:s.translate(str.maketrans({'‘':'“','’':'”'}))
        return bool(q and set(point)=={'kind','text','evidence_ids','anchors'}
            and point.get('kind')=='needs_detail' and isinstance(point.get('text'),str)
            and normalize(point['text'])==normalize(q['text'])
            and point['evidence_ids']==[q['evidence_id']]
            and point['anchors']==[{'evidence_id':q['evidence_id'],'fragment_id':q['fragment_id']}])
    if any(p.get('kind')!='not_done' and not same_source_question(p) for p in raw_uncertainties):
        errors.append('positioning.uncertainties: 本轮已有来源问题，不自由生成未知项或技术例子；此处只保留本人明确没做过的原文项，其他留空。')
    if next_action.get('kind') not in {'clarify_experience','clarify_question'}:
        return value,trusted,errors
    if set(next_action)!={'kind','choice'} or next_action.get('choice') not in options:
        errors.append('positioning.next_action: 要先澄清时只输出kind和choice，choice选择'+', '.join(options)+'；不自行写text/reader_gain/reason、字段例子或选项。')
        return value,trusted,errors
    q=options[next_action['choice']]
    value=deepcopy(value)
    point={'evidence_ids':[q['evidence_id']],
           'anchors':[{'evidence_id':q['evidence_id'],'fragment_id':q['fragment_id']}]}
    value['next_action']={**point,'kind':next_action['kind'],'text':q['text'],'reason':q['reason']}
    value['uncertainties']=[p for p in raw_uncertainties if p.get('kind')=='not_done']
    index=len(value['uncertainties'])
    value['uncertainties'].append({**deepcopy(point),'kind':'needs_detail','text':q['problem']})
    trusted={'next_action.text':q['text'],'next_action.reason':q['reason'],f'uncertainties.{index}.text':q['problem']}
    return value,trusted,errors


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class _Point(_Strict):
    text: str = Field(min_length=1, max_length=900)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class _Uncertainty(_Point):
    kind: Literal['needs_detail', 'not_done', 'unknown']


class _Next(_Point):
    kind: Literal['clarify_experience', 'clarify_question', 'practice']
    reason: str = Field(min_length=1, max_length=600)


class _Positioning(_Strict):
    role: str = Field(min_length=1, max_length=100)
    starting_point: str = Field(min_length=1, max_length=1000)
    connections: list[_Point] = Field(max_length=4)
    uncertainties: list[_Uncertainty] = Field(max_length=3)
    next_action: _Next


def _public_quote(entry):
    if entry['kind'] != 'student_statement':
        return entry['quote']
    try:
        value = json.loads(entry['quote'])
    except (ValueError, TypeError):
        return entry['quote']
    if not isinstance(value, (dict, list)):
        return entry['quote']
    texts = []
    def visit(node):
        if isinstance(node, list):
            for item in node: visit(item)
        elif isinstance(node, dict):
            for key, item in node.items():
                if key in {'example', 'reflection', 'comment', 'answer_text', 'answer_label'} and isinstance(item, str) and item.strip():
                    texts.append(item)
                elif key == 'skills' and isinstance(item, list):
                    names = [s for s in item if isinstance(s, str)]
                    if names: texts.append('本人填写的技能：' + '、'.join(names))
                elif isinstance(item, (dict, list)):
                    visit(item)
    visit(value)
    return '\n'.join(dict.fromkeys(texts)) or '本人提交的结构化自述，仅作为探索参考，不等于事实核验。'


class _Anchor(_Strict):
    evidence_id: str = Field(min_length=1, max_length=100)
    fragment_id: str | None = Field(default=None, min_length=1, max_length=120)
    quote: str | None = Field(default=None, min_length=2, max_length=400)


class _AnchoredPoint(_Point):
    text: str = Field(min_length=1,max_length=900,
        description='只解释所引任务与方向的具体联系。不是个人能力评价；不增加受益人、业务效果、实现方式或未提供的例子。与起点重复就不创建本项。')
    anchors: list[_Anchor] = Field(min_length=1, max_length=3)


class _AnchoredUncertainty(_AnchoredPoint):
    kind: Literal['needs_detail', 'not_done', 'unknown']
    text: str = Field(min_length=1,max_length=900,
        description='只写影响本轮下一步的必要疑问或本人明确没做过的事。不为完整性增设技术考题；不举名字、号码、异常或跨活动等假设场景。没有必要疑问则不创建本项。')


class _AnchoredNext(_AnchoredPoint):
    kind: Literal['clarify_experience', 'clarify_question', 'practice']
    text: str = Field(min_length=1,max_length=900,
        description='围绕学生已说的具体任务，只做一件事。已回答分工和做法时不另设技术问题。让学生使用现有记录，不生成示例答案或假想记录。')
    reason: str = Field(min_length=1, max_length=600,validation_alias=AliasChoices('reader_gain','reason'),
        description='学生或听者通过这一步能明白什么具体信息。只谈他们能看懂的结果，不谈模型、事实推断或校验机制，不评价能力或成熟度。')


class _AnchoredPositioning(_Strict):
    role: str = Field(min_length=1, max_length=100)
    starting_point: str = Field(min_length=1, max_length=700,
        description='已有任务的结果能回答什么。事实原话另行展示，不再重写职责，不增加实际效果、技术实现、受益者或假设情境。')
    anchors: list[_Anchor] = Field(min_length=1, max_length=2)
    connections: list[_AnchoredPoint] = Field(max_length=3)
    uncertainties: list[_AnchoredUncertainty] = Field(max_length=3)
    next_action: _AnchoredNext


def positioning_output_schema(bank=None):
    """Use the validator's own bounds for both first output and one correction."""
    schema=_AnchoredPositioning.model_json_schema()
    known=[]
    for entry in bank or []:
        if not entry.get('reading_known_fragments'):continue
        anchor=deepcopy(schema['$defs']['_Anchor'])
        anchor['properties']['evidence_id']={'type':'string','const':entry['id']}
        anchor['properties']['fragment_id']={'type':'string','enum':[f['fragment_id'] for f in entry['reading_known_fragments']]}
        anchor['properties']['quote']={'type':'null'}
        anchor['required']=list(dict.fromkeys([*anchor.get('required',[]),'fragment_id']))
        known.append(anchor)
    if known:schema['properties']['anchors']['items']=known[0] if len(known)==1 else {'anyOf':known}
    # Native JSON Schema NOT is supported by the configured provider; unlike
    # regex lookaround it was verified against the actual failed request.
    # Exact source quotes/IDs are separate fields and are never filtered.
    cross_activity=r'同一.{0,5}(人|用户|学生).{0,10}(多个|不同|多项)活动'
    source='\n'.join(str(e.get('quote') or '') for e in bank or [] if e.get('kind') in {'resume_experience','student_statement'})
    exclusions=r'例如|比如|譬如|[（(]如|不是简单查表|[0-9]+\s*(人|条|次|%|％)'
    if not re.search(cross_activity,source):exclusions+='|'+cross_activity
    no_examples={'pattern':r'[\s\S]*('+exclusions+r')[\s\S]*'}
    for name in ('_AnchoredPoint','_AnchoredNext','_AnchoredUncertainty'):
        schema['$defs'][name]['properties']['text']['not']=deepcopy(no_examples)
    schema['$defs']['_AnchoredNext']['properties']['reader_gain']['not']=deepcopy(no_examples)
    schema['properties']['starting_point']['not']=deepcopy(no_examples)
    options=reading_question_options(bank or [])
    if options:
        schema['$defs']['_AnchoredNext']['properties']['kind']={'type':'string','const':'practice'}
        choice={'type':'object','additionalProperties':False,'required':['kind','choice'],
            'properties':{'kind':{'type':'string','enum':['clarify_experience','clarify_question']},
                          'choice':{'type':'string','enum':list(options)}}}
        schema['properties']['next_action']={'anyOf':[{'$ref':'#/$defs/_AnchoredNext'},choice]}
        schema['$defs']['_AnchoredUncertainty']['properties']['kind']={'type':'string','const':'not_done'}
        # No denial in the source means no not_done item is eligible. Merely
        # changing an unknown question's kind must not turn it into a student fact.
        schema['properties']['uncertainties']['maxItems']=2 if any(
            e.get('kind')=='student_statement' and e.get('declares_not_done') for e in bank) else 0
    return schema


def positioning_output_constraints():
    """Only nonduplicated array bounds for the existing prose/JSON protocol."""
    schema=positioning_output_schema()
    bounds=lambda value:{key:value[key] for key in ('minItems','maxItems')}
    return {'positioning.anchors':bounds(schema['properties']['anchors']),
            'each_section_item.anchors':bounds(schema['$defs']['_AnchoredPoint']['properties']['anchors']),
            'additionalProperties':schema['additionalProperties']}


def check_plain_interpretation(text, *, bank=None):
    """Conservative output boundary, not a semantic truth classifier.

    Personal facts are quoted by the server, not generated inside these fields.
    These guards catch known violations; real-model semantic review is separate.
    """
    from .career_exploration import _public_model_text
    text = _public_model_text(text, 1000)
    source = '\n'.join(e['quote'] for e in bank or [] if e['kind'] in {'resume_experience', 'student_statement'})
    if bank and '报名' in source and not re.search(r'重复|判重|去重',source):
        for clause in re.split(r'[。；;\n]',text):
            if re.search(r'(?:哪些|有无|列出|识别|找出|判定|判断).{0,16}重复',clause) and not re.search(r'不能|无法|不代表|不等于|是否|尚不清楚|未说明|没有说明|当时|请|建议|[？?]',clause):
                raise ValueError('报名计数不支持识别重复；不能借未引用的核对动作解释本项查询结果')
    if re.search(r'不是简单查表|并非简单查表|不只是简单查表', text):
        raise ValueError('不评定任务简单或高级；直接解释已有结果回答什么问题')
    cross_activity = r'同一.{0,5}(?:人|用户|学生).{0,10}(?:多个|不同|多项)活动'
    if re.search(cross_activity, text) and not re.search(cross_activity, source):
        raise ValueError('重复报名不等于跨活动报名；原文未说明判重范围，不能把同一人报多个活动判作重复')
    duplicate_scope = r'同一(?:人|用户|学生)[^。；\n]{0,16}(?:同一|一个|多个|不同|多项)活动'
    if re.search(duplicate_scope,text) and not re.search(duplicate_scope,source):
        raise ValueError('本项原文未说明判重范围，不能补成同一人对同一或不同活动的报名规则')
    # Ordering illustrations are fine; invented record variants are not a
    # necessary personal gap merely because phrased as a question.
    for match in re.finditer(r'(?:例如|比如|譬如|[（(]如)[^。；;\n）)]*', text):
        example=match.group()
        if re.search(r'[“「‘"\d]|姓名|手机号|号码|不同活动|录入差异',example) and example not in source:
            raise ValueError('不要补造记录差异或跨活动判重示例；围绕已提供经历解释本轮要做的一件事')
    if re.search(r'(?:例如|比如|譬如|举例(?:说|来说)?|示例)[^。；;\n]*'
                 r'(?:报了|报名了|换号|改名|发现了|遇到过|做了|完成了)', text):
        raise ValueError('不要编造具体经历或重复报名示例，即使标为举例也不能当成已有结果；'
                         '让学生使用原有统计与真实记录，未发现重复也可如实说明，不补造活动、姓名或数字')
    if re.search(r'闭环|分水岭|链条下游|技能意识|技术闭环|价值传递|能力跃迁|数据治理|基础价值|高度重合|数据查看者|端到端|统计口径设计|异常归因|脏数据|指标体系搭建|衔接点|小场景验证|实践颗粒度|分析价值|价值落地|业务指标|控制统计口径', text):
        raise ValueError('解释使用了抽象术语，请直接说明具体任务、尚需了解的细节及其用途')
    if re.search(r'\d+\s*(?:人|条|次|%|％)', text):
        raise ValueError('不要杜撰示例人数、条数或比例；请让学生使用已有结果，不编造活动及数字')
    only_registration = not bank or ('报名' in source and not re.search(r'签到|到场|出席', source))
    if bank and '报名' in source:
        for clause in re.split(r'[。；;\n]',text):
            if re.search(r'更受欢迎|资源.{0,8}(?:分配|倾斜|合理)|报名异常|真实(?:参与|报名)意愿',clause) and not re.search(
                    r'不能|不代表|不等于|无法|不足以|并非',clause):
                raise ValueError('报名计数不证明受欢迎程度、资源安排或异常；只解释原统计对象及结果含义')
    if only_registration and re.search(r'实际有多少人参与|参与规模|(?:真实|实际)?(?:参与|到场|出席)(?:人数|人次|率)', text):
        raise ValueError('报名不等于实际到场；本轮只讨论报名记录和报名人数，不能改成参与人数或参与率')
    if re.search(r'是否准备好|准备好向|是否能迈出', text):
        raise ValueError('一次小尝试不是能力或准备程度评估，请说明这件事的实际用途')
    if re.search(r'缺乏自动化|未.{0,5}自动化|没有.{0,5}自动化|在.{0,5}人工(?:核对|检查)', text):
        raise ValueError('不能从核对动作推断没有自动化或使用人工方式；具体做法只引用本人原话')
    for sentence in re.split(r'[。；;\n]', text):
        alternative=bool(re.fullmatch(r'(?:当时|那时)?是[^。]{1,80}还是[^。]{1,80}[？?]',sentence.strip()))
        for clause in re.split(r'[，,]',sentence):
            if re.search(r'人工(?:逐项)?(?:核对|校验|比对|检查)|(?:自动|程序化)(?:去重|识别|校验|核对|检查|处理)', clause) and not alternative and not re.search(
                    r'是否|还是|区分|如果|假如|若|请|尝试|试着|建议|考虑', clause):
                raise ValueError('实现方式只逐字展示原文，解释不能把核对认定为人工或自动处理；未知时可提出做法的问题')
    if re.search(r'(?:原始数据|系统|数据质量).{0,8}(?:不理想|不完善|不足)|主动开展', text):
        raise ValueError('不能从核对动作推断原始数据质量差、系统不完善或主动性；解释只说明任务用途')
    for clause in re.split(r'[，。；;\n]', text):
        if '独立' in clause and not re.search(r'是否[^。]{0,20}独立', clause):
            raise ValueError('独立职责只逐字展示该项原文，不能扩写到其他动作')
    if re.search(r'(?:说|描述|讲|列举).{0,8}你.{0,6}(?:写过|做过|用过|完成过)', text) and not re.search(r'如果|是否|有没有', text):
        raise ValueError('不能在提问中预设已有实践；先问是否尝试过，或请说明本人负责的部分')
    if re.search(r'熟练|擅长|胜任|掌握|认可|偏好|沟通能力|具备|确认自己|已在实践|(?:你|本人|学生).{0,8}(?:已经|已完成|已做|负责|完成了|编写了|做过|没有|还没|尚未|参与了)|(?:已|已经)(?:完成|实现)|尚未(?:涉及|进入|实践|落地)', text):
        raise ValueError('解释不能生成新的个人职责、能力、动机或否认；事实只使用逐项原文引用')
    return text


def answered_duplicate_check_question(bank):
    """Recognise one explicit, answered clarification, not general competence.

    Only this turn's student words count. Joint authorship, a concrete rule,
    and the specific untried explanation must each be stated positively/in
    the relevant clause. Query-only authorship or a denial about another
    action cannot advance this conversation. No model answer is manufactured.
    """
    entry = next((e for e in bank if e['id'] == 'student.message'
                  and e['kind'] == 'student_statement'), None)
    if not entry or not re.search(r'为什么|为何|用途', entry['quote']):
        return False
    fragments = [f['text'] for f in entry.get('fragments', [])]
    positive = [text for text in fragments if not re.search(r'不是|是否|吗|如果|假如', text)]
    joint_authorship = any(re.search(
        r'查询.{0,6}(?:和|与|及).{0,8}(?:重复|判重).{0,8}(?:核对|检查|识别|处理)'
        r'.{0,3}(?:都是|均是|都由|均由)(?:我|本人)(?:独立)?(?:编写|写)', text)
        for text in positive)
    explicit_rule = any(re.search(
        r'(?:我|本人)(?:用|按|根据|依据).{2,40}(?:识别|判定|判断)重复', text)
        and not re.search(r'没|未|不清楚|不确定', text) for text in positive)
    untried_explanation = any(re.search(
        r'(?:我|本人)(?:没有|还没|尚未|未曾).{0,12}(?:讲解|解释|讲过).{0,8}(?:统计|结果|结论)', text)
        for text in positive)
    return joint_authorship and explicit_rule and untried_explanation


def _validate_anchored(value, roles, bank):
    if not isinstance(value, dict) or not value.get('anchors'):
        raise ValueError('新解读缺少逐项原文锚点，请依据真实片段重新组织')
    original=value
    value,trusted,protocol_errors=_prepare_reading_choice(value,bank)
    # A legacy-shaped candidate may have several prose errors as well as the
    # new choice error. Return them together to the existing single correction.
    if protocol_errors:
        plain_bank=[{k:v for k,v in e.items() if k!='reading_question_options'} for e in bank]
        try:_validate_anchored(original,roles,plain_bank)
        except ValidationError as error:
            protocol_errors.extend('positioning.'+'.'.join(map(str,e['loc']))+': '+e['msg']
                for e in error.errors(include_input=False,include_url=False))
        except ValueError as error:protocol_errors.append(str(error))
        raise ValueError('\n'.join(protocol_errors))
    parsed = _AnchoredPositioning.model_validate(value).model_dump()
    if parsed['role'] not in roles:
        raise ValueError('个人解读包含未选择的方向')
    quotes = {e['id']: e for e in bank}
    used = set()

    def checked(text, field, anchors):
        if field in trusted:
            if text!=trusted[field]:raise ValueError('来源问题的产品呈现被改写')
            return text
        try:
            if re.search(r'例如|比如|譬如|[（(]如',text):
                raise ValueError('不要在个人解读中追加例子或假想记录；只解释现有任务，问题只问本轮实际缺少的一点')
            if re.search(r'是否构成|确认.{0,12}执行事实|判断.{0,12}实践',text):
                raise ValueError('不要把阅读变成技术考核；说明任务结果与当前问题的关系，不评审是否构成某种实践')
            if re.search(r'个人行动推断|本轮(?:校验|门禁|原文锚点)|服务端|校验器',text):
                raise ValueError('正文不解释内部校验；请直接说明学生或听者能看懂什么结果')
            selected_bank=[]
            for anchor in anchors:
                entry=quotes.get(anchor['evidence_id'])
                fragments=(entry or {}).get('fragments',[])
                selected=next((f['text'] for f in fragments if f['id']==anchor['fragment_id']),None) if anchor['fragment_id'] else anchor['quote']
                if (not entry or selected not in [f['text'] for f in fragments]
                        or (anchor['quote'] is not None and anchor['quote']!=selected)):
                    raise ValueError('原文片段ID不属于本项依据，或引用不一致')
                selected_bank.append({**entry,'quote':selected})
            return check_plain_interpretation(text, bank=selected_bank)
        except ValueError as exc:
            # Keep the same factuality rejection; identify the field so a
            # correction does not rewrite unrelated valid evidence or claims.
            raise ValueError(f'positioning.{field}: {exc}') from exc

    # Collect all known prose failures before any optional section can be
    # omitted. The existing ONE correction gets a coherent task, not one error
    # at a time, and a bad optional paragraph is not disguised as partial success.
    prose=[('starting_point',parsed['starting_point'],parsed['anchors'])]
    for field in ('connections','uncertainties'):
        prose.extend((f'{field}.{i}.text',p['text'],p['anchors']) for i,p in enumerate(parsed[field])
                     if p.get('kind')!='not_done')
    prose.extend(('next_action.'+key,parsed['next_action'][key],parsed['next_action']['anchors']) for key in ('text','reason'))
    prose_errors=[]
    known=[(entry['id'],f) for entry in bank for f in entry.get('reading_known_fragments',[])]
    if known and not all(any(anchor['evidence_id']==key and
            (anchor['fragment_id']==f['fragment_id'] if anchor['fragment_id'] else anchor['quote']==f['quote'])
            for key,f in known) for anchor in parsed['anchors']):
        prose_errors.append('positioning.starting_point: 起点只解释当前简历解读已明确的片段，不能用待澄清片段推断判重范围或方法。可选fragment_id：'+', '.join(f['fragment_id'] for _,f in known))
    for field,body,anchors in prose:
        try:checked(body,field,anchors)
        except ValueError as exc:prose_errors.append(str(exc))
    if answered_duplicate_check_question(bank) and parsed['next_action']['kind']!='practice':
        prose_errors.append('positioning.next_action: 已回答分工与规则；本轮解释下一步用途，不重新提问。')
    if prose_errors:raise ValueError('\n'.join(prose_errors))

    def verify(anchors, ids=None):
        keys = {a['evidence_id'] for a in anchors}
        if ids is not None and keys != set(ids):
            raise ValueError('每项引用必须与本项原文片段一一对应，不能借其他记录扩大职责')
        for anchor in anchors:
            entry = quotes.get(anchor['evidence_id'])
            fragments = entry.get('fragments', []) if entry else []
            selected = next((f['text'] for f in fragments if f['id'] == anchor['fragment_id']), None) if anchor['fragment_id'] else None
            if anchor['fragment_id']:
                if selected is None or (anchor['quote'] is not None and anchor['quote'] != selected):
                    raise ValueError('原文片段ID不属于本项依据，或附带文字与该片段不同；只能选已给出的fragment_id')
                anchor['quote'] = selected
            elif anchor['quote'] not in [f['text'] for f in fragments]:
                raise ValueError('引用必须逐字选用本项完整原文片段；建议只选fragment_id，由服务端回填原文')
            if not entry:
                raise ValueError('原文依据未读取')
            used.add(entry['id'])
        return [quotes[a['evidence_id']] for a in anchors]

    entries = verify(parsed['anchors'])
    if any(e['kind'] == 'direction_catalogue' for e in entries):
        raise ValueError('个人起点不能把方向目录当作本人经历')
    if any(e['kind'] == 'resume_experience' and a['quote'] == e['label'] for a, e in zip(parsed['anchors'], entries)):
        raise ValueError('起点不能只引用项目名，请选实际任务或团队功能的原文片段；团队功能仍不证明本人职责')
    # Responsibility scope survives verbatim; a separate model explanation may
    # discuss the task's use, never rewrite who did what or how independently.
    facts = []
    for anchor, entry in zip(parsed['anchors'], entries):
        prefix = {'resume_skill': '简历列出', 'student_statement': '你补充说'}.get(entry['kind'], '你在「' + entry['label'] + '」中写到')
        facts.append(prefix + '：“' + anchor['quote'] + '”。')
    lead = checked(parsed['starting_point'], 'starting_point',parsed['anchors'])
    public = {'role': parsed['role'], 'starting_point': ''.join(facts) + '\n' + lead,
              'connections': [], 'uncertainties': [], 'content_version': CONTENT_VERSION,
              'reading_status': 'initial_interpretation'}
    for field in ('connections', 'uncertainties', 'next_action'):
        points = [parsed[field]] if field == 'next_action' else parsed[field]
        for index, point in enumerate(points):
            entries = verify(point['anchors'], point['evidence_ids'])
            if field == 'connections' and not any(e['kind'] == 'resume_experience' or
                (e['kind'] == 'student_statement' and any(re.search(r'(?:我|本人)(?:曾经|曾)?(?:写|编写|做|整理|负责|核对|讲解)', a['quote'])
                 for a in point['anchors'] if a['evidence_id'] == e['id'])) for e in entries):
                # Tool-only information is not a personal experience connection.
                # Drop it instead of converting listed skills into an advantage.
                continue
            item = {k: v for k, v in point.items() if k != 'anchors'}
            if field == 'next_action':
                failures = []
                for key in ('text', 'reason'):
                    try:
                        item[key] = checked(item[key], 'next_action.' + key,point['anchors'])
                    except ValueError as exc:
                        failures.append(str(exc))
                if answered_duplicate_check_question(bank) and item['kind'] != 'practice':
                    failures.append('positioning.next_action: 已回答本轮查询与重复核对的本人分工及判定规则；'
                        '学生正在问下一步的用途，不能重开SQL或人工、如何标记的问卷。'
                        '请用已有统计结果提出一件简短说明练习并解释用途，不补造实现细节，不重做项目或生成计划')
                if failures:
                    # Report all known failures of this one priority together:
                    # the existing single correction must not fix only the
                    # first symptom and miss a simultaneously invalid reason.
                    raise ValueError('\n'.join(failures))
                public[field] = item
                continue
            if item.get('kind') == 'not_done':
                if any(e['kind'] != 'student_statement' for e in entries) or any(
                    not re.search(r'(?:我|本人)[^。！？\n]{0,30}(?:没有|还没|尚未|还未|从未|未曾|没做|没用|没试)', a['quote'])
                    for a in point['anchors']):
                    raise ValueError('尚未做过必须引用本项本人明确否认的原文，不能借另一件事的否认')
                item['text'] = '你提到：“' + '；'.join(a['quote'].rstrip('。；;') for a in point['anchors']) + '”。'
            else:
                item['text'] = checked(item['text'], field + f'.{index}.text',point['anchors'])
            public[field].append(item)
    public['evidence'] = [{'id': e['id'], 'label': e['label'], 'quote': _public_quote(e), 'kind': e['kind']}
                          for e in bank if e['id'] in used]
    return public


def unresolved_team_scope(roles, bank):
    """A truthful partial read, only when the student explicitly withheld scope.

    Used after the one permitted correction fails, never as a model-quality pass.
    It does not classify other unknowns or invent a useful-looking conclusion.
    """
    admissions = [(e, f) for e in bank if e['id'] == 'student.message'
                  for f in e.get('fragments', [])
                  if re.search(r'(?:没|未).{0,5}说明.{0,5}(?:本人|我).{0,6}负责', f['text'])]
    projects = [(e, f) for e in bank if e['kind'] == 'resume_experience'
                for f in e.get('fragments', []) if re.search(r'团队|小组|我们', f['text'])]
    if not admissions or not projects or len({e['id'] for e, _ in projects}) != 1 or len(roles) != 1:
        return None
    admission, statement = admissions[-1]
    project, fragment = projects[0]
    role = roles[0]
    position = {'role': role, 'content_version': CONTENT_VERSION,
        'reading_status': 'needs_clarification',
        'reading_note': '这轮未形成可靠的个人起点解读，只保留原始记录和一个职责问题。',
        'starting_point': '你在「' + project['label'] + '」中写到：“' + fragment['text'] + '”。你补充说：“' + statement['text'] + '”。团队完成的功能还不能算作本人的经历，可以先补清其中一件事。',
        'connections': [],
        'uncertainties': [{'kind':'needs_detail','text':'需要先分清团队成果中本人实际负责的部分。','evidence_ids':[admission['id']]}],
        'next_action': {'kind':'clarify_experience',
            'text':'请说明在「' + project['label'] + '」中本人具体负责的一件事，以及当时做了什么；如果还没有参与具体操作，也可以直接说明。',
            'reason':'分清本人负责的部分后，才能说明这段经历与「' + role + '」的联系，决定从已有任务继续，还是先做一次小尝试。',
            'evidence_ids':[project['id'],admission['id']]},
        'evidence':[{'id':e['id'],'label':e['label'],'quote':_public_quote(e),'kind':e['kind']}
                    for e in bank if e['id'] in {project['id'],admission['id']}]}
    return {'reply':position['starting_point']+'\n\n下一步：'+position['next_action']['text'],
        'question':None,'proposal':None,'review':None,'positioning':position,'comparisons':[]}


def validate_positioning(value, roles, bank, *, require_grounding=False):
    if require_grounding:
        return _validate_anchored(value, roles, bank)
    from .career_exploration import _public_model_text
    parsed = _Positioning.model_validate(value).model_dump()
    if parsed['role'] not in roles:
        raise ValueError('个人解读包含未选择的方向')
    quotes = {e['id']: e for e in bank}
    used = set()
    for point in [*parsed['connections'], *parsed['uncertainties'], parsed['next_action']]:
        ids = point['evidence_ids']
        if any(key not in quotes for key in ids):
            raise ValueError('个人解读引用了未读取的依据')
        if point in parsed['connections'] and not any(quotes[key]['kind'] != 'direction_catalogue' for key in ids):
            raise ValueError('个人联系不能只依据方向目录')
        if point.get('kind') == 'not_done' and not any(quotes[key].get('declares_not_done') is True for key in ids):
            # Downgrade the unsupported conclusion, not the entire useful read.
            # Do not retain model wording such as "you have never done this".
            names = list(dict.fromkeys(quotes[key]['label'] for key in ids))
            point['kind'] = 'unknown'
            point['text'] = '关于' + '、'.join(names[:2]) + ('等内容' if len(names)>2 else '') + '，现有资料还不能判断本人做过哪些部分。没有记录不等于没有做过，可以补清具体经历。'
        point['text'] = _public_model_text(point['text'], 900)
        used.update(ids)
    parsed['starting_point'] = _public_model_text(parsed['starting_point'], 1000)
    parsed['next_action']['reason'] = _public_model_text(parsed['next_action']['reason'], 600)
    # Narrow guards derived from saved real-model counterexamples. This is not
    # a general factuality classifier: quoted provenance still needs review.
    personal = '\n'.join(e['quote'] for e in bank if e['kind'] in {'resume_experience','student_statement'})
    assertions = [parsed['starting_point'], *[p['text'] for p in parsed['connections']]]
    unsupported = []
    for assertion in assertions:
        for clause in re.split(r'[，。；;\n]', assertion):
            if re.search(r'是否|不清楚|尚不|尚未确认|未明确|不能判断|未能判断', clause):
                continue
            for term, pattern in (('独立', r'独立(?:完成|执行|编写|负责|处理|实现|搭建)'),
                                  ('人工', r'人工[^，。；]{0,8}(?:核对|校验|比对|检查)'),
                                  ('端到端', r'端到端'),
                                  ('单表', r'单表'), ('多表', r'多表')):
                if re.search(pattern, clause) and term not in personal:
                    unsupported.append(assertion)
            if re.search(r'尚未(?:涉及|进入)|未涉及', clause) or (not any(e.get('declares_not_done') for e in bank) and re.search(r'尚未落地|还没实践|尚未实践|没有做过|没有.{0,12}经验', clause)):
                unsupported.append(assertion)
    if unsupported:
        # A completed read may yield a PARTIAL interpretation. Keep actual
        # records and a useful question; never publish the unsupported sentence.
        records = [e for e in bank if e['kind'] == 'resume_experience']
        skills = [e for e in bank if e['kind'] == 'resume_skill']
        anchor = records[0] if records else skills[0] if skills else bank[0] if bank else None
        if not anchor:
            raise ValueError('尚无可读取的起点依据')
        if records:
            grounded = '你在「' + anchor['label'] + '」中记录了：' + anchor['quote'] + '。可以从这段实际经历出发，先讲清下面这一件事，再了解它与「' + parsed['role'] + '」的联系。'
        else:
            grounded = '目前记录了「' + anchor['label'] + '」，但具体使用经过还不清楚。没有写清不等于没有做过，可以先从一次本人真实接触说起。'
        if len(grounded) > 1000:
            raise ValueError('起点原文超出单次展示范围，请缩小问题后读取；未截断为完整解读')
        if parsed['starting_point'] in unsupported:
            parsed['starting_point'] = grounded
        for item in parsed['connections']:
            if item['text'] in unsupported:
                item['text'] = '这项联系涉及尚未说明的个人细节，先对照下面的原始记录，再补清本人实际负责的部分。'
        used.add(anchor['id'])
        parsed['reading_status'] = 'needs_clarification'
        parsed['reading_note'] = '起点保留本人原始记录；尚无依据的具体实现判断未采用，下一步仍需你的说明。'
    else:
        parsed['reading_status'] = 'initial_interpretation'
    return {**parsed, 'evidence': [{'id':e['id'],'label':e['label'],'quote':_public_quote(e),'kind':e['kind']}
                                  for e in bank if e['id'] in used]}


POSITIONING_INSTRUCTION = '''
【个人起点阅读】
已有事实由服务端逐字呈现。每项anchors只选本人实际任务或本次补充的evidence_id、fragment_id，不生成quote；evidence_ids与同项引用一致。起点最多2项引用，其他段落最多3项。

starting_point：解释这项任务的结果能回答什么问题，不重写原文中的人、分工、做法或成果，不附想象的用途和实际效果。
不是评定“有没有做过”“是否构成独立实践”，而是从已写清的具体任务开始。统计只解释所记录对象的计数或分布，不推断喜好、原因或安排是否合理。
connections：最多2项有新含义的经历与方向联系，与起点重复则留空。方向目录只是技能线索，不是岗位标准或能力等级。
uncertainties可以为空；只保留影响当前一步的必要不确定。needs_detail为必要细节待说明，unknown为不能判断，not_done仅引用本人明确没做过的那件事。已回答的问题不再转成新的技术疑问。
next_action：只选一件事。描述未讲清时先补清；本人明确没做过且有必要时才做小尝试。学生已经回答分工和做法并问下一步为什么做，应接着解释一步具体尝试的直接用途，而非再开问卷。kind为clarify_experience、clarify_question或practice。
next_action.reader_gain：说明这一步能帮学生或读者弄清什么，不说抽象的能力、价值或准备程度，不把一次尝试当考试，也不解释内部校验。

正文用具体日常语言，不写技术术语评价，不补设理想项目要求。问题和行动不附示例答案、假想记录、可能的实现选项或括号旁注。使用学生原有材料即可，篇幅按内容需要，不填满栏目。
学生后来的明确补充按本人自述使用，不反写为已确认简历事实。职责只属于明确说过的动作；项目功能不等于个人负责，资料没写不等于不会。原话中否认一件事不推广成能力不足。实现方式、使用者、实际结果都不能从工具名或一般做法推测。
connections、uncertainties每项含text、evidence_ids、anchors，uncertainties另含kind。anchors每项为evidence_id、fragment_id；不写quote。
'''
