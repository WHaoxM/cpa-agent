"""Owner-only exploration submissions; neither model nor browser selects an owner."""
from flask import g, jsonify, request

from . import career_bp
from ._helpers import ScopeError, resolve_request_scope, scope_error_response, safe_error
from ..services.career_exploration import ExplorationService, ExplorationConflict
from ..services.db import get_mysql_client


def _respond(action, allowed_query=()):
    try:
        scope = resolve_request_scope()
        if getattr(g, 'cpa_admin', False):
            raise ScopeError('此入口仅供学生进行本人方向探索', status_code=403)
        if set(request.args) - set(allowed_query) or any(len(request.args.getlist(k)) != 1 for k in request.args):
            raise ValueError('探索接口不接受客户端指定身份或来源')
        data = action(ExplorationService(get_mysql_client()), scope.student_id)
        response = jsonify(success=True, data=data)
        response.headers['Cache-Control'] = 'no-store'
        return response
    except ScopeError as exc:
        return scope_error_response(exc)
    except LookupError:
        return jsonify(success=False, error='探索记录不存在或不可访问'), 404
    except ExplorationConflict as exc:
        return jsonify(success=False, code='exploration_conflict', error=str(exc)), 409
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    except Exception as exc:
        return jsonify(safe_error('职业方向探索', exc, message='本次操作暂未完成，请保留输入并重新读取记录后核对；不会显示为已保存')), 503


@career_bp.route('/explorations', methods=['GET', 'POST'])
def explorations():
    return _respond(lambda service, sid: service.list(sid, request.args.get('role')) if request.method == 'GET'
                    else service.create(sid, request.get_json(silent=True)),
                    allowed_query=('role',) if request.method == 'GET' else ())


@career_bp.route('/explorations/<eid>', methods=['GET', 'PATCH'])
def exploration_detail(eid):
    return _respond(lambda service, sid: service.get(eid, sid) if request.method == 'GET'
                    else service.patch(eid, sid, request.get_json(silent=True)))


@career_bp.route('/explorations/<eid>/rounds', methods=['POST'])
def exploration_round(eid):
    return _respond(lambda service, sid: service.start_round(eid, sid, request.get_json(silent=True)))


@career_bp.route('/explorations/<eid>/rounds/<rid>/restart', methods=['POST'])
def exploration_restart(eid, rid):
    return _respond(lambda service, sid: service.restart_unplanned(eid, rid, sid, request.get_json(silent=True)))


@career_bp.route('/explorations/<eid>/session', methods=['POST'])
def exploration_session(eid):
    def connect(service, sid):
        if request.get_json(silent=True) not in (None, {}):
            raise ValueError('会话由服务端绑定')
        service._session(service._owner(eid, sid), sid)
        return service.get(eid, sid)
    return _respond(connect)


@career_bp.route('/explorations/<eid>/analysis-turns', methods=['POST'])
def exploration_analysis_turn(eid):
    def submit(service, sid):
        from ._helpers import resolve_request_scope
        from ..services.career_exploration_analysis import AnalysisService
        from ..services.agent_runtime.agent_run_store import AgentRunRepository
        from ..services.agent_runtime.agent_run_dispatch import AgentRunDispatchStore
        from ..services.agent_runtime.agent_run_jobs import dispatch_agent_run
        turn = AnalysisService(service.mysql).submit(eid, sid, request.get_json(silent=True),
            request.headers.get('Idempotency-Key'), resolve_request_scope().principal)
        repository = AgentRunRepository(service.mysql)
        # A transport failure does not erase the committed turn. The durable
        # outbox and Beat recover it; the page must show queued, not success.
        try:
            dispatch_agent_run(turn['run_id'], repository=repository,
                               dispatch_store=AgentRunDispatchStore(repository))
        except Exception:
            from flask import current_app
            current_app.logger.warning('Exploration analysis dispatch deferred to outbox replay')
        return service.get(eid, sid)
    return _respond(submit)


@career_bp.route('/explorations/<eid>/rounds/<rid>/plan', methods=['POST'])
def exploration_plan(eid, rid):
    def generate(service, sid):
        if request.get_json(silent=True) not in (None, {}):
            raise ValueError('计划依据由服务端读取，不接受客户端替换')
        return service.generate_plan(eid, rid, sid)
    return _respond(generate)


@career_bp.route('/explorations/<eid>/rounds/<rid>/review', methods=['POST'])
def exploration_review(eid, rid):
    return _respond(lambda service, sid: service.generate_review(eid, rid, sid, request.get_json(silent=True)))


@career_bp.route('/explorations/<eid>/rounds/<rid>/decision', methods=['PUT'])
def exploration_decision(eid, rid):
    return _respond(lambda service, sid: service.decide(eid, rid, sid, request.get_json(silent=True)))
