"""Run the U3 Bilibili fetcher against a supplied video URL."""

import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.exceptions import FetchError
from app.fetchers.bili_fetcher import _fetch_bili_details


async def run(url: str, expect_no_subtitle: bool) -> int:
    try:
        result, language = await _fetch_bili_details(url)
    except FetchError as exc:
        if expect_no_subtitle and "没有可用字幕" in str(exc):
            print(f"EXPECTED NO-SUBTITLE: {exc}")
            return 0
        print(f"FETCH FAILED: {exc}")
        return 1

    if expect_no_subtitle:
        print("FETCH FAILED: 该视频实际返回了字幕，与预期的无字幕验收不符。")
        return 1

    print(f"title: {result['title']}")
    print(f"subtitle language: {language}")
    print(f"source_type: {result['source_type']}; raw_kind: {result['raw_kind']}")
    print(f"subtitle length: {len(result['raw'])} characters")
    print("subtitle text preview:")
    print(result["raw"][:1200])
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="HTTPS bilibili.com video URL")
    parser.add_argument(
        "--expect-no-subtitle",
        action="store_true",
        help="Treat a no-subtitle FetchError as the expected result",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.url, args.expect_no_subtitle)))


if __name__ == "__main__":
    main()
