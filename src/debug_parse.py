"""Developer checkpoint CLI for Singapore Pools TOTO parsing."""

from __future__ import annotations

import sys
from pathlib import Path

from prize_source import fetch_singaporepools_toto_next_draw, parse_singaporepools_toto


DEFAULT_TOTO_URL = (
    "https://www.singaporepools.com.sg/en/product/pages/toto_results.aspx"
)
FIXTURE_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "toto_results_sample.html"


def main() -> int:
    try:
        result = fetch_singaporepools_toto_next_draw(DEFAULT_TOTO_URL, debug=True)
        print(f"[debug] Parse succeeded: {result}")
        return 0
    except Exception as exc:
        print(f"[debug] Live fetch parse failed: {exc}")
        if not FIXTURE_PATH.exists():
            return 1

        print(f"[debug] Falling back to local fixture: {FIXTURE_PATH}")
        html = FIXTURE_PATH.read_text(encoding="utf-8")
        result = parse_singaporepools_toto(html, debug=True)
        print(f"[debug] Fixture parse succeeded: {result}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
