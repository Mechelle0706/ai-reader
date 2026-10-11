"""Xiaohongshu fetcher tests using a mocked browser page."""

import unittest
from unittest.mock import AsyncMock, patch

from app.exceptions import FetchError
from app.fetchers.xhs_fetcher import _extract_title, fetch_xhs


class _FakeLocator:
    _COMMENT_CONTAINERS = {".comments-container", ".comment-list", "[class*='comments-container']"}
    _NOTE_SCROLLER = ".note-scroller"
    _NOTE_IMAGE_SLIDER = ".xhs-slider-container"
    _NOTE_IMAGE_SLIDES = ".xhs-slider-container .swiper-slide"
    _ACTIVE_NOTE_IMAGE = ".xhs-slider-container .swiper-slide-active .note-slider-img > img"
    _NOTE_IMAGES = ".xhs-slider-container .swiper-slide .note-slider-img > img"
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
            if self.selector == self._NOTE_IMAGES:
                return int(self.index < len(self.page.image_urls))
            return int(self.selector in self.page.comment_items[self.index])
        if self.selector in self._COMMENT_CONTAINERS:
            return int(self.page.has_comment_container)
        if self.selector == self._NOTE_SCROLLER:
            return int(self.page.has_note_scroller)
        if self.selector == self._NOTE_IMAGE_SLIDER:
            return int(self.page.has_image_slider)
        if self.selector == self._NOTE_IMAGE_SLIDES:
            return self.page.note_slide_count
        if self.selector == self._ACTIVE_NOTE_IMAGE:
            return int(bool(self.page.image_urls))
        if self.selector == self._NOTE_IMAGES:
            return len(self.page.image_urls)
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
        if self.index is not None and self.selector == self._NOTE_IMAGES and name == "src":
            return self.page.image_urls[self.index]
        return self.page.values.get((self.selector, name))

    async def evaluate(self, _script):
        if self.index is not None and self.selector == self._NOTE_IMAGES:
            return self.page.image_urls[self.index]
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

    async def down(self):
        pass

    async def up(self):
        pass

    async def bounding_box(self):
        if self.selector in self._COMMENT_CONTAINERS and self.page.has_comment_container:
            return {"x": 0, "y": 100, "width": 400, "height": 500}
        if self.selector in {self._NOTE_SCROLLER, self._NOTE_IMAGE_SLIDER} and (
            self.page.has_note_scroller if self.selector == self._NOTE_SCROLLER else self.page.has_image_slider
        ):
            return {"x": 0, "y": 100, "width": 400, "height": 700}
        return None


class _FakeMouse:
    def __init__(self, page):
        self.page = page
        self.move = AsyncMock()
        self.down = AsyncMock()
        self.up = AsyncMock(side_effect=self._up)
        self.wheel = AsyncMock(side_effect=self._wheel)

    async def _up(self):
        if self.page.image_urls_after_swipe:
            self.page.image_urls.extend(self.page.image_urls_after_swipe.pop(0))
        if self.page.challenge_after_swipe:
            self.page.challenge_text = "访问过于频繁，请稍后再试。"

    async def _wheel(self, delta_x, delta_y):
        self.page.scroll_count += 1
        if self.page.scroll_batches:
            self.page.comment_items.extend(self.page.scroll_batches.pop(0))
        if self.page.comment_container_after_scroll:
            self.page.has_comment_container = True
        if self.page.image_urls_after_scroll is not None:
            self.page.image_urls = self.page.image_urls_after_scroll
            self.page.image_urls_after_scroll = None
        if self.page.challenge_after_scroll:
            self.page.challenge_text = "访问过于频繁，请稍后再试。"


class _FakeResponse:
    status = 200


class _FakeImageResponse:
    def __init__(self, status=200, content_length=3, body=b"img"):
        self.status = status
        self.headers = {"content-type": "image/png", "content-length": str(content_length)}
        self.body = AsyncMock(return_value=body)
        self.dispose = AsyncMock()


class _FakeAPIRequest:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.get = AsyncMock(side_effect=self._get)

    async def _get(self, url, **_kwargs):
        return self.responses[url]


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
        image_urls=None,
        image_urls_after_scroll=None,
        image_slide_count=None,
        image_urls_after_swipe=None,
        challenge_after_swipe=False,
    ):
        self.values = values
        self.url = url
        self.comment_items = list(comment_items or [])
        self.scroll_batches = list(scroll_batches or [])
        self.has_comment_container = comment_items is not None or scroll_batches is not None
        self.has_note_scroller = has_note_scroller
        self.image_urls = list(image_urls or [])
        self.image_urls_after_scroll = image_urls_after_scroll
        self.image_urls_after_swipe = list(image_urls_after_swipe or [])
        self.note_slide_count = image_slide_count if image_slide_count is not None else max(
            len(self.image_urls), len(self.image_urls_after_swipe)
        )
        self.has_image_slider = image_urls is not None or image_urls_after_scroll is not None
        self.has_image_slider = self.has_image_slider or image_slide_count is not None or image_urls_after_swipe is not None
        self.comment_container_after_scroll = comment_container_after_scroll
        self.challenge_after_scroll = challenge_after_scroll
        self.challenge_after_swipe = challenge_after_swipe
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
    def __init__(self, page, image_responses=None):
        self.new_page = AsyncMock(return_value=page)
        self.request = _FakeAPIRequest(image_responses)


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
                "raw": (
                    "## 图片文字\n\n未发现可读取的笔记图片。\n\n"
                    "## 正文\n\n第一段\n\n第二段\n\n"
                    "## 高赞评论\n\n未获取到符合条件的评论。"
                ),
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
        self.assertIn("## 正文\n\n正文首行作为标题\n后续正文内容", result["raw"])
        self.assertIn("## 图片文字\n\n未发现可读取的笔记图片。", result["raw"])
        self.assertIn("## 高赞评论", result["raw"])

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
            "## 图片文字\n\n未发现可读取的笔记图片。\n\n"
            "## 正文\n\n笔记正文\n\n## 高赞评论\n\n"
            "- 小明:这条评论内容足够长\n"
            "- 小白:我也觉得这个方法确实很好",
        )

    async def test_note_images_are_deduplicated_capped_and_ocr_keeps_order(self):
        image_urls = [
            "https://sns-webpic-qc.xhscdn.com/note/one",
            "https://sns-webpic-qc.xhscdn.com/note/two",
            "https://sns-webpic-qc.xhscdn.com/note/one",
            *[f"https://sns-webpic-qc.xhscdn.com/note/{index}" for index in range(3, 11)],
        ]
        responses = {url: _FakeImageResponse() for url in set(image_urls)}
        page = _FakePage(
            {
                ".note-content .title": "笔记标题",
                ".note-content .desc": "正文保留",
                "body": "笔记标题\n正文保留",
            },
            image_urls=image_urls,
        )
        context = _FakeContext(page, responses)
        with patch("app.fetchers.xhs_fetcher.get_browser_context", new_callable=AsyncMock, return_value=context), patch(
            "app.fetchers.xhs_fetcher.recognize_image_bytes",
            side_effect=[f"图片文字 {index}" for index in range(1, 10)],
        ) as recognize:
            result = await fetch_xhs("https://www.xiaohongshu.com/explore/note-id")

        image_section = result["raw"].split("## 图片文字\n\n", 1)[1].split("\n\n## 正文", 1)[0]
        self.assertIn("图片 1\n图片文字 1", image_section)
        self.assertIn("图片 9\n图片文字 9", image_section)
        self.assertIn("达到最多 9 张图片的处理上限", image_section)
        self.assertEqual(context.request.get.await_count, 9)
        self.assertEqual(recognize.call_count, 9)
        self.assertTrue(all(call.kwargs["max_redirects"] == 0 for call in context.request.get.await_args_list))

    async def test_each_horizontal_slide_loads_images_before_url_collection(self):
        image_urls = [
            f"https://sns-webpic-qc.xhscdn.com/note/lazy-{index}"
            for index in range(1, 4)
        ]
        batches = [[url] for url in image_urls]
        page = _FakePage(
            {
                ".note-content .title": "笔记标题",
                ".note-content .desc": "正文保留",
                "body": "笔记标题\n正文保留",
            },
            image_urls=[],
            image_slide_count=3,
            image_urls_after_swipe=batches,
        )
        context = _FakeContext(page, {url: _FakeImageResponse() for url in image_urls})
        with patch("app.fetchers.xhs_fetcher.get_browser_context", new_callable=AsyncMock, return_value=context), patch(
            "app.fetchers.xhs_fetcher.recognize_image_bytes",
            side_effect=["第一张文字", "第二张文字", "第三张文字"],
        ) as recognize:
            result = await fetch_xhs("https://www.xiaohongshu.com/explore/note-id")

        image_section = result["raw"].split("## 图片文字\n\n", 1)[1].split("\n\n## 正文", 1)[0]
        self.assertLess(image_section.index("第一张文字"), image_section.index("第二张文字"))
        self.assertLess(image_section.index("第二张文字"), image_section.index("第三张文字"))
        self.assertEqual(page.mouse.up.await_count, 3)
        self.assertEqual(context.request.get.await_count, 3)
        self.assertEqual(recognize.call_count, 3)

    async def test_risk_notice_after_horizontal_slide_stops_before_image_request(self):
        page = _FakePage(
            {
                ".note-content .title": "笔记标题",
                ".note-content .desc": "正文保留",
                "body": "笔记标题\n正文保留",
            },
            image_urls=[],
            image_slide_count=4,
            image_urls_after_swipe=[
                ["https://sns-webpic-qc.xhscdn.com/note/one"],
                ["https://sns-webpic-qc.xhscdn.com/note/two"],
            ],
            challenge_after_swipe=True,
        )
        context = _FakeContext(page)
        with patch("app.fetchers.xhs_fetcher.get_browser_context", new_callable=AsyncMock, return_value=context):
            with self.assertRaisesRegex(FetchError, "访问限制.*停止抓取"):
                await fetch_xhs("https://www.xiaohongshu.com/explore/note-id")

        self.assertEqual(page.mouse.up.await_count, 1)
        context.request.get.assert_not_awaited()

    async def test_non_allowlisted_image_host_is_never_requested(self):
        page = _FakePage(
            {
                ".note-content .title": "笔记标题",
                ".note-content .desc": "正文保留",
                "body": "笔记标题\n正文保留",
            },
            image_urls=["https://images.example.test/note.png"],
        )
        context = _FakeContext(page)
        with patch("app.fetchers.xhs_fetcher.get_browser_context", new_callable=AsyncMock, return_value=context):
            result = await fetch_xhs("https://www.xiaohongshu.com/explore/note-id")

        context.request.get.assert_not_awaited()
        self.assertIn("不在允许的图片域名内", result["raw"])
        self.assertIn("## 正文\n\n正文保留", result["raw"])

    async def test_oversized_image_is_skipped_without_blocking_note(self):
        image_url = "https://sns-webpic-qc.xhscdn.com/note/large"
        response = _FakeImageResponse(content_length=5 * 1024 * 1024 + 1)
        page = _FakePage(
            {
                ".note-content .title": "笔记标题",
                ".note-content .desc": "正文保留",
                "body": "笔记标题\n正文保留",
            },
            image_urls=[image_url],
        )
        context = _FakeContext(page, {image_url: response})
        with patch("app.fetchers.xhs_fetcher.get_browser_context", new_callable=AsyncMock, return_value=context), patch(
            "app.fetchers.xhs_fetcher.recognize_image_bytes"
        ) as recognize:
            result = await fetch_xhs("https://www.xiaohongshu.com/explore/note-id")

        response.body.assert_not_awaited()
        recognize.assert_not_called()
        self.assertIn("超过 5 MiB 上限", result["raw"])
        self.assertIn("## 正文\n\n正文保留", result["raw"])

    async def test_ocr_failure_does_not_block_body_or_comments(self):
        image_url = "https://sns-webpic-qc.xhscdn.com/note/ocr-fails"
        comment = {
            ".comment-inner-container .author-wrapper .author .name": "评论者",
            ".comment-inner-container .note-text": "这条评论内容足够长",
        }
        page = _FakePage(
            {
                ".note-content .title": "笔记标题",
                ".note-content .desc": "正文保留",
                "body": "笔记标题\n正文保留",
            },
            comment_items=[comment],
            image_urls=[image_url],
        )
        context = _FakeContext(page, {image_url: _FakeImageResponse()})
        with patch("app.fetchers.xhs_fetcher.get_browser_context", new_callable=AsyncMock, return_value=context), patch(
            "app.fetchers.xhs_fetcher.recognize_image_bytes", side_effect=RuntimeError("OCR unavailable")
        ):
            result = await fetch_xhs("https://www.xiaohongshu.com/explore/note-id")

        self.assertIn("本机 OCR 暂不可用", result["raw"])
        self.assertIn("## 正文\n\n正文保留", result["raw"])
        self.assertIn("- 评论者:这条评论内容足够长", result["raw"])

    async def test_image_rate_limit_stops_fetch(self):
        image_url = "https://sns-webpic-qc.xhscdn.com/note/limited"
        page = _FakePage(
            {
                ".note-content .title": "笔记标题",
                ".note-content .desc": "正文",
                "body": "笔记标题\n正文",
            },
            image_urls=[image_url],
        )
        context = _FakeContext(page, {image_url: _FakeImageResponse(status=429)})
        with patch("app.fetchers.xhs_fetcher.get_browser_context", new_callable=AsyncMock, return_value=context):
            with self.assertRaisesRegex(FetchError, "图片请求触发登录限制或访问风控"):
                await fetch_xhs("https://www.xiaohongshu.com/explore/note-id")

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

        comments_section = result["raw"].split("## 高赞评论\n\n", 1)[1]
        comment_lines = [line for line in comments_section.splitlines() if line.startswith("- ")]
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