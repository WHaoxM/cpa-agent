"""Drafts must never masquerade as confirmed resume evidence."""
import pytest
import json
import requests
from backend.tests.test_student_workbench_http_mysql import workbench_isolated_mysql, _start_profile_http


def test_draft_form_rejects_owner_and_preserves_empty_experience():
    from app.services.resume_draft import validate_form, render_text
    form = validate_form({"name": "林知夏", "projects": [{"name": "课程项目", "role": "", "desc": "<p>实现查询接口</p>"}]})
    assert form["grade"] == "" and form["workExps"] == []
    assert "实现查询接口" in render_text(form)
    assert "<p>" not in render_text(form) and "未填写" not in render_text(form)
    with pytest.raises(ValueError):
        validate_form({"owner": "other"})


def test_preview_uses_existing_extractor_without_entity_linking(monkeypatch):
    from app.services import resume_draft
    from app.pipeline.entity_extractor import resume_structure
    observed = []
    def extract(client, text, **kwargs):
        observed.append((text, kwargs))
        return {"name": "林知夏", "skills": ["Python"], "source_field_issues": [{"field": "grade"}],
                "projects": [{"name": "课程项目", "desc": "编写查询接口"}]}
    monkeypatch.setattr(resume_structure, "extract_canonical_profile", extract)
    result = resume_draft.parse_preview("林知夏使用 Python 完成课程项目，编写查询接口。", client=object())
    assert observed[0][1] == {"link_entities": False}
    assert result["form"]["skills"] == ["Python"] and result["form"]["grade"] == ""
    assert result["warnings"]


def test_preview_failure_and_partial_are_not_complete(monkeypatch):
    from app.services import resume_draft
    from app.pipeline.entity_extractor import resume_structure
    monkeypatch.setattr(resume_structure, "extract_canonical_profile", lambda *_a, **_k: {"error": "timeout"})
    with pytest.raises(resume_draft.PreviewUnavailable):
        resume_draft.parse_preview("学生说明一段课程项目经历，内容足够供读取。", client=object())
    monkeypatch.setattr(resume_structure, "extract_canonical_profile", lambda *_a, **_k: {
        "skills": ["Python"], "error": "partial_chunk_extraction", "failed_offsets": [100]})
    assert resume_draft.parse_preview("学生说明一段课程项目经历，内容足够供读取。", client=object())["status"] == "partial"
    monkeypatch.setattr(resume_structure, "extract_canonical_profile", lambda *_a, **_k: {
        "skills": ["Python"], "structure_calls": [{"finish_reason": "length"}]})
    assert resume_draft.parse_preview("学生说明一段课程项目经历，内容足够供读取。", client=object())["status"] == "partial"


@pytest.mark.parametrize('payload', [None, [], {'student_id':'other'}, {'skills':['x'] * 101}, {'projects':[{'owner':'other'}]}])
def test_invalid_draft_form_is_rejected(payload):
    from app.services.resume_draft import validate_form
    with pytest.raises(ValueError):
        validate_form(payload)


def test_draft_concurrent_updates_only_one_revision_wins(monkeypatch, workbench_isolated_mysql):
    from app.services.resume_draft import read_draft, save_draft, DraftConflict
    from concurrent.futures import ThreadPoolExecutor
    mysql, _ = workbench_isolated_mysql
    payload = {'form':{'name':'林知夏'}, 'theme':'#F4514B', 'expected_revision':0}
    save_draft(mysql, 'cas_owner', payload)
    def write(name):
        try:
            return save_draft(mysql, 'cas_owner', {**payload, 'form':{'name':name}, 'expected_revision':1})['revision']
        except DraftConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ['窗口一', '窗口二']))
    assert sorted(results, key=str) == [2, 'conflict']
    assert read_draft(mysql, 'cas_owner')['revision'] == 2


def test_save_acknowledges_its_own_revision_even_if_another_tab_writes_after_commit(monkeypatch,workbench_isolated_mysql):
    from app.services import resume_draft
    mysql,_=workbench_isolated_mysql
    original=resume_draft._with_guidance_status
    raced=[]
    def race(mysql,sid,result):
        if not raced:
            raced.append(True)
            resume_draft.save_draft(mysql,sid,{'form':{'name':'另一个窗口'},'expected_revision':1,'theme':'#F4514B'})
        return original(mysql,sid,result)
    monkeypatch.setattr(resume_draft,'_with_guidance_status',race)
    first=resume_draft.save_draft(mysql,'ack_owner',{'form':{'name':'本次输入'},'expected_revision':0,'theme':'#F4514B'})
    assert first['revision']==1 and first['form']['name']=='本次输入'
    assert resume_draft.read_draft(mysql,'ack_owner')['revision']==2
    assert resume_draft.read_draft(mysql,'ack_owner')['form']['name']=='另一个窗口'


def test_draft_and_fill_preserve_existing_confirmed_profile(monkeypatch, workbench_isolated_mysql):
    from app.services.resume_draft import save_draft, parse_preview
    from app.pipeline.entity_extractor import resume_structure
    from backend.tests.test_student_workbench_http_mysql import _seed_confirmed_resume
    mysql, _ = workbench_isolated_mysql
    _seed_confirmed_resume(mysql, student_id='confirmed_draft_owner', confirmation_task_id='task_oldconfirmation',
                           candidate={'name':'原简历姓名', 'skills':['Java']})
    before = mysql.fetch_one('SELECT * FROM student_profile WHERE student_id=:sid', {'sid':'confirmed_draft_owner'})
    task_before = mysql.fetch_one('SELECT * FROM pipeline_task WHERE task_id=:tid', {'tid':'task_oldconfirmation'})
    save_draft(mysql, 'confirmed_draft_owner', {'form':{'name':'新草稿姓名'}, 'theme':'#F4514B','expected_revision':0})
    monkeypatch.setattr(resume_structure, 'extract_canonical_profile', lambda *_a, **_k: {'name':'建议姓名','skills':['Python']})
    parse_preview('这是一段新项目说明，Python 开发查询接口并完成课程演示。', client=object())
    assert mysql.fetch_one('SELECT * FROM student_profile WHERE student_id=:sid', {'sid':'confirmed_draft_owner'}) == before
    assert mysql.fetch_one('SELECT * FROM pipeline_task WHERE task_id=:tid', {'tid':'task_oldconfirmation'}) == task_before


def test_draft_http_persistence_conflicts_and_owner_boundary(monkeypatch, workbench_isolated_mysql):
    from app.config import Config
    from app.pipeline.entity_extractor import resume_structure
    mysql, _ = workbench_isolated_mysql
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    identity = {"principal_id": "draft_owner", "student_id": "draft_owner"}
    server, thread, base = _start_profile_http(identity)
    try:
        path = base + '/api/profile/student/resume-draft'
        assert requests.get(path).json()['data']['revision'] == 0
        payload = {"expected_revision": 0, "form": {"name": "林知夏", "summary": "本人完成课程查询接口的设计与开发"}, "theme": "#F4514B"}
        saved = requests.put(path, json=payload)
        assert saved.status_code == 200, saved.text
        assert saved.json()['data']['revision'] == 1
        assert requests.put(path, json=payload).status_code == 409
        payload['expected_revision'] = 1
        payload['form']['owner'] = 'other'
        assert requests.put(path, json=payload).status_code == 400
        payload['form'].pop('owner')
        assert requests.put(path, json={**payload, 'student_id':'other'}).status_code == 400
        assert requests.get(path + '?student_id=draft_owner').status_code == 400
        assert requests.get(path).json()['data']['revision'] == 1
        identity.update(principal_id='draft_other', student_id='draft_other')
        assert requests.get(path).json()['data']['revision'] == 0
        assert requests.put(path, json=payload).status_code == 409
        identity.clear()
        assert requests.get(path).status_code == 401
        assert requests.put(path, json=payload).status_code == 401
        assert requests.post(path+'/parse-preview', json={'text':'x'*30}).status_code == 401
        identity.update(principal_id='draft_owner', student_id='draft_owner')
        monkeypatch.setattr(resume_structure, 'extract_canonical_profile', lambda *_a, **_k: {'name':'林知夏', 'skills':['Python']})
        preview = requests.post(path+'/parse-preview', json={'text':'林知夏使用 Python 开发课程查询接口，完成接口测试。'})
        assert preview.status_code == 200, preview.text
        assert preview.json()['data']['status'] == 'ready'
        monkeypatch.setattr(resume_structure, 'extract_canonical_profile', lambda *_a, **_k: {
            'skills':['Python'], 'structure_calls':[{'finish_reason':'length'}]})
        limited = requests.post(path+'/parse-preview', json={'text':'林知夏使用 Python 开发课程查询接口，完成接口测试。'})
        assert limited.status_code == 200 and limited.json()['data']['status'] == 'partial'
        assert any('部分' in warning for warning in limited.json()['data']['warnings'])
        monkeypatch.setattr(resume_structure, 'extract_canonical_profile', lambda *_a, **_k: {'error':'model_timeout'})
        assert requests.post(path+'/parse-preview', json={'text':'林知夏使用 Python 开发课程查询接口，完成接口测试。'}).status_code == 503
        assert requests.post(path+'/parse-preview', json={'text':'x'*30,'student_id':'other'}).status_code == 400
        assert mysql.fetch_one('SELECT COUNT(*) AS n FROM student_profile')['n'] == 0
        assert mysql.fetch_one('SELECT COUNT(*) AS n FROM pipeline_task')['n'] == 0
        row = mysql.fetch_one('SELECT student_id,revision,form_json FROM student_resume_draft')
        assert row['student_id'] == 'draft_owner' and row['revision'] == 1
        assert json.loads(row['form_json'])['name'] == '林知夏'
    finally:
        server.shutdown(); server.server_close(); thread.join(5)


def test_legacy_fill_route_writes_profile_without_structured_fields(monkeypatch, workbench_isolated_mysql):
    """Reproduce why the editor must not use the legacy text endpoint."""
    from flask import Flask, g
    from app.api import pipeline_ext_bp
    from app.config import Config
    mysql, _ = workbench_isolated_mysql
    monkeypatch.setattr(Config, 'DEMO_MODE', False)
    app = Flask(__name__)
    app.register_blueprint(pipeline_ext_bp, url_prefix='/api/pipeline/ext')
    @app.before_request
    def auth():
        g.cpa_principal = {'principal_id':'legacy_draft', 'student_id':'legacy_draft'}
    from werkzeug.serving import make_server
    import threading
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        response = requests.post(f'http://127.0.0.1:{server.server_port}/api/pipeline/ext/parse/text', json={'text':'林知夏\n使用 Python 开发课程项目，完成查询接口设计与测试。'})
        assert response.status_code == 200
        assert 'skills' not in response.json()['data']
        assert mysql.fetch_one('SELECT student_id FROM student_profile')['student_id'] == 'legacy_draft'
    finally:
        server.shutdown(); server.server_close(); thread.join(5)
