import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


os.environ.setdefault('SECRET_KEY', 'test-secret-key')
_temp_dir = tempfile.mkdtemp(prefix='outlookEmail-icloud-hme-external-tests-')
os.environ['DATABASE_PATH'] = os.path.join(_temp_dir, 'test.db')

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

web_outlook_app = importlib.import_module('web_outlook_app')


class IcloudHmeExternalApiTests(unittest.TestCase):
    def setUp(self):
        self.app = web_outlook_app.app
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()
        self.api_headers = {'X-API-Key': 'test-external-key'}

        with self.app.app_context():
            web_outlook_app.init_db()
            db = web_outlook_app.get_db()
            for table in (
                'mailbox_claims',
                'account_tags',
                'account_aliases',
                'account_refresh_logs',
                'accounts',
                'icloud_hme_sources',
                'tags',
            ):
                self._delete_table_if_exists(db, table)
            db.execute("DELETE FROM groups WHERE name NOT IN ('默认分组', '临时邮箱')")
            db.commit()
            self.assertTrue(web_outlook_app.set_setting('external_api_key', 'test-external-key'))

        state = getattr(web_outlook_app, 'EXTERNAL_VERIFICATION_REFRESH_STATE', None)
        if isinstance(state, dict):
            state.clear()

    def _delete_table_if_exists(self, db, table):
        try:
            db.execute(f'DELETE FROM {table}')
        except sqlite3.OperationalError as exc:
            if 'no such table' not in str(exc):
                raise

    def _create_source(self, password='receiver-secret', cookie='cookie-secret'):
        with self.app.app_context():
            db = web_outlook_app.get_db()
            cursor = db.execute(
                '''
                INSERT INTO icloud_hme_sources (
                    name, region, receiver_email, receiver_provider, receiver_imap_host,
                    receiver_imap_port, receiver_imap_password, receiver_folder, use_ssl,
                    cookie, maildomain_host
                )
                VALUES (?, 'global', ?, 'custom', 'imap.example.com', 993, ?, 'INBOX', 1, ?, ?)
                ''',
                (
                    'HME Source',
                    'receiver@example.com',
                    web_outlook_app.encrypt_data(password),
                    web_outlook_app.encrypt_data(cookie),
                    'maildomain.icloud.com',
                ),
            )
            db.commit()
            return cursor.lastrowid

    def _create_hme_account(self, email_addr, source_id, group_id=1):
        with self.app.app_context():
            db = web_outlook_app.get_db()
            cursor = db.execute(
                '''
                INSERT INTO accounts (
                    email, password, client_id, refresh_token, group_id, remark, status,
                    account_type, provider, imap_host, imap_port, imap_password,
                    proxy_url, icloud_hme_source_id
                )
                VALUES (?, '', '', '', ?, 'hme external test', 'active',
                        'icloud_hme', 'icloud_hme', '', 993, '', '', ?)
                ''',
                (email_addr, group_id, source_id),
            )
            db.commit()
            return cursor.lastrowid

    def test_external_accounts_includes_hme_without_source_secrets(self):
        source_id = self._create_source(password='secret', cookie='cookie-secret')
        account_id = self._create_hme_account('abc@icloud.com', source_id)

        response = self.client.get('/api/external/accounts', headers=self.api_headers)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        mailbox = next(item for item in payload['accounts'] if item['id'] == account_id)
        self.assertEqual(mailbox['resource_type'], 'account')
        self.assertEqual(mailbox['account_type'], 'icloud_hme')
        self.assertEqual(mailbox['provider'], 'icloud_hme')
        self.assertNotIn('receiver_imap_password', mailbox)
        self.assertNotIn('cookie', mailbox)
        self.assertNotIn('maildomain_host', mailbox)
        serialized = json.dumps(mailbox, ensure_ascii=False)
        self.assertNotIn('secret', serialized)
        self.assertNotIn('cookie-secret', serialized)

    def test_external_emails_fetches_hme_account_emails(self):
        source_id = self._create_source()
        account_id = self._create_hme_account('abc@icloud.com', source_id)
        list_result = {
            'success': True,
            'emails': [
                {
                    'id': 'hme-1',
                    'subject': 'HME message',
                    'from': 'sender@example.com',
                    'body_preview': 'hello',
                    'folder': 'inbox',
                    'method': 'imap',
                    'id_mode': 'uid',
                },
            ],
            'has_more': False,
        }

        with patch.object(web_outlook_app, 'fetch_account_emails', return_value=list_result) as fetch_mock:
            response = self.client.get('/api/external/emails?email=abc@icloud.com', headers=self.api_headers)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['emails'][0]['id'], 'hme-1')
        fetch_mock.assert_called_once()
        account_arg, folder_arg, skip_arg, top_arg = fetch_mock.call_args.args
        self.assertEqual(account_arg['id'], account_id)
        self.assertEqual(account_arg['account_type'], 'icloud_hme')
        self.assertEqual(account_arg['provider'], 'icloud_hme')
        self.assertEqual(folder_arg, 'inbox')
        self.assertEqual(skip_arg, 0)
        self.assertEqual(top_arg, 1)

    def test_external_verification_code_uses_hme_detail_reader(self):
        source_id = self._create_source()
        self._create_hme_account('abc@icloud.com', source_id)
        list_result = {
            'success': True,
            'emails': [
                {
                    'id': '42',
                    'subject': 'Verification code',
                    'from': 'noreply@example.com',
                    'body_preview': 'Use this code',
                    'folder': 'inbox',
                    'method': 'imap',
                    'id_mode': 'uid',
                },
            ],
        }
        detail_result = {
            'success': True,
            'email': {
                'id': '42',
                'subject': 'Verification code',
                'from': 'noreply@example.com',
                'body': '<p>Your verification code is 123456</p>',
                'body_type': 'html',
            },
        }

        with patch.object(web_outlook_app, 'fetch_account_emails', return_value=list_result):
            with patch.object(
                web_outlook_app,
                'fetch_icloud_hme_account_detail_response',
                return_value=detail_result,
            ) as hme_detail_mock:
                with patch.object(
                    web_outlook_app,
                    'fetch_oauth_imap_detail_response',
                    return_value={'success': False},
                ):
                    response = self.client.get(
                        '/api/external/verification-code?email=abc@icloud.com&folder=inbox',
                        headers=self.api_headers,
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertTrue(payload['found'])
        self.assertEqual(payload['code'], '123456')
        hme_detail_mock.assert_called_once()
        args = hme_detail_mock.call_args.args
        self.assertEqual(args[0]['email'], 'abc@icloud.com')
        self.assertEqual(args[1], 'inbox')
        self.assertEqual(args[2], '42')
        self.assertEqual(args[4], 'uid')
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn('body', payload)
        self.assertNotIn('verification code is 123456', serialized.lower())

    def test_email_matches_filters_reads_hme_detail_for_keyword(self):
        account = {
            'id': 7,
            'email': 'abc@icloud.com',
            'account_type': 'icloud_hme',
            'provider': 'icloud_hme',
            'group_id': None,
        }
        item = {
            'id': '77',
            'subject': 'Welcome',
            'from': 'sender@example.com',
            'body_preview': 'no keyword here',
            'folder': 'inbox',
            'method': 'imap',
            'id_mode': 'uid',
        }
        detail_result = {
            'success': True,
            'email': {
                'id': '77',
                'body': '<p>The hidden pineapple keyword is here.</p>',
                'body_type': 'html',
            },
        }

        with patch.object(
            web_outlook_app,
            'fetch_icloud_hme_account_detail_response',
            return_value=detail_result,
        ) as hme_detail_mock:
            with patch.object(web_outlook_app, 'get_email_detail_graph', return_value=None) as graph_mock:
                matched = web_outlook_app.email_matches_filters(
                    account, item, keyword='pineapple'
                )

        self.assertTrue(matched)
        hme_detail_mock.assert_called_once()
        graph_mock.assert_not_called()


if __name__ == '__main__':
    unittest.main()
