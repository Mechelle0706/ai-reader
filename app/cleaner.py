"""Convert fetcher results into safe, readable Markdown."""

import re
from urllib.parse import urlsplit

import trafilatura
from lxml import html as lxml_html
from markdownify import markdownify

from app.exceptions import FetchError


_UNSAFE_ELEMENTS = {
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "svg",
    "form",
    "input",
    "button",
    "textarea",
    "select",
}
_UNSAFE_SCHEMES = {"javascript", "data", "vbscript", "file"}


def _clean_text(raw: str, source_type: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = text.replace("\u00a0", " ")
    text = "\n".join(line.rstrip() for line in text.split("\n"))

    if source_type == "bilibili":
        # Subtitle payloads often contain one short utterance per line. Join
        # those fragments conservatively without rewriting their wording.
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)

    text = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text)
    return text.strip()


def _safe_content_html(extracted_html: str) -> str:
    try:
        root = lxml_html.fragment_fromstring(extracted_html, create_parent="div")
    except (TypeError, ValueError) as exc:
        raise FetchError("无法解析提取出的 HTML 正文。") from exc

    for element in list(root.iterdescendants()):
        if not isinstance(element.tag, str):
            element.drop_tree()
            continue
        if element.tag.lower() in _UNSAFE_ELEMENTS:
            element.drop_tree()
            continue

        for attribute in list(element.attrib):
            value = element.attrib[attribute]
            if attribute.lower().startswith("on") or attribute.lower() in {"style", "srcdoc"}:
                del element.attrib[attribute]
            elif attribute.lower() == "href":
                scheme = urlsplit(value.strip()).scheme.lower()
                if scheme in _UNSAFE_SCHEMES or (scheme and scheme not in {"http", "https", "mailto"}):
                    del element.attrib[attribute]
            elif attribute.lower() not in {"colspan", "rowspan", "scope", "abbr"}:
                del element.attrib[attribute]

    return lxml_html.tostring(root, encoding="unicode", method="html")


def _clean_html(raw: str) -> str:
    extracted_html = trafilatura.extract(
        raw,
        output_format="html",
        include_tables=True,
        include_links=True,
        include_images=False,
        include_comments=False,
        include_formatting=True,
    )
    if not extracted_html or not extracted_html.strip():
        raise FetchError("未能从 HTML 页面提取到正文。")

    safe_html = _safe_content_html(extracted_html)
    content_md = markdownify(
        safe_html,
        heading_style="ATX",
        bullets="-",
        strip=["script", "style", "iframe", "object", "embed"],
    )
    content_md = re.sub(r"[ \t]+\n", "\n", content_md)
    content_md = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", content_md)
    return content_md.strip()


def clean(fetched: dict) -> dict:
    """Clean a fetcher contract dictionary into ``title`` and ``content_md``."""
    if not isinstance(fetched, dict):
        raise FetchError("清洗输入格式无效，预期为 fetcher 结果字典。")

    raw = fetched.get("raw")
    raw_kind = fetched.get("raw_kind")
    source_type = fetched.get("source_type", "")
    source_url = fetched.get("source_url", "")
    title = fetched.get("title", "")

    if not isinstance(raw, str) or not raw.strip():
        raise FetchError("正文为空，无法清洗或发布。")
    if raw_kind not in {"html", "text"}:
        raise FetchError("不支持的正文类型；raw_kind 必须为 html 或 text。")

    if not isinstance(title, str):
        title = ""
    if not title.strip():
        title = source_url.strip() if isinstance(source_url, str) else ""
    if not title:
        title = str(source_type).strip() or "未命名内容"
    title = title.strip()

    try:
        if raw_kind == "html":
            content_md = _clean_html(raw)
        else:
            content_md = _clean_text(raw, str(source_type))
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError("清洗正文失败，请检查内容格式。") from exc

    if not content_md:
        raise FetchError("清洗后正文为空，未生成可发布内容。")

    return {"title": title, "content_md": content_md}
