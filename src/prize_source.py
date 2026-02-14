"""Prize source integrations for Singapore Pools TOTO data."""

from __future__ import annotations

import re
from typing import Dict, Optional

from html.parser import HTMLParser


_NEXT_JACKPOT_PATTERN = re.compile(r"next\s*jackpot", flags=re.IGNORECASE)
_JACKPOT_PATTERN = re.compile(r"jackpot", flags=re.IGNORECASE)
_NEXT_PATTERN = re.compile(r"next", flags=re.IGNORECASE)
_NEXT_DRAW_PATTERN = re.compile(r"next\s*draw", flags=re.IGNORECASE)
DEFAULT_TOTO_NEXT_DRAW_ESTIMATE_URL = (
    "https://www.singaporepools.com.sg/DataFileArchive/Lottery/Output/toto_next_draw_estimate_en.html"
)


class _VisibleTextParser(HTMLParser):
    """Minimal HTML parser that gathers visible text chunks."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self._chunks.append(data)

    def get_text(self) -> str:
        return " ".join(self._chunks)


def _html_to_text(html: str) -> str:
    """Convert HTML to normalized visible text for anchor-based parsing."""
    parser = _VisibleTextParser()
    parser.feed(html)
    visible_text = parser.get_text()
    return re.sub(r"\s+", " ", visible_text).strip()


def _find_jackpot_anchor(text: str) -> int:
    """Locate the jackpot anchor index using the requested fallback semantics."""
    next_jackpot = _NEXT_JACKPOT_PATTERN.search(text)
    if next_jackpot:
        return next_jackpot.start()

    for match in _JACKPOT_PATTERN.finditer(text):
        lookback_start = max(0, match.start() - 40)
        if _NEXT_PATTERN.search(text[lookback_start : match.start()]):
            return match.start()

    return -1


def _parse_amount_to_float(raw_amount: str) -> float:
    cleaned = re.sub(r"^(?:S\$|\$)\s*", "", raw_amount.strip(), flags=re.IGNORECASE)
    cleaned = cleaned.replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        raise ValueError(f"Unrecognized jackpot amount format: {raw_amount!r}")
    return float(cleaned)


def _extract_jackpot_match(text: str) -> str:
    jackpot_patterns = [
        re.compile(
            r"next\s*jackpot\s*(?:est\.?\s*)?(?:is\s*)?(?P<amount>(?:S\$|\$)?\s*\d[\d,]*(?:\.\d+)?)",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"jackpot\s*(?:est\.?\s*)?(?:is\s*)?(?P<amount>(?:S\$|\$)?\s*\d[\d,]*(?:\.\d+)?)",
            flags=re.IGNORECASE,
        ),
    ]
    for pattern in jackpot_patterns:
        match = pattern.search(text)
        if match:
            return match.group(0)
    raise ValueError("Could not parse jackpot estimate from Singapore Pools page text.")


def _extract_next_draw_match(text: str) -> str:
    next_draw_pattern = re.compile(
        r"next\s*draw\s*[:\-]?\s*(?P<draw>(?:[A-Za-z]{3}\s*,\s*)?\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s*,\s*\d{1,2}[.:]\d{2}\s*(?:am|pm))",
        flags=re.IGNORECASE,
    )
    match = next_draw_pattern.search(text)
    if not match:
        raise ValueError("Could not parse next draw date/time from Singapore Pools page text.")
    return match.group(0)


def _extract_jackpot_estimate(text: str, debug: bool = False) -> float:
    """Extract and normalize the Singapore Pools next jackpot estimate."""
    jackpot_match = _extract_jackpot_match(text)
    amount_match = re.search(r"(?:S\$|\$)?\s*\d[\d,]*(?:\.\d+)?", jackpot_match, flags=re.IGNORECASE)
    if not amount_match:
        raise ValueError("Could not parse a numeric jackpot amount from matched jackpot text.")
    return _parse_amount_to_float(amount_match.group(0))


def _extract_next_draw_text(text: str, debug: bool = False) -> str:
    """Extract the 'Next Draw' text from normalized page text."""
    next_draw_match = _extract_next_draw_match(text)
    draw_match = re.search(
        r"(?:[A-Za-z]{3}\s*,\s*)?\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s*,\s*\d{1,2}[.:]\d{2}\s*(?:am|pm)",
        next_draw_match,
        flags=re.IGNORECASE,
    )
    if not draw_match:
        raise ValueError("Could not parse draw date/time from matched next draw text.")
    return draw_match.group(0).strip()


def _truncate_for_debug(text: str, limit: int = 200) -> str:
    """Return a one-line, length-limited string for concise debug output."""
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def parse_singaporepools_toto(html: str, debug: bool = False) -> Dict[str, object]:
    """Parse Singapore Pools TOTO HTML and return normalized draw metadata."""
    plain_text = _html_to_text(html)
    jackpot_match = _extract_jackpot_match(plain_text)
    next_draw_match = _extract_next_draw_match(plain_text)

    jackpot_estimate = _extract_jackpot_estimate(plain_text, debug=debug)
    draw_datetime_text = _extract_next_draw_text(plain_text, debug=debug)
    if debug:
        print(f"[debug] Normalized text: {_truncate_for_debug(plain_text, limit=200)}")
        print(f"[debug] Matched jackpot substring: {_truncate_for_debug(jackpot_match, limit=200)}")
        print(f"[debug] Matched next draw substring: {_truncate_for_debug(next_draw_match, limit=200)}")
        print(
            "[debug] Final parsed values: "
            f"jackpot_estimate={jackpot_estimate}, draw_datetime_text={draw_datetime_text!r}"
        )
    return {
        "jackpot_estimate": jackpot_estimate,
        "draw_datetime_text": draw_datetime_text,
    }


def fetch_singaporepools_toto_next_draw(url: Optional[str], debug: bool = False) -> Dict[str, object]:
    """Fetch Singapore Pools TOTO page and return next jackpot estimate and next draw text."""
    from urllib import error, request

    target_url = url.strip() if isinstance(url, str) else ""
    if not target_url:
        target_url = DEFAULT_TOTO_NEXT_DRAW_ESTIMATE_URL

    req = request.Request(target_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with request.urlopen(req, timeout=20) as response:
            status_code = getattr(response, "status", None)
            html = response.read().decode("utf-8", errors="replace")
    except error.URLError as exc:
        raise ValueError(f"Failed to fetch Singapore Pools TOTO page: {exc}") from exc

    if debug:
        print(f"[debug] HTTP status code: {status_code}")
        print(f"[debug] URL: {target_url}")

    return parse_singaporepools_toto(html, debug=debug)


__all__ = ["fetch_singaporepools_toto_next_draw", "parse_singaporepools_toto"]
