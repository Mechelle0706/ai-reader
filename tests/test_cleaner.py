"""U5 cleaner tests using small local fixtures only."""

import unittest
from unittest.mock import patch

from app.cleaner import clean
from app.exceptions import FetchError


class CleanerTests(unittest.TestCase):
    def test_html_becomes_markdown_and_preserves_common_structure(self):
        fetched = {
            "source_type": "wechat",
            "source_url": "https://mp.weixin.qq.com/s/example",
            "title": "Fixture article",
            "raw_kind": "html",
            "raw": """
                <!doctype html><html><head><title>Fixture article</title></head>
                <body><nav>Navigation noise</nav><main>
                  <h1>Fixture article</h1>
                  <p>A <strong>bold</strong> paragraph with a
                  <a href="https://example.com">link</a>.</p>
                  <ul><li>First item</li><li>Second item</li></ul>
                  <table><tr><th>Key</th><th>Value</th></tr>
                    <tr><td>Color</td><td>Blue</td></tr></table>
                </main></body></html>
            """,
        }
        result = clean(fetched)
        self.assertEqual(result["title"], "Fixture article")
        self.assertIn("# Fixture article", result["content_md"])
        self.assertIn("**bold**", result["content_md"])
        self.assertIn("[link](https://example.com)", result["content_md"])
        self.assertIn("- First item", result["content_md"])
        self.assertIn("| Key | Value |", result["content_md"])
        self.assertNotIn("Navigation noise", result["content_md"])

    def test_html_dangerous_content_and_attributes_are_removed(self):
        fetched = {
            "source_type": "wechat",
            "source_url": "https://mp.weixin.qq.com/s/example",
            "title": "Safe title",
            "raw_kind": "html",
            "raw": """
                <html><body><article>
                <h1>Safe title</h1><p onclick="alert(1)">Visible text</p>
                <script>run_bad_code()</script><iframe src="https://evil.example"></iframe>
                <a href="javascript:alert(1)">unsafe link</a>
                </article></body></html>
            """,
        }
        result = clean(fetched)
        self.assertIn("Visible text", result["content_md"])
        self.assertNotIn("run_bad_code", result["content_md"])
        self.assertNotIn("iframe", result["content_md"].lower())
        self.assertNotIn("onclick", result["content_md"].lower())
        self.assertNotIn("javascript:", result["content_md"].lower())

    def test_text_normalizes_line_endings_and_preserves_paragraphs(self):
        fetched = {
            "source_type": "pdf",
            "source_url": "sample.pdf",
            "title": " Sample PDF ",
            "raw_kind": "text",
            "raw": "First line  \r\nSecond line\r\n\r\n\r\nThird paragraph\x00\r\n",
        }
        result = clean(fetched)
        self.assertEqual(result["title"], "Sample PDF")
        self.assertEqual(result["content_md"], "First line\nSecond line\n\nThird paragraph")

    def test_subtitle_text_joins_single_segment_lines(self):
        fetched = {
            "source_type": "bilibili",
            "source_url": "https://www.bilibili.com/video/example",
            "title": "Subtitle fixture",
            "raw_kind": "text",
            "raw": "第一句\n第二句\n\n第三句",
        }
        self.assertEqual(clean(fetched)["content_md"], "第一句 第二句\n\n第三句")

    def test_html_extraction_failure_is_a_fetch_error(self):
        fetched = {
            "source_type": "wechat",
            "source_url": "https://mp.weixin.qq.com/s/example",
            "title": "No content",
            "raw_kind": "html",
            "raw": "<html><body><nav>Only navigation</nav></body></html>",
        }
        with patch("app.cleaner.trafilatura.extract", return_value=None):
            with self.assertRaisesRegex(FetchError, "未能.*提取到正文"):
                clean(fetched)

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(FetchError):
            clean(None)
        with self.assertRaisesRegex(FetchError, "raw_kind"):
            clean({"raw": "text", "raw_kind": "binary"})
        with self.assertRaisesRegex(FetchError, "正文为空"):
            clean({"raw": "  ", "raw_kind": "text"})

    def test_clean_result_has_only_the_document_fields(self):
        result = clean({"title": "T", "raw": "Body", "raw_kind": "text"})
        self.assertEqual(set(result), {"title", "content_md"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
