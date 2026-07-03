import importlib
import os
import sys
import tempfile
import time
import unittest
from email.message import EmailMessage
from unittest.mock import Mock, patch


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


class SlowFetchHmeMail(FakeHmeMail):
    def __init__(self, messages, *, fetch_delay=0.0):
        super().__init__(messages)
        self.fetch_delay = fetch_delay
        self.fetched_uids = []

    def uid(self, command, *args):
        if command == 'FETCH':
            uid = args[0].decode('ascii') if isinstance(args[0], bytes) else str(args[0])
            self.fetched_uids.append(uid)
            if self.fetch_delay:
                time.sleep(self.fetch_delay)
        return super().uid(command, *args)


class IcloudHmeMailFetchTests(unittest.TestCase):
    def setUp(self):
        self.app = web_outlook_app.app
        self.app.config['TESTING'] = True
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
            'use_ssl': True,
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
                to='',
                body='Your alias abc@icloud.com received a message.',
            ),
        })

        self.assertTrue(result['success'])
        self.assertEqual([item['id'] for item in result['emails']], ['101'])

    def test_recipient_header_for_other_alias_blocks_body_fallback(self):
        result, _fake_mail = self._fetch_list_with_messages({
            '101': make_raw_message(
                'Other alias with body mention',
                to='other@icloud.com',
                body='Forwarded notice mentions abc@icloud.com in the body.',
            ),
        })

        self.assertTrue(result['success'])
        self.assertEqual(result['emails'], [])

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

    def test_hme_source_imap_config_includes_use_ssl(self):
        with self.app_context_with_hme_source(use_ssl=False) as source_id:
            config = web_outlook_app.get_icloud_hme_source_imap_config({
                'email': 'abc@icloud.com',
                'icloud_hme_source_id': source_id,
            })

        self.assertFalse(config['use_ssl'])

    def test_hme_fetch_uses_plain_imap_when_source_ssl_disabled(self):
        fake_mail = FakeHmeMail({
            '101': make_raw_message(
                'Plain IMAP hit',
                extra_headers={'Delivered-To': 'abc@icloud.com'},
            ),
        })
        plain_calls = []

        class PlainImapFactory:
            error = web_outlook_app.imaplib.IMAP4.error

            def __call__(self, host, port, timeout=None):
                plain_calls.append((host, port, timeout))
                return fake_mail

        config = dict(self.source_config, use_ssl=False)
        with patch.object(web_outlook_app, 'get_icloud_hme_source_imap_config', return_value=config), \
             patch.object(web_outlook_app.imaplib, 'IMAP4', PlainImapFactory()), \
             patch.object(web_outlook_app.imaplib, 'IMAP4_SSL', Mock(side_effect=AssertionError('SSL should not be used'))):
            result = web_outlook_app.fetch_account_emails(self.account, 'inbox', 0, 20)

        self.assertTrue(result['success'], msg=result)
        self.assertEqual([item['id'] for item in result['emails']], ['101'])
        self.assertEqual(plain_calls, [('imap.example.com', 993, web_outlook_app.IMAP_TIMEOUT)])

    def test_hme_all_folder_fetch_uses_overall_timeout(self):
        def fake_fetch_folder(_account, folder, _skip, _top):
            if folder == 'inbox':
                time.sleep(0.25)
                return {'success': True, 'emails': [], 'method': 'IMAP (HME)', 'has_more': False}
            return {
                'success': True,
                'emails': [{
                    'id': '201',
                    'subject': 'Junk hit',
                    'date': '2026-04-14T08:20:50+00:00',
                    'folder': folder,
                }],
                'method': 'IMAP (HME)',
                'has_more': False,
                'request_method': 'imap',
            }

        with patch.object(web_outlook_app, 'MAIL_FETCH_OVERALL_TIMEOUT', 0.05), \
             patch.object(web_outlook_app, 'fetch_icloud_hme_folder_emails', side_effect=fake_fetch_folder):
            started = time.monotonic()
            result = web_outlook_app.fetch_icloud_hme_account_emails(self.account, 'all', 0, 20)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.2)
        self.assertTrue(result['success'], msg=result)
        self.assertTrue(result.get('partial'), msg=result)
        self.assertEqual([item['id'] for item in result['emails']], ['201'])
        inbox_error = result['folder_summaries']['inbox']['error']
        self.assertEqual(inbox_error['code'], 'EMAIL_FETCH_TIMEOUT')

    def test_hme_all_folder_workers_have_flask_app_context(self):
        def fake_fetch_folder(_account, folder, _skip, _top):
            web_outlook_app.get_db()
            return {
                'success': True,
                'emails': [{
                    'id': folder,
                    'subject': f'{folder} hit',
                    'date': '2026-04-14T08:20:50+00:00',
                    'folder': folder,
                }],
                'method': 'IMAP (HME)',
                'has_more': False,
                'request_method': 'imap',
            }

        with patch.object(web_outlook_app, 'fetch_icloud_hme_folder_emails', side_effect=fake_fetch_folder):
            result = web_outlook_app.fetch_icloud_hme_account_emails(self.account, 'all', 0, 20)

        self.assertTrue(result['success'], msg=result)
        self.assertEqual([item['id'] for item in result['emails']], ['inbox', 'junkemail'])

    def test_hme_folder_returns_partial_matches_before_scan_timeout(self):
        messages = {
            str(uid): make_raw_message(
                f'Match {uid}',
                extra_headers={'Delivered-To': 'abc@icloud.com'},
            )
            for uid in range(1, 80)
        }
        fake_mail = SlowFetchHmeMail(messages, fetch_delay=0.01)

        with patch.object(web_outlook_app, 'get_icloud_hme_source_imap_config', return_value=self.source_config), \
             patch.object(web_outlook_app, 'create_imap_connection', return_value=fake_mail), \
             patch.object(web_outlook_app, 'get_hme_folder_scan_timeout_seconds', return_value=0.06):
            started = time.monotonic()
            result = web_outlook_app.fetch_icloud_hme_folder_emails(self.account, 'inbox', 0, 20)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.3)
        self.assertTrue(result['success'], msg=result)
        self.assertTrue(result.get('partial'), msg=result)
        self.assertTrue(result.get('scan_timed_out'), msg=result)
        self.assertGreater(len(result['emails']), 0)
        self.assertLess(len(fake_mail.fetched_uids), len(messages))

    def test_hme_folder_timeout_is_checked_after_non_matching_messages(self):
        messages = {
            str(uid): make_raw_message(
                f'Message {uid}',
                extra_headers={'Delivered-To': 'abc@icloud.com'} if uid == 80 else None,
            )
            for uid in range(1, 81)
        }
        fake_mail = SlowFetchHmeMail(messages, fetch_delay=0.01)

        with patch.object(web_outlook_app, 'get_icloud_hme_source_imap_config', return_value=self.source_config), \
             patch.object(web_outlook_app, 'create_imap_connection', return_value=fake_mail), \
             patch.object(web_outlook_app, 'get_hme_folder_scan_timeout_seconds', return_value=0.06):
            started = time.monotonic()
            result = web_outlook_app.fetch_icloud_hme_folder_emails(self.account, 'inbox', 0, 20)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.3)
        self.assertTrue(result['success'], msg=result)
        self.assertTrue(result.get('partial'), msg=result)
        self.assertEqual([item['subject'] for item in result['emails']], ['Message 80'])
        self.assertLess(len(fake_mail.fetched_uids), len(messages))

    def test_hme_source_cache_is_shared_by_accounts_on_same_source(self):
        with HmeSourceCacheContext(self.app) as ctx:
            abc_account = ctx.create_hme_account('abc@icloud.com')
            other_account = ctx.create_hme_account('other@icloud.com')
            fake_mail = FakeHmeMail({
                '101': make_raw_message(
                    'ABC message',
                    extra_headers={'Delivered-To': 'abc@icloud.com'},
                ),
                '102': make_raw_message(
                    'Other message',
                    extra_headers={'Delivered-To': 'other@icloud.com'},
                ),
            })

            with patch.object(web_outlook_app, 'create_imap_connection', return_value=fake_mail):
                abc_result = web_outlook_app.fetch_account_emails(abc_account, 'inbox', 0, 20)

            with patch.object(
                web_outlook_app,
                'create_imap_connection',
                Mock(side_effect=AssertionError('same source should use local HME cache')),
            ):
                other_result = web_outlook_app.fetch_account_emails(other_account, 'inbox', 0, 20)

        self.assertTrue(abc_result['success'], msg=abc_result)
        self.assertEqual([item['subject'] for item in abc_result['emails']], ['ABC message'])
        self.assertTrue(other_result['success'], msg=other_result)
        self.assertEqual([item['subject'] for item in other_result['emails']], ['Other message'])
        self.assertEqual(len(fake_mail.login_calls), 1)

    def test_hme_cached_detail_is_returned_without_imap_refetch(self):
        with HmeSourceCacheContext(self.app) as ctx:
            account = ctx.create_hme_account('abc@icloud.com')
            fake_mail = FakeHmeMail({
                '101': make_raw_message(
                    'Cached detail',
                    extra_headers={'Delivered-To': 'abc@icloud.com'},
                    body='cached body text',
                ),
            })

            with patch.object(web_outlook_app, 'create_imap_connection', return_value=fake_mail):
                list_result = web_outlook_app.fetch_account_emails(account, 'inbox', 0, 20)

            with patch.object(
                web_outlook_app,
                'create_imap_connection',
                Mock(side_effect=AssertionError('cached detail should not refetch IMAP')),
            ):
                detail_result = web_outlook_app.fetch_icloud_hme_account_detail_response(
                    account, 'inbox', '101', 'imap', 'uid'
                )

        self.assertTrue(list_result['success'], msg=list_result)
        self.assertTrue(detail_result['success'], msg=detail_result)
        self.assertEqual(detail_result['email']['subject'], 'Cached detail')
        self.assertIn('cached body text', detail_result['email']['body'])

    def app_context_with_hme_source(self, *, use_ssl):
        return HmeSourceContext(self.app, use_ssl=use_ssl)


class HmeSourceContext:
    def __init__(self, app, *, use_ssl):
        self.app = app
        self.use_ssl = use_ssl
        self.context = None
        self.source_id = None

    def __enter__(self):
        self.context = self.app.app_context()
        self.context.__enter__()
        web_outlook_app.init_db()
        db = web_outlook_app.get_db()
        for table in ('accounts', 'icloud_hme_sources'):
            db.execute(f'DELETE FROM {table}')
        cursor = db.execute(
            '''
            INSERT INTO icloud_hme_sources (
                name, region, receiver_email, receiver_provider, receiver_imap_host,
                receiver_imap_port, receiver_imap_password, receiver_folder, use_ssl,
                cookie, maildomain_host
            )
            VALUES (?, 'global', ?, 'custom', ?, 143, ?, 'INBOX', ?, '', '')
            ''',
            (
                'Plain Source',
                'receiver@example.com',
                'imap.example.com',
                web_outlook_app.encrypt_data('source-password'),
                1 if self.use_ssl else 0,
            ),
        )
        db.commit()
        self.source_id = cursor.lastrowid
        return self.source_id

    def __exit__(self, exc_type, exc, tb):
        return self.context.__exit__(exc_type, exc, tb)


class HmeSourceCacheContext:
    def __init__(self, app):
        self.app = app
        self.context = None
        self.source_id = None

    def __enter__(self):
        self.context = self.app.app_context()
        self.context.__enter__()
        web_outlook_app.init_db()
        db = web_outlook_app.get_db()
        for table in (
            'icloud_hme_source_message_recipients',
            'icloud_hme_source_messages',
            'accounts',
            'icloud_hme_sources',
        ):
            db.execute(f'DELETE FROM {table}')
        cursor = db.execute(
            '''
            INSERT INTO icloud_hme_sources (
                name, region, receiver_email, receiver_provider, receiver_imap_host,
                receiver_imap_port, receiver_imap_password, receiver_folder, use_ssl,
                cookie, maildomain_host
            )
            VALUES (?, 'global', ?, 'custom', ?, 993, ?, 'INBOX', 1, '', '')
            ''',
            (
                'Shared Gmail Source',
                'receiver@example.com',
                'imap.example.com',
                web_outlook_app.encrypt_data('source-password'),
            ),
        )
        self.source_id = cursor.lastrowid
        db.commit()
        return self

    def create_hme_account(self, email_addr):
        db = web_outlook_app.get_db()
        cursor = db.execute(
            '''
            INSERT INTO accounts (
                email, client_id, refresh_token, password, group_id, remark, status,
                account_type, provider, imap_host, imap_port, imap_password,
                proxy_url, icloud_hme_source_id
            )
            VALUES (?, '', '', '', 1, '', 'active', 'icloud_hme',
                    'icloud_hme', '', 993, '', '', ?)
            ''',
            (email_addr, self.source_id),
        )
        db.commit()
        return {
            'id': cursor.lastrowid,
            'email': email_addr,
            'account_type': 'icloud_hme',
            'provider': 'icloud_hme',
            'icloud_hme_source_id': self.source_id,
            'proxy_url': '',
        }

    def __exit__(self, exc_type, exc, tb):
        return self.context.__exit__(exc_type, exc, tb)


if __name__ == '__main__':
    unittest.main()
