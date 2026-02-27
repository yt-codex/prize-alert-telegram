#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ALLOWED_STATUSES = {"OK", "WARN", "FAIL"}
REQUIRED_CHECKS = [
    "config_valid",
    "price_fetch_success_rate",
    "freshness_within_threshold",
    "rules_evaluated",
    "telegram_send_success_rate",
    "state_persisted",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            parsed = date.fromisoformat(text)
            return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def status_rank(status: str) -> int:
    normalized = str(status or "WARN").upper()
    if normalized == "FAIL":
        return 2
    if normalized == "WARN":
        return 1
    return 0


def merge_status(current: str, new_status: str) -> str:
    return new_status if status_rank(new_status) > status_rank(current) else current


def normalize_status(value: Any, default: str = "WARN") -> str:
    normalized = str(value or default).upper().strip()
    return normalized if normalized in ALLOWED_STATUSES else default


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in runtime report: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Runtime report root must be a JSON object.")
    return payload


def normalize_row_counts(value: Any) -> dict[str, int | float]:
    result: dict[str, int | float] = {}
    if not isinstance(value, dict):
        return result
    for key, raw in value.items():
        name = str(key).strip()
        if not name:
            continue
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int):
            result[name] = raw
            continue
        if isinstance(raw, float):
            result[name] = raw
            continue
        try:
            result[name] = float(raw)
        except (TypeError, ValueError):
            continue
    return result


def parse_artifacts(values: list[str]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in values:
        if "=" in item:
            label, url = item.split("=", 1)
            label = label.strip() or "artifact"
            url = url.strip()
        else:
            label = "artifact"
            url = item.strip()
        if url:
            result.append({"label": label, "url": url})
    return result


def normalize_artifacts(value: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if not isinstance(value, list):
        return result
    for item in value:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "artifact")).strip() or "artifact"
        url = str(item.get("url", "")).strip()
        if url:
            result.append({"label": label, "url": url})
    return result


def normalize_checks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        value = []

    normalized: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status", "WARN")).upper()
        if status not in ALLOWED_STATUSES:
            status = "WARN"
        normalized.append(
            {
                "name": str(row.get("name", "check")),
                "status": status,
                "detail": str(row.get("detail", "")),
                **({"metric": row["metric"]} if "metric" in row else {}),
            }
        )

    known_names = {item["name"] for item in normalized}
    for required in REQUIRED_CHECKS:
        if required in known_names:
            continue
        normalized.append(
            {
                "name": required,
                "status": "WARN",
                "detail": "Missing from runtime report.",
            }
        )

    return normalized


def run_metadata() -> dict[str, Any]:
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else None
    return {
        "run_id": run_id,
        "run_url": run_url,
        "workflow": os.environ.get("GITHUB_WORKFLOW"),
        "job": os.environ.get("GITHUB_JOB"),
        "sha": os.environ.get("GITHUB_SHA"),
    }


def build_probe(
    runtime_report: dict[str, Any],
    args: argparse.Namespace,
    runtime_report_warning: str | None,
) -> dict[str, Any]:
    current = now_utc()
    end = parse_dt(args.end_time) or current
    last_run = parse_dt(str(runtime_report.get("last_run_time", "")).strip()) or end
    start = parse_dt(args.start_time)

    duration = to_float(args.duration_seconds)
    if duration is None:
        duration = to_float(runtime_report.get("duration_seconds"))
    if duration is None and start is not None:
        duration = round(max(0.0, (end - start).total_seconds()), 3)

    status = normalize_status(runtime_report.get("status"), default="OK")
    workload_outcome = str(args.workload_outcome or "").strip().lower()
    if workload_outcome in {"failure", "cancelled", "timed_out"}:
        status = merge_status(status, "FAIL")
    elif workload_outcome and workload_outcome != "success":
        status = merge_status(status, "WARN")

    freshness = runtime_report.get("freshness", {})
    max_date_value = freshness.get("max_date") if isinstance(freshness, dict) else None
    lag_seconds = to_float(freshness.get("lag_seconds")) if isinstance(freshness, dict) else None
    if lag_seconds is None:
        max_dt = parse_dt(str(max_date_value)) if max_date_value else None
        if max_dt:
            lag_seconds = round(max(0.0, (current - max_dt).total_seconds()), 3)

    warnings = [str(item).strip() for item in runtime_report.get("warnings", []) if str(item).strip()]
    warnings.extend([item.strip() for item in args.warning if item.strip()])
    if runtime_report_warning:
        warnings.append(runtime_report_warning)
    if workload_outcome and workload_outcome != "success":
        warnings.append(f"workload outcome: {workload_outcome}")
    warnings = list(dict.fromkeys(warnings))

    key_checks = normalize_checks(runtime_report.get("key_checks", []))
    if any(check["status"] == "FAIL" for check in key_checks):
        status = "FAIL"
    elif any(check["status"] == "WARN" for check in key_checks):
        status = merge_status(status, "WARN")
    if warnings and status == "OK":
        status = "WARN"

    normalized_artifacts = normalize_artifacts(runtime_report.get("artifact_links", []))
    normalized_artifacts.extend(parse_artifacts(args.artifact))

    meta = run_metadata()
    if meta.get("run_url") and not any(a["url"] == meta["run_url"] for a in normalized_artifacts):
        normalized_artifacts.append({"label": "workflow_run", "url": meta["run_url"]})

    schema_hash = runtime_report.get("schema_hash")
    schema_hash = str(schema_hash).strip() if schema_hash is not None else None
    schema_hash = schema_hash or None

    probe = {
        "status": status,
        "last_run_time": iso_utc(last_run),
        "duration_seconds": duration,
        "freshness": {
            "max_date": max_date_value,
            "lag_seconds": lag_seconds,
        },
        "row_counts": normalize_row_counts(runtime_report.get("row_counts", {})),
        "schema_hash": schema_hash,
        "key_checks": key_checks,
        "warnings": warnings,
        "artifact_links": normalized_artifacts,
        "meta": meta,
    }
    return probe


def write_probe(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def fallback_probe(warning: str, end_time: datetime) -> dict[str, Any]:
    meta = run_metadata()
    artifacts = []
    if meta.get("run_url"):
        artifacts.append({"label": "workflow_run", "url": meta["run_url"]})
    return {
        "status": "FAIL",
        "last_run_time": iso_utc(end_time),
        "duration_seconds": None,
        "freshness": {"max_date": None, "lag_seconds": None},
        "row_counts": {},
        "schema_hash": None,
        "key_checks": normalize_checks([]),
        "warnings": [warning],
        "artifact_links": artifacts,
        "meta": meta,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit standardized ops/probe.json.")
    parser.add_argument("--output", default="ops/probe.json")
    parser.add_argument("--runtime-report", default=".state/runtime_report.json")
    parser.add_argument("--workload-outcome", default="")
    parser.add_argument("--start-time")
    parser.add_argument("--end-time")
    parser.add_argument("--duration-seconds")
    parser.add_argument("--warning", action="append", default=[])
    parser.add_argument("--artifact", action="append", default=[], help="label=url")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    output_path = Path(args.output)
    try:
        runtime_path = Path(args.runtime_report)
        runtime_report_warning: str | None = None
        runtime_report: dict[str, Any] = {}
        if runtime_path.exists():
            runtime_report = load_json_file(runtime_path)
        else:
            runtime_report_warning = f"Runtime report missing at {runtime_path}."

        probe = build_probe(runtime_report, args, runtime_report_warning)
        write_probe(output_path, probe)
        print(f"Wrote probe: {output_path}")
        return 0
    except Exception as exc:
        if args.strict:
            raise
        fallback = fallback_probe(f"Probe emitter failed: {exc}", now_utc())
        write_probe(output_path, fallback)
        print(f"Emitter error ignored (non-blocking): {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
