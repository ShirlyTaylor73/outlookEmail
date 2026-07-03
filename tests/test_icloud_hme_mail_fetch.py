import importlib
import os
import sys
import tempfile
import unittest
from email.message import EmailMessage
from unittest.mock import patch


os.environ.setdefault('SECRET_KEY', 'test-secret-key')
if 'DATABASE_PATH' not in os.environ:
    _temp_dir = tempfile.mkdtemp(prefix='outlookEmail-hme-mail-tests-')
    os.environ['DATABASE_PATH'] = os.path.join(_temp_dir, 'test.db')
ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

web_outlook_app = importlib.import_module('web_outlook_app')


def make_raw_message(subject, *, to='receiver@example.com', extra_headers=None, body='body'):
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = 'sender@example.com'
    msg['To'] = to
    msg['Date'] = 'Tue, 14 Apr 2026 08:20:50 +0000'
    for name, value in (extra_headers or {}).items():
        msg[name] = value
    msg.set_content(body)
    return msg.as_bytes()


class FakeHmeMail:
    def __init__(self, messages):
        self.messages = {str(uid): raw for uid, raw in messages.items()}
        self.login_calls = []
        self.select_calls = []
        self.logged_out = False

    def login(self, email_addr, password):
        self.login_calls.append((email_addr, password))
        return 'OK', [b'logged in']

    def xatom(self, *_args):
        return 'OK', [b'ID completed']

    def select(self, folder_name, readonly=True):
        self.select_calls.append((folder_name, readonly))
        return 'OK', [str(len(self.messages)).encode('ascii')]

    def list(self):
        return 'OK', [b'(\\HasNoChildren) "/" "INBOX"']

    def uid(self, command, *args):
        if command == 'SEARCH':
            ids = ' '.join(self.messages.keys()).encode('ascii')
            return 'OK', [ids]
        if command == 'FETCH':
            uid = args[0].decode('ascii') if isinstance(args[0], bytes) else str(args[0])
            raw = self.messages.get(uid)
            if raw is None:
                return 'NO', [b'not found']
            return 'OK', [(f'{uid} (FLAGS () INTERNALDATE "14-Apr-2026 08:20:50 +0000" RFC822 {{{len(raw)}}}'.encode('ascii'), raw)]
        return 'BAD', [b'unsupported']

    def search(self, *_args):
        return 'OK', [b'']

    def fetch(self, message_id, _query):
        uid = str(message_id)
        raw = self.messages.get(uid)
        if raw is None:
            return 'NO', [b'not found']
        return 'OK', [(f'{uid} (RFC822 {{{len(raw)}}}'.encode('ascii'), raw)]

    def logout(self):
        self.logged_out = True
        return 'BYE', [b'logout']


class IcloudHmeMailFetchTests(unittest.TestCase):
    def setUp(self):
        web_outlook_app.set_normal_mail_local_retention_enabled_cache(False)
        self.account = {
            'id': 49,
            'email': 'abc@icloud.com',
            'account_type': 'icloud_hme',
            'provider': 'icloud_hme',
            'icloud_hme_source_id': 7,
        }
        self.source_config = {
            'email_addr': 'receiver@example.com',
            'imap_password': 'source-password',
            'imap_host': 'imap.example.com',
            'imap_port': 993,
            'provider': 'custom',
            'folder': 'inbox',
            'proxy_url': '',
        }

    def _fetch_list_with_messages(self, messages):
        fake_mail = FakeHmeMail(messages)
        with patch.object(web_outlook_app, 'get_icloud_hme_source_imap_config', return_value=self.source_config), \
             patch.object(web_outlook_app, 'create_imap_connection', return_value=fake_mail):
            result = web_outlook_app.fetch_account_emails(self.account, 'inbox', 0, 20)
        return result, fake_mail

    def test_delivered_to_header_matches_hme_address(self):
        result, fake_mail = self._fetch_list_with_messages({
            '101': make_raw_message(
                'Delivered-To hit',
                extra_headers={'Delivered-To': 'abc@icloud.com'},
            ),
            '102': make_raw_message(
                'Other mailbox',
                extra_headers={'Delivered-To': 'other@icloud.com'},
            ),
        })

        self.assertTrue(result['success'])
        self.assertEqual([item['id'] for item in result['emails']], ['101'])
        self.assertEqual(result['emails'][0]['id_mode'], 'uid')
        self.assertEqual(result['emails'][0]['method'], 'imap')
        self.assertEqual(fake_mail.login_calls, [('receiver@example.com', 'source-password')])

    def test_x_original_to_header_matches_hme_address(self):
        result, _fake_mail = self._fetch_list_with_messages({
            '101': make_raw_message(
                'X-Original-To hit',
                extra_headers={'X-Original-To': 'abc@icloud.com'},
            ),
        })

        self.assertTrue(result['success'])
        self.assertEqual([item['id'] for item in result['emails']], ['101'])

    def test_body_fallback_matches_hme_address(self):
        result, _fake_mail = self._fetch_list_with_messages({
            '101': make_raw_message(
                'Body fallback hit',
                to='receiver@example.com',
                body='Your alias abc@icloud.com received a message.',
            ),
        })

        self.assertTrue(result['success'])
        self.assertEqual([item['id'] for item in result['emails']], ['101'])

    def test_other_hme_address_message_is_filtered_out(self):
        result, _fake_mail = self._fetch_list_with_messages({
            '101': make_raw_message(
                'Other only',
                extra_headers={'Delivered-To': 'other@icloud.com'},
                body='No matching alias here.',
            ),
        })

        self.assertTrue(result['success'])
        self.assertEqual(result['emails'], [])

    def test_detail_revalidates_hme_ownership_for_same_uid(self):
        matching_mail = FakeHmeMail({
            '101': make_raw_message(
                'Owned detail',
                extra_headers={'Delivered-To': 'abc@icloud.com'},
                body='owned detail body',
            ),
        })
        with patch.object(web_outlook_app, 'get_icloud_hme_source_imap_config', return_value=self.source_config), \
             patch.object(web_outlook_app, 'create_imap_connection', return_value=matching_mail):
            owned = web_outlook_app.fetch_icloud_hme_account_detail_response(
                self.account, 'inbox', '101', 'imap', 'uid'
            )

        self.assertTrue(owned['success'])
        self.assertEqual(owned['email']['id'], '101')
        self.assertEqual(owned['email']['subject'], 'Owned detail')

        other_mail = FakeHmeMail({
            '101': make_raw_message(
                'Not owned detail',
                extra_headers={'Delivered-To': 'other@icloud.com'},
                body='not owned detail body',
            ),
        })
        with patch.object(web_outlook_app, 'get_icloud_hme_source_imap_config', return_value=self.source_config), \
             patch.object(web_outlook_app, 'create_imap_connection', return_value=other_mail):
            not_owned = web_outlook_app.fetch_icloud_hme_account_detail_response(
                self.account, 'inbox', '101', 'imap', 'uid'
            )

        self.assertFalse(not_owned['success'])


if __name__ == '__main__':
    unittest.main()
