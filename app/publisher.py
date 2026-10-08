"""Publish cleaned content into the local SQLite database."""

import secrets
import string
from datetime import datetime, timezone

from app import db
from app.exceptions import FetchError


_SLUG_ALPHABET = string.ascii_letters + string.digits
_SLUG_LENGTH = 8
_SLUG_ATTEMPTS = 5


def _new_slug() -> str:
    return "".join(secrets.choice(_SLUG_ALPHABET) for _ in range(_SLUG_LENGTH))


def publish(cleaned: dict, source: dict) -> str:
    """Persist a cleaned document and return its non-sequential local slug."""
    if not isinstance(cleaned, dict) or not isinstance(source, dict):
        raise FetchError("发布输入格式无效。")

    title = cleaned.get("title")
    content_md = cleaned.get("content_md")
    source_type = source.get("source_type")
    source_url = source.get("source_url")
    if not isinstance(title, str) or not title.strip():
        raise FetchError("内容标题为空，无法发布。")
    if not isinstance(content_md, str) or not content_md.strip():
        raise FetchError("正文为空，无法发布。")
    if not isinstance(source_type, str) or not source_type.strip():
        raise FetchError("来源类型缺失，无法发布。")
    if not isinstance(source_url, str):
        raise FetchError("来源地址缺失，无法发布。")

    created_at = datetime.now(timezone.utc).isoformat()
    for _ in range(_SLUG_ATTEMPTS):
        slug = _new_slug()
        try:
            db.insert_document(
                {
                    "id": slug,
                    "source_type": source_type.strip(),
                    "source_url": source_url,
                    "title": title.strip(),
                    "content_md": content_md.strip(),
                    "created_at": created_at,
                }
            )
            return slug
        except Exception as exc:
            # Retry only a primary-key collision; other database errors should
            # fail clearly rather than being mistaken for a collision.
            import sqlite3

            if isinstance(exc, sqlite3.IntegrityError) and "documents.id" in str(exc):
                continue
            raise FetchError("无法将内容写入本地数据库。") from exc

    raise FetchError("生成阅读链接失败，请重试。")


def delete_document(slug: str) -> bool:
    """Maintenance helper for deleting one locally published item."""
    return db.delete_document(slug)
