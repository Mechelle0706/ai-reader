"""Browser login-flow tests using a mocked Playwright context."""

import unittest
from unittest.mock import AsyncMock, patch

from app import browser
from app.exceptions import FetchError


class _FakePage:
    def __init__(self):
        self.url = ""
        self.closed = False
        self.goto = AsyncMock(return_value=None)
        self.bring_to_front = AsyncMock()
        self.close = AsyncMock()

    def set_default_navigation_timeout(self, _timeout):
        pass

    def is_closed(self):
        return self.closed


class _FakeContext:
    def __init__(self, page):
        self.page = page
        self.new_page = AsyncMock(return_value=page)


class BrowserLoginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await browser.complete_xhs_login()

    async def test_login_page_gates_fetch_until_user_confirms_completion(self):
        page = _FakePage()
        context = _FakeContext(page)
        with patch("app.browser.get_browser_context", new_callable=AsyncMock, return_value=context):
            await browser.open_xhs_login_page()

        page.goto.assert_awaited_once_with(
            "https://www.xiaohongshu.com/login", wait_until="domcontentloaded"
        )
        with self.assertRaisesRegex(FetchError, "完成小红书登录"):
            browser.require_xhs_login_complete()

        await browser.complete_xhs_login()
        browser.require_xhs_login_complete()

    async def test_reopening_pending_login_brings_existing_page_forward(self):
        page = _FakePage()
        context = _FakeContext(page)
        with patch("app.browser.get_browser_context", new_callable=AsyncMock, return_value=context):
            await browser.open_xhs_login_page()
            await browser.open_xhs_login_page()

        context.new_page.assert_awaited_once()
        page.bring_to_front.assert_awaited_once()

    async def test_login_startup_failure_is_a_fetch_error(self):
        with patch(
            "app.browser.get_browser_context",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Edge unavailable"),
        ):
            with self.assertRaisesRegex(FetchError, "无法打开小红书登录页"):
                await browser.open_xhs_login_page()


if __name__ == "__main__":
    unittest.main(verbosity=2)