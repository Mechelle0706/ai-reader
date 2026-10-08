"""Fetch one or more public WeChat article URLs and print title and HTML previews."""

import argparse
import asyncio
from pathlib import Path
import sys

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.browser import close_browser_context
from app.exceptions import FetchError
from app.fetchers.wechat_fetcher import fetch_wechat


async def run(urls: list[str]) -> int:
    failed = False
    try:
        for index, url in enumerate(urls, start=1):
            print(f"\n=== Article {index}: {url} ===")
            try:
                result = await fetch_wechat(url)
            except FetchError as exc:
                failed = True
                print(f"FETCH FAILED: {exc}")
                continue

            print(f"title: {result['title']}")
            print(f"source_type: {result['source_type']}; raw_kind: {result['raw_kind']}")
            soup = BeautifulSoup(result["raw"], "html.parser")
            article = soup.select_one("#js_content, .rich_media_content")
            if article is None:
                failed = True
                print("FETCH FAILED: 返回 HTML 中未找到正文容器。")
                continue
            body_text = article.get_text(" ", strip=True)
            if not body_text:
                failed = True
                print("FETCH FAILED: 正文容器中没有可读文本。")
                continue
            print("article body first 500 characters:")
            print(body_text[:500])
            print(f"article body length: {len(body_text)} characters")
            print(f"raw length: {len(result['raw'])} characters")
    finally:
        await close_browser_context()
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="+", help="HTTPS mp.weixin.qq.com article URLs")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.urls)))


if __name__ == "__main__":
    main()
