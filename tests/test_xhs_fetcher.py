"""Xiaohongshu fetcher tests using a mocked browser page."""

import unittest
from unittest.mock import AsyncMock, patch

from app.exceptions import FetchError
from app.fetchers.xhs_fetcher import _extract_title, fetch_xhs


class _FakeLocator:
    _COMMENT_CONTAINERS = {".comments-container", ".comment-list", "[class*='comments-container']"}
    _NOTE_SCROLLER = ".note-scroller"
    _COMMENT_ITEMS = {
        ".comments-container .parent-comment > .comment-item",
    }

    def __init__(self, page, selector, index=None):
        self.page = page
        self.selector = selector
        self.index = index
        self.first = self

    async def count(self):
        if self.index is not None:
            return int(self.selector in self.page.comment_items[self.index])
        if self.selector in self._COMMENT_CONTAINERS:
            return int(self.page.has_comment_container)
        if self.selector == self._NOTE_SCROLLER:
            return int(self.page.has_note_scroller)
        if self.selector in self._COMMENT_ITEMS:
            return len(self.page.comment_items)
        return int(self.selector in self.page.values or self.selector == "body")

    async def is_visible(self):
        return bool(await self.count())

    async def inner_text(self):
        if self.index is not None:
            return self.page.comment_items[self.index].get(self.selector, "")
        if self.selector == "body":
            return self.page.challenge_text or self.page.values.get("body", "")
        return self.page.values.get(self.selector, "")

    async def get_attribute(self, name):
        return self.page.values.get((self.selector, name))

    async def evaluate(self, _script):
        return not self.page.comment_items[self.index].get("nested", False)

    async def wait_for(self, state, timeout):
        if not await self.count():
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError

            raise PlaywrightTimeoutError("not found")

    def locator(self, selector):
        return _FakeLocator(self.page, selector, self.index)

    def nth(self, index):
        return _FakeLocator(self.page, self.selector, index)

    async def scroll_into_view_if_needed(self):
        pass

    async def bounding_box(self):
        if self.selector in self._COMMENT_CONTAINERS and self.page.has_comment_container:
            return {"x": 0, "y": 100, "width": 400, "height": 500}
        if self.selector == self._NOTE_SCROLLER and self.page.has_note_scroller:
            return {"x": 0, "y": 100, "width": 400, "height": 700}
        return None


class _FakeMouse:
    def __init__(self, page):
        self.page = page
        self.move = AsyncMock()
        self.wheel = AsyncMock(side_effect=self._wheel)

    async def _wheel(self, delta_x, delta_y):
        self.page.scroll_count += 1
        if self.page.scroll_batches:
            self.page.comment_items.extend(self.page.scroll_batches.pop(0))
        if self.page.comment_container_after_scroll:
            self.page.has_comment_container = True
        if self.page.challenge_after_scroll:
            self.page.challenge_text = "访问过于频繁，请稍后再试。"


class _FakeResponse:
    status = 200


class _FakePage:
    def __init__(
        self,
        values,
        url="https://www.xiaohongshu.com/explore/note-id",
        comment_items=None,
        scroll_batches=None,
        challenge_after_scroll=False,
        has_note_scroller=False,
        comment_container_after_scroll=False,
    ):
        self.values = values
        self.url = url
        self.comment_items = list(comment_items or [])
        self.scroll_batches = list(scroll_batches or [])
        self.has_comment_container = comment_items is not None or scroll_batches is not None
        self.has_note_scroller = has_note_scroller
        self.comment_container_after_scroll = comment_container_after_scroll
        self.challenge_after_scroll = challenge_after_scroll
        self.challenge_text = ""
        self.scroll_count = 0
        self.mouse = _FakeMouse(self)
        self.goto = AsyncMock(return_value=_FakeResponse())
        self.close = AsyncMock()
        self.wait_for_timeout = AsyncMock()

    def set_default_navigation_timeout(self, _timeout):
        pass

    def set_default_timeout(self, _timeout):
        pass

    def locator(self, selector):
        return _FakeLocator(self, selector)

    async def title(self):
        return self.values.get("page-title", "")


class _FakeContext:
    def __init__(self, page):
        self.new_page = AsyncMock(return_value=page)


class XhsFetcherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.browser import complete_xhs_login

        await complete_xhs_login()

    async def test_returns_cleaner_contract_with_note_title_and_body(self):
        url = "https://www.xiaohongshu.com/explore/note-id"
        page = _FakePage(
            {
                ".note-content .title": "笔记标题",
                ".note-content .desc": "第一段\n\n第二段",
                "body": "笔记标题\n第一段\n第二段",
            }
        )
        with patch(
            "app.fetchers.xhs_fetcher.get_browser_context",
            new_callable=AsyncMock,
            return_value=_FakeContext(page),
        ):
            result = await fetch_xhs(url)

        self.assertEqual(
            result,
            {
                "source_type": "xhs",
                "source_url": url,
                "title": "笔记标题",
                "raw": "第一段\n\n第二段\n\n## 评论\n\n未获取到符合条件的评论。",
                "raw_kind": "text",
            },
        )
        page.close.assert_awaited_once()

    async def test_missing_separate_title_falls_back_to_first_body_line(self):
        page = _FakePage(
            {
                ".note-content .desc": "正文首行作为标题\n后续正文内容",
                "body": "正文首行作为标题\n后续正文内容",
                "page-title": "小红书 - 你的生活兴趣社区",
            }
        )
        with patch(
            "app.fetchers.xhs_fetcher.get_browser_context",
            new_callable=AsyncMock,
            return_value=_FakeContext(page),
        ):
            result = await fetch_xhs("https://www.xiaohongshu.com/explore/note-id")

        self.assertEqual(result["title"], "正文首行作为标题")
        self.assertIn("## 评论", result["raw"])

    async def test_title_falls_back_to_default_when_no_title_or_body_line_exists(self):
        page = _FakePage(
            {
                "page-title": "小红书 - 你的生活兴趣社区",
            }
        )
        self.assertEqual(await _extract_title(page, " \n\n "), "小红书笔记")

    async def test_comments_keep_nicknames_and_filter_mentions_short_and_spam(self):
        url = "https://www.xiaohongshu.com/explore/note-id"
        comments = [
            {".comment-inner-container .author-wrapper .author .name": "小明", ".comment-inner-container .note-text": "这条评论内容足够长"},
            {".comment-inner-container .author-wrapper .author .name": "小红", ".comment-inner-container .note-text": "@好友甲 @好友乙"},
            {".comment-inner-container .author-wrapper .author .name": "小蓝", ".comment-inner-container .note-text": "不错"},
            {".comment-inner-container .author-wrapper .author .name": "小绿", ".comment-inner-container .note-text": "哈哈哈哈"},
            {
                ".comment-inner-container .author-wrapper .author .name": "楼中楼用户",
                ".comment-inner-container .note-text": "这是一条嵌套楼中楼评论",
                "nested": True,
            },
            {".comment-inner-container .author-wrapper .author .name": "小白", ".comment-inner-container .note-text": "我也觉得这个方法确实很好"},
        ]
        page = _FakePage(
            {
                ".note-content .title": "笔记标题",
                ".note-content .desc": "笔记正文",
                "body": "笔记标题\n笔记正文",
            },
            comment_items=comments,
        )
        with patch(
            "app.fetchers.xhs_fetcher.get_browser_context",
            new_callable=AsyncMock,
            return_value=_FakeContext(page),
        ):
            result = await fetch_xhs(url)

        self.assertEqual(
            result["raw"],
            "笔记正文\n\n## 评论\n\n"
            "- 小明:这条评论内容足够长\n"
            "- 小白:我也觉得这个方法确实很好",
        )

    async def test_comment_inspection_is_capped_at_first_fifty_nodes(self):
        def _comments(start, end):
            return [
                {
                    ".comment-inner-container .author-wrapper .author .name": f"用户{index}",
                    ".comment-inner-container .note-text": f"这是第{index}条有效评论内容",
                }
                for index in range(start, end)
            ]

        page = _FakePage(
            {
                ".note-content .title": "笔记标题",
                ".note-content .desc": "笔记正文",
                "body": "笔记标题\n笔记正文",
            },
            comment_items=_comments(0, 5),
            scroll_batches=[_comments(start, start + 10) for start in range(5, 65, 10)],
        )
        with patch(
            "app.fetchers.xhs_fetcher.get_browser_context",
            new_callable=AsyncMock,
            return_value=_FakeContext(page),
        ):
            result = await fetch_xhs("https://www.xiaohongshu.com/explore/note-id")

        comment_lines = [line for line in result["raw"].splitlines() if line.startswith("- ")]
        self.assertEqual(len(comment_lines), 50)
        self.assertIn("- 用户49:这是第49条有效评论内容", comment_lines)
        self.assertEqual(page.mouse.wheel.await_count, 5)
        self.assertLessEqual(page.mouse.wheel.await_count, 6)
        self.assertEqual(page.wait_for_timeout.await_count, page.mouse.wheel.await_count + 1)

    async def test_comments_mount_after_scrolling_note_scroller(self):
        comment = {
            ".comment-inner-container .author-wrapper .author .name": "小明",
            ".comment-inner-container .note-text": "评论区加载后出现的有效文字",
        }
        page = _FakePage(
            {
                ".note-content .title": "笔记标题",
                ".note-content .desc": "笔记正文",
                "body": "笔记标题\n笔记正文",
            },
            comment_items=[],
            scroll_batches=[[comment]],
            has_note_scroller=True,
            comment_container_after_scroll=True,
        )
        with patch(
            "app.fetchers.xhs_fetcher.get_browser_context",
            new_callable=AsyncMock,
            return_value=_FakeContext(page),
        ):
            result = await fetch_xhs("https://www.xiaohongshu.com/explore/note-id")

        self.assertIn("- 小明:评论区加载后出现的有效文字", result["raw"])
        self.assertTrue(page.has_comment_container)
        self.assertLessEqual(page.mouse.wheel.await_count, 6)

    async def test_challenge_after_comment_scroll_stops_fetch(self):
        page = _FakePage(
            {
                ".note-content .title": "笔记标题",
                ".note-content .desc": "笔记正文",
                "body": "笔记标题\n笔记正文",
            },
            comment_items=[
                {".comment-inner-container .author-wrapper .author .name": "小明", ".comment-inner-container .note-text": "这条评论内容足够长"}
            ],
            scroll_batches=[
                [{".comment-inner-container .author-wrapper .author .name": "小红", ".comment-inner-container .note-text": "另一条评论内容也够长"}]
            ],
            challenge_after_scroll=True,
        )
        with patch(
            "app.fetchers.xhs_fetcher.get_browser_context",
            new_callable=AsyncMock,
            return_value=_FakeContext(page),
        ):
            with self.assertRaisesRegex(FetchError, "访问限制.*停止抓取"):
                await fetch_xhs("https://www.xiaohongshu.com/explore/note-id")

        page.mouse.wheel.assert_awaited_once_with(0, 400)

    async def test_challenge_page_stops_without_returning_content(self):
        page = _FakePage({"body": "访问过于频繁，请稍后再试。"})
        with patch(
            "app.fetchers.xhs_fetcher.get_browser_context",
            new_callable=AsyncMock,
            return_value=_FakeContext(page),
        ):
            with self.assertRaisesRegex(FetchError, "访问限制.*停止抓取"):
                await fetch_xhs("https://www.xiaohongshu.com/explore/note-id")

    async def test_missing_note_body_is_a_fetch_error(self):
        page = _FakePage(
            {
                ".note-content .title": "笔记标题",
                "body": "笔记标题",
            }
        )
        with patch(
            "app.fetchers.xhs_fetcher.get_browser_context",
            new_callable=AsyncMock,
            return_value=_FakeContext(page),
        ):
            with self.assertRaisesRegex(FetchError, "未能提取小红书笔记正文"):
                await fetch_xhs("https://www.xiaohongshu.com/explore/note-id")

    async def test_rejects_non_xhs_urls(self):
        for url in (
            "https://example.com/note",
            "http://www.xiaohongshu.com/explore/note-id",
            "https://xiaohongshu.com.evil.example/note",
        ):
            with self.subTest(url=url), self.assertRaises(FetchError):
                await fetch_xhs(url)


if __name__ == "__main__":
    unittest.main(verbosity=2)