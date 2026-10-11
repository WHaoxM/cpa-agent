"""
Pipeline_ext Agent 子模块

Agent 对话历史存储与统计 API (3 路由) + 数据库健康检查 (1 路由):
- POST /agent/session/save           保存对话历史
- GET  /agent/stats                  获取工具执行统计
- GET  /agent/session/<id>/messages  获取对话消息历史
- GET  /db/health                    数据库健康检查 (混杂路由, 原位于 Agent 区)

依赖: ..services.db (MySQL/Neo4j/Milvus/Redis)
"""

import time
import uuid

from flask import request, jsonify

from ._helpers import (
    ScopeError, clamp_pagination, require_owner, resolve_request_scope,
    safe_error, scope_error_response,
)
from .pipeline_ext_common import pipeline_ext_bp, logger


@pipeline_ext_bp.route('/agent/session/save', methods=['POST'])
def save_agent_session():
    """
    Agent: 保存对话历史

    Body:
        {
            "session_id": "sess_001",
            "student_id": "stu_001",
            "title": "职业规划咨询",
            "messages": [
                {"role": "user", "content": "我想了解Python后端发展"},
                {"role": "assistant", "content": "Python后端是个好方向..."}
            ]
        }
    """
    data = request.get_json() or {}

    session_id = data.get('session_id', '')
    title = data.get('title', '')
    messages = data.get('messages', [])

    if not session_id or not messages:
        return jsonify({
            "success": False,
            "error": "session_id 和 messages 必填"
        }), 400

    try:
        scope = resolve_request_scope()
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        conv_sql = """
        INSERT IGNORE INTO conversation (session_id, student_id, title, status, message_count, updated_at)
        VALUES (:session_id, :student_id, :title, 'active', 0, NOW())
        """
        mysql.execute_write(conv_sql, {
            "session_id": session_id,
            "student_id": scope.student_id,
            "title": title or f"对话 {session_id}",
        })
        owner_rows = mysql.execute(
            "SELECT student_id FROM conversation WHERE session_id=:session_id LIMIT 1",
            {"session_id": session_id},
        )
        require_owner(owner_rows[0].get("student_id") if owner_rows else None, scope, resource="会话")

        msg_sql = """
        INSERT INTO conversation_message (session_id, message_id, role, content, created_at)
        VALUES (:session_id, :message_id, :role, :content, FROM_UNIXTIME(:timestamp))
        """
        message_ids = []
        for msg in messages:
            msg_id = f"{session_id}_{uuid.uuid4().hex}"
            mysql.execute_write(msg_sql, {
                "session_id": session_id,
                "message_id": msg_id,
                "role": msg.get('role', 'user'),
                "content": msg.get('content', ''),
                "timestamp": int(time.time())
            })
            message_ids.append(msg_id)

        placeholders = ", ".join(f":mid_{i}" for i in range(len(message_ids)))
        verified = mysql.execute(
            "SELECT COUNT(*) AS saved FROM conversation_message cm "
            "JOIN conversation c ON c.session_id=cm.session_id "
            f"WHERE c.session_id=:session_id AND c.student_id=:student_id AND cm.message_id IN ({placeholders})",
            {"session_id": session_id, "student_id": scope.student_id,
             **{f"mid_{i}": mid for i, mid in enumerate(message_ids)}},
        )
        if not verified or int(verified[0].get("saved") or 0) != len(message_ids):
            return jsonify({"success": False, "error": "对话消息写入后未能核验"}), 500
        mysql.execute_write(
            "UPDATE conversation SET message_count=(SELECT COUNT(*) FROM conversation_message "
            "WHERE session_id=:session_id) WHERE session_id=:session_id AND student_id=:student_id",
            {"session_id": session_id, "student_id": scope.student_id},
        )

        return jsonify({
            "success": True,
            "data": {
                "session_id": session_id,
                "messages_saved": len(messages)
            }
        })

    except ScopeError as error:
        return scope_error_response(error)
    except Exception as e:
        return jsonify(safe_error("保存对话历史", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/agent/stats', methods=['GET'])
def get_agent_stats():
    """
    Agent: 获取工具执行统计

    Query:
        ?session_id=xxx  (可选)
    """
    session_id = request.args.get('session_id', '')

    try:
        scope = resolve_request_scope()
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        if session_id:
            owner = mysql.execute(
                "SELECT student_id FROM conversation WHERE session_id=:session_id LIMIT 1",
                {"session_id": session_id},
            )
            if not owner:
                return jsonify({"success": False, "error": "会话不存在"}), 404
            require_owner(owner[0].get("student_id"), scope, resource="会话")
            sql = """
            SELECT role, COUNT(*) as count,
                   AVG(LENGTH(content)) as avg_content_length
            FROM conversation_message
            WHERE session_id = :session_id
            GROUP BY role
            """
            rows = mysql.fetch_all(sql, {"session_id": session_id})
        else:
            sql = """
            SELECT 'total_sessions' as metric, COUNT(DISTINCT session_id) as value
            FROM conversation WHERE student_id=:student_id
            UNION ALL
            SELECT 'total_messages' as metric, COUNT(*) as value
            FROM conversation_message cm JOIN conversation c ON c.session_id=cm.session_id
            WHERE c.student_id=:student_id
            UNION ALL
            SELECT 'avg_messages_per_session' as metric,
                   COUNT(*) / NULLIF(COUNT(DISTINCT c.session_id), 0) as value
            FROM conversation_message cm JOIN conversation c ON c.session_id=cm.session_id
            WHERE c.student_id=:student_id
            """
            rows = mysql.fetch_all(sql, {"student_id": scope.student_id})

        return jsonify({
            "success": True,
            "data": {
                "session_id": session_id or "all",
                "stats": rows
            }
        })

    except ScopeError as error:
        return scope_error_response(error)
    except Exception as e:
        return jsonify(safe_error("获取Agent统计", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/agent/session/<session_id>/messages', methods=['GET'])
def get_agent_session_messages(session_id: str):
    """
    Agent: 获取对话消息历史

    Path:
        session_id: 会话ID
    """
    limit, offset = clamp_pagination(
        request.args.get('limit', type=int),
        request.args.get('offset', type=int),
        default_limit=50,
    )

    try:
        scope = resolve_request_scope()
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()
        owner = mysql.execute(
            "SELECT student_id FROM conversation WHERE session_id=:session_id LIMIT 1",
            {"session_id": session_id},
        )
        if not owner:
            return jsonify({"success": False, "error": "会话不存在"}), 404
        require_owner(owner[0].get("student_id"), scope, resource="会话")

        sql = """
        SELECT cm.message_id, cm.role, cm.content, cm.created_at
        FROM conversation_message cm
        JOIN conversation c ON c.session_id=cm.session_id
        WHERE cm.session_id = :session_id AND c.student_id = :student_id
        ORDER BY cm.created_at ASC
        LIMIT :limit OFFSET :offset
        """
        rows = mysql.fetch_all(sql, {
            "session_id": session_id,
            "student_id": scope.student_id,
            "limit": limit,
            "offset": offset
        })

        return jsonify({
            "success": True,
            "data": {
                "session_id": session_id,
                "messages": rows,
                "count": len(rows)
            }
        })

    except ScopeError as error:
        return scope_error_response(error)
    except Exception as e:
        return jsonify(safe_error("获取对话消息", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/db/health', methods=['GET'])
def db_health_check():
    """
    数据库健康检查

    检查 MySQL, Neo4j, Milvus, Redis 四个数据库的连接状态和响应时间
    """
    results = {
        "mysql": {"status": "unknown", "response_time_ms": None, "error": None},
        "neo4j": {"status": "unknown", "response_time_ms": None, "error": None},
        "milvus": {"status": "unknown", "response_time_ms": None, "error": None},
        "redis": {"status": "unknown", "response_time_ms": None, "error": None}
    }

    def _retry_call(func, *args, retries=2, **kwargs):
        last_error = None
        for attempt in range(retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < retries - 1:
                    time.sleep(0.1)
        raise last_error

    try:
        from ..services.db import get_mysql_client
        start = time.time()
        mysql = get_mysql_client()
        _retry_call(mysql.execute_single, "SELECT 1 AS v")
        results["mysql"]["status"] = "healthy"
        results["mysql"]["response_time_ms"] = round((time.time() - start) * 1000, 2)
    except Exception as e:
        results["mysql"]["status"] = "unhealthy"
        results["mysql"]["error"] = str(e)

    try:
        from ..services.db import get_neo4j_client
        start = time.time()
        neo4j = get_neo4j_client()
        _retry_call(neo4j.execute_single, "RETURN 1 AS v")
        results["neo4j"]["status"] = "healthy"
        results["neo4j"]["response_time_ms"] = round((time.time() - start) * 1000, 2)
    except Exception as e:
        results["neo4j"]["status"] = "unhealthy"
        results["neo4j"]["error"] = str(e)

    try:
        from ..services.db import get_milvus_client
        start = time.time()
        milvus = get_milvus_client()
        _retry_call(milvus.connect)
        results["milvus"]["status"] = "healthy"
        results["milvus"]["response_time_ms"] = round((time.time() - start) * 1000, 2)
    except Exception as e:
        results["milvus"]["status"] = "unhealthy"
        results["milvus"]["error"] = str(e)

    try:
        from ..services.db import get_redis_client
        start = time.time()
        redis_client = get_redis_client()
        _retry_call(redis_client.test_connection)
        results["redis"]["status"] = "healthy"
        results["redis"]["response_time_ms"] = round((time.time() - start) * 1000, 2)
    except Exception as e:
        results["redis"]["status"] = "unhealthy"
        results["redis"]["error"] = str(e)

    all_healthy = all(r["status"] == "healthy" for r in results.values())

    return jsonify({
        "success": True,
        "data": {
            "overall": "healthy" if all_healthy else "degraded",
            "databases": results
        }
    })
