import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


os.environ.setdefault('SECRET_KEY', 'test-secret-key')
if 'DATABASE_PATH' not in os.environ:
    _temp_dir = tempfile.mkdtemp(prefix='outlookEmail-temp-email-groups-tests-')
    os.environ['DATABASE_PATH'] = os.path.join(_temp_dir, 'test.db')

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

web_outlook_app = importlib.import_module('web_outlook_app')


class TempEmailGroupTests(unittest.TestCase):
    def setUp(self):
        self.app = web_outlook_app.app
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()

        with self.app.app_context():
            web_outlook_app.init_db()
            db = web_outlook_app.get_db()
            db.execute('DELETE FROM temp_email_tags')
            db.execute('DELETE FROM temp_email_messages')
            db.execute('DELETE FROM temp_email_shares')
            db.execute('DELETE FROM temp_emails')
            db.execute('DELETE FROM accounts')
            db.execute("DELETE FROM groups WHERE name NOT IN ('默认分组', '临时邮箱')")
            db.execute(
                "UPDATE groups SET sort_order = CASE WHEN name = '临时邮箱' THEN 0 ELSE 1 END"
            )
            db.commit()

        with self.client.session_transaction() as sess:
            sess['logged_in'] = True

    def _table_columns(self, table_name):
        db = web_outlook_app.get_db()
        return {row['name'] for row in db.execute(f'PRAGMA table_info({table_name})').fetchall()}

    def _default_group(self):
        db = web_outlook_app.get_db()
        return db.execute("SELECT * FROM groups WHERE name = '默认分组'").fetchone()

    def _default_temp_group(self):
        db = web_outlook_app.get_db()
        return db.execute("SELECT * FROM groups WHERE name = '临时邮箱'").fetchone()

    def _create_group(self, name, mailbox_type='account', sort_order=10):
        db = web_outlook_app.get_db()
        columns = self._table_columns('groups')
        if 'mailbox_type' in columns:
            db.execute(
                '''
                INSERT INTO groups (name, mailbox_type, description, color, sort_order)
                VALUES (?, ?, '', '#1a1a1a', ?)
                ''',
                (name, mailbox_type, sort_order)
            )
        else:
            db.execute(
                '''
                INSERT INTO groups (name, description, color, sort_order)
                VALUES (?, '', '#1a1a1a', ?)
                ''',
                (name, sort_order)
            )
        db.commit()
        row = db.execute('SELECT * FROM groups WHERE name = ?', (name,)).fetchone()
        self.assertIsNotNone(row)
        return row

    def _insert_temp_email(self, email_addr, group_id=None, provider='gptmail'):
        db = web_outlook_app.get_db()
        columns = self._table_columns('temp_emails')
        if 'group_id' in columns:
            db.execute(
                'INSERT INTO temp_emails (email, status, provider, group_id) VALUES (?, ?, ?, ?)',
                (email_addr, 'active', provider, group_id)
            )
        else:
            db.execute(
                'INSERT INTO temp_emails (email, status, provider) VALUES (?, ?, ?)',
                (email_addr, 'active', provider)
            )
        db.commit()
        row = db.execute('SELECT * FROM temp_emails WHERE email = ?', (email_addr,)).fetchone()
        self.assertIsNotNone(row)
        return row

    def _insert_account(self, email_addr, group_id):
        db = web_outlook_app.get_db()
        db.execute(
            '''
            INSERT INTO accounts (
                email, password, client_id, refresh_token, group_id, status, account_type, provider
            ) VALUES (?, 'pwd', 'client-id', 'refresh-token', ?, 'active', 'outlook', 'outlook')
            ''',
            (email_addr, group_id)
        )
        db.commit()
        row = db.execute('SELECT * FROM accounts WHERE email = ?', (email_addr,)).fetchone()
        self.assertIsNotNone(row)
        return row

    def test_init_db_adds_group_type_and_temp_email_group_id(self):
        with self.app.app_context():
            db = web_outlook_app.get_db()
            group_columns = {row['name'] for row in db.execute('PRAGMA table_info(groups)').fetchall()}
            temp_columns = {row['name'] for row in db.execute('PRAGMA table_info(temp_emails)').fetchall()}
            self.assertIn('mailbox_type', group_columns)
            self.assertIn('group_id', temp_columns)
            temp_group = db.execute("SELECT * FROM groups WHERE name = '临时邮箱'").fetchone()
            default_group = db.execute("SELECT * FROM groups WHERE name = '默认分组'").fetchone()
            self.assertEqual(temp_group['mailbox_type'], 'temp_email')
            self.assertEqual(default_group['mailbox_type'], 'account')

    def test_existing_temp_emails_are_backfilled_to_default_temp_group(self):
        with self.app.app_context():
            db = web_outlook_app.get_db()
            temp_columns = self._table_columns('temp_emails')
            self.assertIn('group_id', temp_columns)
            db.execute(
                "INSERT INTO temp_emails (email, status, group_id) VALUES ('legacy@example.com', 'active', NULL)"
            )
            db.commit()

            web_outlook_app.init_db()

            temp_group = self._default_temp_group()
            rows = db.execute('SELECT email, group_id FROM temp_emails').fetchall()
            self.assertTrue(rows)
            self.assertEqual([], [row['email'] for row in rows if row['group_id'] is None])
            self.assertEqual(
                ['legacy@example.com'],
                [row['email'] for row in rows if row['group_id'] == temp_group['id']]
            )

    def test_groups_api_counts_by_mailbox_type(self):
        with self.app.app_context():
            default_group = self._default_group()
            temp_group = self._default_temp_group()
            self._insert_account('account@example.com', default_group['id'])
            self._insert_temp_email('first-temp@example.com', temp_group['id'])
            self._insert_temp_email('second-temp@example.com', temp_group['id'])

        response = self.client.get('/api/groups')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        groups = {group['name']: group for group in payload['groups']}
        self.assertEqual(groups['默认分组']['mailbox_type'], 'account')
        self.assertEqual(groups['默认分组']['account_count'], 1)
        self.assertEqual(groups['临时邮箱']['mailbox_type'], 'temp_email')
        self.assertEqual(groups['临时邮箱']['account_count'], 2)

    def test_get_temp_emails_filters_by_group_id(self):
        with self.app.app_context():
            first_group = self._create_group('临时邮箱 A', mailbox_type='temp_email', sort_order=2)
            second_group = self._create_group('临时邮箱 B', mailbox_type='temp_email', sort_order=3)
            self._insert_temp_email('first-filter@example.com', first_group['id'])
            self._insert_temp_email('second-filter@example.com', second_group['id'])

        response = self.client.get(f'/api/temp-emails?group_id={first_group["id"]}')

        self.assertEqual(response.status_code, 200)
        emails = response.get_json()['emails']
        self.assertEqual(['first-filter@example.com'], [item['email'] for item in emails])
        self.assertTrue(all(item['group_id'] == first_group['id'] for item in emails))

    def test_generate_temp_email_defaults_to_default_temp_group(self):
        with patch.object(web_outlook_app, 'generate_temp_email', return_value='generated@example.com'):
            response = self.client.post('/api/temp-emails/generate', json={'provider': 'gptmail'})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        with self.app.app_context():
            row = web_outlook_app.get_db().execute(
                'SELECT * FROM temp_emails WHERE email = ?',
                ('generated@example.com',)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['group_id'], self._default_temp_group()['id'])

    def test_import_temp_email_accepts_temp_group_and_rejects_account_group(self):
        with self.app.app_context():
            temp_group = self._create_group('导入临时邮箱组', mailbox_type='temp_email', sort_order=2)
            account_group = self._default_group()

        accepted = self.client.post('/api/temp-emails/import', json={
            'provider': 'gptmail',
            'group_id': temp_group['id'],
            'account_string': 'accepted-temp@example.com',
        })
        rejected = self.client.post('/api/temp-emails/import', json={
            'provider': 'gptmail',
            'group_id': account_group['id'],
            'account_string': 'rejected-temp@example.com',
        })

        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(accepted.get_json()['success'])
        with self.app.app_context():
            row = web_outlook_app.get_db().execute(
                'SELECT * FROM temp_emails WHERE email = ?',
                ('accepted-temp@example.com',)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['group_id'], temp_group['id'])
        self.assertEqual(rejected.status_code, 400)
        self.assertFalse(rejected.get_json()['success'])

    def test_account_cannot_move_to_temp_email_group(self):
        with self.app.app_context():
            account_group = self._default_group()
            temp_group = self._create_group('账号不可移入临时组', mailbox_type='temp_email', sort_order=2)
            account = self._insert_account('move-account@example.com', account_group['id'])

        response = self.client.post('/api/accounts/batch-update-group', json={
            'account_ids': [account['id']],
            'group_id': temp_group['id'],
        })

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])

    def test_temp_email_can_move_only_to_temp_group(self):
        with self.app.app_context():
            account_group = self._default_group()
            source_group = self._create_group('移动来源临时组', mailbox_type='temp_email', sort_order=2)
            target_group = self._create_group('移动目标临时组', mailbox_type='temp_email', sort_order=3)
            temp_email = self._insert_temp_email('move-temp@example.com', source_group['id'])

        rejected = self.client.post('/api/temp-emails/batch-update-group', json={
            'temp_email_ids': [temp_email['id']],
            'group_id': account_group['id'],
        })
        accepted = self.client.post('/api/temp-emails/batch-update-group', json={
            'temp_email_ids': [temp_email['id']],
            'group_id': target_group['id'],
        })

        self.assertEqual(rejected.status_code, 400)
        self.assertFalse(rejected.get_json()['success'])
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(accepted.get_json()['success'])
        with self.app.app_context():
            row = web_outlook_app.get_db().execute(
                'SELECT * FROM temp_emails WHERE id = ?',
                (temp_email['id'],)
            ).fetchone()
            self.assertEqual(row['group_id'], target_group['id'])

    def test_system_temp_group_cannot_be_deleted_but_can_be_sorted(self):
        with self.app.app_context():
            temp_group = self._default_temp_group()
            account_group = self._default_group()
            custom_group = self._create_group('排序普通分组', mailbox_type='account', sort_order=2)

        delete_response = self.client.delete(f'/api/groups/{temp_group["id"]}')
        reorder_response = self.client.put('/api/groups/reorder', json={
            'group_ids': [custom_group['id'], temp_group['id'], account_group['id']],
        })

        self.assertEqual(delete_response.status_code, 400)
        self.assertFalse(delete_response.get_json()['success'])
        self.assertEqual(reorder_response.status_code, 200)
        self.assertTrue(reorder_response.get_json()['success'])
        with self.app.app_context():
            rows = web_outlook_app.get_db().execute(
                '''
                SELECT id FROM groups
                WHERE id IN (?, ?, ?)
                ORDER BY sort_order, id
                ''',
                (custom_group['id'], temp_group['id'], account_group['id'])
            ).fetchall()
            self.assertEqual(
                [custom_group['id'], temp_group['id'], account_group['id']],
                [row['id'] for row in rows]
            )


if __name__ == '__main__':
    unittest.main()
