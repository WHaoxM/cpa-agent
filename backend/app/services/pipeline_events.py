"""Business-state journal, atomically maintained by MySQL, not an Agent runtime.

Install explicitly during migration; never perform DDL in a request transaction.
"""
from sqlalchemy import text


TABLE_DDL = """CREATE TABLE IF NOT EXISTS pipeline_task_event (
    event_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL,
    student_id VARCHAR(64) NULL,
    status VARCHAR(20) NOT NULL,
    occurred_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY idx_task_cursor (task_id, event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""

TRIGGERS = {
    "cpa_pipeline_created_v1": """CREATE TRIGGER cpa_pipeline_created_v1
        AFTER INSERT ON pipeline_task FOR EACH ROW
        INSERT INTO pipeline_task_event(task_id, student_id, status)
        VALUES(NEW.task_id, NEW.student_id, NEW.status)""",
    "cpa_pipeline_transition_v1": """CREATE TRIGGER cpa_pipeline_transition_v1
        AFTER UPDATE ON pipeline_task FOR EACH ROW
        BEGIN
          IF NOT (OLD.status <=> NEW.status) THEN
            INSERT INTO pipeline_task_event(task_id, student_id, status)
            VALUES(NEW.task_id, NEW.student_id, NEW.status);
          END IF;
        END""",
}


def install_pipeline_event_journal(mysql):
    """Additive, idempotent deployment migration. Existing tasks are not backfilled."""
    with mysql.engine.connect() as conn:
        conn.execute(text(TABLE_DDL))
        names = {row[0] for row in conn.execute(text(
            "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()"))}
        for name, ddl in TRIGGERS.items():
            if name not in names:
                conn.execute(text(ddl))
        conn.commit()


def read_pipeline_events(mysql, *, student_id, task_id, cursor=0, limit=100):
    """One repeatable-read snapshot: final status cannot race the event page.

    A task-specific cursor avoids cross-task auto-increment commit-order gaps.
    Events contain no resume text, prompt, model content, or filesystem paths.
    """
    if cursor < 0 or not 1 <= limit <= 200:
        raise ValueError("Invalid cursor or limit")
    with mysql.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        with conn.begin():
            task = conn.execute(text(
                "SELECT task_id, pipeline, status FROM pipeline_task "
                "WHERE task_id=:tid AND student_id=:sid"),
                {"tid": task_id, "sid": student_id}).mappings().first()
            if task is None:
                raise LookupError("Task not found")
            rows = list(conn.execute(text(
                "SELECT event_id, task_id, status, occurred_at FROM pipeline_task_event "
                "WHERE task_id=:tid AND student_id=:sid AND event_id>:cursor "
                "ORDER BY event_id LIMIT :lim"),
                {"tid": task_id, "sid": student_id, "cursor": cursor, "lim": limit + 1}).mappings())
            events = [{"event_id": r["event_id"], "task_id": r["task_id"],
                       "type": "pipeline.status", "status": r["status"],
                       "occurred_at": r["occurred_at"].isoformat()} for r in rows[:limit]]
            return {"task": dict(task), "events": events,
                    "cursor": events[-1]["event_id"] if events else cursor,
                    "has_more": len(rows) > limit, "resume_state": "not_available"}


class ConfirmationTransaction:
    """Reuse existing pipeline validators against one SQLAlchemy transaction."""
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, parameters=None, commit=False):
        result = self.conn.execute(text(sql), parameters or {})
        return list(result.mappings()) if result.returns_rows else []

    fetch_all = execute


if __name__ == "__main__":
    from .db.mysql_client import get_mysql_client
    install_pipeline_event_journal(get_mysql_client())
    print("pipeline event journal installed; historical events were not fabricated")
