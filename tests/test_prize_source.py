"""Unit tests for deterministic parsing of Singapore Pools TOTO HTML."""

from pathlib import Path

import pytest

from src import prize_source
from src.prize_source import parse_singaporepools_toto


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

    with pytest.raises(ValueError, match="Could not parse jackpot estimate"):
        parse_singaporepools_toto(html)


def test_parse_handles_archive_style_estimate_and_spaced_draw_comma() -> None:
    html = """
    <div>Next Jackpot Est. S$1,234,567</div>
    <div>Next Draw Mon, 16 Feb 2026 , 6.30pm</div>
    """

    parsed = parse_singaporepools_toto(html)

    assert parsed["jackpot_estimate"] == 1234567.0
    assert parsed["draw_datetime_text"] == "Mon, 16 Feb 2026 , 6.30pm"


def test_fetch_defaults_to_archive_url_when_url_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    class DummyResponse:
        status = 200

        def read(self) -> bytes:
            return b"<div>Next Jackpot Est $1,000,000</div><div>Next Draw Tue, 17 Feb 2026, 6.30pm</div>"

        def __enter__(self) -> "DummyResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    def fake_urlopen(req, timeout: int) -> DummyResponse:
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        return DummyResponse()

    from urllib import request as urllib_request

    monkeypatch.setattr(urllib_request, "urlopen", fake_urlopen)

    parsed = prize_source.fetch_singaporepools_toto_next_draw("  ")

    assert captured["url"] == prize_source.DEFAULT_TOTO_NEXT_DRAW_ESTIMATE_URL
    assert captured["timeout"] == 20
    assert parsed["jackpot_estimate"] == 1000000.0
    assert parsed["draw_datetime_text"] == "Tue, 17 Feb 2026, 6.30pm"
