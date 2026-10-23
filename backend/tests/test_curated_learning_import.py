"""Transactional resource diff tests; real MySQL replay separately checks locks/IDs."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

path = Path(__file__).resolve().parents[1] / 'scripts' / 'import_curated_learning.py'
spec = importlib.util.spec_from_file_location('curated_learning_import', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE course (course_id TEXT PRIMARY KEY,title TEXT,category TEXT,description TEXT)'))
        conn.execute(text('CREATE TABLE chapter (id INTEGER PRIMARY KEY,course_id TEXT,sequence INTEGER,title TEXT,content_summary TEXT,video_url TEXT)'))
        conn.execute(text('CREATE TABLE learning_progress (chapter_id INTEGER,completed INTEGER)'))
    # SQLite has no SELECT FOR UPDATE. MySQL browser replay uses the real SQL.
    monkeypatch.setattr(module, 'text', lambda sql: text(sql.replace(' FOR UPDATE', '')))
    yield SimpleNamespace(engine=engine)
    engine.dispose()


def test_preview_is_read_only_and_contains_reviewable_changes(db):
    result = module.import_resources(db)
    assert result['applied'] is False and len(result['changes']) == 8
    with db.engine.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM course')).scalar() == 0


def test_repeat_preserves_chapter_ids_and_learning_records(db):
    module.import_resources(db, apply=True)
    with db.engine.begin() as conn:
        before = conn.execute(text('SELECT id,course_id,sequence FROM chapter ORDER BY id')).all()
        conn.execute(text('INSERT INTO learning_progress VALUES (:chapter,1)'), {'chapter': before[0][0]})
    result = module.import_resources(db, apply=True)
    assert all(change['action'] == 'unchanged' for change in result['changes'])
    with db.engine.connect() as conn:
        assert conn.execute(text('SELECT id,course_id,sequence FROM chapter ORDER BY id')).all() == before
        assert conn.execute(text('SELECT completed FROM learning_progress')).scalar() == 1


def test_update_keeps_ids_and_collision_rolls_back_all_changes(db):
    module.import_resources(db, apply=True)
    with db.engine.begin() as conn:
        ids = conn.execute(text('SELECT id FROM chapter ORDER BY id')).scalars().all()
        conn.execute(text("UPDATE chapter SET title='旧的标题' WHERE id=:id"), {'id':ids[0]})
    assert any(c['action'] == 'update' for c in module.import_resources(db, apply=True)['changes'])
    with db.engine.begin() as conn:
        conn.execute(text("UPDATE course SET description='非本导入管理的资料' WHERE course_id='curated_postgresql17_sql'"))
        conn.execute(text("UPDATE chapter SET title='保留的旧标题' WHERE id=:id"), {'id':ids[0]})
    with pytest.raises(ValueError, match='ID collision'):
        module.import_resources(db, apply=True)
    with db.engine.connect() as conn:
        assert conn.execute(text('SELECT id FROM chapter ORDER BY id')).scalars().all() == ids
        assert conn.execute(text('SELECT title FROM chapter WHERE id=:id'), {'id':ids[0]}).scalar() == '保留的旧标题'
