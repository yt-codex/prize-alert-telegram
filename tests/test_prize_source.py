"""Unit tests for deterministic parsing of Singapore Pools TOTO HTML."""

from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from prize_source import parse_singaporepools_toto


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "toto_results_sample.html"


def test_parse_singaporepools_toto_from_saved_fixture() -> None:
    """Parser should extract jackpot and draw text from static fixture HTML."""
    html = FIXTURE_PATH.read_text(encoding="utf-8")

    parsed = parse_singaporepools_toto(html)

    assert parsed["jackpot_estimate"] == 1234567.0
    assert parsed["draw_datetime_text"] == "Mon, 08 Jul 2024, 6:30pm"


def test_parse_handles_separated_html_nodes_for_jackpot_and_draw() -> None:
    html = """
    <div>
      <span>Next</span><span>Jackpot</span>
      <div><strong>S$</strong><em>1,000,000</em></div>
      <p>Next <span>Draw</span>: Thu, 11 Jul 2024, 6:30pm</p>
      <div>Results</div>
    </div>
    """

    parsed = parse_singaporepools_toto(html)

    assert parsed["jackpot_estimate"] == 1000000.0
    assert parsed["draw_datetime_text"] == "Thu, 11 Jul 2024, 6:30pm"


def test_parse_raises_helpful_error_when_jackpot_anchor_missing() -> None:
    html = "<div>Next Draw Mon, 08 Jul 2024, 6:30pm</div>"

    with pytest.raises(ValueError, match="Could not find jackpot anchor"):
        parse_singaporepools_toto(html)
