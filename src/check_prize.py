"""Prize checking entry point."""

from __future__ import annotations

import hashlib
import os
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.prize_source import fetch_singaporepools_toto_next_draw
from src.telegram import send_telegram_message


DEFAULT_CONFIG_PATH = "config.yaml"
DEFAULT_CURRENCY = "SGD"
DEFAULT_STATE_PATH = ".state/last_alert.json"
DEFAULT_RUNTIME_REPORT_PATH = ".state/runtime_report.json"
DEFAULT_FRESHNESS_THRESHOLD_SECONDS = 3 * 24 * 60 * 60

STATUS_OK = "OK"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"

CHECK_CONFIG_VALID = "config_valid"
CHECK_PRICE_FETCH_SUCCESS_RATE = "price_fetch_success_rate"
CHECK_FRESHNESS_WITHIN_THRESHOLD = "freshness_within_threshold"
CHECK_RULES_EVALUATED = "rules_evaluated"
CHECK_TELEGRAM_SEND_SUCCESS_RATE = "telegram_send_success_rate"
CHECK_STATE_PERSISTED = "state_persisted"
REQUIRED_CHECKS = [
    CHECK_CONFIG_VALID,
    CHECK_PRICE_FETCH_SUCCESS_RATE,
    CHECK_FRESHNESS_WITHIN_THRESHOLD,
    CHECK_RULES_EVALUATED,
    CHECK_TELEGRAM_SEND_SUCCESS_RATE,
    CHECK_STATE_PERSISTED,
]

BP_CONFIG = "config_load_validation"
BP_FETCH = "market_price_fetch"
BP_RULES = "transformation_signal_evaluation"
BP_STATE = "dedupe_state_read_write"
BP_TELEGRAM = "telegram_send_step"
BP_FINAL = "final_summary_write"

ALERT_PAYLOAD_SCHEMA_SIGNATURE = "telegram_text_v1|prize_amount|threshold_amount|currency|draw_datetime_text"
try:
    SINGAPORE_TIMEZONE = ZoneInfo("Asia/Singapore")
except ZoneInfoNotFoundError:
    SINGAPORE_TIMEZONE = timezone(timedelta(hours=8))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(dt_value: datetime) -> str:
    return dt_value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _status_rank(status: str) -> int:
    normalized = str(status or STATUS_WARN).upper()
    if normalized == STATUS_FAIL:
        return 2
    if normalized == STATUS_WARN:
        return 1
    return 0


def _merge_status(current: str, new_status: str) -> str:
    return new_status if _status_rank(new_status) > _status_rank(current) else current


def _schema_hash() -> str:
    return hashlib.sha256(ALERT_PAYLOAD_SCHEMA_SIGNATURE.encode("utf-8")).hexdigest()


def _new_runtime_report(started_at: datetime) -> dict[str, Any]:
    return {
        "status": STATUS_FAIL,
        "last_run_time": None,
        "duration_seconds": None,
        "freshness": {"max_date": None, "lag_seconds": None},
        "row_counts": {
            "symbols_monitored": 0,
            "prices_fetched": 0,
            "alerts_generated": 0,
            "alerts_sent": 0,
            "alerts_failed": 0,
        },
        "schema_hash": _schema_hash(),
        "key_checks": [
            {"name": name, "status": STATUS_WARN, "detail": "Not evaluated."} for name in REQUIRED_CHECKS
        ],
        "warnings": [],
        "breakpoints": {
            BP_CONFIG: {"status": STATUS_WARN, "detail": "Not started."},
            BP_FETCH: {"status": STATUS_WARN, "detail": "Not started."},
            BP_RULES: {"status": STATUS_WARN, "detail": "Not started."},
            BP_STATE: {"status": STATUS_WARN, "detail": "Not started."},
            BP_TELEGRAM: {"status": STATUS_WARN, "detail": "Not started."},
            BP_FINAL: {"status": STATUS_WARN, "detail": "Not started."},
        },
        "run_started_at": _iso_utc(started_at),
        "run_finished_at": None,
    }


def _add_warning(runtime_report: dict[str, Any], warning: str) -> None:
    text = warning.strip()
    if not text:
        return
    warnings = runtime_report.setdefault("warnings", [])
    if text not in warnings:
        warnings.append(text)


def _set_breakpoint(runtime_report: dict[str, Any], name: str, status: str, detail: str) -> None:
    normalized = str(status or STATUS_WARN).upper()
    if normalized not in {STATUS_OK, STATUS_WARN, STATUS_FAIL}:
        normalized = STATUS_WARN
    runtime_report.setdefault("breakpoints", {})[name] = {"status": normalized, "detail": str(detail).strip()}


def _set_key_check(
    runtime_report: dict[str, Any],
    name: str,
    status: str,
    detail: str,
    metric: float | int | None = None,
) -> None:
    normalized = str(status or STATUS_WARN).upper()
    if normalized not in {STATUS_OK, STATUS_WARN, STATUS_FAIL}:
        normalized = STATUS_WARN

    checks = runtime_report.setdefault("key_checks", [])
    for check in checks:
        if check.get("name") == name:
            check["status"] = normalized
            check["detail"] = str(detail).strip()
            if metric is None:
                check.pop("metric", None)
            else:
                check["metric"] = metric
            return

    payload: dict[str, Any] = {
        "name": name,
        "status": normalized,
        "detail": str(detail).strip(),
    }
    if metric is not None:
        payload["metric"] = metric
    checks.append(payload)


def _draw_datetime_to_utc(draw_datetime_text: str) -> datetime | None:
    compact = re.sub(r"\s+", " ", draw_datetime_text.strip())
    if not compact:
        return None
    compact = compact.replace(" ,", ",").replace(".", ":")

    patterns = [
        "%a, %d %b %Y, %I:%M%p",
        "%a, %d %b %Y,%I:%M%p",
        "%d %b %Y, %I:%M%p",
        "%d %b %Y,%I:%M%p",
    ]
    for pattern in patterns:
        try:
            parsed = datetime.strptime(compact, pattern)
        except ValueError:
            continue
        return parsed.replace(tzinfo=SINGAPORE_TIMEZONE).astimezone(timezone.utc)

    date_match = re.search(r"\d{1,2}\s+[A-Za-z]{3}\s+\d{4}", compact)
    if not date_match:
        return None
    try:
        parsed_date = datetime.strptime(date_match.group(0), "%d %b %Y")
    except ValueError:
        return None
    return parsed_date.replace(tzinfo=timezone.utc)


def _freshness_threshold_seconds() -> float:
    raw = os.getenv("FRESHNESS_THRESHOLD_SECONDS", str(DEFAULT_FRESHNESS_THRESHOLD_SECONDS))
    try:
        threshold = float(raw)
    except ValueError:
        return float(DEFAULT_FRESHNESS_THRESHOLD_SECONDS)
    return threshold if threshold >= 0 else float(DEFAULT_FRESHNESS_THRESHOLD_SECONDS)


def _evaluate_freshness(runtime_report: dict[str, Any], draw_datetime_text: str) -> None:
    draw_utc = _draw_datetime_to_utc(draw_datetime_text)
    freshness = runtime_report.setdefault("freshness", {})

    if draw_utc is None:
        freshness["max_date"] = None
        freshness["lag_seconds"] = None
        _set_key_check(
            runtime_report,
            CHECK_FRESHNESS_WITHIN_THRESHOLD,
            STATUS_WARN,
            "Unable to parse source draw date/time for freshness validation.",
        )
        _add_warning(runtime_report, "Freshness check skipped because draw date parsing failed.")
        return

    lag_seconds = max(0.0, (_utc_now() - draw_utc).total_seconds())
    lag_seconds = round(lag_seconds, 3)
    threshold = _freshness_threshold_seconds()
    freshness["max_date"] = _iso_utc(draw_utc)
    freshness["lag_seconds"] = lag_seconds

    if lag_seconds <= threshold:
        _set_key_check(
            runtime_report,
            CHECK_FRESHNESS_WITHIN_THRESHOLD,
            STATUS_OK,
            f"Freshness lag {lag_seconds:.1f}s is within threshold {threshold:.1f}s.",
            metric=lag_seconds,
        )
        return

    _set_key_check(
        runtime_report,
        CHECK_FRESHNESS_WITHIN_THRESHOLD,
        STATUS_WARN,
        f"Freshness lag {lag_seconds:.1f}s exceeds threshold {threshold:.1f}s.",
        metric=lag_seconds,
    )
    _add_warning(runtime_report, "Source data appears stale; investigate upstream freshness.")


def _write_runtime_report(path: Path, runtime_report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(runtime_report, indent=2) + "\n", encoding="utf-8")


def _finalize_runtime_report(
    runtime_report: dict[str, Any],
    started_at: datetime,
    finished_at: datetime,
    exit_code: int,
) -> None:
    runtime_report["run_finished_at"] = _iso_utc(finished_at)
    runtime_report["last_run_time"] = _iso_utc(finished_at)
    runtime_report["duration_seconds"] = round(max(0.0, (finished_at - started_at).total_seconds()), 3)

    status = STATUS_OK
    for check in runtime_report.get("key_checks", []):
        check_status = str(check.get("status", STATUS_WARN)).upper()
        if check_status == STATUS_FAIL:
            status = STATUS_FAIL
            break
        if check_status == STATUS_WARN:
            status = _merge_status(status, STATUS_WARN)

    if exit_code != 0:
        status = STATUS_FAIL
    elif runtime_report.get("warnings"):
        status = _merge_status(status, STATUS_WARN)

    runtime_report["status"] = status


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


def _run_pipeline(runtime_report: dict[str, Any]) -> int:
    config_path = os.getenv("CONFIG_PATH", DEFAULT_CONFIG_PATH)
    runtime_report["row_counts"]["symbols_monitored"] = 1

    try:
        config = _load_yaml_config(config_path)

        threshold_cfg = config.get("threshold", {})
        if not isinstance(threshold_cfg, dict):
            raise ValueError("'threshold' must be a mapping in config.")
        threshold_amount = float(threshold_cfg["amount"])
        currency = str(threshold_cfg.get("currency", DEFAULT_CURRENCY)).strip() or DEFAULT_CURRENCY

        alert_cfg = config.get("alert", {})
        if not isinstance(alert_cfg, dict):
            raise ValueError("'alert' must be a mapping in config.")
        message_template = str(alert_cfg.get("message_template", ""))
        if not message_template.strip():
            raise ValueError("Missing 'alert.message_template' in config.")

        _set_key_check(
            runtime_report,
            CHECK_CONFIG_VALID,
            STATUS_OK,
            f"Config loaded and validated from {config_path}.",
            metric=1.0,
        )
        _set_breakpoint(runtime_report, BP_CONFIG, STATUS_OK, f"Config loaded from {config_path}.")
    except Exception as exc:
        detail = f"Config load/validation failed: {exc}"
        _set_key_check(runtime_report, CHECK_CONFIG_VALID, STATUS_FAIL, detail, metric=0.0)
        _set_breakpoint(runtime_report, BP_CONFIG, STATUS_FAIL, detail)
        raise

    prize_source_cfg = config.get("prize_source", {})
    source_url = prize_source_cfg.get("url") if isinstance(prize_source_cfg, dict) else None
    try:
        live = fetch_singaporepools_toto_next_draw(source_url)
        runtime_report["row_counts"]["prices_fetched"] = 1
        _set_key_check(
            runtime_report,
            CHECK_PRICE_FETCH_SUCCESS_RATE,
            STATUS_OK,
            "Fetched market price data for 1/1 monitored symbol.",
            metric=1.0,
        )
        _set_breakpoint(runtime_report, BP_FETCH, STATUS_OK, "Fetched jackpot estimate from source.")
    except Exception as exc:
        detail = f"Market price fetch failed: {exc}"
        runtime_report["row_counts"]["prices_fetched"] = 0
        _set_key_check(runtime_report, CHECK_PRICE_FETCH_SUCCESS_RATE, STATUS_FAIL, detail, metric=0.0)
        _set_breakpoint(runtime_report, BP_FETCH, STATUS_FAIL, detail)
        raise

    try:
        jackpot_estimate = float(live["jackpot_estimate"])
        draw_datetime_text = str(live["draw_datetime_text"])
        draw_id = _normalize_draw_id(draw_datetime_text)
    except Exception as exc:
        detail = f"Signal evaluation failed: {exc}"
        _set_key_check(runtime_report, CHECK_RULES_EVALUATED, STATUS_FAIL, detail, metric=0.0)
        _set_breakpoint(runtime_report, BP_RULES, STATUS_FAIL, detail)
        raise ValueError(detail) from exc

    _evaluate_freshness(runtime_report, draw_datetime_text)
    state_path = Path(os.getenv("STATE_PATH", DEFAULT_STATE_PATH))

    signal_triggered = jackpot_estimate > threshold_amount
    runtime_report["row_counts"]["alerts_generated"] = 1 if signal_triggered else 0
    _set_key_check(
        runtime_report,
        CHECK_RULES_EVALUATED,
        STATUS_OK,
        "Signal evaluation completed for threshold rule.",
        metric=1.0,
    )
    _set_breakpoint(
        runtime_report,
        BP_RULES,
        STATUS_OK,
        f"Signal evaluated: jackpot_estimate={jackpot_estimate:.2f}, threshold_amount={threshold_amount:.2f}, triggered={signal_triggered}.",
    )

    prize_amount_str = format(jackpot_estimate, ",.0f")
    threshold_amount_str = format(threshold_amount, ",.0f")

    if jackpot_estimate <= threshold_amount:
        print(
            "No alert: "
            f"jackpot_estimate={prize_amount_str}, "
            f"threshold_amount={threshold_amount_str}, "
            f"draw_datetime_text={draw_datetime_text}"
        )
        _set_key_check(
            runtime_report,
            CHECK_TELEGRAM_SEND_SUCCESS_RATE,
            STATUS_OK,
            "No alert generated; Telegram send not required.",
            metric=1.0,
        )
        _set_key_check(
            runtime_report,
            CHECK_STATE_PERSISTED,
            STATUS_OK,
            "No state write required because signal did not trigger.",
            metric=1.0,
        )
        _set_breakpoint(runtime_report, BP_STATE, STATUS_OK, "State unchanged because signal did not trigger.")
        _set_breakpoint(runtime_report, BP_TELEGRAM, STATUS_OK, "Telegram send skipped because signal did not trigger.")
        return 0

    try:
        last_alerted_draw_id = _read_last_alerted_draw_id(state_path)
        _set_breakpoint(runtime_report, BP_STATE, STATUS_OK, "State read completed for dedupe check.")
    except Exception as exc:
        detail = f"State read failed: {exc}"
        _set_key_check(runtime_report, CHECK_STATE_PERSISTED, STATUS_FAIL, detail, metric=0.0)
        _set_breakpoint(runtime_report, BP_STATE, STATUS_FAIL, detail)
        raise ValueError(detail) from exc

    if last_alerted_draw_id == draw_id:
        print("Already alerted for this draw")
        _set_key_check(
            runtime_report,
            CHECK_TELEGRAM_SEND_SUCCESS_RATE,
            STATUS_OK,
            "Duplicate draw detected; Telegram send skipped.",
            metric=1.0,
        )
        _set_key_check(
            runtime_report,
            CHECK_STATE_PERSISTED,
            STATUS_OK,
            "State already contains the current draw id.",
            metric=1.0,
        )
        _set_breakpoint(runtime_report, BP_STATE, STATUS_OK, "Duplicate draw found in state; no write required.")
        _set_breakpoint(runtime_report, BP_TELEGRAM, STATUS_OK, "Telegram send skipped because draw was already alerted.")
        return 0

    alert_cfg = config.get("alert", {})
    message_template = str(alert_cfg.get("message_template", ""))

    try:
        message = message_template.format(
            prize_amount=prize_amount_str,
            threshold_amount=threshold_amount_str,
            currency=currency,
            draw_datetime_text=draw_datetime_text,
        )
    except Exception as exc:
        detail = f"Signal transformation failed while rendering alert template: {exc}"
        _set_key_check(runtime_report, CHECK_RULES_EVALUATED, STATUS_FAIL, detail, metric=0.0)
        _set_breakpoint(runtime_report, BP_RULES, STATUS_FAIL, detail)
        raise ValueError(detail) from exc

    if os.getenv("DRY_RUN") == "1":
        print("DRY_RUN enabled; Telegram message not sent.")
        print(message)
        _set_key_check(
            runtime_report,
            CHECK_TELEGRAM_SEND_SUCCESS_RATE,
            STATUS_OK,
            "DRY_RUN enabled; Telegram request intentionally skipped.",
            metric=1.0,
        )
        _set_breakpoint(runtime_report, BP_TELEGRAM, STATUS_OK, "DRY_RUN enabled; Telegram send skipped.")
        try:
            _write_last_alerted_draw_id(state_path, draw_id)
        except Exception as exc:
            detail = f"State write failed: {exc}"
            _set_key_check(runtime_report, CHECK_STATE_PERSISTED, STATUS_FAIL, detail, metric=0.0)
            _set_breakpoint(runtime_report, BP_STATE, STATUS_FAIL, detail)
            raise ValueError(detail) from exc
        _set_key_check(
            runtime_report,
            CHECK_STATE_PERSISTED,
            STATUS_OK,
            "State updated after DRY_RUN signal.",
            metric=1.0,
        )
        _set_breakpoint(runtime_report, BP_STATE, STATUS_OK, "State file updated with latest draw id.")
        return 0

    try:
        bot_token = _require_env("TELEGRAM_BOT_TOKEN")
        chat_id = _require_env("TELEGRAM_CHAT_ID")
        send_telegram_message(bot_token=bot_token, chat_id=chat_id, text=message)
    except Exception as exc:
        detail = f"Telegram send failed: {exc}"
        runtime_report["row_counts"]["alerts_failed"] = 1
        _set_key_check(
            runtime_report,
            CHECK_TELEGRAM_SEND_SUCCESS_RATE,
            STATUS_FAIL,
            detail,
            metric=0.0,
        )
        _set_key_check(
            runtime_report,
            CHECK_STATE_PERSISTED,
            STATUS_WARN,
            "State persistence skipped because Telegram delivery failed.",
            metric=0.0,
        )
        _set_breakpoint(runtime_report, BP_TELEGRAM, STATUS_FAIL, detail)
        raise ValueError(detail) from exc

    print("Alert sent.")
    runtime_report["row_counts"]["alerts_sent"] = 1
    _set_key_check(
        runtime_report,
        CHECK_TELEGRAM_SEND_SUCCESS_RATE,
        STATUS_OK,
        "Telegram alert delivered.",
        metric=1.0,
    )
    _set_breakpoint(runtime_report, BP_TELEGRAM, STATUS_OK, "Telegram alert sent successfully.")

    try:
        _write_last_alerted_draw_id(state_path, draw_id)
    except Exception as exc:
        detail = f"State write failed: {exc}"
        _set_key_check(runtime_report, CHECK_STATE_PERSISTED, STATUS_FAIL, detail, metric=0.0)
        _set_breakpoint(runtime_report, BP_STATE, STATUS_FAIL, detail)
        raise ValueError(detail) from exc

    _set_key_check(
        runtime_report,
        CHECK_STATE_PERSISTED,
        STATUS_OK,
        "State persisted with latest alerted draw id.",
        metric=1.0,
    )
    _set_breakpoint(runtime_report, BP_STATE, STATUS_OK, "State file updated with latest draw id.")
    return 0


def main() -> int:
    started_at = _utc_now()
    runtime_report = _new_runtime_report(started_at)
    exit_code = 1
    runtime_report_path = Path(os.getenv("OPS_RUNTIME_PATH", DEFAULT_RUNTIME_REPORT_PATH))

    try:
        exit_code = _run_pipeline(runtime_report)
    except Exception as exc:
        _add_warning(runtime_report, str(exc))
        print(f"Error: {exc}", file=sys.stderr)
        exit_code = 1
    finally:
        finished_at = _utc_now()
        _finalize_runtime_report(runtime_report, started_at, finished_at, exit_code)
        _set_breakpoint(runtime_report, BP_FINAL, STATUS_OK, f"Runtime report prepared at {runtime_report_path}.")
        try:
            _write_runtime_report(runtime_report_path, runtime_report)
        except Exception as exc:
            print(f"Warning: failed to write runtime report: {exc}", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
