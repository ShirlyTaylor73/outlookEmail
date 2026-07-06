import importlib
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


os.environ.setdefault('SECRET_KEY', 'test-secret-key')
_temp_dir = tempfile.mkdtemp(prefix='outlookEmail-icloud-hme-long-runner-tests-')
os.environ['DATABASE_PATH'] = os.path.join(_temp_dir, 'test.db')

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

web_outlook_app = importlib.import_module('web_outlook_app')


class ICloudHmeLongRunnerTestCase(unittest.TestCase):
    def setUp(self):
        self.app = web_outlook_app.app
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self._stop_running_thread()
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['logged_in'] = True

        with self.app.app_context():
            web_outlook_app.init_db()
            db = web_outlook_app.get_db()
            for table in (
                'icloud_hme_generation_logs',
                'icloud_hme_generated_addresses',
                'icloud_hme_generation_tasks',
                'icloud_hme_address_cache',
                'account_shares',
                'account_aliases',
                'account_tags',
                'accounts',
                'icloud_hme_sources',
            ):
                try:
                    db.execute(f'DELETE FROM {table}')
                except sqlite3.OperationalError as exc:
                    if 'no such table' not in str(exc):
                        raise
            db.commit()
            web_outlook_app.ICLOUD_HME_LONG_RUNNER_PAYLOADS.clear()
            web_outlook_app.ICLOUD_HME_LONG_RUNNER_STOP.clear()

    def tearDown(self):
        self._stop_running_thread()

    def _stop_running_thread(self):
        thread = getattr(web_outlook_app, 'ICLOUD_HME_LONG_RUNNER_THREAD', None)
        if thread and thread.is_alive():
            web_outlook_app.ICLOUD_HME_LONG_RUNNER_STOP.set()
            thread.join(timeout=2)
        web_outlook_app.ICLOUD_HME_LONG_RUNNER_THREAD = None
        web_outlook_app.ICLOUD_HME_LONG_RUNNER_STOP.clear()

    def _source_payload(self, **overrides):
        payload = {
            'name': 'Receiver',
            'region': 'global',
            'receiver_email': 'receiver@example.com',
            'receiver_provider': 'custom',
            'receiver_imap_host': 'imap.example.com',
            'receiver_imap_port': 993,
            'receiver_imap_password': 'app-password',
            'receiver_folder': 'INBOX',
            'use_ssl': True,
            'cookie': 'encrypted-cookie',
            'maildomain_host': 'maildomain.icloud.com',
        }
        payload.update(overrides)
        return payload

    def _create_source(self, **overrides):
        response = self.client.post('/api/icloud-hme/sources', json=self._source_payload(**overrides))
        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertTrue(data['success'], msg=data)
        return data['source']['id']

    def _start_payload(self, source_id, **overrides):
        payload = {
            'source_id': source_id,
            'target_group_id': 1,
            'target_count': 1,
            'label_prefix': 'TestHME',
            'note': 'created by long-runner test',
            'success_delay_seconds': 0,
            'failure_delay_seconds': 0,
        }
        payload.update(overrides)
        return payload

    def _wait_for_task(self, task_id, terminal=True, timeout=3):
        deadline = time.time() + timeout
        last_task = None
        while time.time() < deadline:
            response = self.client.get(f'/api/icloud-hme/long-runner/status?task_id={task_id}')
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertTrue(data['success'], msg=data)
            last_task = data['task']
            if not terminal or (last_task and last_task['status'] in {'completed', 'failed', 'stopped'}):
                return last_task
            time.sleep(0.05)
        self.fail(f'Task {task_id} did not reach a terminal status; last task: {last_task}')

    def _assert_start_rejected(self, payload, expected_status, expected_error_part=None):
        with patch.object(web_outlook_app, 'generate_icloud_hme', return_value={
            'success': False,
            'error': 'should not start',
        }):
            response = self.client.post('/api/icloud-hme/long-runner/start', json=payload)
            data = response.get_json()
            if response.status_code == 202 and data and data.get('task'):
                self._wait_for_task(data['task']['id'])

        self.assertEqual(response.status_code, expected_status, msg=data)
        self.assertFalse(data['success'], msg=data)
        if expected_error_part:
            self.assertIn(expected_error_part, data.get('error', ''))

    def test_start_rejects_non_positive_target_count(self):
        source_id = self._create_source()

        for target_count in (0, -1):
            with self.subTest(target_count=target_count):
                self._assert_start_rejected(
                    self._start_payload(source_id, target_count=target_count),
                    400,
                )

    def test_start_rejects_negative_success_delay_seconds(self):
        source_id = self._create_source()

        self._assert_start_rejected(
            self._start_payload(source_id, success_delay_seconds=-1),
            400,
        )

    def test_start_rejects_missing_source_id(self):
        self._assert_start_rejected(
            self._start_payload(999999),
            404,
            'iCloud HME 接收源不存在',
        )

    def test_start_rejects_missing_target_group_id(self):
        source_id = self._create_source()

        self._assert_start_rejected(
            self._start_payload(source_id, target_group_id=999999),
            400,
            '目标分组不存在',
        )

    def test_start_rejects_when_another_hme_registration_task_is_running(self):
        source_id = self._create_source()
        with self.app.app_context():
            db = web_outlook_app.get_db()
            db.execute(
                '''
                INSERT INTO icloud_hme_generation_tasks (
                    source_id, batch_id, status, total_requested, updated_at
                )
                VALUES (?, 'existing-batch', 'running', 1, CURRENT_TIMESTAMP)
                ''',
                (source_id,),
            )
            db.commit()

        response = self.client.post(
            '/api/icloud-hme/long-runner/start',
            json=self._start_payload(source_id),
        )

        data = response.get_json()
        self.assertEqual(response.status_code, 409, msg=data)
        self.assertFalse(data['success'], msg=data)
        self.assertIn('已有 HME 注册任务正在运行', data.get('error', ''))

    def test_start_generates_reserves_and_imports_hme_account(self):
        source_id = self._create_source()
        generated_hme = 'generated-hme@example.com'

        with patch.object(web_outlook_app, 'generate_icloud_hme', return_value={
            'success': True,
            'result': {'hme': generated_hme},
        }) as generate_mock, patch.object(web_outlook_app, 'reserve_icloud_hme', return_value={
            'success': True,
        }) as reserve_mock:
            response = self.client.post(
                '/api/icloud-hme/long-runner/start',
                json=self._start_payload(source_id, target_count=1),
            )
            data = response.get_json()
            self.assertEqual(response.status_code, 202, msg=data)
            self.assertTrue(data['success'], msg=data)
            task = self._wait_for_task(data['task']['id'])

        self.assertEqual(task['status'], 'completed', msg=task)
        self.assertEqual(task['success_count'], 1, msg=task)
        generate_mock.assert_called_once()
        reserve_mock.assert_called_once()
        with self.app.app_context():
            db = web_outlook_app.get_db()
            generated_rows = db.execute(
                '''
                SELECT hme, source_id, status, account_id
                FROM icloud_hme_generated_addresses
                WHERE task_id = ?
                ''',
                (task['id'],),
            ).fetchall()
            account = db.execute(
                '''
                SELECT email, group_id, account_type, provider, icloud_hme_source_id
                FROM accounts
                WHERE LOWER(email) = LOWER(?)
                ''',
                (generated_hme,),
            ).fetchone()

        self.assertEqual(len(generated_rows), 1)
        self.assertEqual(generated_rows[0]['hme'], generated_hme)
        self.assertEqual(generated_rows[0]['source_id'], source_id)
        self.assertEqual(generated_rows[0]['status'], 'imported')
        self.assertIsNotNone(generated_rows[0]['account_id'])
        self.assertIsNotNone(account)
        self.assertEqual(account['email'], generated_hme)
        self.assertEqual(account['group_id'], 1)
        self.assertEqual(account['account_type'], 'icloud_hme')
        self.assertEqual(account['provider'], 'icloud_hme')
        self.assertEqual(account['icloud_hme_source_id'], source_id)

    def test_generation_failure_records_counter_log_and_last_error(self):
        source_id = self._create_source()

        with patch.object(web_outlook_app, 'generate_icloud_hme', return_value={
            'success': False,
            'error': 'Apple API unavailable',
        }), patch.object(web_outlook_app, 'reserve_icloud_hme') as reserve_mock:
            response = self.client.post(
                '/api/icloud-hme/long-runner/start',
                json=self._start_payload(source_id, target_count=1, failure_delay_seconds=0),
            )
            data = response.get_json()
            self.assertEqual(response.status_code, 202, msg=data)
            task = self._wait_for_task(data['task']['id'])

        self.assertEqual(task['status'], 'completed', msg=task)
        self.assertEqual(task['failure_count'], 1, msg=task)
        self.assertIn('Apple API unavailable', task['last_error'])
        reserve_mock.assert_not_called()
        with self.app.app_context():
            logs = web_outlook_app.get_db().execute(
                '''
                SELECT level, message
                FROM icloud_hme_generation_logs
                WHERE task_id = ?
                ORDER BY id ASC
                ''',
                (task['id'],),
            ).fetchall()

        self.assertTrue(any(row['level'] == 'error' and 'Apple API unavailable' in row['message'] for row in logs))

    def test_stop_api_marks_running_task_as_stopping_or_stopped(self):
        source_id = self._create_source()

        with patch.object(web_outlook_app, 'generate_icloud_hme', return_value={
            'success': True,
            'result': {'hme': 'stop-me@example.com'},
        }), patch.object(web_outlook_app, 'reserve_icloud_hme', return_value={'success': True}):
            response = self.client.post(
                '/api/icloud-hme/long-runner/start',
                json=self._start_payload(source_id, target_count=1, success_delay_seconds=60),
            )
            data = response.get_json()
            self.assertEqual(response.status_code, 202, msg=data)
            task_id = data['task']['id']

            stop_response = self.client.post('/api/icloud-hme/long-runner/stop', json={'task_id': task_id})
            stop_data = stop_response.get_json()
            self.assertEqual(stop_response.status_code, 200, msg=stop_data)
            self.assertTrue(stop_data['success'], msg=stop_data)
            self.assertIn(stop_data['task']['status'], {'stopping', 'stopped'})
            self.assertTrue(stop_data['task']['stop_requested'], msg=stop_data)
            final_task = self.client.get(f'/api/icloud-hme/long-runner/status?task_id={task_id}').get_json()['task']

        self.assertIn(final_task['status'], {'stopping', 'stopped'}, msg=final_task)
        self.assertTrue(final_task['stop_requested'], msg=final_task)


if __name__ == '__main__':
    unittest.main()
