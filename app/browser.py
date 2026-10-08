"""Persistent Playwright context backed by the locally installed Microsoft Edge."""

import asyncio

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from app.config import BROWSER_PROFILE_PATH, PLAYWRIGHT_BROWSER_CHANNEL
from app.exceptions import FetchError


_playwright: Playwright | None = None
_context: BrowserContext | None = None
_context_closed = False
_xhs_login_pending = False
_xhs_login_page: Page | None = None
_context_lock = asyncio.Lock()
# A persistent profile is a single-user resource. Serialize complete fetches so
# simultaneous submissions cannot race page creation/closure or profile state.
browser_operation_lock = asyncio.Lock()


def _mark_context_closed(closed_context: BrowserContext) -> None:
    global _context_closed, _xhs_login_pending, _xhs_login_page
    if _context is closed_context:
        _context_closed = True
        _xhs_login_pending = False
        _xhs_login_page = None


async def get_browser_context() -> BrowserContext:
    """Return the shared headed Edge context with a persistent project profile."""
    global _playwright, _context, _context_closed

    async with _context_lock:
        if _context is not None and not _context_closed:
            return _context

        # Edge may be closed by the user or may exit unexpectedly while the
        # Python references remain cached. Dispose those stale references and
        # create a fresh persistent context on the next submission.
        if _playwright is not None:
            try:
                await _playwright.stop()
            except Exception:
                pass
        _playwright = None
        _context = None
        _context_closed = False

        BROWSER_PROFILE_PATH.mkdir(parents=True, exist_ok=True)
        _playwright = await async_playwright().start()
        try:
            _context = await _playwright.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_PROFILE_PATH),
                channel=PLAYWRIGHT_BROWSER_CHANNEL,
                headless=False,
                # Playwright otherwise adds --no-sandbox by default. Edge warns
                # about that flag; explicitly keep Chromium's sandbox.
                chromium_sandbox=True,
            )
            _context.on("close", lambda context=_context: _mark_context_closed(context))
        except Exception:
            await _playwright.stop()
            _playwright = None
            _context = None
            raise

        return _context


async def close_browser_context() -> None:
    """Close the shared context while keeping its profile on disk for reuse."""
    global _playwright, _context, _context_closed, _xhs_login_pending, _xhs_login_page

    async with browser_operation_lock:
        async with _context_lock:
            try:
                if _context is not None and not _context_closed:
                    await _context.close()
            finally:
                _context = None
                _context_closed = False
                _xhs_login_pending = False
                _xhs_login_page = None
                if _playwright is not None:
                    await _playwright.stop()
                    _playwright = None


async def open_xhs_login_page() -> None:
    """Open the XHS login page in the shared headed Edge profile."""
    global _xhs_login_pending, _xhs_login_page

    async with browser_operation_lock:
        if _xhs_login_pending and _xhs_login_page is not None and not _xhs_login_page.is_closed():
            await _xhs_login_page.bring_to_front()
            return

        page = None
        try:
            context = await get_browser_context()
            page = await context.new_page()
            page.set_default_navigation_timeout(25000)
            response = await page.goto("https://www.xiaohongshu.com/login", wait_until="domcontentloaded")
            if response is not None and response.status >= 400:
                raise FetchError(f"小红书登录页返回 HTTP {response.status}。")
        except FetchError:
            if page is not None and not page.is_closed():
                try:
                    await page.close()
                except Exception:
                    pass
            raise
        except Exception as exc:
            if page is not None and not page.is_closed():
                try:
                    await page.close()
                except Exception:
                    pass
            raise FetchError("无法打开小红书登录页，请检查 Edge 是否正常运行。") from exc

        _xhs_login_page = page
        _xhs_login_pending = True


async def complete_xhs_login() -> None:
    """Allow note fetching after the user confirms manual login is complete."""
    global _xhs_login_pending, _xhs_login_page

    async with browser_operation_lock:
        _xhs_login_pending = False
        _xhs_login_page = None


def require_xhs_login_complete() -> None:
    """Reject fetching while the user is still completing the login flow."""
    if _xhs_login_pending:
        raise FetchError("请先在 Edge 中完成小红书登录，再点击“我已完成登录”。")
