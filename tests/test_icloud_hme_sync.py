import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


os.environ.setdefault('SECRET_KEY', 'test-secret-key')
if 'DATABASE_PATH' not in os.environ:
    _temp_dir = tempfile.mkdtemp(prefix='outlookEmail-icloud-hme-sync-tests-')
    os.environ['DATABASE_PATH'] = os.path.join(_temp_dir, 'test.db')

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

web_outlook_app = importlib.import_module('web_outlook_app')


class ICloudHmeSyncTestCase(unittest.TestCase):
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
            "name": "Receiver",
            "region": "global",
            "receiver_email": "receiver@example.com",
            "receiver_provider": "custom",
            "receiver_imap_host": "imap.example.com",
            "receiver_imap_port": 993,
            "receiver_imap_password": "app-password",
            "receiver_folder": "INBOX",
            "use_ssl": True,
            "cookie": "encrypted-cookie",
            "maildomain_host": "maildomain.icloud.com",
        }
        payload.update(overrides)
        return payload

    def _create_source(self, **overrides):
        response = self.client.post("/api/icloud-hme/sources", json=self._source_payload(**overrides))
        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertTrue(data["success"], msg=data)
        return data["source"]["id"]

    def test_sync_creates_and_updates_hme_accounts(self):
        source_id = self._create_source(cookie="encrypted-cookie")
        with patch.object(web_outlook_app, "fetch_icloud_hme_list", return_value={
            "success": True,
            "hmeEmails": [
                {"hme": "new@icloud.com", "label": "new label", "isActive": True},
                {"hme": "old@icloud.com", "label": "old label", "isActive": False},
            ],
        }):
            response = self.client.post(f"/api/icloud-hme/sources/{source_id}/sync")

        data = response.get_json()
        self.assertTrue(data["success"], msg=data)
        self.assertEqual(data["created"], 2)
        self.assertEqual(data["inactive"], 1)
        self.assertEqual(data["conflicts"], [])
        self.assertEqual(data["errors"], [])
        with self.app.app_context():
            rows = web_outlook_app.get_db().execute(
                """
                SELECT email, remark, status, account_type, provider, icloud_hme_source_id
                FROM accounts
                ORDER BY email
                """
            ).fetchall()
        self.assertEqual([row["email"] for row in rows], ["new@icloud.com", "old@icloud.com"])
        self.assertEqual(rows[0]["remark"], "new label")
        self.assertEqual(rows[1]["remark"], "old label")
        self.assertEqual(rows[1]["status"], "inactive")
        self.assertEqual(rows[0]["account_type"], "icloud_hme")
        self.assertEqual(rows[0]["provider"], "icloud_hme")
        self.assertEqual(rows[0]["icloud_hme_source_id"], source_id)

    def test_sync_updates_existing_same_source_account(self):
        source_id = self._create_source()
        import_response = self.client.post("/api/icloud-hme/accounts/import", json={
            "source_id": source_id,
            "group_id": 1,
            "account_string": "old@icloud.com",
            "remark": "before",
            "status": "active",
        }).get_json()
        self.assertTrue(import_response["success"], msg=import_response)

        with patch.object(web_outlook_app, "fetch_icloud_hme_list", return_value={
            "success": True,
            "hmeEmails": [
                {"hme": "old@icloud.com", "label": "after", "isActive": False},
            ],
        }):
            response = self.client.post(f"/api/icloud-hme/sources/{source_id}/sync")

        data = response.get_json()
        self.assertTrue(data["success"], msg=data)
        self.assertEqual(data["created"], 0)
        self.assertEqual(data["updated"], 1)
        self.assertEqual(data["inactive"], 1)
        with self.app.app_context():
            row = web_outlook_app.get_db().execute(
                "SELECT remark, status FROM accounts WHERE LOWER(email) = 'old@icloud.com'"
            ).fetchone()
        self.assertEqual(row["remark"], "after")
        self.assertEqual(row["status"], "inactive")

    def test_sync_failure_writes_last_sync_error(self):
        source_id = self._create_source()
        with patch.object(web_outlook_app, "fetch_icloud_hme_list", return_value={
            "success": False,
            "error": "Cookie expired",
        }):
            response = self.client.post(f"/api/icloud-hme/sources/{source_id}/sync")

        data = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(data["success"])
        self.assertIn("Cookie expired", data["error"])
        with self.app.app_context():
            row = web_outlook_app.get_db().execute(
                "SELECT last_sync_status, last_sync_error FROM icloud_hme_sources WHERE id = ?",
                (source_id,),
            ).fetchone()
        self.assertEqual(row["last_sync_status"], "failed")
        self.assertIn("Cookie expired", row["last_sync_error"])

    def test_sync_missing_cookie_fails_without_fetching_hme_list_or_leaking_cookie(self):
        source_id = self._create_source(cookie="")
        with patch.object(web_outlook_app, "fetch_icloud_hme_list") as fetch_mock:
            response = self.client.post(f"/api/icloud-hme/sources/{source_id}/sync")

        data = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(data["success"])
        self.assertIn("Cookie", data["error"])
        self.assertNotIn("cookie", data)
        self.assertNotIn("encrypted-cookie", response.get_data(as_text=True))
        fetch_mock.assert_not_called()
        with self.app.app_context():
            row = web_outlook_app.get_db().execute(
                "SELECT last_sync_status, last_sync_error FROM icloud_hme_sources WHERE id = ?",
                (source_id,),
            ).fetchone()
        self.assertEqual(row["last_sync_status"], "failed")
        self.assertIsNotNone(row["last_sync_error"])
        self.assertIn("Cookie", row["last_sync_error"])

    def test_sync_across_source_conflict_does_not_migrate_account(self):
        first_source_id = self._create_source(name="Receiver 1", receiver_email="one@example.com")
        second_source_id = self._create_source(name="Receiver 2", receiver_email="two@example.com")
        import_response = self.client.post("/api/icloud-hme/accounts/import", json={
            "source_id": first_source_id,
            "group_id": 1,
            "account_string": "shared@icloud.com",
        }).get_json()
        self.assertTrue(import_response["success"], msg=import_response)

        with patch.object(web_outlook_app, "fetch_icloud_hme_list", return_value={
            "success": True,
            "hmeEmails": [
                {"hme": "shared@icloud.com", "label": "shared", "isActive": True},
            ],
        }):
            response = self.client.post(f"/api/icloud-hme/sources/{second_source_id}/sync")

        data = response.get_json()
        self.assertTrue(data["success"], msg=data)
        self.assertEqual(data["created"], 0)
        self.assertEqual(data["updated"], 0)
        self.assertEqual(len(data["conflicts"]), 1)
        self.assertEqual(data["conflicts"][0]["existing_source_id"], first_source_id)
        with self.app.app_context():
            row = web_outlook_app.get_db().execute(
                "SELECT icloud_hme_source_id FROM accounts WHERE LOWER(email) = 'shared@icloud.com'"
            ).fetchone()
        self.assertEqual(row["icloud_hme_source_id"], first_source_id)

    def test_sync_success_writes_last_sync_status(self):
        source_id = self._create_source()
        with patch.object(web_outlook_app, "fetch_icloud_hme_list", return_value={
            "success": True,
            "hmeEmails": [],
        }):
            response = self.client.post(f"/api/icloud-hme/sources/{source_id}/sync")

        data = response.get_json()
        self.assertTrue(data["success"], msg=data)
        with self.app.app_context():
            row = web_outlook_app.get_db().execute(
                "SELECT last_sync_at, last_sync_status, last_sync_error FROM icloud_hme_sources WHERE id = ?",
                (source_id,),
            ).fetchone()
        self.assertIsNotNone(row["last_sync_at"])
        self.assertEqual(row["last_sync_status"], "success")
        self.assertIsNone(row["last_sync_error"])

    def test_fetch_hme_list_treats_legacy_generic_maildomain_host_as_default(self):
        captured = {}

        class ResponseStub:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"hmeEmails":[]}'

        def fake_urlopen(request_obj, timeout=0):
            captured["url"] = request_obj.full_url
            return ResponseStub()

        with patch.object(web_outlook_app.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = web_outlook_app.fetch_icloud_hme_list(
                "cookie=value",
                "global",
                "maildomain.icloud.com",
            )

        self.assertTrue(result["success"], msg=result)
        self.assertEqual(captured["url"], "https://p68-maildomainws.icloud.com/v2/hme/list")


if __name__ == '__main__':
    unittest.main()
