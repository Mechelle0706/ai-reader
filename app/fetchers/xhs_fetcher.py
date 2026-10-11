"""Fetch an accessible Xiaohongshu note through the user's Edge profile."""

import asyncio
import re
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.browser import browser_operation_lock, get_browser_context, require_xhs_login_complete
from app.exceptions import FetchError
from app.fetchers.xhs_ocr import recognize_image_bytes


_NOTE_TITLE_SELECTORS = (
    ".note-content .title",
    ".note-scroller .title",
    ".note-detail-mask .title",
    "#detail-title",
    ".note-title",
    "h1",
)
_NOTE_BODY_SELECTORS = (
    ".note-content .desc",
    ".note-scroller .desc",
    ".note-detail-mask .desc",
    "#detail-desc",
    ".note-desc",
    "[class*='note-content'] [class*='desc']",
)
_COMMENT_CONTAINER_SELECTORS = (
    ".comments-container",
    ".comment-list",
    "[class*='comments-container']",
)
_COMMENT_ITEM_SELECTORS = (
    ".comments-container .parent-comment > .comment-item",
)
_COMMENT_AUTHOR_SELECTORS = (
    ".comment-inner-container .author-wrapper .author .name",
    ".comment-inner-container .author-wrapper .name",
)
_COMMENT_TEXT_SELECTORS = (
    ".comment-inner-container .note-text",
    ".comment-inner-container .content",
)
_NOTE_IMAGE_SELECTOR = ".xhs-slider-container .swiper-slide .note-slider-img > img"
_NOTE_IMAGE_SLIDER_SELECTOR = ".xhs-slider-container"
_NOTE_IMAGE_SLIDE_SELECTOR = ".xhs-slider-container .swiper-slide"
_ACTIVE_NOTE_IMAGE_SELECTOR = ".xhs-slider-container .swiper-slide-active .note-slider-img > img"
_ALLOWED_IMAGE_HOST = "sns-webpic-qc.xhscdn.com"
_MAX_NOTE_IMAGES = 9
_MAX_IMAGE_DOM_CANDIDATES = 36
_MAX_IMAGE_SLIDE_ADVANCES = 18
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_MAX_TOTAL_IMAGE_BYTES = 20 * 1024 * 1024
_IMAGE_REQUEST_TIMEOUT_MS = 15000
_IMAGE_LOAD_WAIT_MS = 700
_ACTIVE_IMAGE_WAIT_TIMEOUT_MS = 1200
_MAX_COMMENT_ITEMS = 50
_MAX_COMMENT_SCROLLS = 6
_COMMENT_SCROLL_WAIT_MS = 1000
_MIN_COMMENT_CHARACTERS = 5
_CHALLENGE_SELECTORS = (
    "[class*='captcha']",
    "[class*='verify-container']",
    "[class*='verify-modal']",
    "[class*='login-modal']",
    "#login-container",
    ".login-container",
)
_CHALLENGE_TEXT = (
    "访问过于频繁",
    "操作过于频繁",
    "操作频繁",
    "当前环境异常",
    "账号存在风险",
    "请完成安全验证",
    "请完成验证",
    "请输入验证码",
    "人机验证",
    "登录后查看",
    "登录后继续",
    "请先登录",
)
_GENERIC_TITLES = ("小红书", "你的生活指南", "发现美好")
_ALLOWED_HOSTS = ("xiaohongshu.com", "xhslink.com")


def _validate_xhs_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise FetchError("小红书链接格式无效。") from exc

    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not any(hostname == domain or hostname.endswith(f".{domain}") for domain in _ALLOWED_HOSTS)
    ):
        raise FetchError("仅支持 HTTPS 小红书笔记链接。")


async def _raise_if_challenged(page) -> None:
    for selector in _CHALLENGE_SELECTORS:
        locator = page.locator(selector).first
        if await locator.count() and await locator.is_visible():
            raise FetchError("小红书页面要求登录或验证，或触发访问限制，已停止抓取。")

    body_text = (await page.locator("body").inner_text()).lower()
    if any(marker.lower() in body_text for marker in _CHALLENGE_TEXT):
        raise FetchError("小红书页面要求登录或验证，或触发访问限制，已停止抓取。")


async def _find_visible_text(page, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            continue
        text = (await locator.inner_text()).strip()
        if text:
            return text
    return ""


def _clean_title(title: str) -> str:
    title = title.strip()
    for suffix in (" - 小红书", " | 小红书", "_小红书"):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    if not title or any(generic in title for generic in _GENERIC_TITLES):
        return ""
    return title


async def _extract_title(page, body_text: str) -> str:
    title = _clean_title(await _find_visible_text(page, _NOTE_TITLE_SELECTORS))
    if title:
        return title

    og_title = page.locator('meta[property="og:title"]').first
    if await og_title.count():
        title = _clean_title(await og_title.get_attribute("content") or "")
        if title:
            return title

    title = _clean_title(await page.title())
    if title:
        return title

    first_body_line = next((line.strip() for line in body_text.splitlines() if line.strip()), "")
    return first_body_line[:120] or "小红书笔记"


def _is_allowed_image_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False

    return (
        parsed.scheme.lower() == "https"
        and parsed.hostname == _ALLOWED_IMAGE_HOST
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
    )


async def _read_note_image_urls(page) -> tuple[list[str], bool]:
    slider = page.locator(_NOTE_IMAGE_SLIDER_SELECTOR).first
    if await slider.count() == 0:
        return [], False

    async def _collect_urls() -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        images = page.locator(_NOTE_IMAGE_SELECTOR)
        candidate_count = min(await images.count(), _MAX_IMAGE_DOM_CANDIDATES)
        for index in range(candidate_count):
            image = images.nth(index)
            image_url = (await image.evaluate(
                "element => element.currentSrc || element.src || element.getAttribute('data-src') || element.getAttribute('data-lazy-src') || ''"
            )).strip()
            if image_url and image_url not in seen:
                seen.add(image_url)
                urls.append(image_url)
                if len(urls) > _MAX_NOTE_IMAGES:
                    break
        return urls

    await slider.scroll_into_view_if_needed()
    await page.wait_for_timeout(300)
    await _raise_if_challenged(page)

    urls = await _collect_urls()
    slides = page.locator(_NOTE_IMAGE_SLIDE_SELECTOR)
    slide_count = min(await slides.count(), _MAX_IMAGE_DOM_CANDIDATES)
    advances = 0
    while len(urls) < _MAX_NOTE_IMAGES and advances < min(slide_count, _MAX_IMAGE_SLIDE_ADVANCES):
        await _raise_if_challenged(page)
        box = await slider.bounding_box()
        if box is None:
            break

        center_y = box["y"] + box["height"] / 2
        start_x = box["x"] + box["width"] * 0.78
        end_x = box["x"] + box["width"] * 0.22
        await page.mouse.move(start_x, center_y)
        await page.mouse.down()
        await page.mouse.move(end_x, center_y, steps=5)
        await page.mouse.up()
        advances += 1

        await page.wait_for_timeout(_IMAGE_LOAD_WAIT_MS)
        await _raise_if_challenged(page)
        active_image = page.locator(_ACTIVE_NOTE_IMAGE_SELECTOR).first
        try:
            await active_image.wait_for(state="attached", timeout=_ACTIVE_IMAGE_WAIT_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            pass
        await _raise_if_challenged(page)
        urls = await _collect_urls()

    truncated = len(urls) > _MAX_NOTE_IMAGES or (
        len(urls) >= _MAX_NOTE_IMAGES
        and advances < min(slide_count, _MAX_IMAGE_SLIDE_ADVANCES)
    )
    return urls[:_MAX_NOTE_IMAGES], truncated


async def _download_note_image(context, image_url: str, remaining_bytes: int) -> tuple[bytes | None, str | None, bool]:
    if not _is_allowed_image_url(image_url):
        return None, "图片地址不在允许的图片域名内，已跳过。", False

    response = None
    try:
        response = await context.request.get(
            image_url,
            timeout=_IMAGE_REQUEST_TIMEOUT_MS,
            max_redirects=0,
        )
        if response.status in {401, 403, 412, 429}:
            raise FetchError("小红书图片请求触发登录限制或访问风控，已停止抓取。")
        if 300 <= response.status < 400:
            return None, "图片请求发生跳转，已跳过。", False
        if response.status != 200:
            return None, f"图片请求返回 HTTP {response.status}，已跳过。", False

        headers = response.headers
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if not content_type.startswith("image/"):
            return None, "响应不是图片格式，已跳过。", False

        content_length = headers.get("content-length")
        if content_length is None or not content_length.isdigit():
            return None, "图片大小无法核验，已跳过。", False
        declared_size = int(content_length)
        if declared_size > _MAX_IMAGE_BYTES:
            return None, "图片超过 5 MiB 上限，已跳过。", False
        if declared_size > remaining_bytes:
            return None, "已达到 20 MiB 图片总量上限，剩余图片未处理。", True

        image_bytes = await response.body()
        if len(image_bytes) > _MAX_IMAGE_BYTES:
            return None, "图片超过 5 MiB 上限，已跳过。", False
        if len(image_bytes) > remaining_bytes:
            return None, "已达到 20 MiB 图片总量上限，剩余图片未处理。", True
        return image_bytes, None, False
    except FetchError:
        raise
    except Exception:
        return None, "图片下载失败，已跳过。", False
    finally:
        if response is not None:
            try:
                await response.dispose()
            except Exception:
                pass


async def _extract_image_text(context, page) -> str:
    image_urls, truncated = await _read_note_image_urls(page)
    if not image_urls:
        return "未发现可读取的笔记图片。"

    sections: list[str] = []
    total_bytes = 0
    for index, image_url in enumerate(image_urls, start=1):
        await _raise_if_challenged(page)
        image_bytes, download_message, stop_for_size = await _download_note_image(
            context,
            image_url,
            _MAX_TOTAL_IMAGE_BYTES - total_bytes,
        )
        await _raise_if_challenged(page)
        if stop_for_size:
            sections.append(download_message or "已达到图片总量上限，剩余图片未处理。")
            break
        if image_bytes is None:
            sections.append(f"图片 {index}：{download_message or '下载失败，已跳过。'}")
            await _raise_if_challenged(page)
            continue

        total_bytes += len(image_bytes)
        try:
            text = await asyncio.to_thread(recognize_image_bytes, image_bytes)
        except Exception:
            text = ""
            sections.append(f"图片 {index}：本机 OCR 暂不可用，已跳过。")
        else:
            sections.append(f"图片 {index}\n{text}" if text else f"图片 {index}：未识别到文字。")
        await _raise_if_challenged(page)

    if truncated:
        sections.append("已达到最多 9 张图片的处理上限。")
    return "\n\n".join(sections)


async def _find_first_text(locator, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        text_locator = locator.locator(selector).first
        if await text_locator.count():
            text = " ".join((await text_locator.inner_text()).split())
            if text:
                return text
    return ""


async def _is_top_level_comment(locator) -> bool:
    return await locator.evaluate(
        """element => {
            for (let parent = element.parentElement; parent; parent = parent.parentElement) {
                if (parent.matches('.comment-item')) return false;
            }
            return true;
        }"""
    )


def _is_pure_mention(text: str) -> bool:
    parts = text.split()
    return bool(parts) and all(part.startswith("@") and len(part) > 1 for part in parts)


def _is_repeated_character_spam(text: str) -> bool:
    meaningful = [character for character in text if character.isalnum()]
    return len(meaningful) >= 4 and len(set(meaningful)) == 1


def _is_usable_comment(text: str) -> bool:
    character_count = sum(character.isalnum() for character in text)
    return (
        not _is_pure_mention(text)
        and character_count >= _MIN_COMMENT_CHARACTERS
        and not _is_repeated_character_spam(text)
    )


async def _find_comment_container(page, *, wait: bool = False):
    for selector in _COMMENT_CONTAINER_SELECTORS:
        container = page.locator(selector).first
        if await container.count():
            return container
    if wait:
        container = page.locator(_COMMENT_CONTAINER_SELECTORS[0]).first
        try:
            await container.wait_for(state="visible", timeout=4000)
        except PlaywrightTimeoutError:
            pass
        if await container.count():
            return container
    return None


async def _find_comment_items(page):
    last_items = None
    for selector in _COMMENT_ITEM_SELECTORS:
        items = page.locator(selector)
        last_items = items
        if await items.count():
            return items
    return last_items


async def _extract_comments(page) -> list[str]:
    """Read at most 50 default-ordered top-level comments with gentle scrolling."""
    container = await _find_comment_container(page, wait=True)
    note_scroller = page.locator(".note-scroller").first
    has_note_scroller = await note_scroller.count() > 0
    if container is None and not has_note_scroller:
        return []

    scroll_target = container or note_scroller
    await scroll_target.scroll_into_view_if_needed()
    await page.wait_for_timeout(_COMMENT_SCROLL_WAIT_MS)
    await _raise_if_challenged(page)
    items = await _find_comment_items(page)
    if items is None:
        return []

    output: list[str] = []
    processed_count = 0
    consecutive_no_growth = 0

    for scroll_number in range(_MAX_COMMENT_SCROLLS + 1):
        await _raise_if_challenged(page)
        container = await _find_comment_container(page)
        scroll_target = container or note_scroller
        available_count = await items.count()
        bounded_count = min(available_count, _MAX_COMMENT_ITEMS)

        for index in range(processed_count, bounded_count):
            item = items.nth(index)
            if not await _is_top_level_comment(item):
                continue
            nickname = await _find_first_text(item, _COMMENT_AUTHOR_SELECTORS)
            comment_text = await _find_first_text(item, _COMMENT_TEXT_SELECTORS)
            if nickname and comment_text and _is_usable_comment(comment_text):
                output.append(f"- {nickname}:{comment_text}")

        processed_count = bounded_count
        if processed_count >= _MAX_COMMENT_ITEMS or scroll_number >= _MAX_COMMENT_SCROLLS:
            break

        box = await scroll_target.bounding_box()
        if box is None:
            break
        await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await page.mouse.wheel(0, 400)
        await page.wait_for_timeout(_COMMENT_SCROLL_WAIT_MS)
        await _raise_if_challenged(page)

        new_count = await items.count()
        container = await _find_comment_container(page)
        if container is not None and new_count <= available_count:
            consecutive_no_growth += 1
            if consecutive_no_growth >= 2:
                break
        else:
            consecutive_no_growth = 0

    return output


async def fetch_xhs(url: str) -> dict:
    """Fetch a user's accessible note, returning plain text for the cleaner."""
    _validate_xhs_url(url)

    async with browser_operation_lock:
        require_xhs_login_complete()
        context = await get_browser_context()
        page = await context.new_page()
        page.set_default_navigation_timeout(25000)
        page.set_default_timeout(8000)

        try:
            response = await page.goto(url, wait_until="domcontentloaded")
            if response is not None and response.status >= 400:
                raise FetchError(f"小红书页面返回 HTTP {response.status}。")

            final_url = page.url
            _validate_xhs_url(final_url)
            if urlsplit(final_url).path.startswith("/login"):
                raise FetchError("小红书页面要求登录，已停止抓取；请先在 Edge 中正常登录。")

            await _raise_if_challenged(page)
            raw = await _find_visible_text(page, _NOTE_BODY_SELECTORS)
            if not raw:
                await _raise_if_challenged(page)
                raise FetchError("未能提取小红书笔记正文；页面可能不可访问或结构已变化。")
            title = await _extract_title(page, raw)

            image_text = await _extract_image_text(context, page)
            comments = await _extract_comments(page)
            comments_section = "\n".join(comments) if comments else "未获取到符合条件的评论。"
            raw = (
                f"## 图片文字\n\n{image_text}\n\n"
                f"## 正文\n\n{raw}\n\n"
                f"## 高赞评论\n\n{comments_section}"
            )

            return {
                "source_type": "xhs",
                "source_url": url,
                "title": title,
                "raw": raw,
                "raw_kind": "text",
            }
        except FetchError:
            raise
        except PlaywrightTimeoutError as exc:
            raise FetchError("打开小红书笔记或等待正文超时；已停止抓取。") from exc
        except PlaywrightError as exc:
            raise FetchError("Playwright 访问小红书笔记失败；已停止抓取。") from exc
        finally:
            await page.close()