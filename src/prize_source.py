"""Prize source integrations for Singapore Pools TOTO data."""

from __future__ import annotations

import re
from typing import Dict

from html.parser import HTMLParser


_NEXT_JACKPOT_PATTERN = re.compile(r"next\s*jackpot", flags=re.IGNORECASE)
_JACKPOT_PATTERN = re.compile(r"jackpot", flags=re.IGNORECASE)
_NEXT_PATTERN = re.compile(r"next", flags=re.IGNORECASE)
_NEXT_DRAW_PATTERN = re.compile(r"next\s*draw", flags=re.IGNORECASE)


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


def _extract_jackpot_estimate(text: str, debug: bool = False) -> float:
    """Extract and normalize the Singapore Pools next jackpot estimate from anchor window."""
    anchor_index = _find_jackpot_anchor(text)
    if debug:
        print(
            f"[debug] Next Jackpot anchor found: {anchor_index != -1}; index: {anchor_index}"
        )

    excerpt_index = anchor_index
    if excerpt_index == -1:
        fallback = _JACKPOT_PATTERN.search(text)
        excerpt_index = fallback.start() if fallback else 0
    if debug:
        print(f"[debug] Jackpot excerpt: {_truncate_for_debug(text[excerpt_index:excerpt_index+200])}")

    if anchor_index == -1:
        raise ValueError(
            "Could not find jackpot anchor: expected 'Next Jackpot' or 'Jackpot' with 'Next' within 40 characters before it."
        )

    window = text[anchor_index : anchor_index + 400]
    if debug:
        print(f"[debug] Jackpot window: {_truncate_for_debug(window, limit=200)}")

    amount_patterns = [
        re.compile(r"(?:S\$|\$)\s*\d[\d,]*(?:\.\d+)?", flags=re.IGNORECASE),
        re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?"),
    ]
    amount_match = None
    for pattern in amount_patterns:
        amount_match = pattern.search(window)
        if amount_match:
            break

    if not amount_match:
        raise ValueError(
            "Could not find a jackpot amount near the jackpot anchor (expected currency-like values such as '$1,000,000' or '1,000,000')."
        )

    jackpot_estimate = _parse_amount_to_float(amount_match.group(0))
    if debug:
        print(f"[debug] Parsed jackpot_estimate: {jackpot_estimate}")
    return jackpot_estimate


def _extract_next_draw_text(text: str, debug: bool = False) -> str:
    """Extract the 'Next Draw' text from a forward anchor window."""
    draw_anchor = _NEXT_DRAW_PATTERN.search(text)
    anchor_index = draw_anchor.start() if draw_anchor else -1
    if debug:
        print(f"[debug] Next Draw anchor found: {draw_anchor is not None}; index: {anchor_index}")

    excerpt_index = anchor_index if anchor_index != -1 else 0
    if debug:
        print(f"[debug] Next Draw excerpt: {_truncate_for_debug(text[excerpt_index:excerpt_index+200])}")

    if not draw_anchor:
        raise ValueError("Could not find 'Next Draw' anchor in Singapore Pools page text.")

    window = text[anchor_index : anchor_index + 500]
    if debug:
        print(f"[debug] Next Draw window: {_truncate_for_debug(window, limit=200)}")

    after_label = window[draw_anchor.end() - anchor_index :].strip(" .:-")
    stop_patterns = [
        re.compile(r"\b(?:Draw\s*Results?|Results?|Jackpot)\b", flags=re.IGNORECASE),
    ]

    stop_index = len(after_label)
    for stop_pattern in stop_patterns:
        stop_match = stop_pattern.search(after_label)
        if stop_match:
            stop_index = min(stop_index, stop_match.start())

    draw_text = after_label[:stop_index].strip(" .:-")
    draw_text = re.sub(r"\s+", " ", draw_text)
    if not draw_text:
        raise ValueError("Found 'Next Draw' anchor but could not extract a draw datetime text.")

    if debug:
        print(f"[debug] Parsed draw_datetime_text: {draw_text}")

    return draw_text


def _truncate_for_debug(text: str, limit: int = 200) -> str:
    """Return a one-line, length-limited string for concise debug output."""
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def parse_singaporepools_toto(html: str, debug: bool = False) -> Dict[str, object]:
    """Parse Singapore Pools TOTO HTML and return normalized draw metadata."""
    plain_text = _html_to_text(html)
    if debug:
        print(f"[debug] Normalized text length: {len(plain_text)}")

    jackpot_estimate = _extract_jackpot_estimate(plain_text, debug=debug)
    draw_datetime_text = _extract_next_draw_text(plain_text, debug=debug)
    return {
        "jackpot_estimate": jackpot_estimate,
        "draw_datetime_text": draw_datetime_text,
    }


def fetch_singaporepools_toto_next_draw(url: str, debug: bool = False) -> Dict[str, object]:
    """Fetch Singapore Pools TOTO page and return next jackpot estimate and next draw text.

    Args:
        url: Singapore Pools TOTO results URL.

    Returns:
        Dictionary with:
          - jackpot_estimate: float dollar estimate of the next jackpot.
          - draw_datetime_text: next draw date/time text as shown on the page.

    Raises:
        requests.RequestException: For network/HTTP failures.
        ValueError: If required fields cannot be found or parsed.
    """
    import requests

    response = requests.get(url, timeout=20)
    if debug:
        print(f"[debug] HTTP status code: {response.status_code}")

    response.raise_for_status()

    return parse_singaporepools_toto(response.text, debug=debug)


__all__ = ["fetch_singaporepools_toto_next_draw", "parse_singaporepools_toto"]
