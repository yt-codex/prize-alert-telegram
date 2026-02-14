"""Prize source integrations for Singapore Pools TOTO data."""

from __future__ import annotations

import re
from typing import Dict

import requests


_JACKPOT_LABEL_PATTERN = re.compile(
    r"Next\s*Jackpot\s*\.?\s*(?P<value>[^<\n\r]+)",
    flags=re.IGNORECASE,
)
_DRAW_LABEL_PATTERN = re.compile(
    r"Next\s*Draw\s*\.?\s*(?P<value>[^<\n\r]+)",
    flags=re.IGNORECASE,
)


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


def fetch_singaporepools_toto_next_draw(url: str) -> Dict[str, object]:
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
    response = requests.get(url, timeout=20)
    response.raise_for_status()

    html_text = response.text
    jackpot_estimate = _extract_jackpot_estimate(html_text)
    draw_datetime_text = _extract_next_draw_text(html_text)

    return {
        "jackpot_estimate": jackpot_estimate,
        "draw_datetime_text": draw_datetime_text,
    }


__all__ = ["fetch_singaporepools_toto_next_draw"]
