import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


os.environ.setdefault('SECRET_KEY', 'test-secret-key')
if 'DATABASE_PATH' not in os.environ:
    _temp_dir = tempfile.mkdtemp(prefix='outlookEmail-icloud-hme-share-tests-')
    os.environ['DATABASE_PATH'] = os.path.join(_temp_dir, 'test.db')

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

web_outlook_app = importlib.import_module('web_outlook_app')


class IcloudHmeShareTests(unittest.TestCase):
    def setUp(self):
        self.app = web_outlook_app.app
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False

        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['logged_in'] = True
        self.public_client = self.app.test_client()

        with self.app.app_context():
            web_outlook_app.init_db()
            db = web_outlook_app.get_db()
            for table in (
                'account_shares',
                'retained_normal_mail_messages',
                'account_tags',
                'account_aliases',
                'accounts',
                'icloud_hme_sources',
                'temp_email_shares',
                'temp_email_messages',
                'temp_emails',
            ):
                self._delete_table_if_exists(db, table)
            db.commit()

    def _delete_table_if_exists(self, db, table):
        try:
            db.execute(f'DELETE FROM {table}')
        except sqlite3.OperationalError as exc:
            if 'no such table' not in str(exc):
                raise

    def _create_source(self, password='receiver-password-secret', cookie='cookie-secret'):
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
                    'Shared HME Source',
                    'receiver@example.com',
                    web_outlook_app.encrypt_data(password),
                    web_outlook_app.encrypt_data(cookie),
                    'maildomain-secret.icloud.com',
                ),
            )
            db.commit()
            return cursor.lastrowid

    def _create_hme_account(self, email_addr='shared-hme@icloud.com'):
        source_id = self._create_source()
        with self.app.app_context():
            db = web_outlook_app.get_db()
            cursor = db.execute(
                '''
                INSERT INTO accounts (
                    email, password, client_id, refresh_token, group_id, remark, status,
                    account_type, provider, imap_host, imap_port, imap_password,
                    proxy_url, icloud_hme_source_id
                )
                VALUES (?, '', '', '', 1, 'hme share test', 'active',
                        'icloud_hme', 'icloud_hme', '', 993, '', '', ?)
                ''',
                (email_addr, source_id),
            )
            db.commit()
            return cursor.lastrowid

    def _create_share(self, account_id):
        response = self.client.post(f'/api/accounts/{account_id}/shares', json={})
        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertTrue(data['success'])
        return data['share']

    def test_hme_account_can_create_share_and_public_info_has_account_label_without_source_secrets(self):
        account_id = self._create_hme_account()
        share = self._create_share(account_id)

        response = self.public_client.get(f'/api/shared/{share["token"]}')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['share_type'], 'account')
        self.assertEqual(payload['email']['provider'], 'icloud_hme')
        self.assertIn(payload['email']['provider_label'], {'iCloud HME', 'iCloud Hide My Email'})
        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in (
            'receiver_imap_password',
            'receiver-password-secret',
            'cookie',
            'cookie-secret',
            'maildomain_host',
            'maildomain-secret',
            'icloud_hme_source_id',
        ):
            self.assertNotIn(forbidden, serialized)

    def test_hme_shared_messages_fetch_via_account_fetcher(self):
        account_id = self._create_hme_account('list-hme@icloud.com')
        share = self._create_share(account_id)

        with patch.object(web_outlook_app, 'fetch_account_emails', return_value={
            'success': True,
            'method': 'imap',
            'emails': [{
                'id': 'hme-list-1',
                'from': 'sender@example.com',
                'to': 'list-hme@icloud.com',
                'subject': 'HME list',
                'body_preview': 'preview',
                'date': '2026-06-01T00:00:00Z',
                'folder': 'inbox',
                'id_mode': 'uid',
            }],
        }) as fetch_mock:
            response = self.public_client.get(f'/api/shared/{share["token"]}/messages')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['share_type'], 'account')
        self.assertEqual(payload['emails'][0]['id'], 'hme-list-1')
        fetch_mock.assert_called_once()
        account_arg, folder_arg, skip_arg, top_arg = fetch_mock.call_args.args
        self.assertEqual(account_arg['account_type'], 'icloud_hme')
        self.assertEqual(account_arg['provider'], 'icloud_hme')
        self.assertEqual((folder_arg, skip_arg, top_arg), ('all', 0, 50))

    def test_hme_shared_refresh_throttle_uses_local_cache_without_fetching_upstream(self):
        account_id = self._create_hme_account('throttle-hme@icloud.com')
        share = self._create_share(account_id)
        with self.app.app_context():
            db = web_outlook_app.get_db()
            db.execute(
                "UPDATE account_shares SET last_refreshed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (share["id"],),
            )
            db.commit()

        with patch.object(web_outlook_app, 'fetch_retained_normal_mail_list', return_value={
            'success': True,
            'emails': [{
                'id': 'hme-cached-1',
                'from': 'sender@example.com',
                'to': 'throttle-hme@icloud.com',
                'subject': 'HME cached',
                'body_preview': 'cached preview',
                'date': '2026-06-01T00:00:00Z',
                'folder': 'inbox',
                'id_mode': 'uid',
                'method': 'local',
            }],
            'method': 'Local Retention',
            'request_method': 'local',
        }) as retained_mock, patch.object(web_outlook_app, 'fetch_account_emails', return_value={
            'success': True,
            'method': 'imap',
            'emails': [{
                'id': 'hme-throttled-1',
                'from': 'sender@example.com',
                'to': 'throttle-hme@icloud.com',
                'subject': 'HME throttled',
                'body_preview': 'preview',
                'date': '2026-06-01T00:00:00Z',
                'folder': 'inbox',
                'id_mode': 'uid',
            }],
        }) as fetch_mock:
            response = self.public_client.post(f'/api/shared/{share["token"]}/refresh')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'], msg=payload)
        self.assertTrue(payload['throttled'])
        self.assertEqual(payload['share_type'], 'account')
        self.assertEqual(payload['emails'][0]['id'], 'hme-cached-1')
        self.assertEqual(payload['emails'][0]['method'], 'local')
        fetch_mock.assert_not_called()
        retained_mock.assert_called_once()

    def test_hme_shared_refresh_throttle_with_empty_cache_does_not_fetch_upstream(self):
        account_id = self._create_hme_account('empty-throttle-hme@icloud.com')
        share = self._create_share(account_id)
        with self.app.app_context():
            db = web_outlook_app.get_db()
            db.execute(
                "UPDATE account_shares SET last_refreshed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (share["id"],),
            )
            db.commit()

        with patch.object(web_outlook_app, 'fetch_retained_normal_mail_list', return_value={
            'success': True,
            'emails': [],
            'method': 'Local Retention',
            'request_method': 'local',
        }) as retained_mock, patch.object(web_outlook_app, 'fetch_account_emails') as fetch_mock:
            response = self.public_client.post(f'/api/shared/{share["token"]}/refresh')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'], msg=payload)
        self.assertTrue(payload['throttled'])
        self.assertEqual(payload['share_type'], 'account')
        self.assertEqual(payload['emails'], [])
        self.assertEqual(payload['count'], 0)
        fetch_mock.assert_not_called()
        retained_mock.assert_called_once()

    def test_hme_shared_detail_uses_hme_detail_branch_and_sanitizes_detail_payload(self):
        account_id = self._create_hme_account('detail-hme@icloud.com')
        share = self._create_share(account_id)

        with patch.object(web_outlook_app, 'fetch_icloud_hme_account_detail_response', return_value={
            'success': True,
            'email': {
                'id': 'hme-detail-1',
                'from': 'sender@example.com',
                'to': 'detail-hme@icloud.com',
                'subject': 'HME detail',
                'body': '<p>body</p>',
                'body_type': 'html',
                'date': '2026-06-01T00:00:00Z',
                'folder': 'inbox',
                'id_mode': 'uid',
                'receiver_imap_password': 'receiver-password-secret',
                'cookie': 'cookie-secret',
                'maildomain_host': 'maildomain-secret.icloud.com',
                'raw_mime': 'RAW MIME SECRET',
                'attachments': [{'content': 'BINARY SECRET'}],
            },
        }) as detail_mock:
            response = self.public_client.get(
                f'/api/shared/{share["token"]}/messages/hme-detail-1?folder=inbox&method=imap&id_mode=uid'
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['share_type'], 'account')
        self.assertEqual(payload['email']['body_type'], 'html')
        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in (
            'receiver_imap_password',
            'receiver-password-secret',
            'cookie',
            'cookie-secret',
            'maildomain_host',
            'maildomain-secret',
            'raw_mime',
            'RAW MIME SECRET',
            'attachments',
            'BINARY SECRET',
        ):
            self.assertNotIn(forbidden, serialized)
        detail_mock.assert_called_once()

    def test_public_page_js_maps_hme_provider_to_human_label_without_source_fields(self):
        public_js = open(os.path.join(ROOT_DIR, 'static', 'js', 'shared-temp-email.js'), encoding='utf-8').read()

        self.assertIn('normalizeSharedProviderLabel', public_js)
        self.assertTrue('iCloud HME' in public_js or 'iCloud Hide My Email' in public_js)
        self.assertNotIn('icloud_hme_source_id', public_js)


if __name__ == '__main__':
    unittest.main()
