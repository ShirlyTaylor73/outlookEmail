import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


os.environ.setdefault('SECRET_KEY', 'test-secret-key')
_temp_dir = tempfile.mkdtemp(prefix='outlookEmail-icloud-hme-deactivation-tests-')
os.environ['DATABASE_PATH'] = os.path.join(_temp_dir, 'test.db')

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

web_outlook_app = importlib.import_module('web_outlook_app')


class ICloudHmeDeactivationTestCase(unittest.TestCase):
    def setUp(self):
        self.app = web_outlook_app.app
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()
        self._message_sequence = 0
        with self.client.session_transaction() as sess:
            sess['logged_in'] = True

        with self.app.app_context():
            web_outlook_app.init_db()
            web_outlook_app.ensure_icloud_hme_management_runtime_columns()
            db = web_outlook_app.get_db()
            for table in (
                'icloud_hme_deactivation_candidates',
                'icloud_hme_source_message_recipients',
                'icloud_hme_source_messages',
                'icloud_hme_address_cache',
                'account_shares',
                'account_aliases',
                'account_tags',
                'accounts',
                'groups',
                'icloud_hme_sources',
            ):
                try:
                    db.execute(f'DELETE FROM {table}')
                except sqlite3.OperationalError as exc:
                    if 'no such table' not in str(exc):
                        raise
            db.commit()

    def _create_group(self, name='HME Group'):
        db = web_outlook_app.get_db()
        cursor = db.execute(
            '''
            INSERT INTO groups (name, mailbox_type)
            VALUES (?, 'account')
            ''',
            (name,),
        )
        db.commit()
        return int(cursor.lastrowid)

    def _create_source(self, name='Receiver', email='receiver@example.com'):
        db = web_outlook_app.get_db()
        cursor = db.execute(
            '''
            INSERT INTO icloud_hme_sources (
                name, region, receiver_email, receiver_provider, receiver_imap_host,
                receiver_imap_password, receiver_folder, use_ssl, cookie, maildomain_host
            )
            VALUES (?, 'global', ?, 'custom', 'imap.example.com',
                    'app-password', 'INBOX', 1, 'cookie=value', 'maildomain.icloud.com')
            ''',
            (name, email),
        )
        db.commit()
        return int(cursor.lastrowid)

    def _create_hme_account(self, source_id, group_id, hme, status='active', remark=''):
        db = web_outlook_app.get_db()
        cursor = db.execute(
            '''
            INSERT INTO accounts (
                email, group_id, remark, status, account_type, provider, icloud_hme_source_id
            )
            VALUES (?, ?, ?, ?, 'icloud_hme', 'icloud_hme', ?)
            ''',
            (hme, group_id, remark, status, source_id),
        )
        db.commit()
        return int(cursor.lastrowid)

    def _cache_hme_address(self, source_id, hme, anonymous_id, status='active'):
        db = web_outlook_app.get_db()
        db.execute(
            '''
            INSERT INTO icloud_hme_address_cache (
                source_id, hme, label, note, status, anonymous_id, last_seen_at, updated_at
            )
            VALUES (?, ?, '', '', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ''',
            (source_id, hme, status, anonymous_id),
        )
        db.commit()

    def _create_source_message(self, source_id, hme, subject, folder='inbox'):
        self._message_sequence += 1
        db = web_outlook_app.get_db()
        cursor = db.execute(
            '''
            INSERT INTO icloud_hme_source_messages (
                source_id, folder, provider_message_id, id_mode, subject, sender,
                recipients, received_at, received_at_sort, body_preview
            )
            VALUES (?, ?, ?, 'uid', ?, 'OpenAI <noreply@example.com>',
                    ?, '2026-07-07T00:00:00Z', ?, '')
            ''',
            (
                source_id,
                folder,
                f'msg-{self._message_sequence}',
                subject,
                hme,
                float(self._message_sequence),
            ),
        )
        message_id = int(cursor.lastrowid)
        db.execute(
            '''
            INSERT INTO icloud_hme_source_message_recipients (
                source_message_id, source_id, hme_address
            )
            VALUES (?, ?, ?)
            ''',
            (message_id, source_id, hme),
        )
        db.commit()
        return message_id

    def _create_candidate(self, source_id, account_id, hme, reason='OpenAI - Access Deactivated'):
        db = web_outlook_app.get_db()
        cursor = db.execute(
            '''
            INSERT INTO icloud_hme_deactivation_candidates (
                source_id, hme, account_id, reason, status
            )
            VALUES (?, ?, ?, ?, 'pending')
            ''',
            (source_id, hme, account_id, reason),
        )
        db.commit()
        return int(cursor.lastrowid)

    def test_scan_openai_access_deactivated_creates_candidate_with_account_group_and_anonymous_id(self):
        with self.app.app_context():
            source_id = self._create_source()
            group_id = self._create_group()
            hme = 'openai-hme@icloud.com'
            account_id = self._create_hme_account(source_id, group_id, hme)
            self._cache_hme_address(source_id, hme, 'anon-openai')
            self._create_source_message(
                source_id,
                hme,
                'OpenAI - Access Deactivated [C-5GiU3pJbeSBF]',
            )

        response = self.client.post(
            '/api/icloud-hme/deactivation-candidates/scan',
            json={'source_id': source_id},
        )

        data = response.get_json()
        self.assertEqual(response.status_code, 200, msg=data)
        self.assertTrue(data['success'], msg=data)
        self.assertEqual(data['candidate_count'], 1)
        self.assertEqual(len(data['candidates']), 1)
        candidate = data['candidates'][0]
        self.assertEqual(candidate['hme'], hme)
        self.assertEqual(candidate['account_id'], account_id)
        self.assertEqual(candidate['group_id'], group_id)
        self.assertEqual(candidate['anonymous_id'], 'anon-openai')
        with self.app.app_context():
            row = web_outlook_app.get_db().execute(
                '''
                SELECT source_id, hme, account_id, status
                FROM icloud_hme_deactivation_candidates
                WHERE source_id = ? AND hme = ?
                ''',
                (source_id, hme),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['account_id'], account_id)
        self.assertEqual(row['status'], 'pending')

    def test_scan_ignores_non_target_subject_and_filters_other_group(self):
        with self.app.app_context():
            source_id = self._create_source()
            target_group_id = self._create_group('Target Group')
            other_group_id = self._create_group('Other Group')
            target_hme = 'target@icloud.com'
            other_hme = 'other@icloud.com'
            self._create_hme_account(source_id, target_group_id, target_hme)
            self._create_hme_account(source_id, other_group_id, other_hme)
            self._cache_hme_address(source_id, target_hme, 'anon-target')
            self._cache_hme_address(source_id, other_hme, 'anon-other')
            self._create_source_message(source_id, target_hme, 'OpenAI - Welcome to ChatGPT')
            self._create_source_message(
                source_id,
                other_hme,
                'OpenAI - Access Deactivated [other-group]',
            )

        response = self.client.post(
            '/api/icloud-hme/deactivation-candidates/scan',
            json={'source_id': source_id, 'group_id': target_group_id},
        )

        data = response.get_json()
        self.assertEqual(response.status_code, 200, msg=data)
        self.assertTrue(data['success'], msg=data)
        self.assertEqual(data['candidate_count'], 0)
        self.assertEqual(data['candidates'], [])
        with self.app.app_context():
            count = web_outlook_app.get_db().execute(
                'SELECT COUNT(*) AS total FROM icloud_hme_deactivation_candidates'
            ).fetchone()['total']
        self.assertEqual(count, 0)

    def test_delete_candidates_deactivates_before_delete_and_marks_account_inactive(self):
        with self.app.app_context():
            source_id = self._create_source()
            group_id = self._create_group()
            hme = 'delete-me@icloud.com'
            account_id = self._create_hme_account(source_id, group_id, hme, remark='before')
            self._cache_hme_address(source_id, hme, 'anon-delete')
            candidate_id = self._create_candidate(source_id, account_id, hme)

        calls = []

        def fake_deactivate(_cookie, _region, _host, anonymous_id):
            calls.append(('deactivate', anonymous_id))
            return {'success': True}

        def fake_delete(_cookie, _region, _host, anonymous_id):
            calls.append(('delete', anonymous_id))
            return {'success': True}

        realtime_list = {
            'success': True,
            'list_complete': True,
            'hmeEmails': [{'hme': hme, 'anonymousId': 'anon-delete', 'isActive': True}],
        }
        with patch.object(web_outlook_app, 'fetch_icloud_hme_list', return_value=realtime_list), \
                patch.object(web_outlook_app, 'deactivate_icloud_hme', side_effect=fake_deactivate), \
                patch.object(web_outlook_app, 'delete_icloud_hme', side_effect=fake_delete):
            response = self.client.post(
                '/api/icloud-hme/deactivation-candidates/delete',
                json={'source_id': source_id, 'candidate_ids': [candidate_id]},
            )

        data = response.get_json()
        self.assertEqual(response.status_code, 200, msg=data)
        self.assertTrue(data['success'], msg=data)
        self.assertEqual(calls, [('deactivate', 'anon-delete'), ('delete', 'anon-delete')])
        self.assertEqual(data['results'], [{
            'id': candidate_id,
            'hme': hme,
            'state': 'deleted',
        }])
        with self.app.app_context():
            db = web_outlook_app.get_db()
            candidate = db.execute(
                'SELECT status, last_error, deleted_at FROM icloud_hme_deactivation_candidates WHERE id = ?',
                (candidate_id,),
            ).fetchone()
            account = db.execute(
                'SELECT status, remark FROM accounts WHERE id = ?',
                (account_id,),
            ).fetchone()
        self.assertEqual(candidate['status'], 'deleted')
        self.assertEqual(candidate['last_error'], '')
        self.assertIsNotNone(candidate['deleted_at'])
        self.assertEqual(account['status'], 'inactive')
        self.assertIn('HME deleted at', account['remark'])

    def test_delete_candidates_keeps_overall_success_and_marks_each_failed_item(self):
        with self.app.app_context():
            source_id = self._create_source()
            group_id = self._create_group()
            success_hme = 'success@icloud.com'
            failed_hme = 'failed@icloud.com'
            success_account_id = self._create_hme_account(source_id, group_id, success_hme)
            failed_account_id = self._create_hme_account(source_id, group_id, failed_hme)
            self._cache_hme_address(source_id, success_hme, 'anon-success')
            self._cache_hme_address(source_id, failed_hme, 'anon-failed')
            success_candidate_id = self._create_candidate(source_id, success_account_id, success_hme)
            failed_candidate_id = self._create_candidate(source_id, failed_account_id, failed_hme)

        def fake_delete(_cookie, _region, _host, anonymous_id):
            if anonymous_id == 'anon-failed':
                return {'success': False, 'error': 'delete failed'}
            return {'success': True}

        realtime_list = {
            'success': True,
            'list_complete': True,
            'hmeEmails': [
                {'hme': success_hme, 'anonymousId': 'anon-success', 'isActive': True},
                {'hme': failed_hme, 'anonymousId': 'anon-failed', 'isActive': True},
            ],
        }
        with patch.object(web_outlook_app, 'fetch_icloud_hme_list', return_value=realtime_list), \
                patch.object(web_outlook_app, 'deactivate_icloud_hme', return_value={'success': True}), \
                patch.object(web_outlook_app, 'delete_icloud_hme', side_effect=fake_delete):
            response = self.client.post(
                '/api/icloud-hme/deactivation-candidates/delete',
                json={'source_id': source_id, 'candidate_ids': [success_candidate_id, failed_candidate_id]},
            )

        data = response.get_json()
        self.assertEqual(response.status_code, 200, msg=data)
        self.assertTrue(data['success'], msg=data)
        result_by_id = {item['id']: item for item in data['results']}
        self.assertEqual(result_by_id[success_candidate_id]['state'], 'deleted')
        self.assertEqual(result_by_id[failed_candidate_id]['state'], 'failed')
        self.assertIn('delete failed', result_by_id[failed_candidate_id]['error'])
        with self.app.app_context():
            rows = web_outlook_app.get_db().execute(
                '''
                SELECT id, status, last_error
                FROM icloud_hme_deactivation_candidates
                WHERE id IN (?, ?)
                ORDER BY id
                ''',
                (success_candidate_id, failed_candidate_id),
            ).fetchall()
        status_by_id = {int(row['id']): row['status'] for row in rows}
        error_by_id = {int(row['id']): row['last_error'] for row in rows}
        self.assertEqual(status_by_id[success_candidate_id], 'deleted')
        self.assertEqual(status_by_id[failed_candidate_id], 'failed')
        self.assertIn('delete failed', error_by_id[failed_candidate_id])

    def test_delete_candidates_uses_one_realtime_list_and_latest_anonymous_id(self):
        with self.app.app_context():
            source_id = self._create_source()
            group_id = self._create_group()
            first_hme = 'first@icloud.com'
            second_hme = 'second@icloud.com'
            first_account_id = self._create_hme_account(source_id, group_id, first_hme)
            second_account_id = self._create_hme_account(source_id, group_id, second_hme)
            self._cache_hme_address(source_id, first_hme, 'stale-first')
            self._cache_hme_address(source_id, second_hme, 'stale-second')
            first_id = self._create_candidate(source_id, first_account_id, first_hme)
            second_id = self._create_candidate(source_id, second_account_id, second_hme)

        realtime_list = {
            'success': True,
            'list_complete': True,
            'hmeEmails': [
                {'hme': first_hme, 'anonymousId': 'fresh-first', 'isActive': True},
                {'hme': second_hme, 'anonymousId': 'fresh-second', 'isActive': True},
            ],
        }
        calls = []

        def record_action(_cookie, _region, _host, anonymous_id):
            calls.append(anonymous_id)
            return {'success': True}

        with patch.object(web_outlook_app, 'fetch_icloud_hme_list', return_value=realtime_list) as fetch_mock, \
                patch.object(web_outlook_app, 'deactivate_icloud_hme', side_effect=record_action), \
                patch.object(web_outlook_app, 'delete_icloud_hme', side_effect=record_action):
            response = self.client.post(
                '/api/icloud-hme/deactivation-candidates/delete',
                json={'source_id': source_id, 'candidate_ids': [first_id, second_id]},
            )

        data = response.get_json()
        self.assertEqual(response.status_code, 200, msg=data)
        self.assertEqual(fetch_mock.call_count, 1)
        self.assertEqual(calls, ['fresh-first', 'fresh-first', 'fresh-second', 'fresh-second'])

    def test_delete_candidate_already_absent_finishes_locally_without_apple_actions(self):
        with self.app.app_context():
            source_id = self._create_source()
            group_id = self._create_group()
            hme = 'already-gone@icloud.com'
            account_id = self._create_hme_account(source_id, group_id, hme, remark='before')
            self._cache_hme_address(source_id, hme, 'stale-anonymous-id')
            candidate_id = self._create_candidate(source_id, account_id, hme)

        realtime_list = {'success': True, 'list_complete': True, 'hmeEmails': []}
        with patch.object(web_outlook_app, 'fetch_icloud_hme_list', return_value=realtime_list), \
                patch.object(web_outlook_app, 'deactivate_icloud_hme') as deactivate_mock, \
                patch.object(web_outlook_app, 'delete_icloud_hme') as delete_mock:
            response = self.client.post(
                '/api/icloud-hme/deactivation-candidates/delete',
                json={'source_id': source_id, 'candidate_ids': [candidate_id]},
            )

        data = response.get_json()
        self.assertEqual(response.status_code, 200, msg=data)
        self.assertEqual(data['already_absent_count'], 1)
        self.assertEqual(data['error_count'], 0)
        self.assertEqual(data['results'][0]['state'], 'already_absent')
        deactivate_mock.assert_not_called()
        delete_mock.assert_not_called()
        with self.app.app_context():
            db = web_outlook_app.get_db()
            candidate = db.execute(
                'SELECT status, last_error, deleted_at FROM icloud_hme_deactivation_candidates WHERE id = ?',
                (candidate_id,),
            ).fetchone()
            cache = db.execute(
                'SELECT status FROM icloud_hme_address_cache WHERE source_id = ? AND hme = ?',
                (source_id, hme),
            ).fetchone()
            account = db.execute('SELECT status, remark FROM accounts WHERE id = ?', (account_id,)).fetchone()
        self.assertEqual(candidate['status'], 'already_absent')
        self.assertEqual(candidate['last_error'], '')
        self.assertIsNotNone(candidate['deleted_at'])
        self.assertEqual(cache['status'], 'deleted')
        self.assertEqual(account['status'], 'inactive')
        self.assertIn('HME deleted at', account['remark'])

    def test_delete_candidates_stops_when_realtime_list_fails(self):
        with self.app.app_context():
            source_id = self._create_source()
            group_id = self._create_group()
            hme = 'keep-safe@icloud.com'
            account_id = self._create_hme_account(source_id, group_id, hme)
            self._cache_hme_address(source_id, hme, 'stale-id')
            candidate_id = self._create_candidate(source_id, account_id, hme)

        with patch.object(web_outlook_app, 'fetch_icloud_hme_list', return_value={
            'success': False,
            'error': 'list unavailable',
        }), patch.object(web_outlook_app, 'deactivate_icloud_hme') as deactivate_mock, \
                patch.object(web_outlook_app, 'delete_icloud_hme') as delete_mock:
            response = self.client.post(
                '/api/icloud-hme/deactivation-candidates/delete',
                json={'source_id': source_id, 'candidate_ids': [candidate_id]},
            )

        data = response.get_json()
        self.assertEqual(response.status_code, 502, msg=data)
        self.assertFalse(data['success'])
        self.assertIn('list unavailable', data['error'])
        deactivate_mock.assert_not_called()
        delete_mock.assert_not_called()

    def test_realtime_present_minus_41003_stays_failed(self):
        with self.app.app_context():
            source_id = self._create_source()
            group_id = self._create_group()
            hme = 'still-listed@icloud.com'
            account_id = self._create_hme_account(source_id, group_id, hme)
            self._cache_hme_address(source_id, hme, 'stale-id')
            candidate_id = self._create_candidate(source_id, account_id, hme)

        realtime_list = {
            'success': True,
            'list_complete': True,
            'hmeEmails': [{'hme': hme, 'anonymousId': 'fresh-id', 'isActive': True}],
        }
        with patch.object(web_outlook_app, 'fetch_icloud_hme_list', return_value=realtime_list), \
                patch.object(web_outlook_app, 'deactivate_icloud_hme', return_value={
                    'success': False,
                    'error': 'Invalid private email in request (errorCode -41003, retryAfter 2s)',
                }), patch.object(web_outlook_app, 'delete_icloud_hme') as delete_mock:
            response = self.client.post(
                '/api/icloud-hme/deactivation-candidates/delete',
                json={'source_id': source_id, 'candidate_ids': [candidate_id]},
            )

        data = response.get_json()
        self.assertEqual(response.status_code, 200, msg=data)
        self.assertEqual(data['results'][0]['state'], 'failed')
        self.assertIn('-41003', data['results'][0]['error'])
        delete_mock.assert_not_called()


if __name__ == '__main__':
    unittest.main()
