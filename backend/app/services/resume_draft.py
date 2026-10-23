"""A single owner draft; extraction is a read-only adapter, not confirmation."""
import json
import re
from html import escape
from html.parser import HTMLParser

from sqlalchemy import text

STRINGS = ("name", "phone", "email", "school", "major", "grade", "targetRole", "gpa", "summary", "selfEval", "sourceText")
ROWS = {"projects": ("name", "role", "desc"), "workExps": ("company", "position", "period", "desc"),
        "educations": ("school", "major", "degree", "period", "gpa"), "honors": ("type", "label")}


class DraftConflict(ValueError):
    pass


class PreviewUnavailable(RuntimeError):
    pass


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)
    def handle_endtag(self, tag):
        if tag in {"p", "div", "li", "br"}:
            self.parts.append("\n")


def plain(value):
    parser = PlainText()
    parser.feed(value)
    return " ".join("".join(parser.parts).split())


def validate_form(value):
    if not isinstance(value, dict) or set(value) - set(STRINGS) - set(ROWS) - {"skills"}:
        raise ValueError("草稿字段不正确")
    def string(v):
        if not isinstance(v, str) or len(v) > 20000:
            raise ValueError("草稿内容过长或格式不正确")
        return v
    result = {key: string(value.get(key, "")) for key in STRINGS}
    for key, fields in ROWS.items():
        rows = value.get(key, [])
        if not isinstance(rows, list) or len(rows) > 40:
            raise ValueError("经历条目过多或格式不正确")
        result[key] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) - set(fields):
                raise ValueError("经历条目字段不正确")
            item = {field: string(row.get(field, "")) for field in fields}
            if key == "honors" and item["type"] not in {"cert", "intern", "award"}:
                raise ValueError("荣誉类型不正确")
            result[key].append(item)
    skills = value.get("skills", [])
    if not isinstance(skills, list) or len(skills) > 100:
        raise ValueError("技能格式不正确")
    result["skills"] = list(dict.fromkeys(string(s) for s in skills))
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 60000:
        raise ValueError("草稿过长，请精简后保存")
    return result


def render_text(form):
    lines = []
    for key in ("name", "school", "major", "grade", "phone", "email"):
        if form[key].strip():
            lines.append(form[key].strip())
    for key, label in (("targetRole", "意向方向"), ("gpa", "GPA"), ("summary", "个人总结")):
        if form[key].strip():
            lines.append(f"{label}：{form[key].strip()}")
    if form["skills"]:
        lines.append("专业技能：" + "、".join(form["skills"]))
    for key, label in (("projects", "项目经历"), ("workExps", "工作经验"), ("educations", "教育经历"), ("honors", "实习与荣誉")):
        for row in form[key]:
            values = [plain(row[field]) if field == "desc" else row[field].strip()
                      for field in ROWS[key] if field != "type" and row[field].strip()]
            if values:
                lines.append(label + "：" + " | ".join(values))
    if form["selfEval"]:
        lines.append("自我评价：" + plain(form["selfEval"]))
    return "\n".join(lines)


def _project(row):
    form = json.loads(row["form_json"]) if isinstance(row["form_json"], str) else row["form_json"]
    guidance=row.get('guidance_json')
    if isinstance(guidance,str):guidance=json.loads(guidance)
    return {"revision": row["revision"], "form": form, "theme": row["theme"], 'guidance':guidance,
            "updated_at": row["updated_at"].isoformat(), "text": render_text(form)}


def read_draft(mysql, student_id):
    row = mysql.fetch_one("SELECT revision,form_json,theme,updated_at,guidance_json FROM student_resume_draft WHERE student_id=:sid", {"sid": student_id})
    result=_project(row) if row else {"revision": 0, "form": validate_form({}), "theme": "#F4514B", "updated_at": None, "text": "",'guidance':None}
    return _with_guidance_status(mysql, student_id, result)


def _with_guidance_status(mysql, student_id, result):
    if result['guidance']:
        from .resume_diagnostic_report import _require_current
        try:
            profile=_require_current(mysql,student_id)
            current=mysql.fetch_one('SELECT content_sha256 FROM resume_diagnostic_report WHERE student_id=:sid AND report_id=:rid',
                {'sid':student_id,'rid':result['guidance']['report_id']})
            result['guidance']['status']='current' if (profile['_resume_confirmation']['task_id']==result['guidance']['source_confirmation_task_id']
                and current and current['content_sha256']==result['guidance']['report_sha256']) else 'stale'
        except Exception:
            result['guidance']['status']='unavailable'
    return result


def save_draft(mysql, student_id, payload):
    if not isinstance(payload, dict) or set(payload) != {"form", "expected_revision", "theme"}:
        raise ValueError("只允许保存草稿内容、主题色和预期版本")
    revision = payload["expected_revision"]
    if type(revision) is not int or revision < 0:
        raise ValueError("草稿版本不正确")
    theme = payload["theme"]
    if not isinstance(theme, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", theme):
        raise ValueError("主题色不正确")
    form = validate_form(payload["form"])
    params = {"sid": student_id, "rev": revision, "form": json.dumps(form, ensure_ascii=False), "theme": theme}
    with mysql.engine.begin() as conn:
        if revision == 0:
            written = conn.execute(text("INSERT IGNORE INTO student_resume_draft(student_id,revision,form_json,theme,updated_at) VALUES (:sid,1,:form,:theme,NOW())"), params).rowcount
        else:
            written = conn.execute(text("UPDATE student_resume_draft SET revision=revision+1,form_json=:form,theme=:theme,updated_at=NOW() WHERE student_id=:sid AND revision=:rev"), params).rowcount
        if written != 1:
            raise DraftConflict("草稿已在其他页面更新；本页输入已保留，请重新载入后核对")
        row = conn.execute(text("SELECT revision,form_json,theme,updated_at,guidance_json FROM student_resume_draft WHERE student_id=:sid"), {"sid": student_id}).mappings().one()
    # Keep this write's transaction snapshot. A post-commit row re-read could
    # return another tab's revision and incorrectly authorize overwriting it.
    return _with_guidance_status(mysql, student_id, _project(row))


def from_report(mysql, student_id, payload):
    from .resume_diagnostic_report import read_resume_diagnostic, _require_current, ResumeReportStale
    from .resume_improvements import convert_profile
    from .pipeline_events import ConfirmationTransaction
    from .pipeline import require_confirmed_profile
    if not isinstance(payload,dict) or set(payload)!={'report_id','improvement_id','expected_revision','mode'}:
        raise ValueError('只允许提供报告、建议、草稿版本和操作方式')
    if (not isinstance(payload['report_id'],str) or not isinstance(payload['improvement_id'],str)
            or type(payload['expected_revision']) is not int or payload['expected_revision']<0
            or not isinstance(payload['mode'],str)
            or payload['mode'] not in {'preview','start','continue','replace'}):
        raise ValueError('草稿承接参数不正确')
    report=read_resume_diagnostic(mysql,student_id=student_id,report_id=payload['report_id'])
    advice=next((a for a in report.get('improvements',[]) if a['id']==payload['improvement_id']),None)
    if not advice or report.get('improvements_status')!='ready':
        raise DraftConflict('该建议已更新或尚不可用，请重新读取诊断')
    profile=_require_current(mysql,student_id)
    source_row=mysql.fetch_one('SELECT content_sha256 FROM resume_diagnostic_report WHERE student_id=:sid AND report_id=:rid',
                              {'sid':student_id,'rid':report['report_id']})
    converted=convert_profile(profile)
    draft=read_draft(mysql,student_id)
    context={'report_id':report['report_id'],'report_sha256':source_row['content_sha256'],
             'source_confirmation_task_id':report['source_confirmation_task_id'],'advice':advice,
             'origin':'existing_draft' if payload['mode']=='continue' else 'confirmed_resume','status':'current'}
    if payload['mode']=='preview':
        return {**converted,'draft_revision':draft['revision'],'has_existing_draft':bool(draft['text']), 'guidance':context}
    if payload['mode']!='continue' and converted['status']!='ready':
        raise DraftConflict('部分资料不能完整带入制作页；原草稿未改变，请修改原文件后重新上传')
    params={'sid':student_id,'rev':payload['expected_revision'],'rid':report['report_id']}
    with mysql.engine.begin() as conn:
        conn.execute(text('SELECT student_id FROM student_profile WHERE student_id=:sid FOR UPDATE'),params).first()
        latest=require_confirmed_profile(ConfirmationTransaction(conn),student_id)
        row_report=conn.execute(text('SELECT content_sha256 FROM resume_diagnostic_report WHERE student_id=:sid AND report_id=:rid FOR UPDATE'),params).mappings().first()
        if latest['_resume_confirmation']['task_id']!=context['source_confirmation_task_id'] or not row_report or row_report['content_sha256']!=context['report_sha256']:
            raise ResumeReportStale('诊断来源已变化，请重新读取；草稿未改变')
        row=conn.execute(text('SELECT revision,form_json,theme,updated_at,guidance_json FROM student_resume_draft WHERE student_id=:sid FOR UPDATE'),params).mappings().first()
        old=_project(row) if row else None
        # A response-loss retry must return the current draft, not reset edits.
        if old and old.get('guidance'):
            previous=old['guidance']
            if (previous.get('report_sha256')==context['report_sha256'] and previous.get('advice',{}).get('id')==advice['id']
                    and previous.get('initialization_revision')==params['rev']+1):
                return old
        if (row['revision'] if row else 0)!=params['rev']:
            raise DraftConflict('草稿已在其他页面更新，请先读取并保留原稿')
        if payload['mode']=='start' and old and old['text']:
            raise DraftConflict('已有草稿，必须选择继续原稿或明确确认替换')
        if payload['mode']=='continue' and not old:
            raise DraftConflict('没有可继续的草稿，请先预览当前资料')
        context['initialization_revision']=params['rev']+1
        form=old['form'] if payload['mode']=='continue' else converted['form']
        params.update(form=json.dumps(form,ensure_ascii=False),theme=old['theme'] if old else '#F4514B',
                      context=json.dumps(context,ensure_ascii=False))
        if row:
            conn.execute(text('UPDATE student_resume_draft SET revision=revision+1,form_json=:form,theme=:theme,guidance_json=:context,updated_at=NOW() WHERE student_id=:sid AND revision=:rev'),params)
        else:
            conn.execute(text('INSERT INTO student_resume_draft(student_id,revision,form_json,theme,guidance_json,updated_at) VALUES (:sid,1,:form,:theme,:context,NOW())'),params)
        saved=conn.execute(text('SELECT revision,form_json,theme,updated_at,guidance_json FROM student_resume_draft WHERE student_id=:sid'),params).mappings().one()
        return _project(saved)


def parse_preview(raw_text, *, client=None):
    if not isinstance(raw_text, str) or not 20 <= len(raw_text.strip()) <= 20000:
        raise ValueError("请粘贴 20–20000 字的经历文字；超出范围请分段整理，不会自动截断")
    from ..pipeline.entity_extractor.resume_structure import extract_canonical_profile
    if client is None:
        from ..utils.llm_client import get_llm_client
        client = get_llm_client()
    profile = extract_canonical_profile(client, raw_text, link_entities=False)
    form = validate_form({})
    for source, target in (("name", "name"), ("phone", "phone"), ("email", "email"), ("school", "school"),
                           ("major", "major"), ("grade", "grade"), ("target_role", "targetRole"), ("gpa", "gpa")):
        if profile.get(source) is not None:
            form[target] = str(profile[source])
    form["skills"] = [s if isinstance(s, str) else s.get("name", "") for s in profile.get("skills", [])]
    if profile.get('degree'):
        form['educations'] = [{'school': form['school'], 'major': form['major'], 'degree': str(profile['degree']), 'period':'', 'gpa':form['gpa']}]
    # Model output is plain text, never trusted HTML for the rich-text preview.
    form["projects"] = [{"name": str(p.get("name", "")), "role": str(p.get("role", "")),
                         "desc": escape(str(p.get("desc", "")))} for p in profile.get("projects", [])]
    form["honors"] = [{"type": "cert", "label": str(c)} for c in profile.get("certificates", [])]
    form["honors"] += [{"type": "award", "label": " ".join(str(c.get(k, "")) for k in ("name", "award"))}
                       for c in profile.get("competitions", [])]
    # The canonical schema has no lossless work/education chronology mapping.
    warnings = ["建议仅供核对；原文仍保留在上方，请检查日期、教育和工作经历等未自动填入的内容。"]
    if profile.get("source_field_issues") or profile.get("semantic_fact_issues"):
        warnings.append("部分信息缺少明确原文依据，未作为完整事实填入；请对照原文核对。")
    has_fields = any(form[k] for k in STRINGS) or any(form[k] for k in ROWS) or form["skills"]
    if not has_fields:
        raise PreviewUnavailable("暂时无法从文字取得填充建议；原表单未改变，请稍后重试或手动填写")
    partial = bool(profile.get("error") or profile.get("failed_offsets") or any(
        call.get('finish_reason') in {'length', 'content_filter'}
        for call in (profile.get('structure_calls') or [])))
    if partial:
        warnings.append("本次只取得部分解析结果，不能认为已完整读取；可保留原文后重试。")
    return {"status": "partial" if partial else "ready", "form": validate_form(form), "warnings": warnings}
