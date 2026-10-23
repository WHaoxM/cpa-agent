"""Authenticated owner draft endpoints, isolated from formal resume writes."""
from flask import jsonify, request, g
from . import profile_bp
from ._helpers import resolve_request_scope, ScopeError, scope_error_response, safe_error
from ..services import resume_draft as service
from ..services.db import get_mysql_client


def _scope():
    scope = resolve_request_scope()
    if getattr(g, "cpa_admin", False):
        raise ScopeError("此入口仅供学生整理本人简历", status_code=403)
    return scope


@profile_bp.route('/student/resume-draft', methods=['GET', 'PUT'])
def student_resume_draft():
    try:
        scope = _scope()
        if request.args:
            raise ValueError("不接受客户端指定资料归属")
        if request.method == 'GET':
            data = service.read_draft(get_mysql_client(), scope.student_id)
        else:
            data = service.save_draft(get_mysql_client(), scope.student_id, request.get_json(silent=True))
        response = jsonify(success=True, data=data)
        response.headers['Cache-Control'] = 'no-store'
        return response
    except ScopeError as exc:
        return scope_error_response(exc)
    except service.DraftConflict as exc:
        return jsonify(success=False, error=str(exc), code="draft_revision_conflict"), 409
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    except Exception as exc:
        return jsonify(safe_error("简历草稿", exc, message="草稿暂不可读写，请保留输入后重试")), 503


@profile_bp.route('/student/resume-draft/parse-preview', methods=['POST'])
def student_resume_draft_preview():
    try:
        _scope()
        payload = request.get_json(silent=True)
        if request.args or not isinstance(payload, dict) or set(payload) != {"text"}:
            raise ValueError("只允许提交需要填充的文字")
        return jsonify(success=True, data=service.parse_preview(payload["text"]))
    except ScopeError as exc:
        return scope_error_response(exc)
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    except Exception as exc:
        return jsonify(safe_error("草稿智能填充", exc, message="未能取得填充建议；原表单未改变，请重试或手动填写")), 503


@profile_bp.route('/student/resume-draft/from-report', methods=['POST'])
def resume_draft_from_report():
    from ..services.resume_diagnostic_report import ResumeReportStale, ResumeReportNeedsInput
    try:
        scope=_scope()
        if request.args:raise ValueError('不接受客户端指定资料归属')
        data=service.from_report(get_mysql_client(),scope.student_id,request.get_json(silent=True))
        response=jsonify(success=True,data=data);response.headers['Cache-Control']='no-store';return response
    except ScopeError as exc:return scope_error_response(exc)
    except (service.DraftConflict,ResumeReportStale,ResumeReportNeedsInput) as exc:
        return jsonify(success=False,error=str(exc)),409
    except LookupError:return jsonify(success=False,error='本人当前诊断不存在'),404
    except ValueError as exc:return jsonify(success=False,error=str(exc)),400
    except Exception as exc:return jsonify(safe_error('诊断草稿承接',exc,message='草稿未能准备，请重新读取后重试')),503
