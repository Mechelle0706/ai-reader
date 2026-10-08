"""U7 local form, upload validation, and full pipeline tests."""

import tempfile
import unittest
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pymupdf
from fastapi.testclient import TestClient

from app import db
from app.main import UPLOAD_TMP_DIR, app


def _make_test_pdf() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "U7 PDF upload acceptance content")
    payload = document.tobytes()
    document.close()
    return payload


def _read_path(response) -> str:
    match = re.search(r'href="(/read/[A-Za-z0-9]+)"', response.text)
    if match is None:
        raise AssertionError("submission response did not contain a read link")
    return match.group(1)


class WebFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_patcher = patch("app.db.DATABASE_PATH", self.root / "reader.db")
        self.db_patcher.start()
        self.upload_patcher = patch("app.main.UPLOAD_TMP_DIR", self.root / "tmp")
        self.upload_patcher.start()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.upload_patcher.stop()
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    def test_entry_page_contains_url_and_pdf_inputs(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="url"', response.text)
        self.assertIn('name="url" type="text"', response.text)
        self.assertNotIn('name="url" type="url"', response.text)
        self.assertIn('name="pdf_file"', response.text)
        self.assertIn("20 MiB", response.text)
        self.assertIn("打开小红书登录", response.text)
        self.assertIn("我已完成登录", response.text)

    def test_xhs_login_controls_open_and_complete_manual_login(self):
        with patch("app.main.open_xhs_login_page", new_callable=AsyncMock) as open_login:
            opened = self.client.post("/xhs/login")
        open_login.assert_awaited_once()
        self.assertIn("已在 Edge 中打开小红书登录页", opened.text)

        with patch("app.main.complete_xhs_login", new_callable=AsyncMock) as complete_login:
            completed = self.client.post("/xhs/login/complete")
        complete_login.assert_awaited_once()
        self.assertIn("现在可以提交小红书笔记链接", completed.text)

    def test_graceful_app_shutdown_closes_persistent_browser_context(self):
        with patch("app.main.close_browser_context", new_callable=AsyncMock) as close_context:
            with TestClient(app):
                pass
        close_context.assert_awaited_once()

    def test_wechat_runs_fetch_clean_publish_and_read(self):
        url = "https://mp.weixin.qq.com/s/example"
        fetched = {
            "source_type": "wechat",
            "source_url": url,
            "title": "文章流程验收",
            "raw_kind": "text",
            "raw": "公众号正文直接进入清洗与发布。",
        }
        with patch("app.router.fetch_wechat", new_callable=AsyncMock, return_value=fetched) as fetch:
            response = self.client.post("/", data={"url": url})

        fetch.assert_awaited_once_with(url)
        self.assertEqual(response.status_code, 200)
        read_path = _read_path(response)
        read_response = self.client.get(read_path)
        self.assertEqual(read_response.status_code, 200)
        self.assertIn("文章流程验收", read_response.text)
        self.assertIn("公众号正文直接进入清洗与发布。", read_response.text)

    def test_xhs_runs_fetch_clean_publish_and_read(self):
        url = "https://www.xiaohongshu.com/explore/example?xsec_token=address-token&xsec_source=pc_feed"
        fetched = {
            "source_type": "xhs",
            "source_url": url,
            "title": "小红书流程验收",
            "raw_kind": "text",
            "raw": "笔记正文直接进入清洗与发布。",
        }
        with patch("app.router.fetch_xhs", new_callable=AsyncMock, return_value=fetched) as fetch:
            response = self.client.post("/", data={"url": url})

        fetch.assert_awaited_once_with(url)
        self.assertEqual(response.status_code, 200)
        read_response = self.client.get(_read_path(response))
        self.assertEqual(read_response.status_code, 200)
        self.assertIn("小红书流程验收", read_response.text)
        self.assertIn("笔记正文直接进入清洗与发布。", read_response.text)

    def test_xhs_share_text_runs_through_fetch_pipeline(self):
        url = (
            "https://www.xiaohongshu.com/discovery/item/6ab782660000000018004d15"
            "?source=webshare&xhsshare=pc_web&xsec_token=AB_vQgjn6Xh2Dxtu5SLPRecxllVW7fOUQldYjErjNkCdg="
            "&xsec_source=pc_share"
        )
        share_text = (
            "29 【为什么认真学习会让身体变得脆弱容易生病啊 - 面儿同学 | 小红书 - 你的生活兴趣社区】 "
            f"😆 8AmEm3N7DRAPW1P 😆 {url}"
        )
        fetched = {
            "source_type": "xhs",
            "source_url": url,
            "title": "为什么认真学习会让身体变得脆弱容易生病啊 - 面儿同学",
            "raw_kind": "text",
            "raw": "分享文本提取后的正文。",
        }
        with patch("app.router.fetch_xhs", new_callable=AsyncMock, return_value=fetched) as fetch:
            response = self.client.post("/", data={"url": share_text})

        fetch.assert_awaited_once_with(url)
        self.assertEqual(response.status_code, 200)
        read_response = self.client.get(_read_path(response))
        self.assertIn("为什么认真学习会让身体变得脆弱容易生病啊 - 面儿同学", read_response.text)
        self.assertIn("分享文本提取后的正文。", read_response.text)

    def test_pdf_upload_runs_real_fetch_clean_publish_and_removes_temp_file(self):
        response = self.client.post(
            "/",
            files={"pdf_file": ("acceptance.pdf", _make_test_pdf(), "application/pdf")},
        )
        self.assertEqual(response.status_code, 200)
        read_path = _read_path(response)
        read_response = self.client.get(read_path)
        self.assertEqual(read_response.status_code, 200)
        self.assertIn("acceptance", read_response.text)
        self.assertIn("U7 PDF upload acceptance content", read_response.text)
        self.assertEqual(list((self.root / "tmp").glob("*")), [])

    def test_pdf_extension_signature_and_size_are_checked(self):
        wrong_extension = self.client.post(
            "/", files={"pdf_file": ("notes.txt", b"not a PDF", "text/plain")}
        )
        self.assertIn("必须是 PDF", wrong_extension.text)

        invalid_signature = self.client.post(
            "/", files={"pdf_file": ("fake.pdf", b"not a PDF", "application/pdf")}
        )
        self.assertIn("不是有效的 PDF", invalid_signature.text)

        with patch("app.main.MAX_PDF_UPLOAD_BYTES", 16):
            oversized = self.client.post(
                "/", files={"pdf_file": ("large.pdf", b"%PDF-" + b"x" * 30, "application/pdf")}
            )
        self.assertIn("20 MiB", oversized.text)
        self.assertEqual(list((self.root / "tmp").glob("*")), [])

    def test_exactly_one_input_is_required_and_server_paths_are_not_accepted(self):
        empty = self.client.post("/", data={})
        self.assertIn("每次只提交一种输入", empty.text)

        both = self.client.post(
            "/",
            data={"url": "https://mp.weixin.qq.com/s/example"},
            files={"pdf_file": ("acceptance.pdf", _make_test_pdf(), "application/pdf")},
        )
        self.assertIn("每次只提交一种输入", both.text)

        local_path = self.client.post("/", data={"url": r"C:\private\file.pdf"})
        self.assertIn("PDF 请使用文件上传", local_path.text)

    def test_bilibili_keeps_friendly_u8_placeholder(self):
        response = self.client.post(
            "/", data={"url": "https://www.bilibili.com/video/BV1234567890"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("B 站字幕功能暂停", response.text)
        with db._connection() as connection:
            count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
