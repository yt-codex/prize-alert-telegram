"""Developer checkpoint CLI for Singapore Pools TOTO parsing."""

from __future__ import annotations

import sys

from prize_source import fetch_singaporepools_toto_next_draw


DEFAULT_TOTO_URL = (
    "https://www.singaporepools.com.sg/en/product/pages/toto_results.aspx"
)


def main() -> int:
    try:
        result = fetch_singaporepools_toto_next_draw(DEFAULT_TOTO_URL, debug=True)
        print(f"[debug] Parse succeeded: {result}")
        return 0
    except Exception as exc:
        print(f"[debug] Parse failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
