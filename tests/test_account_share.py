import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


os.environ.setdefault('SECRET_KEY', 'test-secret-key')
if 'DATABASE_PATH' not in os.environ:
    _temp_dir = tempfile.mkdtemp(prefix='outlookEmail-account-share-tests-')
    os.environ['DATABASE_PATH'] = os.path.join(_temp_dir, 'test.db')

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

web_outlook_app = importlib.import_module('web_outlook_app')


def _parse_api_datetime(value):
    normalized = value.strip()
    if normalized.endswith('Z'):
        normalized = f'{normalized[:-1]}+00:00'
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


class AccountShareTests(unittest.TestCase):
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
                'accounts',
                'temp_email_shares',
                'temp_email_messages',
                'temp_emails',
            ):
                try:
                    db.execute(f'DELETE FROM {table}')
                except sqlite3.OperationalError as exc:
                    if 'no such table' not in str(exc):
                        raise
            db.commit()

    def _create_account(self, email='shared-account@example.com', account_type='outlook', provider='outlook'):
        with self.app.app_context():
            db = web_outlook_app.get_db()
            cursor = db.execute(
                '''
                INSERT INTO accounts (
                    email, password, client_id, refresh_token, account_type, provider,
                    imap_host, imap_port, imap_password, proxy_url, fallback_proxy_url_1,
                    fallback_proxy_url_2
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    email,
                    'password-secret',
                    'client-secret',
                    'refresh-secret',
                    account_type,
                    provider,
                    'imap.example.com',
                    993,
                    'imap-password-secret',
                    'http://proxy-secret',
                    'http://fallback-secret-1',
                    'http://fallback-secret-2',
                ),
            )
            db.commit()
            return cursor.lastrowid

    def _create_share(self, account_id, expires_in=None):
        payload = {} if expires_in is None else {'expires_in': expires_in}
        response = self.client.post(f'/api/accounts/{account_id}/shares', json=payload)
        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertTrue(data['success'])
        return data['share']

    def test_create_share_defaults_to_thirty_days_and_allows_multiple_links(self):
        account_id = self._create_account()

        before = datetime.utcnow()
        first = self.client.post(f'/api/accounts/{account_id}/shares', json={})
        after = datetime.utcnow()
        second = self.client.post(f'/api/accounts/{account_id}/shares', json={'expires_in': 0})

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        first_payload = first.get_json()
        second_payload = second.get_json()
        self.assertNotEqual(first_payload['share']['token'], second_payload['share']['token'])
        self.assertIsNone(second_payload['share']['expires_at'])

        expires_at = _parse_api_datetime(first_payload['share']['expires_at'])
        self.assertGreaterEqual(expires_at, before + timedelta(days=30) - timedelta(seconds=5))
        self.assertLessEqual(expires_at, after + timedelta(days=30) + timedelta(seconds=5))

        listed = self.client.get(f'/api/accounts/{account_id}/shares')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.get_json()['total'], 2)

    def test_create_share_rejects_non_preset_expiry(self):
        account_id = self._create_account()

        response = self.client.post(f'/api/accounts/{account_id}/shares', json={'expires_in': 12345})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])

    def test_account_share_token_avoids_existing_temp_email_share_token(self):
        account_id = self._create_account()
        with self.app.app_context():
            db = web_outlook_app.get_db()
            temp_cursor = db.execute(
                "INSERT INTO temp_emails (email, provider) VALUES (?, ?)",
                ('temp-token@example.com', 'gptmail'),
            )
            db.execute(
                "INSERT INTO temp_email_shares (temp_email_id, token) VALUES (?, ?)",
                (temp_cursor.lastrowid, 'existing-temp-token'),
            )
            db.commit()

        with patch.object(web_outlook_app.secrets, 'token_urlsafe', side_effect=['existing-temp-token', 'new-account-token']):
            response = self.client.post(f'/api/accounts/{account_id}/shares', json={})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()['share']['token'], 'new-account-token')

    def test_delete_share_revokes_public_access(self):
        account_id = self._create_account()
        share = self._create_share(account_id)

        deleted = self.client.delete(f'/api/accounts/{account_id}/shares/{share["id"]}')
        public = self.public_client.get(f'/api/shared/{share["token"]}')

        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(public.status_code, 404)

    def test_deleted_account_invalidates_share(self):
        account_id = self._create_account('deleted-account@example.com')
        share = self._create_share(account_id)

        self.client.delete(f'/api/accounts/{account_id}')
        response = self.public_client.get(f'/api/shared/{share["token"]}')

        self.assertEqual(response.status_code, 404)

    def test_direct_account_delete_cascades_account_shares(self):
        account_id = self._create_account('cascade-account@example.com')
        self._create_share(account_id)

        with self.app.app_context():
            db = web_outlook_app.get_db()
            db.execute('DELETE FROM accounts WHERE id = ?', (account_id,))
            db.commit()
            remaining = db.execute(
                'SELECT COUNT(*) AS count FROM account_shares WHERE account_id = ?',
                (account_id,),
            ).fetchone()['count']

        self.assertEqual(remaining, 0)

    def test_public_payload_does_not_expose_account_credentials(self):
        account_id = self._create_account('secret-account@example.com', account_type='imap', provider='custom')
        token = self._create_share(account_id)['token']

        body = self.public_client.get(f'/api/shared/{token}').get_data(as_text=True)

        for secret in (
            'password-secret',
            'client-secret',
            'refresh-secret',
            'imap-password-secret',
            'proxy-secret',
            'fallback-secret',
            'refresh_token',
            'imap_password',
            'proxy_url',
        ):
            self.assertNotIn(secret, body)

    def test_public_shared_account_messages_use_local_cache_without_login(self):
        account_id = self._create_account('local-cache@example.com')
        token = self._create_share(account_id)['token']
        with self.app.app_context():
            db = web_outlook_app.get_db()
            db.execute(
                '''
                INSERT INTO retained_normal_mail_messages (
                    account_id, folder, provider_message_id, id_mode, subject, sender,
                    recipients, received_at, received_at_sort, body_preview, body,
                    body_type, list_cached, body_cached
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)
                ''',
                (
                    account_id,
                    'inbox',
                    'msg-local',
                    'uid',
                    'Cached subject',
                    'sender@example.com',
                    'local-cache@example.com',
                    '2026-06-01T00:00:00Z',
                    1790000000,
                    'Preview',
                    '<p>Cached body</p>',
                    'html',
                ),
            )
            db.commit()

        info = self.public_client.get(f'/api/shared/{token}')
        messages = self.public_client.get(f'/api/shared/{token}/messages')
        detail = self.public_client.get(f'/api/shared/{token}/messages/msg-local?folder=inbox&method=local&id_mode=uid')

        self.assertEqual(info.status_code, 200)
        self.assertEqual(messages.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(info.get_json()['share_type'], 'account')
        self.assertEqual(messages.get_json()['emails'][0]['id'], 'msg-local')
        self.assertEqual(detail.get_json()['email']['body_type'], 'html')

    def test_public_refresh_is_throttled_by_token(self):
        account_id = self._create_account('refresh-account@example.com')
        token = self._create_share(account_id)['token']

        with patch.object(web_outlook_app, 'fetch_account_emails', return_value={
            'success': True,
            'method': 'IMAP',
            'emails': [{
                'id': 'msg-refresh',
                'from': 'sender@example.com',
                'subject': 'Refresh',
                'body_preview': 'fresh',
                'date': '2026-06-01T00:00:00Z',
                'folder': 'inbox',
                'id_mode': 'uid',
            }],
        }) as fetch_mock:
            first = self.public_client.post(f'/api/shared/{token}/refresh')
            second = self.public_client.post(f'/api/shared/{token}/refresh')

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(fetch_mock.call_count, 1)
        self.assertFalse(first.get_json()['throttled'])
        self.assertTrue(second.get_json()['throttled'])
        self.assertEqual(first.get_json()['emails'][0]['method'], 'imap')

    def test_failed_public_refresh_is_sanitized_and_still_throttled(self):
        account_id = self._create_account('failed-refresh@example.com')
        token = self._create_share(account_id)['token']

        with patch.object(web_outlook_app, 'fetch_account_emails', return_value={
            'success': False,
            'error': 'refresh-secret proxy-secret upstream-secret',
        }) as fetch_mock:
            first = self.public_client.post(f'/api/shared/{token}/refresh')
            second = self.public_client.post(f'/api/shared/{token}/refresh')

        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.get_json()['success'])
        self.assertEqual(first.get_json()['error'], '刷新邮件失败')
        self.assertNotIn('refresh-secret', first.get_data(as_text=True))
        self.assertNotIn('proxy-secret', first.get_data(as_text=True))
        self.assertEqual(fetch_mock.call_count, 1)
        self.assertTrue(second.get_json()['throttled'])

    def test_public_outlook_detail_uses_shared_account(self):
        account_id = self._create_account('graph-detail@example.com')
        token = self._create_share(account_id)['token']

        with patch.object(web_outlook_app, 'fetch_graph_detail_response', return_value={
            'success': True,
            'email': {
                'id': 'msg-graph',
                'from': 'sender@example.com',
                'to': 'graph-detail@example.com',
                'subject': 'Graph detail',
                'body': '<strong>Graph</strong>',
                'body_type': 'html',
                'date': '2026-06-01T00:00:00Z',
                'folder': 'inbox',
                'id_mode': 'graph',
            },
        }) as detail_mock:
            response = self.public_client.get(f'/api/shared/{token}/messages/msg-graph?folder=inbox&method=graph&id_mode=graph')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['email']['body_type'], 'html')
        self.assertEqual(detail_mock.call_count, 1)

    def test_public_imap_detail_uses_shared_account(self):
        account_id = self._create_account('imap-detail@example.com', account_type='imap', provider='custom')
        token = self._create_share(account_id)['token']

        with patch.object(web_outlook_app, 'fetch_imap_account_detail_response', return_value={
            'success': True,
            'email': {
                'id': 'msg-imap',
                'from': 'sender@example.com',
                'to': 'imap-detail@example.com',
                'subject': 'IMAP detail',
                'body': 'IMAP body',
                'body_type': 'text',
                'date': '2026-06-01T00:00:00Z',
                'folder': 'inbox',
                'id_mode': 'uid',
            },
        }) as detail_mock:
            response = self.public_client.get(f'/api/shared/{token}/messages/msg-imap?folder=inbox&method=imap&id_mode=uid')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['email']['body'], 'IMAP body')
        self.assertEqual(detail_mock.call_count, 1)

    def test_account_share_ui_hooks_exist(self):
        account_js = open(os.path.join(ROOT_DIR, 'static', 'js', 'index', '02-groups.js'), encoding='utf-8').read()
        share_js = open(os.path.join(ROOT_DIR, 'static', 'js', 'index', '03-temp-emails.js'), encoding='utf-8').read()
        public_js = open(os.path.join(ROOT_DIR, 'static', 'js', 'shared-temp-email.js'), encoding='utf-8').read()
        public_template = open(os.path.join(ROOT_DIR, 'templates', 'shared_temp_email.html'), encoding='utf-8').read()

        self.assertIn('data-account-action="share"', account_js)
        self.assertIn('showAccountShareModal', share_js)
        self.assertIn('/api/accounts/${currentAccountShareTarget.id}/shares', share_js)
        self.assertIn('/shared/${share.token}', share_js)
        self.assertIn('<title>邮箱分享</title>', public_template)
        self.assertIn('document.title = sharedState.shareType === \'account\'', public_js)
        self.assertIn('sharedState.shareType === \'account\'', public_js)
        self.assertIn('id_mode', public_js)
        self.assertIn('DOMPurify.sanitize', public_js)


if __name__ == '__main__':
    unittest.main()
