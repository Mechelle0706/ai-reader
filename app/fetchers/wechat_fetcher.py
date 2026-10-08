"""Fetch public WeChat Official Account article pages using local Edge."""

from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.browser import browser_operation_lock, get_browser_context
from app.exceptions import FetchError


_ARTICLE_SELECTORS = (
    "#js_content",
    ".rich_media_content",
    "#js_article .rich_media_area_primary",
)
_CHALLENGE_SELECTORS = (
    "#verify_check",
    "#wx_verify",
    ".weui-msg__title",
    ".verify_page",
)
_CHALLENGE_TEXT = (
    "当前环境异常",
    "访问过于频繁",
    "操作频繁",
    "安全验证",
    "人机验证",
    "请输入验证码",
    "请完成验证",
)


def _validate_wechat_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "mp.weixin.qq.com":
        raise FetchError("仅支持 https://mp.weixin.qq.com/ 下的公众号文章链接。")


async def _find_article_container(page):
    for selector in _ARTICLE_SELECTORS:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=4000)
        except PlaywrightTimeoutError:
            continue
        if (await locator.inner_text()).strip():
            return locator
    return None


async def _raise_if_challenged(page, *, inspect_text: bool = False) -> None:
    for selector in _CHALLENGE_SELECTORS:
        locator = page.locator(selector).first
        if await locator.count() and await locator.is_visible():
            raise FetchError("公众号页面要求验证或触发访问限制，已停止抓取。")

    if inspect_text:
        page_text = (await page.locator("body").inner_text()).lower()
        if any(marker.lower() in page_text for marker in _CHALLENGE_TEXT):
            raise FetchError("公众号页面要求验证或触发访问限制，已停止抓取。")


async def fetch_wechat(url: str) -> dict:
    """Serialize use of the single persistent Edge profile across requests."""
    async with browser_operation_lock:
        return await _fetch_wechat(url)


async def _fetch_wechat(url: str) -> dict:
    """Fetch a WeChat article and return its page HTML in the shared contract."""
    _validate_wechat_url(url)
    context = await get_browser_context()
    page = await context.new_page()
    page.set_default_navigation_timeout(25000)
    page.set_default_timeout(10000)

    try:
        response = await page.goto(url, wait_until="domcontentloaded")
        if response is not None and response.status >= 400:
            raise FetchError(f"公众号页面返回 HTTP {response.status}。")

        final_url = page.url
        _validate_wechat_url(final_url)

        await _raise_if_challenged(page)
        container = await _find_article_container(page)
        if container is None:
            await _raise_if_challenged(page, inspect_text=True)
            raise FetchError("未能加载公众号正文容器；页面可能不可访问或结构已变化。")

        title = ""
        og_title = page.locator('meta[property="og:title"]').first
        if await og_title.count():
            title = (await og_title.get_attribute("content") or "").strip()
        if not title:
            for selector in ("#activity-name", "h1"):
                title_locator = page.locator(selector).first
                if await title_locator.count():
                    title = (await title_locator.inner_text()).strip()
                    if title:
                        break
        if not title:
            title = (await page.title()).strip()
        if not title:
            raise FetchError("公众号文章标题为空。")

        raw = await page.content()
        if not raw.strip():
            raise FetchError("公众号页面 HTML 为空。")

        return {
            "source_type": "wechat",
            "source_url": url,
            "title": title,
            "raw": raw,
            "raw_kind": "html",
        }
    except FetchError:
        raise
    except PlaywrightTimeoutError as exc:
        raise FetchError("打开公众号页面或等待正文超时。") from exc
    except PlaywrightError as exc:
        raise FetchError(f"Playwright 抓取公众号页面失败：{exc}") from exc
    finally:
        await page.close()
