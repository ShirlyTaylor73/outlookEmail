import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault('SECRET_KEY', 'test-secret-key')
if 'DATABASE_PATH' not in os.environ:
    _temp_dir = tempfile.mkdtemp(prefix='outlookEmail-icloud-hme-address-tests-')
    os.environ['DATABASE_PATH'] = os.path.join(_temp_dir, 'test.db')

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

web_outlook_app = importlib.import_module('web_outlook_app')
TEMPLATE_PATH = Path(ROOT_DIR) / 'templates' / 'partials' / 'index' / 'dialogs-management.html'
SETTINGS_JS_PATH = Path(ROOT_DIR) / 'static' / 'js' / 'index' / '07-settings.js'


class ICloudHmeAddressManagementTestCase(unittest.TestCase):
    def setUp(self):
        self.app = web_outlook_app.app
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['logged_in'] = True

        with self.app.app_context():
            web_outlook_app.init_db()
            db = web_outlook_app.get_db()
            for table in (
                'icloud_hme_address_cache',
                'account_shares',
                'account_aliases',
                'account_tags',
                'accounts',
                'icloud_hme_sources',
            ):
                try:
                    db.execute(f'DELETE FROM {table}')
                except sqlite3.OperationalError as exc:
                    if 'no such table' not in str(exc):
                        raise
            db.commit()

    def _source_payload(self, **overrides):
        payload = {
            'name': 'Receiver',
            'region': 'global',
            'receiver_email': 'receiver@example.com',
            'receiver_provider': 'custom',
            'receiver_imap_host': 'imap.example.com',
            'receiver_imap_port': 993,
            'receiver_imap_password': 'app-password',
            'receiver_folder': 'INBOX',
            'use_ssl': True,
            'cookie': 'secret-cookie',
            'maildomain_host': 'maildomain.icloud.com',
        }
        payload.update(overrides)
        return payload

    def _create_source(self, **overrides):
        response = self.client.post('/api/icloud-hme/sources', json=self._source_payload(**overrides))
        self.assertEqual(response.status_code, 201, msg=response.get_data(as_text=True))
        data = response.get_json()
        self.assertTrue(data['success'], msg=data)
        return data['source']['id']

    def _create_group(self, name):
        with self.app.app_context():
            db = web_outlook_app.get_db()
            db.execute(
                '''
                INSERT OR IGNORE INTO groups (name, description, color, mailbox_type)
                VALUES (?, '', '#2563eb', 'account')
                ''',
                (name,),
            )
            db.commit()
            return db.execute('SELECT id FROM groups WHERE name = ?', (name,)).fetchone()['id']

    def _import_hme_account(self, source_id, group_id, address, remark=''):
        response = self.client.post('/api/icloud-hme/accounts/import', json={
            'source_id': source_id,
            'group_id': group_id,
            'account_string': address,
            'remark': remark,
            'status': 'active',
        })
        self.assertEqual(response.status_code, 200, msg=response.get_data(as_text=True))
        data = response.get_json()
        self.assertTrue(data['success'], msg=data)
        return data

    def _insert_conflicting_account(self, group_id, address):
        with self.app.app_context():
            db = web_outlook_app.get_db()
            db.execute(
                '''
                INSERT INTO accounts (
                    email, password, client_id, refresh_token, group_id,
                    status, account_type, provider, imap_password
                )
                VALUES (?, '', '', '', ?, 'active', 'outlook', 'outlook', 'do-not-leak')
                ''',
                (address, group_id),
            )
            db.commit()

    def _fetch_payload(self):
        return {
            'success': True,
            'hmeEmails': [
                {
                    'hme': 'imported@icloud.com',
                    'label': 'Imported Label',
                    'note': 'Imported Note',
                    'isActive': True,
                    'anonymousId': 'anon-imported',
                    'createTimestamp': 1783814400000,
                },
                {'hme': 'not-imported@icloud.com', 'label': 'Not Imported Label', 'isActive': True},
                {'hme': 'conflict@icloud.com', 'label': 'Conflict Label', 'isActive': True},
                {'hme': 'inactive@icloud.com', 'label': 'Inactive Label', 'isActive': False},
            ],
        }

    def _assert_address_list(self, query, expected_hmes, expected_total):
        response = self.client.get(f'/api/icloud-hme/addresses?{query}')
        self.assertEqual(response.status_code, 200, msg=response.get_data(as_text=True))
        data = response.get_json()
        self.assertTrue(data['success'], msg=data)
        actual_hmes = [item['hme'] for item in data['items']]
        self.assertEqual(len(actual_hmes), len(expected_hmes))
        self.assertCountEqual(actual_hmes, expected_hmes)
        self.assertEqual(data['pagination']['total'], expected_total)
        return data

    def test_address_list_merges_import_state_and_never_leaks_source_secrets(self):
        source_id = self._create_source()
        imported_group_id = self._create_group('HME Imported')
        imported_group_name = 'HME Imported'
        conflict_group_id = self._create_group('HME Conflict')
        self._import_hme_account(source_id, imported_group_id, 'imported@icloud.com')
        self._insert_conflicting_account(conflict_group_id, 'conflict@icloud.com')

        with patch.object(web_outlook_app, 'fetch_icloud_hme_list', return_value=self._fetch_payload()):
            response = self.client.get(
                f'/api/icloud-hme/addresses?source_id={source_id}&active=true&refresh=1'
            )

        self.assertEqual(response.status_code, 200, msg=response.get_data(as_text=True))
        data = response.get_json()
        self.assertTrue(data['success'], msg=data)
        self.assertEqual(data['summary']['imported'], 1)
        self.assertEqual(data['summary']['not_imported'], 1)
        self.assertEqual(data['summary']['conflict'], 1)

        imported_item = next(item for item in data['items'] if item['hme'] == 'imported@icloud.com')
        self.assertEqual(imported_item['group_id'], imported_group_id)
        self.assertEqual(imported_item['group_name'], imported_group_name)
        self.assertEqual(imported_item['note'], 'Imported Note')
        self.assertEqual(imported_item['anonymous_id'], 'anon-imported')
        self.assertEqual(imported_item['created_at'], '2026-07-12T00:00:00Z')

        serialized = response.get_data(as_text=True)
        self.assertNotIn('cookie', serialized.lower())
        self.assertNotIn('secret-cookie', serialized)
        self.assertNotIn('receiver_imap_password', serialized)
        self.assertNotIn('app-password', serialized)
        self.assertNotIn('do-not-leak', serialized)

    def test_address_list_supports_import_group_keyword_and_active_filters(self):
        source_id = self._create_source()
        imported_group_id = self._create_group('HME Filter Imported')
        other_group_id = self._create_group('HME Filter Other')
        self._import_hme_account(source_id, imported_group_id, 'imported@icloud.com')
        self._import_hme_account(source_id, other_group_id, 'other-group@icloud.com')

        fetch_payload = {
            'success': True,
            'hmeEmails': [
                {'hme': 'imported@icloud.com', 'label': 'Imported Label', 'isActive': True},
                {'hme': 'other-group@icloud.com', 'label': 'Other Group', 'isActive': True},
                {'hme': 'fresh@icloud.com', 'label': 'Fresh Label', 'isActive': True},
                {'hme': 'label-match@icloud.com', 'label': 'Special Label', 'isActive': True},
                {'hme': 'email-match@icloud.com', 'label': 'Plain Label', 'isActive': True},
                {'hme': 'inactive@icloud.com', 'label': 'Inactive Label', 'isActive': False},
            ],
        }
        with patch.object(web_outlook_app, 'fetch_icloud_hme_list', return_value=fetch_payload):
            response = self.client.get(
                f'/api/icloud-hme/addresses?source_id={source_id}&refresh=1&active=all'
            )
        self.assertEqual(response.status_code, 200, msg=response.get_data(as_text=True))

        cases = [
            (
                'import_state=not_imported',
                f'source_id={source_id}&import_state=not_imported&active=true',
                ['fresh@icloud.com', 'label-match@icloud.com', 'email-match@icloud.com'],
                3,
            ),
            (
                'group_id',
                f'source_id={source_id}&group_id={imported_group_id}&active=true',
                ['imported@icloud.com'],
                1,
            ),
            (
                'q label',
                f'source_id={source_id}&q=special&active=true',
                ['label-match@icloud.com'],
                1,
            ),
            (
                'q email',
                f'source_id={source_id}&q=email-match&active=true',
                ['email-match@icloud.com'],
                1,
            ),
            (
                'active=all',
                f'source_id={source_id}&active=all',
                [
                    'imported@icloud.com',
                    'other-group@icloud.com',
                    'fresh@icloud.com',
                    'label-match@icloud.com',
                    'email-match@icloud.com',
                    'inactive@icloud.com',
                ],
                6,
            ),
        ]
        for name, query, expected_hmes, expected_total in cases:
            with self.subTest(filter=name):
                self._assert_address_list(query, expected_hmes, expected_total)

    def test_batch_import_addresses_creates_hme_accounts_in_requested_group(self):
        source_id = self._create_source()
        group_id = self._create_group('HME Batch Import')

        response = self.client.post('/api/icloud-hme/addresses/import', json={
            'source_id': source_id,
            'group_id': group_id,
            'addresses': ['new-hme@icloud.com', 'second-hme@icloud.com'],
            'remark': 'from address list',
        })

        self.assertEqual(response.status_code, 200, msg=response.get_data(as_text=True))
        data = response.get_json()
        self.assertTrue(data['success'], msg=data)
        self.assertEqual(len(data['results']), 2)
        for result in data['results']:
            self.assertEqual(result.get('status'), 'imported', msg=result)

        with self.app.app_context():
            rows = web_outlook_app.get_db().execute(
                '''
                SELECT email, account_type, provider, group_id, remark, icloud_hme_source_id
                FROM accounts
                WHERE LOWER(email) IN ('new-hme@icloud.com', 'second-hme@icloud.com')
                '''
            ).fetchall()
        self.assertEqual(len(rows), 2)
        rows_by_email = {row['email'].lower(): row for row in rows}
        self.assertEqual(set(rows_by_email), {'new-hme@icloud.com', 'second-hme@icloud.com'})
        for email, row in rows_by_email.items():
            with self.subTest(email=email):
                self.assertEqual(row['account_type'], 'icloud_hme')
                self.assertEqual(row['provider'], 'icloud_hme')
                self.assertEqual(row['group_id'], group_id)
                self.assertEqual(row['remark'], 'from address list')
                self.assertEqual(row['icloud_hme_source_id'], source_id)

    def test_address_list_paginates_and_filters_groups_server_side(self):
        source_id = self._create_source()
        group_id = self._create_group('Paged Imported')
        self._import_hme_account(source_id, group_id, 'page-2@icloud.com')
        fetch_payload = {
            'success': True,
            'hmeEmails': [
                {'hme': 'page-1@icloud.com', 'isActive': True},
                {'hme': 'page-2@icloud.com', 'isActive': True},
                {'hme': 'page-3@icloud.com', 'isActive': False},
            ],
        }
        with patch.object(web_outlook_app, 'fetch_icloud_hme_list', return_value=fetch_payload):
            self.client.get(f'/api/icloud-hme/addresses?source_id={source_id}&refresh=1&active=all')

        page_response = self.client.get(
            f'/api/icloud-hme/addresses?source_id={source_id}&active=all&limit=1&offset=1'
        )
        page_data = page_response.get_json()
        self.assertEqual(page_response.status_code, 200, msg=page_data)
        self.assertEqual(page_data['pagination']['total'], 3)
        self.assertEqual(len(page_data['items']), 1)

        group_response = self.client.get(
            f'/api/icloud-hme/addresses?source_id={source_id}&active=all&group_id={group_id}'
        )
        group_data = group_response.get_json()
        self.assertEqual(group_response.status_code, 200, msg=group_data)
        self.assertEqual([item['hme'] for item in group_data['items']], ['page-2@icloud.com'])

    def test_address_manager_frontend_exposes_source_status_group_and_row_import(self):
        template = TEMPLATE_PATH.read_text(encoding='utf-8')
        settings_js = SETTINGS_JS_PATH.read_text(encoding='utf-8')

        self.assertIn('id="icloudHmeAddressSourceId"', template)
        self.assertIn('id="icloudHmeAddressSelectAll"', template)
        self.assertIn('创建时间', template)
        self.assertIn('anonymousId', template)
        self.assertIn('id="icloudHmeAddressPaginationInfo"', template)
        self.assertIn('导入到所选分组', settings_js)
        self.assertIn("params.set('group_id', filters.group_id)", settings_js)
        self.assertIn('renderIcloudHmeAddressPagination()', settings_js)


if __name__ == '__main__':
    unittest.main()
