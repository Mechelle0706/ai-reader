"""Run the U1 PDF fetcher against a supplied local PDF and print its result."""

import argparse
from pprint import pformat
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.exceptions import FetchError
from app.fetchers.pdf_fetcher import fetch_pdf


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_path", help="Path to the PDF to inspect")
    args = parser.parse_args()

    try:
        result = fetch_pdf(args.pdf_path)
    except FetchError as exc:
        parser.exit(1, f"PDF 提取失败：{exc}\n")

    print(pformat(result, sort_dicts=False, width=100))


if __name__ == "__main__":
    main()
