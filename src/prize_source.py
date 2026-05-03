"""Prize source integrations for Singapore Pools TOTO data."""

from __future__ import annotations

import os
import re
import time
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


DEFAULT_TOTO_NEXT_DRAW_ESTIMATE_URL = (
    "https://www.singaporepools.com.sg/DataFileArchive/Lottery/Output/toto_next_draw_estimate_en.html"
)
DEFAULT_FETCH_TIMEOUT_SECONDS = 10
DEFAULT_FETCH_ATTEMPTS = 3


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


def _extract_jackpot_estimate(text: str) -> float:
    jackpot_match = _extract_jackpot_match(text)
    amount_match = re.search(r"(?:S\$|\$)?\s*\d[\d,]*(?:\.\d+)?", jackpot_match, flags=re.IGNORECASE)
    if not amount_match:
        raise ValueError("Could not parse a numeric jackpot amount from matched jackpot text.")
    return _parse_amount_to_float(amount_match.group(0))


def _extract_next_draw_text(text: str) -> str:
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


def parse_singaporepools_toto(html: str, debug: bool = False) -> dict[str, object]:
    """Parse Singapore Pools TOTO HTML and return normalized draw metadata."""
    plain_text = _html_to_text(html)
    jackpot_match = _extract_jackpot_match(plain_text)
    next_draw_match = _extract_next_draw_match(plain_text)

    jackpot_estimate = _extract_jackpot_estimate(plain_text)
    draw_datetime_text = _extract_next_draw_text(plain_text)
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


def _positive_int_from_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _with_cache_buster(url: str, attempt_index: int) -> str:
    if attempt_index <= 0:
        return url
    split = urlsplit(url)
    query = parse_qsl(split.query, keep_blank_values=True)
    query.append(("_retry", str(attempt_index)))
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def fetch_singaporepools_toto_next_draw(url: str | None, debug: bool = False) -> dict[str, object]:
    """Fetch Singapore Pools TOTO page and return next jackpot estimate and next draw text.

    Singapore Pools' static archive endpoint occasionally accepts the connection on
    GitHub runners and then stalls during the body read. Use a few bounded attempts
    with fresh requests so one slow edge/cache node does not make the alert miss a
    valid draw.
    """
    from urllib import error, request

    target_url = url.strip() if isinstance(url, str) else ""
    if not target_url:
        target_url = DEFAULT_TOTO_NEXT_DRAW_ESTIMATE_URL

    timeout_seconds = _positive_int_from_env("SINGAPOREPOOLS_FETCH_TIMEOUT_SECONDS", DEFAULT_FETCH_TIMEOUT_SECONDS)
    attempts = _positive_int_from_env("SINGAPOREPOOLS_FETCH_ATTEMPTS", DEFAULT_FETCH_ATTEMPTS)
    last_error: BaseException | None = None
    status_code = None

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; prize-alert-telegram/1.0; +https://github.com/yt-codex/prize-alert-telegram)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-SG,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    for attempt_index in range(attempts):
        attempt_url = _with_cache_buster(target_url, attempt_index)
        req = request.Request(attempt_url, headers=headers)
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                status_code = getattr(response, "status", None)
                html = response.read().decode("utf-8", errors="replace")
            if debug:
                print(f"[debug] HTTP status code: {status_code}")
                print(f"[debug] URL: {attempt_url}")
                print(f"[debug] Fetch attempt: {attempt_index + 1}/{attempts}")
            return parse_singaporepools_toto(html, debug=debug)
        except (TimeoutError, OSError, error.URLError) as exc:
            last_error = exc
            if debug:
                print(f"[debug] Fetch attempt {attempt_index + 1}/{attempts} failed: {exc}")
            if attempt_index + 1 < attempts:
                time.sleep(min(2, attempt_index + 1))

    raise ValueError(
        "Failed to fetch Singapore Pools TOTO page "
        f"after {attempts} attempt(s) with {timeout_seconds}s timeout: {last_error}"
    ) from last_error


__all__ = ["fetch_singaporepools_toto_next_draw", "parse_singaporepools_toto"]
