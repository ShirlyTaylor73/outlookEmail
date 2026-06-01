import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest


os.environ.setdefault('SECRET_KEY', 'test-secret-key')
_temp_dir = tempfile.mkdtemp(prefix='outlookEmail-mailbox-claim-tests-')
os.environ['DATABASE_PATH'] = os.path.join(_temp_dir, 'test.db')

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

web_outlook_app = importlib.import_module('web_outlook_app')


class ExternalMailboxClaimTests(unittest.TestCase):
    def setUp(self):
        self.app = web_outlook_app.app
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()

        with self.app.app_context():
            web_outlook_app.init_db()
            db = web_outlook_app.get_db()
            for table in (
                'mailbox_claims',
                'account_tags',
                'account_aliases',
                'account_refresh_logs',
                'accounts',
                'temp_email_tags',
                'temp_email_messages',
                'temp_emails',
            ):
                self._delete_table_if_exists(db, table)
            db.execute("DELETE FROM groups WHERE name NOT IN ('默认分组', '临时邮箱')")
            db.commit()

            self.assertTrue(web_outlook_app.set_setting('external_api_key', 'test-external-key'))
            self.account_group_id = self._ensure_group('Claim Account Source', 'account')
            self.account_target_group_id = self._ensure_group('Claim Account Target', 'account')
            self.temp_group_id = self._ensure_group('Claim Temp Source', 'temp_email')
            self.temp_target_group_id = self._ensure_group('Claim Temp Target', 'temp_email')

    def _delete_table_if_exists(self, db, table):
        try:
            db.execute(f'DELETE FROM {table}')
        except sqlite3.OperationalError as exc:
            if 'no such table' not in str(exc):
                raise

    def _table_columns(self, db, table):
        return [row['name'] for row in db.execute(f'PRAGMA table_info({table})').fetchall()]

    def _ensure_group(self, name, mailbox_type):
        db = web_outlook_app.get_db()
        existing = db.execute('SELECT id FROM groups WHERE name = ?', (name,)).fetchone()
        if existing:
            return existing['id']

        columns = self._table_columns(db, 'groups')
        type_column = next(
            (candidate for candidate in ('mailbox_type', 'group_type', 'type') if candidate in columns),
            None,
        )
        if type_column:
            cursor = db.execute(
                f'''
                INSERT INTO groups (name, description, color, is_system, {type_column})
                VALUES (?, ?, ?, 0, ?)
                ''',
                (name, f'{mailbox_type} claim tests', '#123456', mailbox_type),
            )
        else:
            cursor = db.execute(
                '''
                INSERT INTO groups (name, description, color, is_system)
                VALUES (?, ?, ?, 0)
                ''',
                (name, f'{mailbox_type} claim tests', '#123456'),
            )
        db.commit()
        return cursor.lastrowid

    def _headers(self):
        return {'X-API-Key': 'test-external-key'}

    def _insert_account(self, email_addr, group_id, status='active'):
        with self.app.app_context():
            db = web_outlook_app.get_db()
            cursor = db.execute(
                '''
                INSERT INTO accounts (
                    email, password, client_id, refresh_token, group_id, remark, status,
                    account_type, provider, imap_host, imap_port, imap_password, proxy_url
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    email_addr,
                    'password-secret',
                    'client-secret',
                    'refresh-secret',
                    group_id,
                    'claim test account',
                    status,
                    'imap',
                    'custom',
                    'imap.example.com',
                    993,
                    'imap-password-secret',
                    'http://proxy-secret',
                ),
            )
            db.commit()
            return cursor.lastrowid

    def _insert_temp_email(self, email_addr, group_id, status='active'):
        with self.app.app_context():
            db = web_outlook_app.get_db()
            columns = self._table_columns(db, 'temp_emails')
            insert_columns = ['email', 'status', 'provider']
            values = [email_addr, status, 'duckmail']
            if 'group_id' in columns:
                insert_columns.append('group_id')
                values.append(group_id)
            secret_values = {
                'duckmail_token': 'duck-token-secret',
                'duckmail_password': 'duck-password-secret',
                'cloudflare_jwt': 'cloudflare-jwt-secret',
            }
            for column, value in secret_values.items():
                if column in columns:
                    insert_columns.append(column)
                    values.append(value)

            placeholders = ', '.join('?' for _ in insert_columns)
            cursor = db.execute(
                f'INSERT INTO temp_emails ({", ".join(insert_columns)}) VALUES ({placeholders})',
                values,
            )
            db.commit()
            return cursor.lastrowid

    def _claim(self, source_group_id=None, caller_id='worker-1', task_id='task-1', headers=None):
        payload = {}
        if source_group_id is not None:
            payload['source_group_id'] = source_group_id
        if caller_id is not None:
            payload['caller_id'] = caller_id
        if task_id is not None:
            payload['task_id'] = task_id
        return self.client.post(
            '/api/external/mailboxes/claim',
            json=payload,
            headers=self._headers() if headers is None else headers,
        )

    def _release(self, claim_token, headers=None):
        return self.client.post(
            '/api/external/mailboxes/release',
            json={'claim_token': claim_token},
            headers=self._headers() if headers is None else headers,
        )

    def _complete(self, claim_token, target_group_id, headers=None):
        return self.client.post(
            '/api/external/mailboxes/complete',
            json={'claim_token': claim_token, 'target_group_id': target_group_id},
            headers=self._headers() if headers is None else headers,
        )

    def _mailbox(self, response):
        payload = response.get_json()
        self.assertTrue(payload['success'])
        return payload['mailbox']

    def _assert_claim_mailbox_fields(self, mailbox):
        self.assertEqual(
            set(mailbox.keys()),
            {'resource_type', 'resource_id', 'email', 'group_id', 'claim_token', 'lease_expires_at'},
        )

    def _claim_status(self, claim_token):
        with self.app.app_context():
            db = web_outlook_app.get_db()
            row = db.execute(
                'SELECT status FROM mailbox_claims WHERE claim_token = ?',
                (claim_token,),
            ).fetchone()
            return row['status'] if row else None

    def test_claim_requires_api_key(self):
        response = self._claim(self.account_group_id, headers={})

        self.assertEqual(response.status_code, 401)

    def test_claim_rejects_missing_source_group(self):
        response = self._claim(source_group_id=None)

        self.assertEqual(response.status_code, 400)

    def test_claim_rejects_missing_caller_or_task(self):
        missing_caller = self._claim(self.account_group_id, caller_id=None)
        missing_task = self._claim(self.account_group_id, task_id=None)

        self.assertEqual(missing_caller.status_code, 400)
        self.assertEqual(missing_task.status_code, 400)

    def test_claim_unknown_source_group_returns_404(self):
        response = self._claim(999999)

        self.assertEqual(response.status_code, 404)

    def test_claim_account_group_returns_oldest_active_account(self):
        first_id = self._insert_account('first@example.com', self.account_group_id)
        self._insert_account('second@example.com', self.account_group_id)

        response = self._claim(self.account_group_id)

        self.assertEqual(response.status_code, 200)
        mailbox = self._mailbox(response)
        self.assertEqual(mailbox['resource_type'], 'account')
        self.assertEqual(mailbox['resource_id'], first_id)
        self.assertEqual(mailbox['email'], 'first@example.com')
        self.assertEqual(mailbox['group_id'], self.account_group_id)
        self.assertTrue(mailbox['claim_token'])
        self.assertTrue(mailbox['lease_expires_at'])
        self._assert_claim_mailbox_fields(mailbox)
        serialized = json.dumps(mailbox, ensure_ascii=False)
        for forbidden in ('password', 'refresh_token', 'imap_password', 'proxy_url'):
            self.assertNotIn(forbidden, serialized)

    def test_claim_temp_email_group_returns_oldest_active_temp_email(self):
        first_id = self._insert_temp_email('first-temp@example.com', self.temp_group_id)
        self._insert_temp_email('second-temp@example.com', self.temp_group_id)

        response = self._claim(self.temp_group_id)

        self.assertEqual(response.status_code, 200)
        mailbox = self._mailbox(response)
        self.assertEqual(mailbox['resource_type'], 'temp_email')
        self.assertEqual(mailbox['resource_id'], first_id)
        self.assertEqual(mailbox['email'], 'first-temp@example.com')
        self.assertEqual(mailbox['group_id'], self.temp_group_id)
        self.assertTrue(mailbox['claim_token'])
        self.assertTrue(mailbox['lease_expires_at'])
        self._assert_claim_mailbox_fields(mailbox)
        serialized = json.dumps(mailbox, ensure_ascii=False)
        for forbidden in ('duckmail_token', 'duckmail_password', 'cloudflare_jwt'):
            self.assertNotIn(forbidden, serialized)

    def test_claim_returns_null_when_no_active_mailbox(self):
        self._insert_account('inactive@example.com', self.account_group_id, status='disabled')

        response = self._claim(self.account_group_id)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()['mailbox'])

    def test_claim_twice_does_not_duplicate_resource(self):
        first_id = self._insert_account('first@example.com', self.account_group_id)
        second_id = self._insert_account('second@example.com', self.account_group_id)

        first = self._mailbox(self._claim(self.account_group_id))
        second = self._mailbox(self._claim(self.account_group_id, caller_id='worker-2', task_id='task-2'))
        third = self._claim(self.account_group_id, caller_id='worker-3', task_id='task-3')

        self.assertEqual({first['resource_id'], second['resource_id']}, {first_id, second_id})
        self.assertNotEqual(first['resource_id'], second['resource_id'])
        self.assertEqual(third.status_code, 200)
        self.assertIsNone(third.get_json()['mailbox'])

    def test_claim_single_resource_returns_null_on_second_claim(self):
        account_id = self._insert_account('single@example.com', self.account_group_id)

        first = self._mailbox(self._claim(self.account_group_id))
        second = self._claim(self.account_group_id, caller_id='worker-2', task_id='task-2')

        self.assertEqual(first['resource_id'], account_id)
        self.assertEqual(second.status_code, 200)
        self.assertIsNone(second.get_json()['mailbox'])

    def test_release_keeps_resource_in_source_group_and_allows_reclaim(self):
        account_id = self._insert_account('release@example.com', self.account_group_id)
        first = self._mailbox(self._claim(self.account_group_id))

        release = self._release(first['claim_token'])
        second = self._mailbox(self._claim(self.account_group_id, caller_id='worker-2', task_id='task-2'))

        self.assertEqual(release.status_code, 200)
        self.assertEqual(second['resource_id'], account_id)
        self.assertNotEqual(second['claim_token'], first['claim_token'])
        with self.app.app_context():
            db = web_outlook_app.get_db()
            group_id = db.execute('SELECT group_id FROM accounts WHERE id = ?', (account_id,)).fetchone()['group_id']
        self.assertEqual(group_id, self.account_group_id)

    def test_release_with_wrong_token_returns_409(self):
        self._insert_account('wrong-release@example.com', self.account_group_id)

        response = self._release('wrong-token')

        self.assertEqual(response.status_code, 409)

    def test_complete_account_moves_to_account_target_group(self):
        account_id = self._insert_account('complete@example.com', self.account_group_id)
        mailbox = self._mailbox(self._claim(self.account_group_id))

        response = self._complete(mailbox['claim_token'], self.account_target_group_id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._claim_status(mailbox['claim_token']), 'completed')
        with self.app.app_context():
            db = web_outlook_app.get_db()
            group_id = db.execute('SELECT group_id FROM accounts WHERE id = ?', (account_id,)).fetchone()['group_id']
        self.assertEqual(group_id, self.account_target_group_id)

    def test_complete_temp_email_moves_to_temp_target_group(self):
        temp_email_id = self._insert_temp_email('complete-temp@example.com', self.temp_group_id)
        mailbox = self._mailbox(self._claim(self.temp_group_id))

        response = self._complete(mailbox['claim_token'], self.temp_target_group_id)

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            db = web_outlook_app.get_db()
            group_id = db.execute(
                'SELECT group_id FROM temp_emails WHERE id = ?',
                (temp_email_id,),
            ).fetchone()['group_id']
        self.assertEqual(group_id, self.temp_target_group_id)

    def test_complete_rejects_target_type_mismatch(self):
        self._insert_account('mismatch@example.com', self.account_group_id)
        mailbox = self._mailbox(self._claim(self.account_group_id))

        response = self._complete(mailbox['claim_token'], self.temp_target_group_id)

        self.assertEqual(response.status_code, 400)

    def test_complete_with_wrong_token_returns_409(self):
        self._insert_account('wrong-complete@example.com', self.account_group_id)

        response = self._complete('wrong-token', self.account_target_group_id)

        self.assertEqual(response.status_code, 409)

    def test_claim_lazily_expires_old_claims(self):
        self._insert_account('expire-one@example.com', self.account_group_id)
        self._insert_account('expire-two@example.com', self.account_group_id)
        first = self._mailbox(self._claim(self.account_group_id))
        with self.app.app_context():
            db = web_outlook_app.get_db()
            db.execute(
                "UPDATE mailbox_claims SET lease_expires_at = datetime('now', '-1 minute') WHERE claim_token = ?",
                (first['claim_token'],),
            )
            db.commit()

        second = self._claim(self.account_group_id, caller_id='worker-2', task_id='task-2')

        self.assertEqual(second.status_code, 200)
        self.assertEqual(self._claim_status(first['claim_token']), 'expired')

    def test_late_complete_after_expired_allowed_if_not_reclaimed(self):
        account_id = self._insert_account('late-complete@example.com', self.account_group_id)
        mailbox = self._mailbox(self._claim(self.account_group_id))
        with self.app.app_context():
            db = web_outlook_app.get_db()
            db.execute(
                "UPDATE mailbox_claims SET status = 'expired', lease_expires_at = datetime('now', '-1 minute') "
                "WHERE claim_token = ?",
                (mailbox['claim_token'],),
            )
            db.commit()

        response = self._complete(mailbox['claim_token'], self.account_target_group_id)

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            db = web_outlook_app.get_db()
            group_id = db.execute('SELECT group_id FROM accounts WHERE id = ?', (account_id,)).fetchone()['group_id']
        self.assertEqual(group_id, self.account_target_group_id)

    def test_late_release_after_expired_returns_409(self):
        self._insert_account('late-release@example.com', self.account_group_id)
        mailbox = self._mailbox(self._claim(self.account_group_id))
        with self.app.app_context():
            db = web_outlook_app.get_db()
            db.execute(
                "UPDATE mailbox_claims SET status = 'expired', lease_expires_at = datetime('now', '-1 minute') "
                "WHERE claim_token = ?",
                (mailbox['claim_token'],),
            )
            db.commit()

        response = self._release(mailbox['claim_token'])

        self.assertEqual(response.status_code, 409)

    def test_old_token_complete_returns_409_after_resource_reclaimed(self):
        self._insert_account('reclaimed@example.com', self.account_group_id)
        old_mailbox = self._mailbox(self._claim(self.account_group_id))
        with self.app.app_context():
            db = web_outlook_app.get_db()
            db.execute(
                "UPDATE mailbox_claims SET status = 'expired', lease_expires_at = datetime('now', '-1 minute') "
                "WHERE claim_token = ?",
                (old_mailbox['claim_token'],),
            )
            db.commit()
        new_mailbox = self._mailbox(self._claim(self.account_group_id, caller_id='worker-2', task_id='task-2'))

        response = self._complete(old_mailbox['claim_token'], self.account_target_group_id)

        self.assertNotEqual(new_mailbox['claim_token'], old_mailbox['claim_token'])
        self.assertEqual(response.status_code, 409)

    def test_old_token_release_returns_409_after_resource_reclaimed(self):
        self._insert_account('release-reclaimed@example.com', self.account_group_id)
        old_mailbox = self._mailbox(self._claim(self.account_group_id))
        with self.app.app_context():
            db = web_outlook_app.get_db()
            db.execute(
                "UPDATE mailbox_claims SET status = 'expired', lease_expires_at = datetime('now', '-1 minute') "
                "WHERE claim_token = ?",
                (old_mailbox['claim_token'],),
            )
            db.commit()
        new_mailbox = self._mailbox(self._claim(self.account_group_id, caller_id='worker-2', task_id='task-2'))

        response = self._release(old_mailbox['claim_token'])

        self.assertNotEqual(new_mailbox['claim_token'], old_mailbox['claim_token'])
        self.assertEqual(response.status_code, 409)


if __name__ == '__main__':
    unittest.main()
