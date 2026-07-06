import importlib
import os
import sys
import tempfile

import pytest


os.environ.setdefault('SECRET_KEY', 'test-secret-key')
_temp_dir = tempfile.mkdtemp(prefix='outlookEmail-icloud-hme-management-schema-')
os.environ['DATABASE_PATH'] = os.path.join(_temp_dir, 'test.db')

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

app_module = importlib.import_module('web_outlook_app')


@pytest.fixture
def client():
    app = app_module.app
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False

    with app.app_context():
        app_module.init_db()
        yield app.test_client()


def test_icloud_hme_management_tables_exist(client):
    db = app_module.get_db()
    table_names = {
        row["name"]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert "icloud_hme_address_cache" in table_names
    assert "icloud_hme_generation_tasks" in table_names
    assert "icloud_hme_generated_addresses" in table_names
    assert "icloud_hme_generation_logs" in table_names
    assert "icloud_hme_deactivation_candidates" in table_names


def test_icloud_hme_management_tables_include_runtime_columns(client):
    db = app_module.get_db()
    address_columns = {
        row["name"]
        for row in db.execute("PRAGMA table_info(icloud_hme_address_cache)").fetchall()
    }
    candidate_columns = {
        row["name"]
        for row in db.execute("PRAGMA table_info(icloud_hme_deactivation_candidates)").fetchall()
    }

    assert "anonymous_id" in address_columns
    assert "deleted_at" in candidate_columns


def test_reset_interrupted_icloud_hme_generation_tasks_marks_incomplete_tasks_stopped(client):
    db = app_module.get_db()
    db.executemany(
        '''
        INSERT INTO icloud_hme_generation_tasks (status, stop_requested, last_error)
        VALUES (?, 0, '')
        ''',
        [('pending',), ('running',), ('stopping',), ('completed',)],
    )
    db.commit()

    app_module.reset_interrupted_icloud_hme_generation_tasks(db)

    rows = db.execute(
        '''
        SELECT status, stop_requested, last_error, stopped_at
        FROM icloud_hme_generation_tasks
        ORDER BY id
        '''
    ).fetchall()

    interrupted_rows = rows[:3]
    completed_row = rows[3]
    assert [row["status"] for row in interrupted_rows] == ["stopped", "stopped", "stopped"]
    assert [row["stop_requested"] for row in interrupted_rows] == [1, 1, 1]
    assert all("interrupted by process restart" in row["last_error"] for row in interrupted_rows)
    assert all(row["stopped_at"] for row in interrupted_rows)
    assert completed_row["status"] == "completed"
    assert completed_row["stop_requested"] == 0
