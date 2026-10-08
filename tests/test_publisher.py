"""U6 publisher and SQLite persistence tests."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from app.exceptions import FetchError
from app.publisher import delete_document, publish


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "reader.db"
        self.db_patcher = patch("app.db.DATABASE_PATH", self.db_path)
        self.db_patcher.start()
        db.init_db()

    def tearDown(self):
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    def test_publish_stores_complete_document_with_random_slug(self):
        cleaned = {"title": "测试标题", "content_md": "完整正文\n第二段"}
        source = {"source_type": "pdf", "source_url": "sample.pdf"}

        first_slug = publish(cleaned, source)
        second_slug = publish(cleaned, source)

        self.assertRegex(first_slug, r"^[A-Za-z0-9]{8}$")
        self.assertNotEqual(first_slug, second_slug)
        stored = db.get_document(first_slug)
        self.assertEqual(stored["source_type"], "pdf")
        self.assertEqual(stored["source_url"], "sample.pdf")
        self.assertEqual(stored["title"], "测试标题")
        self.assertEqual(stored["content_md"], "完整正文\n第二段")
        self.assertTrue(stored["created_at"].endswith("+00:00"))

    def test_delete_returns_true_once_then_false(self):
        slug = publish(
            {"title": "删除测试", "content_md": "正文"},
            {"source_type": "pdf", "source_url": "test.pdf"},
        )
        self.assertTrue(delete_document(slug))
        self.assertIsNone(db.get_document(slug))
        self.assertFalse(delete_document(slug))

    def test_empty_or_invalid_documents_are_rejected(self):
        with self.assertRaisesRegex(FetchError, "正文为空"):
            publish({"title": "标题", "content_md": "  "}, {"source_type": "pdf", "source_url": "a.pdf"})
        with self.assertRaises(FetchError):
            publish({"title": "标题", "content_md": "正文"}, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
