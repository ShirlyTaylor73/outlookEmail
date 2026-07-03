import importlib
import os
import sqlite3
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
            for table in ('account_shares', 'account_aliases', 'account_tags', 'accounts', 'icloud_hme_sources'):
                try:
                    db.execute(f'DELETE FROM {table}')
                except sqlite3.OperationalError as exc:
                    if 'no such table' not in str(exc):
                        raise
            db.commit()

    def test_icloud_hme_provider_meta_exists(self):
        meta = web_outlook_app.get_provider_meta("icloud_hme", "alias@icloud.com")
        self.assertEqual(meta["key"], "icloud_hme")
        self.assertEqual(meta["account_type"], "icloud_hme")
        self.assertEqual(meta["label"], "iCloud Hide My Email")

    def _source_payload(self, **overrides):
        payload = {
            "name": "Gmail receiver",
            "region": "global",
            "receiver_email": "receiver@gmail.com",
            "receiver_provider": "gmail",
            "receiver_imap_host": "imap.gmail.com",
            "receiver_imap_port": 993,
            "receiver_imap_password": "app-password",
            "receiver_folder": "INBOX",
            "use_ssl": True,
            "cookie": "X-APPLE-WEBAUTH-USER=secret",
            "maildomain_host": "p68-maildomainws.icloud.com",
        }
        payload.update(overrides)
        return payload

    def _create_source(self, **overrides):
        response = self.client.post("/api/icloud-hme/sources", json=self._source_payload(**overrides))
        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertTrue(data["success"], msg=data)
        return data["source"]

    def test_create_source_encrypts_secret_and_serializes_safely(self):
        response = self.client.post("/api/icloud-hme/sources", json=self._source_payload())
        data = response.get_json()
        self.assertEqual(response.status_code, 201)
        self.assertTrue(data["success"])
        source = data["source"]
        self.assertNotIn("receiver_imap_password", source)
        self.assertNotIn("cookie", source)
        self.assertTrue(source["use_ssl"])
        self.assertEqual(source["region"], "global")

        with self.app.app_context():
            row = web_outlook_app.get_db().execute(
                "SELECT receiver_imap_password, cookie FROM icloud_hme_sources WHERE id = ?",
                (source["id"],),
            ).fetchone()
        self.assertNotEqual(row["receiver_imap_password"], "app-password")
        self.assertNotEqual(row["cookie"], "X-APPLE-WEBAUTH-USER=secret")
        self.assertTrue(row["receiver_imap_password"].startswith("enc:"))
        self.assertTrue(row["cookie"].startswith("enc:"))

    def test_list_and_detail_do_not_return_secret_fields(self):
        created = self._create_source()

        listed = self.client.get("/api/icloud-hme/sources")
        detail = self.client.get(f"/api/icloud-hme/sources/{created['id']}")

        self.assertEqual(listed.status_code, 200)
        listed_payload = listed.get_json()
        self.assertTrue(listed_payload["success"])
        self.assertEqual(len(listed_payload["sources"]), 1)
        self.assertNotIn("receiver_imap_password", listed_payload["sources"][0])
        self.assertNotIn("cookie", listed_payload["sources"][0])

        self.assertEqual(detail.status_code, 200)
        detail_source = detail.get_json()["source"]
        self.assertNotIn("receiver_imap_password", detail_source)
        self.assertNotIn("cookie", detail_source)

    def test_update_source_keeps_empty_password_and_cookie_and_can_clear_cookie(self):
        created = self._create_source()
        with self.app.app_context():
            before = web_outlook_app.get_db().execute(
                "SELECT receiver_imap_password, cookie FROM icloud_hme_sources WHERE id = ?",
                (created["id"],),
            ).fetchone()
            before_password = before["receiver_imap_password"]
            before_cookie = before["cookie"]

        updated = self.client.put(f"/api/icloud-hme/sources/{created['id']}", json={
            "name": "Updated receiver",
            "region": "invalid-region",
            "receiver_imap_password": "",
            "cookie": "",
        })
        self.assertEqual(updated.status_code, 200)
        payload = updated.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["source"]["name"], "Updated receiver")
        self.assertEqual(payload["source"]["region"], "global")
        self.assertNotIn("receiver_imap_password", payload["source"])
        self.assertNotIn("cookie", payload["source"])

        with self.app.app_context():
            after = web_outlook_app.get_db().execute(
                "SELECT receiver_imap_password, cookie FROM icloud_hme_sources WHERE id = ?",
                (created["id"],),
            ).fetchone()
            self.assertEqual(after["receiver_imap_password"], before_password)
            self.assertEqual(after["cookie"], before_cookie)

        cleared = self.client.put(f"/api/icloud-hme/sources/{created['id']}", json={"clear_cookie": True})
        self.assertEqual(cleared.status_code, 200)
        with self.app.app_context():
            cleared_row = web_outlook_app.get_db().execute(
                "SELECT cookie FROM icloud_hme_sources WHERE id = ?",
                (created["id"],),
            ).fetchone()
        self.assertEqual(cleared_row["cookie"], "")

    def test_delete_source_rejects_when_icloud_hme_account_is_bound(self):
        created = self._create_source()
        with self.app.app_context():
            db = web_outlook_app.get_db()
            db.execute(
                """
                INSERT INTO accounts (
                    email, password, client_id, refresh_token, group_id,
                    remark, status, account_type, provider, imap_host,
                    imap_port, imap_password, icloud_hme_source_id
                )
                VALUES (?, '', '', '', 1, '', 'active', 'icloud_hme', 'icloud_hme', '', 993, '', ?)
                """,
                ("alias@icloud.com", created["id"]),
            )
            db.commit()

        response = self.client.delete(f"/api/icloud-hme/sources/{created['id']}")
        payload = response.get_json()

        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["success"])
        with self.app.app_context():
            row = web_outlook_app.get_db().execute(
                "SELECT id FROM icloud_hme_sources WHERE id = ?",
                (created["id"],),
            ).fetchone()
        self.assertIsNotNone(row)
