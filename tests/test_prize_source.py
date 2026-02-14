"""Unit tests for deterministic parsing of Singapore Pools TOTO HTML."""

from pathlib import Path
import sys

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
