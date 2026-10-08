"""Extract text and metadata from a local PDF file."""

import re
from pathlib import Path

import pymupdf

from app.exceptions import FetchError


def fetch_pdf(file_path: str) -> dict:
    """Extract a PDF into the shared fetcher result format.

    The source URL is intentionally limited to the file name so a local
    machine-specific path is not stored in the published document record.
    """
    path = Path(file_path).expanduser()
    if not path.exists():
        raise FetchError(f"PDF 文件不存在：{path}")
    if not path.is_file():
        raise FetchError(f"指定路径不是文件：{path}")
    if path.suffix.lower() != ".pdf":
        raise FetchError("文件扩展名不是 .pdf")

    try:
        with pymupdf.open(path) as document:
            if document.needs_pass:
                raise FetchError("PDF 受密码保护，当前无法读取。")

            pages = [page.get_text("text").strip() for page in document]
            raw = "\n\n".join(page for page in pages if page)
            raw = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", raw).strip()
            title = (document.metadata or {}).get("title", "").strip()
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError(f"无法读取 PDF：{exc}") from exc

    if not raw:
        raise FetchError("未从 PDF 中提取到文字，文件可能是扫描件或受保护文件。")

    return {
        "source_type": "pdf",
        "source_url": path.name,
        "title": title or path.stem,
        "raw": raw,
        "raw_kind": "text",
    }
