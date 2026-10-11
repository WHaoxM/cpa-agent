"""Durable L0 human confirmation, not a checkpoint of an entire chat turn.

LangGraph owns suspension/replay. The existing transactional domain writer owns
confirmation idempotency, including a crash after its commit and before checkpoint.
"""
from contextlib import contextmanager
import hashlib
from typing import TypedDict


class ConfirmationState(TypedDict):
    student_id: str
    task_id: str
    result: dict


def confirmation_thread(student_id: str, task_id: str) -> str:
    return "resume_confirm_" + hashlib.sha256(
        (student_id + "\0" + task_id).encode("utf-8")).hexdigest()


@contextmanager
def _graph(thread):
    import pymysql
    from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import interrupt
    from ...config import Config
    from ..db.mysql_client import get_mysql_client
    from ..pipeline import confirm_resume_candidate

    # Fail closed: a MemorySaver cannot satisfy this endpoint's restart contract.
    with pymysql.connect(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                         user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                         database=Config.MYSQL_DATABASE, autocommit=True) as conn:
        lock = hashlib.sha256(thread.encode()).hexdigest()
        with conn.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 10)", (lock,))
            if cursor.fetchone()[0] != 1:
                raise RuntimeError("confirmation_busy_retry")
        try:
            saver = PyMySQLSaver(conn)
            saver.setup()
            from .checkpoint_mysql import align_checkpoint_collation
            align_checkpoint_collation()

            def approve(state):
                decision = interrupt({"kind": "resume_confirmation", "task_id": state["task_id"]})
                if not isinstance(decision, dict) or decision.get("confirmed") is not True:
                    raise ValueError("explicit_confirmation_required")
                result = confirm_resume_candidate(get_mysql_client(),
                    student_id=state["student_id"], task_id=state["task_id"],
                    acknowledge_partial=decision.get("acknowledge_partial") is True)
                return {"result": result}

            builder = StateGraph(ConfirmationState)
            builder.add_node("approve", approve)
            builder.add_edge(START, "approve")
            builder.add_edge("approve", END)
            yield builder.compile(checkpointer=saver)
        finally:
            with conn.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (lock,))


def run_confirmation(*, student_id: str, task_id: str, confirmed=False,
                     acknowledge_partial=False) -> dict:
    """Caller supplies Principal-derived owner, never owner from model/request body."""
    from langgraph.types import Command
    from ..db.mysql_client import get_mysql_client
    from ..pipeline import confirm_resume_candidate
    mysql = get_mysql_client()
    rows = mysql.fetch_all("SELECT student_id, pipeline, status FROM pipeline_task "
                           "WHERE task_id=:tid LIMIT 1", {"tid": task_id})
    if not rows or rows[0].get("student_id") != student_id:
        raise LookupError("简历候选不存在")
    if rows[0].get("pipeline") != "L0_parse_resume" or rows[0].get("status") not in {"needs_input", "completed"}:
        raise ValueError("此任务不是可确认的简历候选")
    thread = confirmation_thread(student_id, task_id)
    config = {"configurable": {"thread_id": thread}}
    with _graph(thread) as graph:
        snapshot = graph.get_state(config)
        if snapshot.values and (snapshot.values.get("student_id") != student_id or snapshot.values.get("task_id") != task_id):
            raise LookupError("简历候选不存在")
        if not snapshot.values:
            graph.invoke({"student_id": student_id, "task_id": task_id, "result": {}}, config)
            snapshot = graph.get_state(config)
        if confirmed is True:
            if snapshot.next:
                graph.invoke(Command(resume={"confirmed": True,
                    "acknowledge_partial": acknowledge_partial is True}), config)
            # Authoritative re-read also rejects a candidate superseded since its checkpoint.
            return confirm_resume_candidate(mysql, student_id=student_id, task_id=task_id,
                                             acknowledge_partial=acknowledge_partial)
        return {"workflow": "resume_confirmation", "task_id": task_id,
                "resume_state": "available" if snapshot.next else "completed",
                "next_action": "confirm_resume" if snapshot.next else "generate_portrait"}
