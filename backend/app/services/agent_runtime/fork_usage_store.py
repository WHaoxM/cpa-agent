"""SQLAlchemy accounting adapter, not a second background-job scheduler.

Install the table explicitly before enabling rollup jobs. A running record after
worker loss is unknown, not resumable: no automatic replay of billed work.
"""
import json

from sqlalchemy import text


TABLE_DDL = """CREATE TABLE IF NOT EXISTS agent_fork_usage (
    job_id VARCHAR(64) NOT NULL PRIMARY KEY,
    session_id VARCHAR(128) NOT NULL,
    student_id VARCHAR(64) NOT NULL,
    usage_turn_id VARCHAR(64) NOT NULL,
    kind VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    ledger_json TEXT NOT NULL
)"""


def _engine():
    from ..db.mysql_client import get_mysql_client
    return get_mysql_client().engine


def install_fork_usage_schema(engine=None):
    with (engine or _engine()).begin() as conn:
        conn.execute(text(TABLE_DDL))


def create_record(job):
    with _engine().begin() as conn:
        conn.execute(text("INSERT INTO agent_fork_usage "
            "(job_id,session_id,student_id,usage_turn_id,kind,status,ledger_json) "
            "VALUES (:job_id,:session_id,:student_id,:usage_turn_id,:kind,'queued','{}')"),
            {key: getattr(job, key) for key in
             ("job_id", "session_id", "student_id", "usage_turn_id", "kind")})


def save_record(job, status, ledger):
    with _engine().begin() as conn:
        conn.execute(text("UPDATE agent_fork_usage SET status=:status, ledger_json=:ledger "
            "WHERE job_id=:jid AND session_id=:sid AND student_id=:owner AND usage_turn_id=:tid"),
            {"status": status, "ledger": json.dumps(ledger), "jid": job.job_id,
             "sid": job.session_id, "owner": job.student_id, "tid": job.usage_turn_id})


def read_record(*, job_id, session_id, student_id, usage_turn_id):
    """Internal lookup; callers must authenticate owner before supplying IDs."""
    with _engine().connect() as conn:
        row = conn.execute(text("SELECT status, ledger_json FROM agent_fork_usage "
            "WHERE job_id=:jid AND session_id=:sid AND student_id=:owner AND usage_turn_id=:tid"),
            {"jid": job_id, "sid": session_id, "owner": student_id, "tid": usage_turn_id}).mappings().first()
        return {"status": row["status"], "ledger": json.loads(row["ledger_json"])} if row else None
