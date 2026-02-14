"""Prize checking entry point."""

from __future__ import annotations

import os
import json
import sys
from pathlib import Path
from typing import Any, Dict

from src.prize_source import fetch_singaporepools_toto_next_draw
from src.telegram import send_telegram_message


DEFAULT_CONFIG_PATH = "config.yaml"
DEFAULT_CURRENCY = "SGD"
DEFAULT_STATE_PATH = ".state/last_alert.json"


def _parse_scalar(value: str) -> Any:
    raw = value.strip()
    if not raw:
        return ""
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def _load_yaml_config(path: str) -> Dict[str, Any]:
    """Load a small YAML subset sufficient for this project's config structure."""
    config_path = Path(path)
    if not config_path.exists():
        raise ValueError(f"Config file not found: {config_path}")

    root: Dict[str, Any] = {}
    stack: list[tuple[int, Dict[str, Any]]] = [(-1, root)]

    lines = config_path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1

        if not line.strip() or line.lstrip().startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if ":" not in stripped:
            raise ValueError(f"Invalid config line: {line}")

        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError(f"Invalid indentation near line: {line}")
        current = stack[-1][1]

        if value == "|":
            block_lines: list[str] = []
            while i < len(lines):
                block_line = lines[i]
                if not block_line.strip():
                    block_lines.append("")
                    i += 1
                    continue
                block_indent = len(block_line) - len(block_line.lstrip(" "))
                if block_indent <= indent:
                    break
                block_lines.append(block_line[indent + 2 :] if len(block_line) >= indent + 2 else "")
                i += 1
            current[key] = "\n".join(block_lines).rstrip("\n")
            continue

        if value == "":
            child: Dict[str, Any] = {}
            current[key] = child
            stack.append((indent, child))
            continue

        current[key] = _parse_scalar(value)

    return root


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _normalize_draw_id(draw_datetime_text: str) -> str:
    return " ".join(draw_datetime_text.split())


def _read_last_alerted_draw_id(state_path: Path) -> str | None:
    if not state_path.exists():
        return None
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    draw_id = payload.get("last_alerted_draw_id")
    return str(draw_id) if draw_id is not None else None


def _write_last_alerted_draw_id(state_path: Path, draw_id: str) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"last_alerted_draw_id": draw_id}, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> int:
    config_path = os.getenv("CONFIG_PATH", DEFAULT_CONFIG_PATH)
    config = _load_yaml_config(config_path)

    threshold_cfg = config.get("threshold", {})
    if not isinstance(threshold_cfg, dict):
        raise ValueError("'threshold' must be a mapping in config.")

    threshold_amount = float(threshold_cfg["amount"])
    currency = str(threshold_cfg.get("currency", DEFAULT_CURRENCY)).strip() or DEFAULT_CURRENCY

    prize_source_cfg = config.get("prize_source", {})
    source_url = prize_source_cfg.get("url") if isinstance(prize_source_cfg, dict) else None

    live = fetch_singaporepools_toto_next_draw(source_url)
    jackpot_estimate = float(live["jackpot_estimate"])
    draw_datetime_text = str(live["draw_datetime_text"])
    draw_id = _normalize_draw_id(draw_datetime_text)
    state_path = Path(os.getenv("STATE_PATH", DEFAULT_STATE_PATH))
    prize_amount_str = format(jackpot_estimate, ",.0f")
    threshold_amount_str = format(threshold_amount, ",.0f")

    if jackpot_estimate <= threshold_amount:
        print(
            "No alert: "
            f"jackpot_estimate={prize_amount_str}, "
            f"threshold_amount={threshold_amount_str}, "
            f"draw_datetime_text={draw_datetime_text}"
        )
        return 0

    last_alerted_draw_id = _read_last_alerted_draw_id(state_path)
    if last_alerted_draw_id == draw_id:
        print("Already alerted for this draw")
        return 0

    alert_cfg = config.get("alert", {})
    if not isinstance(alert_cfg, dict):
        raise ValueError("'alert' must be a mapping in config.")
    message_template = str(alert_cfg.get("message_template", ""))
    if not message_template.strip():
        raise ValueError("Missing 'alert.message_template' in config.")

    message = message_template.format(
        prize_amount=prize_amount_str,
        threshold_amount=threshold_amount_str,
        currency=currency,
        draw_datetime_text=draw_datetime_text,
    )

    if os.getenv("DRY_RUN") == "1":
        print("DRY_RUN enabled; Telegram message not sent.")
        print(message)
        _write_last_alerted_draw_id(state_path, draw_id)
        return 0

    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    chat_id = _require_env("TELEGRAM_CHAT_ID")
    send_telegram_message(bot_token=bot_token, chat_id=chat_id, text=message)
    print("Alert sent.")
    _write_last_alerted_draw_id(state_path, draw_id)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
