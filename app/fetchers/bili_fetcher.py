"""Fetch existing Bilibili subtitles with the user's local SESSDATA."""

import re
from urllib.parse import parse_qs, urlparse

from bilibili_api import Credential, NetworkException
from bilibili_api.ass import request_subtitle
from bilibili_api.video import Video

from app.config import BILIBILI_SESSDATA
from app.exceptions import FetchError


_BVID_PATTERN = re.compile(r"BV[a-zA-Z0-9]{10}")
_ALLOWED_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}


def _parse_video_url(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        raise FetchError("仅支持 HTTPS 的 bilibili.com 视频链接。")

    match = _BVID_PATTERN.search(parsed.path)
    if match is None:
        raise FetchError("无法从链接中识别 BV 号。")

    page_values = parse_qs(parsed.query).get("p", ["1"])
    try:
        page_index = max(1, int(page_values[0])) - 1
    except (TypeError, ValueError) as exc:
        raise FetchError("视频分 P 参数无效。") from exc
    return match.group(0), page_index


def _choose_subtitle(subtitles: list[dict]) -> dict:
    chinese = [
        subtitle
        for subtitle in subtitles
        if (subtitle.get("lan") or "").lower().startswith("zh")
        or "中文" in (subtitle.get("lan_doc") or "")
    ]
    return chinese[0] if chinese else subtitles[0]


async def _fetch_bili_details(url: str) -> tuple[dict, str]:
    """Fetch the contract result and subtitle language for the local verifier."""
    if not BILIBILI_SESSDATA:
        raise FetchError("未配置 BILIBILI_SESSDATA，请在项目根目录的 .env 中填写。")

    bvid, page_index = _parse_video_url(url)
    credential = Credential(sessdata=BILIBILI_SESSDATA)
    video = Video(bvid=bvid, credential=credential)

    try:
        info = await video.get_info()
        title = (info.get("title") or "").strip()
        if not title:
            title = bvid

        cid = await video.get_cid(page_index=page_index)
        subtitle_info = await video.get_subtitle(cid=cid) or {}
        available = subtitle_info.get("subtitles") or []
        if not available:
            raise FetchError(f"视频《{title}》没有可用字幕。")

        selected = _choose_subtitle(available)
        language = selected.get("lan_doc") or selected.get("lan") or "未知语言"
        language_code = selected.get("lan")
        subtitle_object = await request_subtitle(
            obj=video,
            page_index=page_index,
            lan_code=language_code,
            credential=credential,
        )
        segments = await subtitle_object.request_ass_data_json(lan_set=language_code)
        raw = "\n".join(
            str(segment.get("content", "")).strip()
            for segment in segments
            if str(segment.get("content", "")).strip()
        ).strip()
        if not raw:
            raise FetchError(f"视频《{title}》的字幕内容为空。")

        return (
            {
                "source_type": "bilibili",
                "source_url": url,
                "title": title,
                "raw": raw,
                "raw_kind": "text",
            },
            language,
        )
    except FetchError:
        raise
    except NetworkException as exc:
        # A 412 response is treated as a platform restriction signal. Do not
        # retry with alternate clients or attempt to work around it.
        if re.search(r"状态码\s*[：:]\s*412\b|\bHTTP\s*412\b", str(exc), re.IGNORECASE):
            raise FetchError(
                "B 站返回 HTTP 412，请求被平台拒绝或限制；已停止本次抓取，没有尝试绕过。"
            ) from exc
        raise FetchError(
            "B 站网络请求失败；请稍后重试并确认视频链接可访问。"
        ) from exc
    except Exception as exc:
        # Do not include exception text: some network/client errors may contain
        # request details. In particular, never expose the SESSDATA value.
        raise FetchError(
            f"B站字幕获取失败（{type(exc).__name__}）。请检查链接和 SESSDATA，或稍后重试。"
        ) from exc


async def fetch_bili(url: str) -> dict:
    """Fetch subtitles and return the shared fetcher result contract."""
    result, _language = await _fetch_bili_details(url)
    return result
