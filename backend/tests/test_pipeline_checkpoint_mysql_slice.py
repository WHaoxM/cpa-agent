"""Isolated LangGraph interrupt/restart experiment; not a production resume API."""
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import TypedDict
import uuid

import pytest


class ConfirmationState(TypedDict):
    owner: str
    candidate_id: str
    confirmed: bool


def run_slice(phase, thread, owner):
    import pymysql
    from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import Command, interrupt
    from app.config import Config

    # Use the library connection API, no custom checkpoint serialization or MemorySaver.
    with pymysql.connect(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                         user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                         database=Config.MYSQL_DATABASE, autocommit=True) as conn:
        saver = PyMySQLSaver(conn)
        saver.setup()
        # Reuse the existing MySQL 8 JSON_TABLE collation compatibility migration.
        from app.services.langgraph_agent import _align_checkpoint_collation
        _align_checkpoint_collation()

        def approval(state: ConfirmationState):
            decision = interrupt({'candidate_id': state['candidate_id'], 'action': 'confirm'})
            if decision is not True:
                raise ValueError('explicit_confirmation_required')
            return {'confirmed': True}

        builder = StateGraph(ConfirmationState)
        builder.add_node('approval', approval)
        builder.add_edge(START, 'approval')
        builder.add_edge('approval', END)
        graph = builder.compile(checkpointer=saver)
        config = {'configurable': {'thread_id': thread}}
        if phase == 'start':
            output = graph.invoke({'owner': owner, 'candidate_id': thread, 'confirmed': False}, config)
            return {'interrupted': bool(output.get('__interrupt__')), 'confirmed': output['confirmed']}
        state = graph.get_state(config)
        # Server boundary of this isolated harness: never trust Command payload as owner.
        if not state.values or state.values.get('owner') != owner:
            return {'error': 'not_found'}
        if phase == 'resume':
            output = graph.invoke(Command(resume=True), config)
            return {'confirmed': output['confirmed'], 'pending': bool(graph.get_state(config).next)}
        return {'confirmed': state.values['confirmed'], 'pending': bool(state.next)}


def test_real_mysql_interrupt_new_process_resume_and_owner_denial():
    if os.getenv('CPA_TEST_PIPELINE_MYSQL') != '1':
        pytest.skip('requires explicit MySQL integration opt-in')
    pytest.importorskip('langgraph.checkpoint.mysql.pymysql')
    from app.services.db.mysql_client import MySQLClient
    from sqlalchemy import text
    thread = 'checkpoint_slice_' + uuid.uuid4().hex
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).parents[1]), 'PYTHONUTF8': '1'}

    def child(phase, owner='isolated_owner'):
        proc = subprocess.run([sys.executable, '-B', __file__, phase, thread, owner],
                              env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])

    try:
        assert child('start') == {'interrupted': True, 'confirmed': False}
        assert child('resume', 'other_owner') == {'error': 'not_found'}
        assert child('inspect') == {'confirmed': False, 'pending': True}
        assert child('resume') == {'confirmed': True, 'pending': False}
        assert child('inspect') == {'confirmed': True, 'pending': False}
    finally:
        db = MySQLClient()
        with db.engine.begin() as conn:
            names = {r[0] for r in conn.execute(text('SHOW TABLES'))}
            for table in ('checkpoint_writes', 'checkpoint_blobs', 'checkpoints'):
                if table in names:
                    conn.execute(text(f'DELETE FROM {table} WHERE thread_id=:tid'), {'tid': thread})
        db.close()


if __name__ == '__main__':
    print(json.dumps(run_slice(*sys.argv[1:]), ensure_ascii=False))
