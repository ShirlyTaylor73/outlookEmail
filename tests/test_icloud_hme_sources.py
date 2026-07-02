import importlib
import os
import sys
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'test-secret-key')
if 'DATABASE_PATH' not in os.environ:
    _temp_dir = tempfile.mkdtemp(prefix='outlookEmail-icloud-hme-source-tests-')
    os.environ['DATABASE_PATH'] = os.path.join(_temp_dir, 'test.db')
ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

web_outlook_app = importlib.import_module('web_outlook_app')


class ICloudHmeSourceTestCase(unittest.TestCase):
    def test_icloud_hme_provider_meta_exists(self):
        meta = web_outlook_app.get_provider_meta("icloud_hme", "alias@icloud.com")
        self.assertEqual(meta["key"], "icloud_hme")
        self.assertEqual(meta["account_type"], "icloud_hme")
        self.assertEqual(meta["label"], "iCloud Hide My Email")
