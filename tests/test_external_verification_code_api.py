import importlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch


os.environ.setdefault('SECRET_KEY', 'test-secret-key')
_temp_dir = tempfile.mkdtemp(prefix='outlookEmail-verification-tests-')
os.environ['DATABASE_PATH'] = os.path.join(_temp_dir, 'test.db')

web_outlook_app = importlib.import_module('web_outlook_app')


class ExternalVerificationCodeApiTests(unittest.TestCase):
    def setUp(self):
        self.app = web_outlook_app.app
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()

        with self.app.app_context():
            db = web_outlook_app.get_db()
            for table in (
                'account_tags',
                'account_aliases',
                'account_refresh_logs',
                'accounts',
                'tags',
            ):
                db.execute(f'DELETE FROM {table}')
            db.execute("DELETE FROM groups WHERE name NOT IN ('默认分组', '临时邮箱')")
            db.commit()

            self.assertTrue(web_outlook_app.set_setting('external_api_key', 'test-external-key'))
            added = web_outlook_app.add_account(
                'user@outlook.com',
                'password123',
                '24d9a0ed-8787-4584-883c-2fd79308940a',
                '0.AXEA_refresh',
                group_id=1,
                remark='primary',
            )
            self.assertTrue(added)
            account = web_outlook_app.get_account_by_email('user@outlook.com')
            self.assertIsNotNone(account)
            alias_ok, _, alias_errors = web_outlook_app.replace_account_aliases(
                account['id'],
                account['email'],
                ['alias@example.com'],
                db,
            )
            self.assertTrue(alias_ok, alias_errors)
            db.commit()

        state = getattr(web_outlook_app, 'EXTERNAL_VERIFICATION_REFRESH_STATE', None)
        if isinstance(state, dict):
            state.clear()

    def get_code(self, query, api_key='test-external-key'):
        headers = {'X-API-Key': api_key} if api_key is not None else {}
        return self.client.get(f'/api/external/verification-code?{query}', headers=headers)

    def test_requires_api_key(self):
        response = self.get_code('email=user@outlook.com', api_key=None)

        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.get_json()['success'])

    def test_rejects_invalid_api_key(self):
        response = self.get_code('email=user@outlook.com', api_key='wrong-key')

        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.get_json()['success'])

    def test_missing_email_returns_400(self):
        response = self.get_code('folder=inbox')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['error'], '缺少 email 参数')

    def test_unknown_email_returns_404(self):
        response = self.get_code('email=missing@example.com')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['error'], '邮箱账号不存在')

    def test_invalid_folder_returns_400(self):
        response = self.get_code('email=user@outlook.com&folder=archive')

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertFalse(payload['success'])
        self.assertIn('folder 参数无效', payload['error'])
        self.assertIn('inbox', payload['error'])

    def test_extracts_code_from_full_html_body_not_preview(self):
        list_result = {
            'success': True,
            'emails': [
                {
                    'id': '1',
                    'subject': 'Welcome',
                    'from': 'noreply@example.com',
                    'body_preview': 'no code here',
                    'folder': 'inbox',
                    'method': 'graph',
                },
                {
                    'id': '4',
                    'subject': 'ChatGPT temporary authentication code',
                    'from': 'ChatGPT <noreply@tm.openai.com>',
                    'body_preview': '<html><head><title>ChatGPT</title></head>',
                    'folder': 'inbox',
                    'method': 'imap',
                    'id_mode': 'sequence',
                    'date': '01-Jun-2026 02:46:44 +0800',
                },
            ],
        }

        def detail_side_effect(_account, _folder, message_id, *_args):
            if str(message_id) == '1':
                return {
                    'success': True,
                    'email': {
                        'id': '1',
                        'subject': 'Welcome',
                        'from': 'noreply@example.com',
                        'body': '<p>No verification token.</p>',
                        'body_type': 'html',
                    },
                }
            return {
                'success': True,
                'email': {
                    'id': '4',
                    'subject': 'ChatGPT temporary authentication code',
                    'from': 'ChatGPT <noreply@tm.openai.com>',
                    'date': '01-Jun-2026 02:46:44 +0800',
                    'body': '<html><body><p>Authentication code</p><strong>051949</strong></body></html>',
                    'body_type': 'html',
                },
            }

        with patch.object(web_outlook_app, 'fetch_account_emails', return_value=list_result):
            with patch.object(
                web_outlook_app,
                'fetch_oauth_imap_detail_response',
                side_effect=detail_side_effect,
            ):
                with patch.object(
                    web_outlook_app,
                    'fetch_graph_detail_response',
                    side_effect=detail_side_effect,
                ):
                    response = self.get_code('email=user@outlook.com&folder=inbox&top=5')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertTrue(payload['found'])
        self.assertEqual(payload['code'], '051949')
        self.assertEqual(payload['source'], 'body')
        self.assertEqual(payload['message_id'], '4')
        self.assertEqual(payload['checked_count'], 2)
        self.assertEqual(payload['method'], 'imap')
        self.assertEqual(payload['id_mode'], 'sequence')
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn('body', payload)
        self.assertNotIn('body_preview', payload)
        self.assertNotIn('Authentication code', serialized)
        for forbidden in ('refresh_token', 'password', 'proxy_url'):
            self.assertNotIn(forbidden, serialized)

    def test_skips_non_code_numbers_and_matches_chatgpt_code_block_html(self):
        list_result = {
            'success': True,
            'emails': [
                {
                    'id': 'plan',
                    'subject': 'ChatGPT - New plan',
                    'from': 'OpenAI <noreply@tm.openai.com>',
                    'body_preview': 'Manage your account. Plan year 2026.',
                    'folder': 'inbox',
                    'method': 'imap',
                    'id_mode': 'sequence',
                },
                {
                    'id': 'code',
                    'subject': 'ChatGPT の一時的な認証コード',
                    'from': 'ChatGPT <noreply@tm.openai.com>',
                    'body_preview': '<html><head><title>ChatGPT の一時的な認証コード</title>',
                    'folder': 'inbox',
                    'method': 'imap',
                    'id_mode': 'sequence',
                },
            ],
        }

        def detail_side_effect(_account, _folder, message_id, *_args):
            if str(message_id) == 'plan':
                return {
                    'success': True,
                    'email': {
                        'id': 'plan',
                        'subject': 'ChatGPT - New plan',
                        'from': 'OpenAI <noreply@tm.openai.com>',
                        'body': (
                            '<p>Manage account: https://chatgpt.com/account/manage'
                            '?account_id=01c864ea-3578-42ab-a76c-ff6c8b8ea22b.</p>'
                            '<p>Your plan renews in 2026.</p>'
                        ),
                        'body_type': 'html',
                    },
                }
            return {
                'success': True,
                'email': {
                    'id': 'code',
                    'subject': 'ChatGPT の一時的な認証コード',
                    'from': 'ChatGPT <noreply@tm.openai.com>',
                    'body': (
                        '<p>この一時検証コードを入力して続行してください:</p>'
                        '<p style="font-family: Menlo, Monaco, Lucida Console, Arial; '
                        'font-size: 24px; line-height: 28px; background-color: #F3F3F3; '
                        'color: #5D5D5D; border-radius: 16px; padding: 28px 24px;">'
                        '<!--[if mso]><span><![endif]-->051949<!--[if mso]></span><![endif]-->'
                        '</p>'
                    ),
                    'body_type': 'html',
                },
            }

        with patch.object(web_outlook_app, 'fetch_account_emails', return_value=list_result):
            with patch.object(
                web_outlook_app,
                'fetch_oauth_imap_detail_response',
                side_effect=detail_side_effect,
            ):
                response = self.get_code('email=user@outlook.com&folder=inbox&top=5')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['found'])
        self.assertEqual(payload['code'], '051949')
        self.assertEqual(payload['message_id'], 'code')
        self.assertEqual(payload['source'], 'body')
        self.assertEqual(payload['checked_count'], 2)

    def test_falls_back_to_subject_and_preview_sources(self):
        list_result = {
            'success': True,
            'emails': [
                {
                    'id': 'subject-code',
                    'subject': 'Your login code is 123456',
                    'from': 'sender@example.com',
                    'body_preview': 'preview without code',
                    'folder': 'inbox',
                    'method': 'graph',
                },
            ],
        }
        body_without_code = {
            'success': True,
            'email': {
                'id': 'subject-code',
                'subject': 'Your login code is 123456',
                'from': 'sender@example.com',
                'body': 'No number in body',
                'body_type': 'text',
            },
        }

        with patch.object(web_outlook_app, 'fetch_account_emails', return_value=list_result):
            with patch.object(web_outlook_app, 'fetch_graph_detail_response', return_value=body_without_code):
                subject_response = self.get_code('email=user@outlook.com&folder=inbox')

        self.assertEqual(subject_response.status_code, 200)
        subject_payload = subject_response.get_json()
        self.assertEqual(subject_payload['code'], '123456')
        self.assertEqual(subject_payload['source'], 'subject')

        preview_list_result = {
            'success': True,
            'emails': [
                {
                    'id': 'preview-code',
                    'subject': 'No subject code',
                    'from': 'sender@example.com',
                    'body_preview': 'Use 654321 to sign in',
                    'folder': 'inbox',
                    'method': 'graph',
                },
            ],
        }
        preview_detail = {
            'success': True,
            'email': {
                'id': 'preview-code',
                'subject': 'No subject code',
                'from': 'sender@example.com',
                'body': 'No number in body',
                'body_type': 'text',
            },
        }

        with patch.object(web_outlook_app, 'fetch_account_emails', return_value=preview_list_result):
            with patch.object(web_outlook_app, 'fetch_graph_detail_response', return_value=preview_detail):
                preview_response = self.get_code('email=user@outlook.com&folder=inbox')

        self.assertEqual(preview_response.status_code, 200)
        preview_payload = preview_response.get_json()
        self.assertEqual(preview_payload['code'], '654321')
        self.assertEqual(preview_payload['source'], 'body_preview')

    def test_filters_candidates_before_reading_details(self):
        list_result = {
            'success': True,
            'emails': [
                {
                    'id': 'skip',
                    'subject': 'newsletter',
                    'from': 'other@example.com',
                    'body_preview': 'login 111111',
                    'folder': 'inbox',
                    'method': 'graph',
                },
                {
                    'id': 'match',
                    'subject': 'Verify login',
                    'from': 'OpenAI <noreply@openai.com>',
                    'body_preview': 'login request',
                    'folder': 'inbox',
                    'method': 'graph',
                },
            ],
        }
        detail_result = {
            'success': True,
            'email': {
                'id': 'match',
                'subject': 'Verify login',
                'from': 'OpenAI <noreply@openai.com>',
                'body': 'Your login code is 222222',
                'body_type': 'text',
            },
        }

        with patch.object(web_outlook_app, 'fetch_account_emails', return_value=list_result):
            with patch.object(
                web_outlook_app,
                'fetch_graph_detail_response',
                return_value=detail_result,
            ) as detail_mock:
                response = self.get_code(
                    'email=user@outlook.com&subject_contains=verify&from_contains=openai&keyword=login'
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['code'], '222222')
        self.assertEqual(detail_mock.call_count, 1)
        self.assertEqual(detail_mock.call_args.args[2], 'match')

    def test_not_found_returns_200_found_false(self):
        list_result = {
            'success': True,
            'emails': [
                {'id': '1', 'subject': 'No code', 'from': 'sender@example.com', 'folder': 'inbox', 'method': 'graph'},
                {'id': '2', 'subject': 'Still none', 'from': 'sender@example.com', 'folder': 'inbox', 'method': 'graph'},
            ],
        }
        detail_result = {
            'success': True,
            'email': {'body': 'No numeric token', 'subject': 'No code', 'from': 'sender@example.com'},
        }

        with patch.object(web_outlook_app, 'fetch_account_emails', return_value=list_result):
            with patch.object(web_outlook_app, 'fetch_graph_detail_response', return_value=detail_result):
                response = self.get_code('email=user@outlook.com')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertFalse(payload['found'])
        self.assertEqual(payload['checked_count'], 2)
        self.assertFalse(payload['throttled'])

    def test_refresh_calls_fetch_once_then_throttles_for_30_seconds(self):
        state = getattr(web_outlook_app, 'EXTERNAL_VERIFICATION_REFRESH_STATE', None)
        if isinstance(state, dict):
            state.clear()
        list_result = {
            'success': True,
            'emails': [{'id': '1', 'subject': 'Code', 'from': 'sender@example.com', 'folder': 'inbox', 'method': 'graph'}],
        }
        detail_result = {
            'success': True,
            'email': {'id': '1', 'subject': 'Code', 'from': 'sender@example.com', 'body': 'Code 333333'},
        }

        with patch.object(web_outlook_app.time, 'time', return_value=1000):
            with patch.object(web_outlook_app, 'fetch_account_emails', return_value=list_result) as fetch_mock:
                with patch.object(web_outlook_app, 'fetch_graph_detail_response', return_value=detail_result):
                    first = self.get_code('email=user@outlook.com&folder=inbox&refresh=1')
                    second = self.get_code('email=user@outlook.com&folder=inbox&refresh=1')

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(first.get_json()['throttled'])
        self.assertTrue(second.get_json()['throttled'])
        self.assertEqual(fetch_mock.call_count, 2)

    def test_uses_generic_imap_detail_for_imap_account(self):
        with self.app.app_context():
            added = web_outlook_app.add_account(
                'imap@example.com',
                '',
                '',
                '',
                group_id=1,
                account_type='imap',
                provider='custom',
                imap_host='imap.example.com',
                imap_port=993,
                imap_password='imap-secret',
            )
            self.assertTrue(added)

        list_result = {
            'success': True,
            'emails': [{'id': '9', 'subject': 'Code', 'from': 'sender@example.com', 'folder': 'inbox'}],
        }
        detail_result = {'success': True, 'email': {'id': '9', 'body': 'Code 444444'}}

        with patch.object(web_outlook_app, 'fetch_account_emails', return_value=list_result):
            with patch.object(
                web_outlook_app,
                'fetch_imap_account_detail_response',
                return_value=detail_result,
            ) as detail_mock:
                response = self.get_code('email=imap@example.com&folder=inbox')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['code'], '444444')
        self.assertEqual(detail_mock.call_count, 1)
        args = detail_mock.call_args.args
        self.assertEqual(args[0]['email'], 'imap@example.com')
        self.assertEqual(args[1], 'inbox')
        self.assertEqual(args[2], '9')

    def test_uses_graph_detail_for_graph_message(self):
        list_result = {
            'success': True,
            'emails': [{'id': 'graph-1', 'subject': 'Code', 'from': 'sender@example.com', 'folder': 'inbox', 'method': 'graph'}],
        }
        detail_result = {'success': True, 'email': {'id': 'graph-1', 'body': 'Code 555555'}}

        with patch.object(web_outlook_app, 'fetch_account_emails', return_value=list_result):
            with patch.object(web_outlook_app, 'fetch_graph_detail_response', return_value=detail_result) as graph_mock:
                response = self.get_code('email=user@outlook.com&folder=inbox')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['code'], '555555')
        self.assertEqual(graph_mock.call_count, 1)
        self.assertEqual(graph_mock.call_args.args[2], 'graph-1')

    def test_uses_oauth_imap_detail_for_imap_id_mode(self):
        list_result = {
            'success': True,
            'emails': [
                {
                    'id': '42',
                    'subject': 'Code',
                    'from': 'sender@example.com',
                    'folder': 'inbox',
                    'method': 'imap',
                    'id_mode': 'uid',
                },
            ],
        }
        detail_result = {'success': True, 'email': {'id': '42', 'body': 'Code 666666'}}

        with patch.object(web_outlook_app, 'fetch_account_emails', return_value=list_result):
            with patch.object(
                web_outlook_app,
                'fetch_oauth_imap_detail_response',
                return_value=detail_result,
            ) as imap_mock:
                response = self.get_code('email=user@outlook.com&folder=inbox')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['code'], '666666')
        self.assertEqual(imap_mock.call_count, 1)
        self.assertEqual(imap_mock.call_args.args[2], '42')
        self.assertEqual(imap_mock.call_args.args[4], 'uid')


if __name__ == '__main__':
    unittest.main()
