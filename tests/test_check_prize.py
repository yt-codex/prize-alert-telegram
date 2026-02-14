"""Tests for check_prize workflow behavior."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import check_prize


BASE_CONFIG = """
threshold:
  amount: 1000000
  currency: "SGD"
prize_source:
  url: "https://example.invalid/toto"
alert:
  message_template: |
    Jackpot {prize_amount} {currency}
    Threshold {threshold_amount} {currency}
    Draw {draw_datetime_text}
"""


def _write_config(path: Path, content: str = BASE_CONFIG) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_no_alert_when_threshold_not_exceeded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    config_path = _write_config(tmp_path / "config.yaml")

    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.delenv("DRY_RUN", raising=False)

    monkeypatch.setattr(
        check_prize,
        "fetch_singaporepools_toto_next_draw",
        lambda url: {"jackpot_estimate": 900000.0, "draw_datetime_text": "Thu, 11 Jul 2024, 6:30pm"},
    )

    called = {"sent": False}

    def _fail_send(**kwargs):
        called["sent"] = True

    monkeypatch.setattr(check_prize, "send_telegram_message", _fail_send)

    assert check_prize.main() == 0
    assert called["sent"] is False
    output = capsys.readouterr().out
    assert "No alert:" in output
    assert "draw_datetime_text=Thu, 11 Jul 2024, 6:30pm" in output


def test_dry_run_prints_message_without_telegram(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    config_path = _write_config(tmp_path / "config.yaml")

    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("DRY_RUN", "1")

    monkeypatch.setattr(
        check_prize,
        "fetch_singaporepools_toto_next_draw",
        lambda url: {"jackpot_estimate": 1100000.0, "draw_datetime_text": "Mon, 08 Jul 2024, 6:30pm"},
    )

    called = {"sent": False}

    def _fail_send(**kwargs):
        called["sent"] = True

    monkeypatch.setattr(check_prize, "send_telegram_message", _fail_send)

    assert check_prize.main() == 0
    assert called["sent"] is False

    output = capsys.readouterr().out
    assert "DRY_RUN enabled; Telegram message not sent." in output
    assert "Draw Mon, 08 Jul 2024, 6:30pm" in output
