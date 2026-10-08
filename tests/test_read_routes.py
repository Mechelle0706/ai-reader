"""U6 server-rendered read route tests."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.publisher import delete_document, publish


class ReadRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "reader.db"
        self.db_patcher = patch("app.db.DATABASE_PATH", self.db_path)
        self.db_patcher.start()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    def test_read_route_returns_complete_server_side_plain_text(self):
        cleaned = {"title": "阅读页标题", "content_md": "第一段\n\n第二段 **保留格式**"}
        source = {"source_type": "wechat", "source_url": "https://mp.weixin.qq.com/s/test"}
        slug = publish(cleaned, source)

        response = self.client.get(f"/read/{slug}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/plain; charset=utf-8")
        self.assertIn("阅读页标题", response.text)
        self.assertIn("第一段\n\n第二段 **保留格式**", response.text)
        self.assertNotIn("<script", response.text.lower())
        self.assertNotIn("<html", response.text.lower())

    def test_unknown_and_deleted_slugs_return_404(self):
        self.assertEqual(self.client.get("/read/not-found").status_code, 404)
        slug = publish(
            {"title": "待删除", "content_md": "正文"},
            {"source_type": "pdf", "source_url": "sample.pdf"},
        )
        self.assertEqual(self.client.get(f"/read/{slug}").status_code, 200)
        self.assertTrue(delete_document(slug))
        self.assertEqual(self.client.get(f"/read/{slug}").status_code, 404)

    def test_u0_health_endpoint_remains_available(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "ai-reader running")


if __name__ == "__main__":
    unittest.main(verbosity=2)
