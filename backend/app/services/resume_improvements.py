"""Resume-specific advice and deterministic draft conversion, not a scoring path."""
import hashlib
import json
import re
from html import escape
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VERSION = 9


def reading_question(focus, quote):
    """Product question wording for a model-selected, exact source fragment.

    This is an input request, not a generated finding about the student. The
    model selects the unresolved focus; it does not write a technical quiz and
    have its rejected prose silently replaced afterwards.
    """
    wording={
        'contribution':('实际分工','你实际参与了哪些工作？如果由同伴完成或没有参与，也可以直接说明。',
                        '弄清这一处，读者可以分清本人参与的工作与其他人的工作。'),
        'rule':('判断依据','当时依据什么作出判断？如果没有明确规则，也可以如实说明。',
                '补清判断依据，读者才能看懂这部分结果是怎样得出的。'),
        'use':('结果用途','这部分结果后来怎样使用？如果还没有使用，也可以直接说明。',
               '补清实际用途，读者就能理解这项结果在这段经历中起了什么作用。'),
        'actual_task':('实际使用经历','有没有用于一次实际任务？若用过，可以说说当时想处理什么；没用过也可如实说明。',
                       '把工具名称和一次真实任务联系起来，读者才能理解它在简历中指的是什么。'),
    }
    label,question,why=wording[focus]
    return {'text':'关于“'+quote+'”，'+question,'kind':'missing_information' if focus=='actual_task' else 'unclear_expression',
            'problem':'这部分的'+label+'尚未说明','why':why}


class AdviceBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['unclear_expression', 'missing_information', 'incomplete_reading']
    problem: str = Field(min_length=1, max_length=180, description='原文中确实妨碍理解的具体缺口；不把未独立完成整个项目视为缺点')
    why: str = Field(min_length=1, max_length=250, description='补清这条事实后，读者能看懂什么具体任务；不评价个人能力、技术高低或可信度')
    questions: list[str] = Field(min_length=1, max_length=4, description='开放询问实际分工或规则；不考核是否独立完成全部工作，不列举原文没有的个人信息字段作为猜测答案')


class Advice(AdviceBody):
    evidence_id: str


def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:
            raise ValueError('重复JSON字段不能覆盖另一条建议')
        result[key]=value
    return result


def target_for(fact_id):
    prefix= fact_id.rsplit('_',1)[0]
    return {'projects':'projects','skills':'skills','certificates':'honors','competitions':'honors',
            'name':'basic','phone':'basic','email':'basic','target_role':'basic','grade':'basic',
            'school':'education','major':'education','degree':'education','gpa':'education'}.get(prefix)


def synthesize(report, *, candidates):
    """Project the report's one validated model draft; never ask another writer."""
    report.update(improvements=[],improvements_version=VERSION,improvements_status='unavailable')
    report['status']='partial'
    report['missing_sections']=list(dict.fromkeys([*report.get('missing_sections',[]),'resume_expression_advice']))
    facts=[item for ch in report['chapters'] for item in ch['items'] if target_for(item['id'])]
    if not facts:
        report['improvements_status']='insufficient_evidence';return report
    try:
        items=[Advice.model_validate(value) for value in candidates]
        if len(items)>3:
            raise ValueError('只能提供至多三条具体建议，不能截断其他内容')
        by_id={fact['id']:fact for fact in facts}
        accepted=[]
        seen=set()
        for advice in items:
            fact=by_id.get(advice.evidence_id)
            if not fact or any(not q.strip() or len(q)>250 for q in advice.questions):
                raise ValueError('unbound advice')
            if advice.evidence_id in seen:
                raise ValueError('同一事实的独有问题必须归入同一项，不能静默丢掉另一条建议')
            seen.add(advice.evidence_id)
            text=' '.join([advice.problem,advice.why,*advice.questions])
            source=json.dumps(fact['fields'],ensure_ascii=False)
            question_scope=' '.join([advice.problem,*advice.questions])
            if re.search(r'(?:是否|未说明|没有说明)[^。？?]{0,35}独立|独立完成(?:全部|整个|所有)',question_scope):
                raise ValueError('询问实际分工，不能将独立完成作为简历表达考核')
            for field in ('姓名','手机号','学号','IP地址','身份证号'):
                if field in text and field not in source:
                    raise ValueError('不能用原文未出现的个人信息字段诱导学生回答')
            if (re.search(r'(?:各|每个|不同)活动.{0,8}报名人数|按活动.{0,8}(?:分组|统计)',source)
                    and re.search(r'(?:未说明|没有说明|缺少|缺乏|未明确|不清楚)[^。]{0,100}(?:查询对象|统计对象|统计维度|分组依据)',advice.problem)):
                raise ValueError('统计对象及按活动区分已在原文明确，不能伪造表达缺口')
            # Conservative guard against invented numerical achievements.
            numbers=set(re.findall(r'\d+(?:\.\d+)?',text))
            known=set(re.findall(r'\d+(?:\.\d+)?',json.dumps(fact['fields'],ensure_ascii=False)))
            if numbers-known or any(word in text for word in ('缺乏能力','没有能力','学校档位','提升到零')):
                raise ValueError('unsupported claim')
            kind='incomplete_reading' if report['coverage'].get('missing_pages') else advice.kind
            content=advice.model_dump();content['kind']=kind
            from .career_positioning import check_plain_interpretation
            check_plain_interpretation(content['why'],bank=[{'kind':'resume_experience','quote':source}])
            if re.search(r'技术含量|技术深度|熟练度|实践深度|SQL设计能力|全栈(?:实践)?能力|严谨性|技术合理性|可信度|工程意识|颗粒度|闭环|端到端|(?:评估|判断|衡量|证明).{0,10}能力',text):
                raise ValueError('建议必须解释具体事实的阅读用途，不能评价能力或用抽象术语代替理由')
            if kind=='incomplete_reading':
                content['problem']='目前仅读取到的片段中：'+content['problem']
            identifier='improve_'+hashlib.sha256(json.dumps([report['report_id'],content],sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
            fields=fact['fields']
            source_text=' · '.join(str(fields[k]) for k in ('name','role','desc','description','value') if fields.get(k))
            outline={'projects':'在[请填写：真实项目背景]中，我负责[请填写：自己完成的环节]，采用[请填写：实际方法]，形成[请填写：可确认的产物或结果；没有数字不必硬填]。',
                     'skills':'我曾在[请填写：真实使用场景]中使用[请填写：实际工具或方法]完成[请填写：具体任务]。'}.get(target_for(advice.evidence_id),
                     '[请填写：这项经历或资料的真实内容，并回答上方问题；无法确认的内容请保留空白]')
            accepted.append({**content,'id':identifier,'title':fact['label'],'target':target_for(advice.evidence_id),
                'source_text':source_text,'evidence_refs':fact['evidence_refs'],'outline':outline})
        report.update(improvements=accepted,improvements_status='ready')
        report['missing_sections']=[key for key in report['missing_sections'] if key!='resume_expression_advice']
        if not report['missing_sections']:
            report['status']='completed'
    except Exception:
        report['improvements_status']='invalid_output'
    return report


def convert_profile(profile):
    """No model call; refuse unsupported facts rather than silently omitting them."""
    from .resume_draft import validate_form
    from .resume_diagnostic_report import _META, _DISPLAY
    form=validate_form({})
    mapped={'name':'name','phone':'phone','email':'email','school':'school','major':'major','grade':'grade',
            'target_role':'targetRole','gpa':'gpa','summary':'summary','self_eval':'selfEval'}
    for source,target in mapped.items():
        if profile.get(source) is not None:form[target]=str(profile[source])
    form['skills']=[str(s) for s in profile.get('skills',[]) if isinstance(s,str)]
    unmapped=[]
    if any(not isinstance(s,str) for s in profile.get('skills',[])):
        unmapped.append('技能补充信息')
    for i,p in enumerate(profile.get('projects',[])):
        if not isinstance(p,dict):unmapped.append(f'项目 {i+1}');continue
        extras={k:v for k,v in p.items() if k not in {'name','role','desc','description','is_team'} and v not in (None,'',[],{})}
        if extras:unmapped.append(f'项目 {i+1} 的补充字段')
        desc=str(p.get('desc') or p.get('description') or '')
        if isinstance(p.get('is_team'),bool):desc+= '\n团队项目' if p['is_team'] else '\n个人项目'
        form['projects'].append({'name':str(p.get('name','')),'role':str(p.get('role','')),'desc':escape(desc)})
    if profile.get('degree') or form['school']:
        form['educations']=[{'school':form['school'],'major':form['major'],'degree':str(profile.get('degree') or ''),'period':'','gpa':form['gpa']}]
    for certificate in profile.get('certificates',[]):
        if isinstance(certificate,str):form['honors'].append({'type':'cert','label':certificate})
        else:unmapped.append('证书详情')
    for competition in profile.get('competitions',[]):
        if isinstance(competition,dict):
            form['honors'].append({'type':'award','label':' · '.join(str(v) for v in competition.values() if v is not None)})
        else:unmapped.append('竞赛详情')
    supported=set(mapped)|{'skills','projects','degree','certificates','competitions'}
    derived=set(_META)|{'semantic_facts','source_evidence'}
    for k,v in profile.items():
        if k not in supported|derived and not k.startswith('_') and v not in (None,'',[],{},0,False):
            unmapped.append(_DISPLAY.get(k,'其他已确认内容'))
    evidence=profile['_resume_confirmation'].get('source_evidence') or {}
    form['sourceText']='\n'.join(b.get('text','') for b in evidence.get('blocks',[]) if isinstance(b,dict))
    if evidence.get('missing_pages') or profile.get('failed_offsets'):unmapped.append('原文件尚未完整读取')
    return {'status':'mapping_incomplete' if unmapped else 'ready','form':validate_form(form),
            'unmapped_fields':unmapped,'notice':'预填仅整理已确认内容，请对照原文检查；建议没有自动写入。'}
