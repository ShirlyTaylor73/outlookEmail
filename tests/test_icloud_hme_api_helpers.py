import importlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
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
        self.assertEqual(captured['url'], 'https://p68-maildomainws.icloud.com/v1/hme/generate')
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
        self.assertEqual(captured['url'], 'https://p68-maildomainws.icloud.com/v1/hme/reserve')
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
        self.assertEqual(captured['url'], 'https://p68-maildomainws.icloud.com/v1/hme/deactivate')
        self.assertEqual(captured['body'], {'anonymousId': 'anon'})

    def test_delete_icloud_hme_posts_delete_with_anonymous_id(self):
        captured = self._capture_action_request(
            lambda: web_outlook_app.delete_icloud_hme('cookie=value', 'global', '', 'anon')
        )

        self.assertEqual(captured['method'], 'POST')
        self.assertEqual(captured['url'], 'https://p68-maildomainws.icloud.com/v1/hme/delete')
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


if __name__ == '__main__':
    unittest.main()
