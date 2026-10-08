"""U4 route dispatch tests; fetchers are mocked to avoid network access."""

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.exceptions import FetchError
from app.router import route


class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_pdf_path_dispatches_to_pdf_fetcher(self):
        expected = {
            "source_type": "pdf",
            "source_url": "sample.pdf",
            "title": "Sample",
            "raw": "body",
            "raw_kind": "text",
        }
        with patch("app.router.fetch_pdf", return_value=expected) as fetch_pdf:
            result = await route(Path("sample.pdf"))
        fetch_pdf.assert_called_once_with(str(Path("sample.pdf")))
        self.assertEqual(result, expected)

    async def test_pdf_string_path_dispatches_to_pdf_fetcher(self):
        expected = {"source_type": "pdf", "raw_kind": "text"}
        with patch("app.router.fetch_pdf", return_value=expected) as fetch_pdf:
            result = await route(r"tests\sample.pdf")
        fetch_pdf.assert_called_once_with(str(Path(r"tests\sample.pdf")))
        self.assertEqual(result, expected)

    async def test_wechat_dispatches_to_async_fetcher(self):
        url = "https://mp.weixin.qq.com/s/example"
        expected = {"source_type": "wechat", "raw_kind": "html"}
        with patch("app.router.fetch_wechat", new_callable=AsyncMock, return_value=expected) as fetch_wechat:
            result = await route(url)
        fetch_wechat.assert_awaited_once_with(url)
        self.assertEqual(result, expected)

    async def test_bilibili_is_pending_without_calling_fetcher(self):
        with patch("app.router.fetch_pdf") as fetch_pdf, patch(
            "app.router.fetch_wechat", new_callable=AsyncMock
        ) as fetch_wechat:
            with self.assertRaisesRegex(FetchError, "B 站字幕功能暂停.*待 U8"):
                await route("https://www.bilibili.com/video/BV1234567890")
        fetch_pdf.assert_not_called()
        fetch_wechat.assert_not_awaited()

    async def test_xhs_is_pending_without_calling_fetcher(self):
        with patch("app.router.fetch_pdf") as fetch_pdf, patch(
            "app.router.fetch_wechat", new_callable=AsyncMock
        ) as fetch_wechat:
            with self.assertRaisesRegex(FetchError, "小红书抓取待 U8"):
                await route("https://www.xiaohongshu.com/explore/example")
        fetch_pdf.assert_not_called()
        fetch_wechat.assert_not_awaited()

    async def test_unknown_input_is_rejected(self):
        with self.assertRaisesRegex(FetchError, "无法识别"):
            await route("not a supported input")

    async def test_unknown_public_domain_is_rejected(self):
        with self.assertRaisesRegex(FetchError, "暂不支持"):
            await route("https://example.com/article")

    async def test_dangerous_schemes_are_rejected(self):
        for value in ("file:///C:/private.pdf", "ftp://mp.weixin.qq.com/s/id", "javascript:alert(1)"):
            with self.subTest(value=value), self.assertRaises(FetchError):
                await route(value)

    async def test_loopback_and_private_network_urls_are_rejected(self):
        for value in (
            "http://127.0.0.1:8000/read/test",
            "http://localhost/private.pdf",
            "http://192.168.1.20/article",
            "http://10.0.0.2/article",
        ):
            with self.subTest(value=value), self.assertRaises(FetchError):
                await route(value)

    async def test_spoofed_host_and_credentials_are_rejected(self):
        for value in (
            "https://mp.weixin.qq.com.evil.example/s/id",
            "https://mp.weixin.qq.com@evil.example/s/id",
        ):
            with self.subTest(value=value), self.assertRaises(FetchError):
                await route(value)

    async def test_unsupported_local_file_type_is_rejected(self):
        with self.assertRaisesRegex(FetchError, "仅支持 PDF"):
            await route(Path("notes.txt"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
