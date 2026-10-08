"""Persistent Playwright context backed by the locally installed Microsoft Edge."""

import asyncio

from playwright.async_api import BrowserContext, Playwright, async_playwright

from app.config import BROWSER_PROFILE_PATH, PLAYWRIGHT_BROWSER_CHANNEL


_playwright: Playwright | None = None
_context: BrowserContext | None = None
_context_closed = False
_context_lock = asyncio.Lock()
# A persistent profile is a single-user resource. Serialize complete fetches so
# simultaneous submissions cannot race page creation/closure or profile state.
browser_operation_lock = asyncio.Lock()


def _mark_context_closed(closed_context: BrowserContext) -> None:
    global _context_closed
    if _context is closed_context:
        _context_closed = True


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
    global _playwright, _context, _context_closed

    async with browser_operation_lock:
        async with _context_lock:
            try:
                if _context is not None and not _context_closed:
                    await _context.close()
            finally:
                _context = None
                _context_closed = False
                if _playwright is not None:
                    await _playwright.stop()
                    _playwright = None
