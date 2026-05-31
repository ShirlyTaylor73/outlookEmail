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
    _temp_dir = tempfile.mkdtemp(prefix='outlookEmail-temp-share-tests-')
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


class TempEmailShareTests(unittest.TestCase):
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
            try:
                db.execute('DELETE FROM temp_email_shares')
            except sqlite3.OperationalError as exc:
                if 'no such table' not in str(exc):
                    raise
            db.execute('DELETE FROM temp_email_tags')
            db.execute('DELETE FROM temp_email_messages')
            db.execute('DELETE FROM temp_emails')
            db.commit()

    def _create_temp_email(self, email_addr='share@example.com', provider='gptmail') -> int:
        with self.app.app_context():
            self.assertTrue(web_outlook_app.add_temp_email(email_addr, provider=provider))
            row = web_outlook_app.get_db().execute(
                'SELECT id FROM temp_emails WHERE email = ?',
                (email_addr,)
            ).fetchone()
            self.assertIsNotNone(row)
            return int(row['id'])

    def _save_temp_email_messages(self, email_addr, messages):
        with self.app.app_context():
            return web_outlook_app.save_temp_email_messages(email_addr, messages)

    def _create_share(self, temp_email_id, expires_in=None):
        payload = {} if expires_in is None else {'expires_in': expires_in}
        response = self.client.post(f'/api/temp-emails/{temp_email_id}/shares', json=payload)
        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertTrue(data['success'])
        self.assertIn('share', data)
        return data['share']

    def test_create_share_defaults_to_thirty_days_and_allows_multiple_links(self):
        temp_email_id = self._create_temp_email()

        before = datetime.utcnow()
        first = self.client.post(f'/api/temp-emails/{temp_email_id}/shares', json={})
        after = datetime.utcnow()
        second = self.client.post(
            f'/api/temp-emails/{temp_email_id}/shares',
            json={'expires_in': 0}
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        first_payload = first.get_json()
        second_payload = second.get_json()
        self.assertTrue(first_payload['success'])
        self.assertTrue(second_payload['success'])
        self.assertNotEqual(first_payload['share']['token'], second_payload['share']['token'])
        expires_at = first_payload['share']['expires_at']
        self.assertIsNotNone(expires_at)
        parsed_expires_at = _parse_api_datetime(expires_at)
        self.assertGreaterEqual(
            parsed_expires_at,
            before + timedelta(days=30) - timedelta(seconds=5)
        )
        self.assertLessEqual(
            parsed_expires_at,
            after + timedelta(days=30) + timedelta(seconds=5)
        )
        self.assertIsNone(second_payload['share']['expires_at'])

        listed = self.client.get(f'/api/temp-emails/{temp_email_id}/shares')
        self.assertEqual(listed.status_code, 200)
        payload = listed.get_json()
        self.assertEqual(payload['total'], 2)

    def test_create_share_rejects_non_preset_expiry(self):
        temp_email_id = self._create_temp_email()

        response = self.client.post(
            f'/api/temp-emails/{temp_email_id}/shares',
            json={'expires_in': 12345}
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])

    def test_delete_share_revokes_public_access(self):
        temp_email_id = self._create_temp_email()
        created = self._create_share(temp_email_id)

        deleted = self.client.delete(f'/api/temp-emails/{temp_email_id}/shares/{created["id"]}')
        self.assertEqual(deleted.status_code, 200)
        public = self.public_client.get(f'/api/shared/{created["token"]}')
        self.assertEqual(public.status_code, 404)

    def test_shared_page_renders_without_login(self):
        temp_email_id = self._create_temp_email('page@example.com')
        token = self._create_share(temp_email_id)['token']

        response = self.public_client.get(f'/shared/{token}')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(f'data-share-token="{token}"', html)
        self.assertIn('shared-temp-email.js', html)

    def test_shared_page_uses_dompurify_for_html_detail(self):
        script_path = os.path.join(ROOT_DIR, 'static', 'js', 'shared-temp-email.js')

        with open(script_path, encoding='utf-8') as script_file:
            script = script_file.read()

        self.assertIn('DOMPurify.sanitize', script)
        self.assertIn('body_type ===', script)

    def test_public_shared_email_and_message_detail_do_not_require_login(self):
        temp_email_id = self._create_temp_email('public@example.com')
        self._save_temp_email_messages('public@example.com', [{
            'id': 'msg-1',
            'from_address': 'sender@example.com',
            'subject': 'Verify',
            'content': 'Code 123456',
            'html_content': '<p>Code <strong>123456</strong></p>',
            'has_html': True,
            'timestamp': 1717200000,
        }])
        token = self._create_share(temp_email_id)['token']

        info = self.public_client.get(f'/api/shared/{token}')
        messages = self.public_client.get(f'/api/shared/{token}/messages')
        detail = self.public_client.get(f'/api/shared/{token}/messages/msg-1')

        self.assertEqual(info.status_code, 200)
        self.assertEqual(messages.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(info.get_json()['email']['email'], 'public@example.com')
        self.assertEqual(messages.get_json()['emails'][0]['id'], 'msg-1')
        self.assertEqual(detail.get_json()['email']['body_type'], 'html')

    def test_public_message_detail_rejects_message_from_another_mailbox(self):
        first_id = self._create_temp_email('first@example.com')
        self._create_temp_email('second@example.com')
        self._save_temp_email_messages('second@example.com', [{
            'id': 'other-msg',
            'from_address': 'sender@example.com',
            'subject': 'Other',
            'content': 'secret',
            'timestamp': 1717200000,
        }])
        token = self._create_share(first_id)['token']

        response = self.public_client.get(f'/api/shared/{token}/messages/other-msg')

        self.assertEqual(response.status_code, 404)

    def test_expired_share_returns_gone(self):
        temp_email_id = self._create_temp_email()
        token = self._create_share(temp_email_id)['token']
        with self.app.app_context():
            db = web_outlook_app.get_db()
            db.execute(
                "UPDATE temp_email_shares SET expires_at = datetime('now', '-1 minute') WHERE token = ?",
                (token,)
            )
            db.commit()

        response = self.public_client.get(f'/api/shared/{token}')

        self.assertEqual(response.status_code, 410)

    def test_deleted_temp_email_invalidates_share(self):
        temp_email_id = self._create_temp_email('deleted@example.com')
        token = self._create_share(temp_email_id)['token']

        self.client.delete('/api/temp-emails/deleted@example.com')
        response = self.public_client.get(f'/api/shared/{token}')

        self.assertEqual(response.status_code, 404)

    def test_public_payload_does_not_expose_provider_credentials(self):
        temp_email_id = self._create_temp_email('secret@example.com', provider='duckmail')
        with self.app.app_context():
            db = web_outlook_app.get_db()
            db.execute(
                'UPDATE temp_emails SET duckmail_password = ?, duckmail_token = ?, cloudflare_jwt = ? WHERE id = ?',
                ('password-secret', 'token-secret', 'jwt-secret', temp_email_id)
            )
            db.commit()
        token = self._create_share(temp_email_id)['token']

        body = self.public_client.get(f'/api/shared/{token}').get_data(as_text=True)

        self.assertNotIn('password-secret', body)
        self.assertNotIn('token-secret', body)
        self.assertNotIn('jwt-secret', body)
        self.assertNotIn('duckmail_password', body)
        self.assertNotIn('duckmail_token', body)
        self.assertNotIn('cloudflare_jwt', body)

    def test_public_refresh_is_throttled_by_token(self):
        temp_email_id = self._create_temp_email('refresh@example.com')
        token = self._create_share(temp_email_id)['token']
        with patch.object(web_outlook_app, 'get_temp_emails_from_api', return_value=[{
            'id': 'msg-refresh',
            'from_address': 'sender@example.com',
            'subject': 'Refresh',
            'content': 'fresh',
            'timestamp': 1717200001,
        }]) as fetch_mock:
            first = self.public_client.post(f'/api/shared/{token}/refresh')
            second = self.public_client.post(f'/api/shared/{token}/refresh')

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(fetch_mock.call_count, 1)
        self.assertFalse(first.get_json()['throttled'])
        self.assertTrue(second.get_json()['throttled'])

    def test_public_refresh_hides_upstream_parse_errors(self):
        temp_email_id = self._create_temp_email('duck-refresh@example.com', provider='duckmail')
        token = self._create_share(temp_email_id)['token']

        with patch.object(web_outlook_app, 'get_duckmail_token_for_email', return_value='duck-token'), \
                patch.object(web_outlook_app, 'duckmail_get_messages', return_value=[{
                    'id': 'duck-msg',
                    'from': {'address': 'sender@example.com'},
                    'subject': 'Bad timestamp',
                    'text': 'body',
                    'html': [],
                    'createdAt': 'not-a-date',
                }]):
            response = self.public_client.post(f'/api/shared/{token}/refresh')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload['success'])
        self.assertEqual(payload['error'], '刷新邮件失败')
        self.assertNotIn('not-a-date', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
