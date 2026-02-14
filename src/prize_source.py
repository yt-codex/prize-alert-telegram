"""Prize source integrations for Singapore Pools TOTO data."""

from __future__ import annotations

import re
from typing import Dict


_JACKPOT_LABEL_PATTERN = re.compile(
    r"Next\s*Jackpot\s*\.?\s*(?P<value>[^<\n\r]+)",
    flags=re.IGNORECASE,
)
_DRAW_LABEL_PATTERN = re.compile(
    r"Next\s*Draw\s*\.?\s*(?P<value>[^<\n\r]+)",
    flags=re.IGNORECASE,
)


_TAG_PATTERN = re.compile(r"<[^>]+>")


def _html_to_text(html: str) -> str:
    """Convert HTML to plain text for label/value regex parsing."""
    no_tags = _TAG_PATTERN.sub(" ", html)
    return re.sub(r"\s+", " ", no_tags).strip()


def _extract_jackpot_estimate(html_text: str) -> float:
    """Extract and normalize the Singapore Pools "Next Jackpot" estimate from HTML text."""
    match = _JACKPOT_LABEL_PATTERN.search(html_text)
    if not match:
        raise ValueError("Could not find 'Next Jackpot' value in Singapore Pools page.")

    raw_value = match.group("value")
    numeric_match = re.search(r"\$?\s*([\d][\d,]*(?:\.\d+)?)", raw_value)
    if not numeric_match:
        raise ValueError("Could not parse numeric jackpot estimate from 'Next Jackpot' value.")

    normalized = numeric_match.group(1).replace(",", "")
    return float(normalized)


def _extract_next_draw_text(html_text: str) -> str:
    """Extract the Singapore Pools "Next Draw" text while preserving date/time formatting."""
    match = _DRAW_LABEL_PATTERN.search(html_text)
    if not match:
        raise ValueError("Could not find 'Next Draw' value in Singapore Pools page.")

    draw_text = re.sub(r"\s+", " ", match.group("value")).strip()
    if not draw_text:
        raise ValueError("Found 'Next Draw' label but draw datetime text is empty.")

    return draw_text


def _truncate_for_debug(text: str, limit: int = 200) -> str:
    """Return a one-line, length-limited string for concise debug output."""
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def parse_singaporepools_toto(html: str) -> Dict[str, object]:
    """Parse Singapore Pools TOTO HTML and return normalized draw metadata."""
    plain_text = _html_to_text(html)
    jackpot_estimate = _extract_jackpot_estimate(plain_text)
    draw_datetime_text = _extract_next_draw_text(plain_text)
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

    html_text = response.text
    if debug:
        print(f"[debug] Response text length: {len(html_text)}")

    jackpot_match = _JACKPOT_LABEL_PATTERN.search(html_text)
    draw_match = _DRAW_LABEL_PATTERN.search(html_text)
    if debug:
        jackpot_snippet = jackpot_match.group(0) if jackpot_match else "<no match>"
        draw_snippet = draw_match.group(0) if draw_match else "<no match>"
        print(
            "[debug] Next Jackpot match snippet: "
            f"{_truncate_for_debug(jackpot_snippet, limit=200)}"
        )
        print(
            "[debug] Next Draw match snippet: "
            f"{_truncate_for_debug(draw_snippet, limit=200)}"
        )

    parsed = parse_singaporepools_toto(html_text)
    if debug:
        print(f"[debug] Parsed jackpot_estimate: {parsed['jackpot_estimate']}")
        print(f"[debug] Parsed draw_datetime_text: {parsed['draw_datetime_text']}")

    return parsed


__all__ = ["fetch_singaporepools_toto_next_draw", "parse_singaporepools_toto"]
