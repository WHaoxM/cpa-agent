import json
from pathlib import Path
import pytest
import requests
from types import SimpleNamespace
from backend.tests.test_student_workbench_http_mysql import workbench_isolated_mysql, _start_profile_http, _seed_confirmed_resume

from app.services.resume_diagnostic_report import build_resume_diagnostic_report
from app.services import resume_improvements as service


def profile():
    return {'name':'林知夏','school':'武汉理工大学','major':'计算机科学','degree':'本科',
            'skills':['Python'], 'projects':[{'name':'公开数据分析','role':'开发','desc':'使用Python整理公开数据'}],
            '_resume_confirmation':{'task_id':'l0_current','source_evidence':{'missing_pages':[],
                'blocks':[{'block_id':'b1','page':1,'text':'公开数据分析 使用Python整理公开数据'}]}}}


def item(**overrides):
    return dict(evidence_id='projects_0',kind='unclear_expression',problem='个人负责的步骤尚不清楚',
                why='补清工作环节可以帮助读者了解你的参与方式',questions=['你具体负责了哪些处理步骤？'],**overrides)


def test_improvements_bind_facts_and_keep_outlines_unfilled():
    report=service.synthesize(build_resume_diagnostic_report(profile(),student_id='owner'),candidates=[item()])
    assert report['improvements_status']=='ready'
    advice=report['improvements'][0]
    assert advice['target']=='projects' and advice['evidence_id']=='projects_0'
    assert '使用Python整理公开数据' in advice['source_text']
    assert '[请填写：' in advice['outline']
    assert advice['id']==service.synthesize(build_resume_diagnostic_report(profile(),student_id='owner'),candidates=[item()])['improvements'][0]['id']


def test_invalid_references_and_novel_metrics_never_become_advice():
    for bad in [dict(item(),evidence_id='someone_else'),dict(item(),problem='实现效率提升30%')]:
        result=service.synthesize(build_resume_diagnostic_report(profile(),student_id='owner'),candidates=[bad])
        assert result['improvements']==[]
        assert result['improvements_status']=='invalid_output'


def test_real_advice_cannot_call_an_explicit_statistical_object_and_grouping_missing():
    root=Path(__file__).parent/'fixtures'
    captured=json.loads((root/'resume_diagnostic_scope_failure.json').read_text(encoding='utf8'))
    bad=json.loads((root/'resume_advice_scope_failure.json').read_text(encoding='utf8'))
    report=build_resume_diagnostic_report(captured['profile'],student_id='scope-test')
    report['status']='completed'
    result=service.synthesize(report,candidates=bad['items'])
    assert result['improvements']==[]
    assert result['improvements_status']=='invalid_output'
    assert result['status']=='partial'
    assert 'resume_expression_advice' in result['missing_sections']


def test_an_actually_unknown_statistical_object_can_still_be_clarified():
    p=profile();p['projects'][0]['desc']='处理资料，核对结果'
    advice=item();advice.update(problem='统计对象不清楚',questions=['统计的是哪些资料？'])
    result=service.synthesize(build_resume_diagnostic_report(p,student_id='owner'),candidates=[advice])
    assert result['improvements_status']=='ready' and result['improvements'][0]['problem']==advice['problem']


def test_well_expressed_resume_does_not_need_three_fake_problems():
    result=service.synthesize(build_resume_diagnostic_report(profile(),student_id='owner'),candidates=[])
    assert result['improvements_status']=='ready' and result['improvements']==[]


def test_partial_reading_and_neutral_wording_never_grade_ability():
    p=profile();p['_resume_confirmation']['source_evidence']['missing_pages']=[2]
    advice=item();advice['why']='补清核对规则，读者才能看懂重复记录如何处理'
    result=service.synthesize(build_resume_diagnostic_report(p,student_id='owner'),candidates=[advice])
    assert len(result['improvements'])==1
    published=result['improvements'][0]
    assert published['kind']=='incomplete_reading' and published['problem'].startswith('目前仅读取到')
    assert published['why']==advice['why']


@pytest.mark.parametrize('why',[
    '判断技术含量，避免被当成简单脚本',
    '读者无法判断该核对工作的逻辑严谨性与技术合理性，影响对SQL设计能力的理解。',
    '职责边界模糊会影响对全栈实践能力的判断。',
])
def test_assessment_jargon_is_rejected_not_replaced_with_a_generic_why(why):
    advice=item();advice['why']=why
    result=service.synthesize(build_resume_diagnostic_report(profile(),student_id='owner'),candidates=[advice])
    assert result['improvements']==[] and result['improvements_status']=='invalid_output'


def test_duplicate_fact_advice_cannot_silently_discard_a_different_question():
    first=item();second=item();second['problem']='核对规则尚未说明';second['questions']=['按什么条件判断重复？']
    result=service.synthesize(build_resume_diagnostic_report(profile(),student_id='owner'),candidates=[first,second])
    assert result['improvements']==[] and result['improvements_status']=='invalid_output'


def test_reading_question_schema_is_the_only_question_writer():
    from app.services.resume_diagnostic_report import _Synthesis
    schema=_Synthesis.model_json_schema()
    question=schema['$defs']['_ReadingQuestion']
    assert question['additionalProperties'] is False
    assert set(question['required'])=={'choice'}
    assert 'text' not in question['properties'] and 'why' not in question['properties']
    assert 'chat_with_metadata' not in service.synthesize.__code__.co_names


def test_plain_reading_reason_is_preserved_without_keyword_replacement():
    advice=item();advice['why']='用简单的话说明统计对象，可以避免读者误解这个数字。'
    result=service.synthesize(build_resume_diagnostic_report(profile(),student_id='owner'),candidates=[advice])
    assert result['improvements_status']=='ready' and result['improvements'][0]['why']==advice['why']


def test_registration_advice_reason_cannot_claim_actual_attendance():
    p=profile();p['projects'][0]['desc']='我统计各活动报名人数，核对重复报名。'
    advice=item();advice['why']='读者需要知道判重逻辑，才能理解最终统计人数是否反映真实参与人次或真实参与人数。'
    result=service.synthesize(build_resume_diagnostic_report(p,student_id='owner'),candidates=[advice])
    assert result['improvements']==[] and result['improvements_status']=='invalid_output'


@pytest.mark.parametrize('problem,question',[
    ('未说明是否独立完成全部前端与查询','SQL和Vue是否都由你独立完成？'),
    ('判重依据未说明','是按学号还是IP地址判断重复？'),
])
def test_advice_asks_actual_scope_without_independence_tests_or_unread_fields(problem,question):
    advice=item();advice.update(problem=problem,questions=[question])
    result=service.synthesize(build_resume_diagnostic_report(profile(),student_id='owner'),candidates=[advice])
    assert result['improvements']==[] and result['improvements_status']=='invalid_output'


def test_advice_can_ask_about_a_field_that_really_appears_in_this_experience():
    p=profile();p['projects'][0]['desc']='我按姓名与手机号核对重复报名。'
    advice=item();advice.update(problem='核对后怎样处理尚未说明',questions=['按姓名与手机号核对后，重复记录怎样处理？'])
    result=service.synthesize(build_resume_diagnostic_report(p,student_id='owner'),candidates=[advice])
    assert result['improvements_status']=='ready'
    assert result['improvements'][0]['questions']==advice['questions']


def test_conversion_preserves_fields_and_refuses_unmapped_or_missing_pages():
    converted=service.convert_profile(profile())
    assert converted['status']=='ready'
    assert converted['form']['projects'][0]['desc']=='使用Python整理公开数据'
    assert '公开数据分析' in converted['form']['sourceText']
    p=profile();p['research']=[{'title':'不得静默丢弃的研究经历'}]
    assert service.convert_profile(p)['status']=='mapping_incomplete'
    p=profile();p['_resume_confirmation']['source_evidence']['missing_pages']=[2]
    assert service.convert_profile(p)['status']=='mapping_incomplete'
    p=profile();p['skills']=[{'name':'Python','detail':'补充说明'}]
    assert service.convert_profile(p)['status']=='mapping_incomplete'


def test_report_to_draft_http_source_cas_and_no_formal_mutation(monkeypatch, workbench_isolated_mysql):
    from app.config import Config
    from app.services.job_publication import canonical_json, content_sha256
    mysql,_=workbench_isolated_mysql
    monkeypatch.setattr(Config,'DEMO_MODE',False)
    owner='improvement_owner'
    candidate=profile();candidate.pop('_resume_confirmation')
    confirmed=_seed_confirmed_resume(mysql,student_id=owner,confirmation_task_id='task_improve_current',candidate=candidate)
    report=service.synthesize(build_resume_diagnostic_report(confirmed,student_id=owner),candidates=[item()])
    mysql.execute('INSERT INTO resume_diagnostic_report(report_id,student_id,source_confirmation_task_id,status,report_json,content_sha256) VALUES (:rid,:sid,:tid,:status,:body,:sha)',
        {'rid':report['report_id'],'sid':owner,'tid':report['source_confirmation_task_id'],'status':report['status'],
         'body':canonical_json(report),'sha':content_sha256(report)},commit=True)
    original=mysql.fetch_one('SELECT * FROM student_profile WHERE student_id=:sid',{'sid':owner})
    identity={'principal_id':owner,'student_id':owner}
    server,thread,base=_start_profile_http(identity)
    path=base+'/api/profile/student/resume-draft'
    body={'report_id':report['report_id'],'improvement_id':report['improvements'][0]['id'],'expected_revision':0,'mode':'preview'}
    try:
        preview=requests.post(path+'/from-report',json=body)
        assert preview.status_code==200,preview.text
        assert preview.json()['data']['status']=='ready'
        assert mysql.fetch_one('SELECT COUNT(*) n FROM student_resume_draft')['n']==0
        start={**body,'mode':'start'}
        response=requests.post(path+'/from-report',json=start)
        assert response.status_code==200,response.text
        draft=response.json()['data'];assert draft['revision']==1
        draft['form']['projects'][0]['desc']='本人新增的真实草稿说明'
        save=requests.put(path,json={'form':draft['form'],'theme':draft['theme'],'expected_revision':1})
        assert save.status_code==200,save.text
        retry=requests.post(path+'/from-report',json=start)
        assert retry.status_code==200 and retry.json()['data']['revision']==2
        assert retry.json()['data']['form']['projects'][0]['desc']=='本人新增的真实草稿说明'
        assert requests.post(path+'/from-report',json={**start,'expected_revision':1}).status_code==409
        # No implicit replacement of an existing edited draft.
        assert requests.post(path+'/from-report',json={**start,'expected_revision':2}).status_code==409
        continued=requests.post(path+'/from-report',json={**body,'mode':'continue','expected_revision':2})
        assert continued.status_code==200,continued.text
        assert continued.json()['data']['guidance']['origin']=='existing_draft'
        assert continued.json()['data']['form']==draft['form']
        assert requests.post(path+'/from-report',json={**body,'student_id':'forged'}).status_code==400
        assert requests.post(path+'/from-report',json={**body,'improvement_id':'forged'}).status_code==409
        identity.update(principal_id='other',student_id='other')
        assert requests.post(path+'/from-report',json=body).status_code==404
        assert requests.get(path).json()['data']['revision']==0
        identity.clear()
        assert requests.post(path+'/from-report',json=body).status_code==401
        identity.update(principal_id=owner,student_id=owner)
        assert mysql.fetch_one('SELECT * FROM student_profile WHERE student_id=:sid',{'sid':owner})==original
        assert json.loads(mysql.fetch_one('SELECT report_json FROM resume_diagnostic_report')['report_json'])==report
        # A new confirmation invalidates advice, but never deletes personal edits.
        mysql.execute('DELETE FROM student_profile WHERE student_id=:sid',{'sid':owner},commit=True)
        _seed_confirmed_resume(mysql,student_id=owner,confirmation_task_id='task_improve_new',candidate=candidate)
        stale=requests.get(path).json()['data']
        assert stale['guidance']['status'] in {'stale','unavailable'}
        assert stale['form']==draft['form']
        assert requests.post(path+'/from-report',json={**body,'expected_revision':3,'mode':'replace'}).status_code==409
        assert requests.put(path,json={'form':draft['form'],'theme':draft['theme'],'expected_revision':3}).json()['data']['guidance']['status']!='current'
    finally:
        server.shutdown();server.server_close();thread.join(5)


def test_incomplete_conversion_and_changed_advice_leave_existing_draft(monkeypatch,workbench_isolated_mysql):
    from app.config import Config
    from app.services.resume_draft import save_draft
    from app.services.job_publication import canonical_json,content_sha256
    mysql,_=workbench_isolated_mysql
    monkeypatch.setattr(Config,'DEMO_MODE',False)
    owner='mapping_owner';candidate=profile();candidate.pop('_resume_confirmation')
    candidate['research']=[{'title':'原文件中的研究成果'}]
    p=_seed_confirmed_resume(mysql,student_id=owner,confirmation_task_id='mapping_current',candidate=candidate)
    report=service.synthesize(build_resume_diagnostic_report(p,student_id=owner),candidates=[item()])
    values={'rid':report['report_id'],'sid':owner,'tid':report['source_confirmation_task_id'],'status':report['status'],'body':canonical_json(report),'sha':content_sha256(report)}
    mysql.execute('INSERT INTO resume_diagnostic_report(report_id,student_id,source_confirmation_task_id,status,report_json,content_sha256) VALUES (:rid,:sid,:tid,:status,:body,:sha)',values,commit=True)
    save_draft(mysql,owner,{'form':{'name':'本人原有草稿'},'theme':'#F4514B','expected_revision':0})
    original=mysql.fetch_one('SELECT * FROM student_resume_draft')
    server,thread,base=_start_profile_http({'principal_id':owner,'student_id':owner})
    path=base+'/api/profile/student/resume-draft/from-report'
    body={'report_id':report['report_id'],'improvement_id':report['improvements'][0]['id'],'expected_revision':1,'mode':'preview'}
    try:
        preview=requests.post(path,json=body)
        assert preview.status_code==200 and preview.json()['data']['status']=='mapping_incomplete'
        assert '研究' in ' '.join(preview.json()['data']['unmapped_fields'])
        assert requests.post(path,json={**body,'mode':'replace'}).status_code==409
        assert mysql.fetch_one('SELECT * FROM student_resume_draft')==original
        # Re-generation changes the advisory identity/hash: old buttons cannot write.
        report['improvements'][0]['id']='updated_advice'
        mysql.execute('UPDATE resume_diagnostic_report SET report_json=:body,content_sha256=:sha WHERE report_id=:rid',
            {'body':canonical_json(report),'sha':content_sha256(report),'rid':report['report_id']},commit=True)
        assert requests.post(path,json={**body,'mode':'continue'}).status_code==409
        assert mysql.fetch_one('SELECT * FROM student_resume_draft')==original
    finally:
        server.shutdown();server.server_close();thread.join(5)
