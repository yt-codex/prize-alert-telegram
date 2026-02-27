"""Tests for check_prize workflow behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import check_prize


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


def _set_runtime_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    runtime_path = tmp_path / "runtime_report.json"
    monkeypatch.setenv("OPS_RUNTIME_PATH", str(runtime_path))
    monkeypatch.setenv("FRESHNESS_THRESHOLD_SECONDS", "999999999")
    monkeypatch.setenv("STATE_PATH", str(tmp_path / "state" / "last_alert.json"))
    return runtime_path


def test_no_alert_when_threshold_not_exceeded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    config_path = _write_config(tmp_path / "config.yaml")
    runtime_path = _set_runtime_path(monkeypatch, tmp_path)

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
    report = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert report["status"] == "OK"
    assert report["row_counts"]["prices_fetched"] == 1


def test_dry_run_prints_message_without_telegram(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    config_path = _write_config(tmp_path / "config.yaml")
    runtime_path = _set_runtime_path(monkeypatch, tmp_path)

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
    report = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert report["status"] == "OK"
    assert report["row_counts"]["alerts_generated"] == 1



def test_alert_above_threshold_updates_state_and_skips_duplicate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    config_path = _write_config(tmp_path / "config.yaml")
    state_path = tmp_path / "state" / "last_alert.json"
    runtime_path = _set_runtime_path(monkeypatch, tmp_path)

    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("STATE_PATH", str(state_path))
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")

    monkeypatch.setattr(
        check_prize,
        "fetch_singaporepools_toto_next_draw",
        lambda url: {"jackpot_estimate": 1100000.0, "draw_datetime_text": "Mon, 08 Jul 2024, 6:30pm"},
    )

    sent = {"count": 0}

    def _send(**kwargs):
        sent["count"] += 1

    monkeypatch.setattr(check_prize, "send_telegram_message", _send)

    assert check_prize.main() == 0
    assert sent["count"] == 1
    assert state_path.exists()
    assert '"last_alerted_draw_id": "Mon, 08 Jul 2024, 6:30pm"' in state_path.read_text(encoding="utf-8")

    assert check_prize.main() == 0
    assert sent["count"] == 1
    output = capsys.readouterr().out
    assert "Already alerted for this draw" in output
    report = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert report["status"] == "OK"
    assert report["row_counts"]["alerts_sent"] == 0


def test_no_alert_below_threshold_does_not_write_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "config.yaml")
    state_path = tmp_path / "state" / "last_alert.json"
    runtime_path = _set_runtime_path(monkeypatch, tmp_path)

    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("STATE_PATH", str(state_path))
    monkeypatch.delenv("DRY_RUN", raising=False)

    monkeypatch.setattr(
        check_prize,
        "fetch_singaporepools_toto_next_draw",
        lambda url: {"jackpot_estimate": 900000.0, "draw_datetime_text": "Thu, 11 Jul 2024, 6:30pm"},
    )

    monkeypatch.setattr(check_prize, "send_telegram_message", lambda **kwargs: None)

    assert check_prize.main() == 0
    assert not state_path.exists()
    report = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert report["status"] == "OK"
    assert report["row_counts"]["alerts_generated"] == 0


def test_runtime_report_marks_fail_when_fetch_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "config.yaml")
    runtime_path = _set_runtime_path(monkeypatch, tmp_path)

    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.delenv("DRY_RUN", raising=False)

    def _fail_fetch(_url):
        raise ValueError("upstream timeout")

    monkeypatch.setattr(check_prize, "fetch_singaporepools_toto_next_draw", _fail_fetch)
    monkeypatch.setattr(check_prize, "send_telegram_message", lambda **kwargs: None)

    assert check_prize.main() == 1
    report = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert report["row_counts"]["prices_fetched"] == 0
    assert any(item["name"] == "price_fetch_success_rate" and item["status"] == "FAIL" for item in report["key_checks"])
