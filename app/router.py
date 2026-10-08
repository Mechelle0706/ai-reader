"""Recognize supported inputs and dispatch them to the available fetchers."""

import re
from pathlib import Path
from typing import TypeAlias
from urllib.parse import urlsplit

from app.exceptions import FetchError
from app.fetchers.pdf_fetcher import fetch_pdf
from app.fetchers.wechat_fetcher import fetch_wechat


InputValue: TypeAlias = str | Path

_URL_PREFIX = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_WINDOWS_DRIVE = re.compile(r"^[a-zA-Z]:[\\/]")
_PLATFORM_SUFFIXES = {
    "wechat": ("mp.weixin.qq.com",),
    "bilibili": ("bilibili.com", "b23.tv"),
    "xhs": ("xiaohongshu.com", "xhslink.com"),
}


def _is_host(hostname: str, domains: tuple[str, ...]) -> bool:
    hostname = hostname.rstrip(".").lower()
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains)


def _classify_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise FetchError("链接格式无效。") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise FetchError("仅支持 HTTP 或 HTTPS 链接；本地文件请提供 PDF 路径。")
    if not hostname or parsed.username is not None or parsed.password is not None:
        raise FetchError("链接格式无效或包含不允许的用户信息。")
    if port not in (None, 80, 443):
        raise FetchError("链接端口不受支持。")

    for source_type, domains in _PLATFORM_SUFFIXES.items():
        if _is_host(hostname, domains):
            if source_type == "wechat" and parsed.scheme.lower() != "https":
                raise FetchError("公众号链接必须使用 HTTPS。")
            return source_type

    raise FetchError("暂不支持该链接来源；目前支持公众号、小红书和 B 站链接。")


async def route(input_value: InputValue) -> dict:
    """Fetch a supported input, or raise a user-readable ``FetchError``.

    PDF paths and WeChat URLs are connected to their real fetchers. XHS and
    Bilibili inputs are recognized but intentionally remain pending U8.
    """
    if isinstance(input_value, Path):
        path = input_value.expanduser()
        if path.suffix.lower() != ".pdf":
            raise FetchError("本地文件目前仅支持 PDF。")
        return fetch_pdf(str(path))

    if not isinstance(input_value, str) or not input_value.strip():
        raise FetchError("请输入有效的公众号链接或 PDF 文件路径。")

    value = input_value.strip()
    looks_like_url = value.lower().startswith(("http://", "https://"))
    has_non_file_scheme = bool(_URL_PREFIX.match(value)) and not _WINDOWS_DRIVE.match(value)

    if looks_like_url or has_non_file_scheme:
        source_type = _classify_url(value)
        if source_type == "wechat":
            return await fetch_wechat(value)
        if source_type == "bilibili":
            raise FetchError("B 站字幕功能暂停，待 U8 浏览器方案实现。")
        if source_type == "xhs":
            raise FetchError("小红书抓取待 U8 浏览器方案实现。")

    path = Path(value).expanduser()
    if path.suffix.lower() != ".pdf":
        raise FetchError("无法识别输入；请输入公众号链接或本地 PDF 文件路径。")
    return fetch_pdf(str(path))
