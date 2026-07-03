import importlib
import os
import sqlite3
import sys
import tempfile
import unittest


os.environ.setdefault('SECRET_KEY', 'test-secret-key')
if 'DATABASE_PATH' not in os.environ:
    _temp_dir = tempfile.mkdtemp(prefix='outlookEmail-icloud-hme-import-tests-')
    os.environ['DATABASE_PATH'] = os.path.join(_temp_dir, 'test.db')

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

web_outlook_app = importlib.import_module('web_outlook_app')


class ICloudHmeImportTestCase(unittest.TestCase):
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
            "cookie": "secret-cookie",
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

    def test_import_hme_addresses_binds_selected_source(self):
        source_id = self._create_source()
        response = self.client.post("/api/icloud-hme/accounts/import", json={
            "source_id": source_id,
            "group_id": 1,
            "account_string": "abc@icloud.com\ndef@icloud.com----备注",
            "status": "active",
        })

        data = response.get_json()
        self.assertTrue(data["success"], msg=data)
        self.assertEqual(data["imported_count"], 2)
        with self.app.app_context():
            rows = web_outlook_app.get_db().execute(
                "SELECT email, account_type, provider, icloud_hme_source_id, remark FROM accounts ORDER BY email"
            ).fetchall()
        self.assertEqual(rows[0]["account_type"], "icloud_hme")
        self.assertEqual(rows[0]["provider"], "icloud_hme")
        self.assertEqual(rows[0]["icloud_hme_source_id"], source_id)
        self.assertEqual(rows[1]["remark"], "备注")

    def test_import_hme_line_suffix_number_is_remark_not_source_id(self):
        source_id = self._create_source()
        response = self.client.post("/api/icloud-hme/accounts/import", json={
            "source_id": source_id,
            "group_id": 1,
            "account_string": "abc@icloud.com----1",
        })

        data = response.get_json()
        self.assertTrue(data["success"], msg=data)
        with self.app.app_context():
            row = web_outlook_app.get_db().execute(
                "SELECT remark, icloud_hme_source_id FROM accounts WHERE email = ?",
                ("abc@icloud.com",),
            ).fetchone()
        self.assertEqual(row["remark"], "1")
        self.assertEqual(row["icloud_hme_source_id"], source_id)

    def test_import_hme_address_in_same_source_updates_editable_fields(self):
        source_id = self._create_source()
        first = self.client.post("/api/icloud-hme/accounts/import", json={
            "source_id": source_id,
            "group_id": 1,
            "account_string": "abc@icloud.com",
            "remark": "old remark",
            "status": "active",
        }).get_json()
        second = self.client.post("/api/icloud-hme/accounts/import", json={
            "source_id": source_id,
            "group_id": 1,
            "account_string": "abc@icloud.com",
            "remark": "new remark",
            "status": "inactive",
        }).get_json()

        self.assertTrue(first["success"], msg=first)
        self.assertTrue(second["success"], msg=second)
        self.assertEqual(second["imported_count"], 0)
        self.assertEqual(second["updated_count"], 1)
        self.assertEqual(second["conflicts"], [])
        with self.app.app_context():
            rows = web_outlook_app.get_db().execute(
                "SELECT remark, status FROM accounts WHERE LOWER(email) = 'abc@icloud.com'"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["remark"], "new remark")
        self.assertEqual(rows[0]["status"], "inactive")

    def test_import_hme_address_in_same_source_with_empty_tags_clears_existing_tags(self):
        source_id = self._create_source()
        with self.app.app_context():
            db = web_outlook_app.get_db()
            cursor = db.execute(
                "INSERT INTO tags (name, color) VALUES (?, ?)",
                ("HME", "#0ea5e9"),
            )
            tag_id = int(cursor.lastrowid)
            db.commit()

        first = self.client.post("/api/icloud-hme/accounts/import", json={
            "source_id": source_id,
            "group_id": 1,
            "account_string": "abc@icloud.com",
            "tags": [tag_id],
        }).get_json()
        second = self.client.post("/api/icloud-hme/accounts/import", json={
            "source_id": source_id,
            "group_id": 1,
            "account_string": "abc@icloud.com",
        }).get_json()

        self.assertTrue(first["success"], msg=first)
        self.assertTrue(second["success"], msg=second)
        self.assertEqual(second["updated_count"], 1)
        with self.app.app_context():
            rows = web_outlook_app.get_db().execute(
                """
                SELECT at.tag_id
                FROM account_tags at
                JOIN accounts a ON a.id = at.account_id
                WHERE LOWER(a.email) = 'abc@icloud.com'
                """
            ).fetchall()
        self.assertEqual([int(row["tag_id"]) for row in rows], [tag_id])

        third = self.client.post("/api/icloud-hme/accounts/import", json={
            "source_id": source_id,
            "group_id": 1,
            "account_string": "abc@icloud.com",
            "tags": [],
        }).get_json()
        self.assertTrue(third["success"], msg=third)
        self.assertEqual(third["updated_count"], 1)
        with self.app.app_context():
            rows = web_outlook_app.get_db().execute(
                """
                SELECT at.tag_id
                FROM account_tags at
                JOIN accounts a ON a.id = at.account_id
                WHERE LOWER(a.email) = 'abc@icloud.com'
                """
            ).fetchall()
        self.assertEqual(rows, [])

    def test_import_hme_duplicate_across_sources_returns_conflict(self):
        first_source_id = self._create_source(name="Receiver 1", receiver_email="one@example.com")
        second_source_id = self._create_source(name="Receiver 2", receiver_email="two@example.com")
        self.client.post("/api/icloud-hme/accounts/import", json={
            "source_id": first_source_id,
            "group_id": 1,
            "account_string": "abc@icloud.com",
        })

        response = self.client.post("/api/icloud-hme/accounts/import", json={
            "source_id": second_source_id,
            "group_id": 1,
            "account_string": "abc@icloud.com",
        })

        data = response.get_json()
        self.assertTrue(data["success"], msg=data)
        self.assertEqual(data["imported_count"], 0)
        self.assertEqual(len(data["conflicts"]), 1)
        self.assertEqual(data["conflicts"][0]["existing_source_id"], first_source_id)
        with self.app.app_context():
            count = web_outlook_app.get_db().execute(
                "SELECT COUNT(*) AS count FROM accounts WHERE LOWER(email) = 'abc@icloud.com'"
            ).fetchone()["count"]
        self.assertEqual(count, 1)

    def test_import_hme_missing_source_defaults_to_unique_source(self):
        source_id = self._create_source()
        response = self.client.post("/api/icloud-hme/accounts/import", json={
            "group_id": 1,
            "account_string": "abc@icloud.com",
        })

        data = response.get_json()
        self.assertTrue(data["success"], msg=data)
        with self.app.app_context():
            row = web_outlook_app.get_db().execute(
                "SELECT icloud_hme_source_id FROM accounts WHERE email = ?",
                ("abc@icloud.com",),
            ).fetchone()
        self.assertEqual(row["icloud_hme_source_id"], source_id)

    def test_import_hme_missing_source_requires_choice_when_multiple_sources(self):
        self._create_source(name="Receiver 1", receiver_email="one@example.com")
        self._create_source(name="Receiver 2", receiver_email="two@example.com")
        response = self.client.post("/api/icloud-hme/accounts/import", json={
            "group_id": 1,
            "account_string": "abc@icloud.com",
        })

        data = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(data["success"])
        self.assertIn("source_id", data["error"])


if __name__ == '__main__':
    unittest.main()
