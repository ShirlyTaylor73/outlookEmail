import importlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch


os.environ.setdefault('SECRET_KEY', 'test-secret-key')
if 'DATABASE_PATH' not in os.environ:
    _temp_dir = tempfile.mkdtemp(prefix='outlookEmail-icloud-hme-api-helper-tests-')
    os.environ['DATABASE_PATH'] = os.path.join(_temp_dir, 'test.db')

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

web_outlook_app = importlib.import_module('web_outlook_app')


class ResponseStub:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode('utf-8')


class ICloudHmeApiHelperTestCase(unittest.TestCase):
    def _capture_action_request(self, call_helper):
        captured = {}

        def fake_urlopen(request_obj, timeout=0):
            captured['url'] = request_obj.full_url
            captured['method'] = request_obj.get_method()
            captured['body'] = json.loads((request_obj.data or b'{}').decode('utf-8'))
            captured['timeout'] = timeout
            return ResponseStub({'ok': True})

        with patch.object(web_outlook_app.urllib.request, 'urlopen', side_effect=fake_urlopen):
            result = call_helper()

        self.assertTrue(result['success'], msg=result)
        return captured

    def test_generate_icloud_hme_posts_generate_with_lang_code(self):
        captured = self._capture_action_request(
            lambda: web_outlook_app.generate_icloud_hme('cookie=value', 'global', '')
        )

        self.assertEqual(captured['method'], 'POST')
        parsed = urllib.parse.urlsplit(captured['url'])
        self.assertEqual(parsed.scheme, 'https')
        self.assertEqual(parsed.netloc, 'p68-maildomainws.icloud.com')
        self.assertEqual(parsed.path, '/v1/hme/generate')
        self.assertEqual(urllib.parse.parse_qs(parsed.query, keep_blank_values=True), {
            'clientBuildNumber': ['2536Project32'],
            'clientMasteringNumber': ['2536B20'],
            'clientId': [''],
            'dsid': [''],
        })
        self.assertEqual(captured['body'], {'langCode': 'en-us'})

    def test_reserve_icloud_hme_posts_reserve_with_hme_label_and_note(self):
        captured = self._capture_action_request(
            lambda: web_outlook_app.reserve_icloud_hme(
                'cookie=value',
                'global',
                '',
                'a@icloud.com',
                'label',
                'note',
            )
        )

        self.assertEqual(captured['method'], 'POST')
        self.assertEqual(urllib.parse.urlsplit(captured['url']).path, '/v1/hme/reserve')
        self.assertEqual(captured['body'], {
            'hme': 'a@icloud.com',
            'label': 'label',
            'note': 'note',
        })

    def test_deactivate_icloud_hme_posts_deactivate_with_anonymous_id(self):
        captured = self._capture_action_request(
            lambda: web_outlook_app.deactivate_icloud_hme('cookie=value', 'global', '', 'anon')
        )

        self.assertEqual(captured['method'], 'POST')
        self.assertEqual(urllib.parse.urlsplit(captured['url']).path, '/v1/hme/deactivate')
        self.assertEqual(captured['body'], {'anonymousId': 'anon'})

    def test_delete_icloud_hme_posts_delete_with_anonymous_id(self):
        captured = self._capture_action_request(
            lambda: web_outlook_app.delete_icloud_hme('cookie=value', 'global', '', 'anon')
        )

        self.assertEqual(captured['method'], 'POST')
        self.assertEqual(urllib.parse.urlsplit(captured['url']).path, '/v1/hme/delete')
        self.assertEqual(captured['body'], {'anonymousId': 'anon'})

    def test_hme_api_helper_returns_sanitized_http_error(self):
        error_body = b'{"error":"bad","access_token":"secret-token","password":"secret-password"}'

        def fake_urlopen(_request_obj, timeout=0):
            raise urllib.error.HTTPError(
                'https://p68-maildomainws.icloud.com/v1/hme/generate',
                403,
                'Forbidden',
                {},
                io.BytesIO(error_body),
            )

        with patch.object(web_outlook_app.urllib.request, 'urlopen', side_effect=fake_urlopen):
            result = web_outlook_app.generate_icloud_hme('cookie=value', 'global', '')

        self.assertFalse(result['success'])
        self.assertEqual(result['status_code'], 403)
        self.assertIn('HTTP 403', result['error'])
        self.assertNotIn('secret-token', result['error'])
        self.assertNotIn('secret-password', result['error'])

    def test_hme_api_helper_recognizes_icloud_error_payload(self):
        with patch.object(
            web_outlook_app.urllib.request,
            'urlopen',
            return_value=ResponseStub({
                'errorCode': '-41017',
                'errorMessage': 'Cannot connect to iCloud',
                'retryAfter': 2,
            }),
        ):
            result = web_outlook_app.generate_icloud_hme('cookie=value', 'global', '')

        self.assertFalse(result['success'])
        self.assertEqual(result['retry_after'], 2)
        self.assertEqual(
            result['error'],
            'Cannot connect to iCloud (errorCode -41017, retryAfter 2s)',
        )

    def test_fetch_icloud_hme_list_reads_result_hme_emails(self):
        hme_items = [{
            'hme': 'listed@icloud.com',
            'label': 'Listed',
            'isActive': True,
            'createTimestamp': 1783814400000,
        }]
        with patch.object(
            web_outlook_app.urllib.request,
            'urlopen',
            return_value=ResponseStub({'success': True, 'result': {'hmeEmails': hme_items}}),
        ):
            result = web_outlook_app.fetch_icloud_hme_list('cookie=value', 'global', '')

        self.assertTrue(result['success'], msg=result)
        self.assertEqual(result['hmeEmails'], hme_items)


if __name__ == '__main__':
    unittest.main()
