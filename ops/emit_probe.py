#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ALLOWED_STATUSES = {"OK", "WARN", "FAIL"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


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


def parse_json_arg(value: str | None, default: Any) -> Any:
    if value is None or value == "":
        return default
    text = value.strip()
    if text.startswith("@"):
        path = Path(text[1:])
        if not path.exists():
            return default
        text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def parse_row_counts(values: list[str]) -> dict[str, int | float]:
    result: dict[str, int | float] = {}
    for item in values:
        if "=" not in item:
            continue
        key, raw = item.split("=", 1)
        key = key.strip()
        if not key:
            continue
        if raw.strip().isdigit():
            result[key] = int(raw.strip())
            continue
        try:
            result[key] = float(raw.strip())
        except ValueError:
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


def normalize_checks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
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
    return normalized


def schema_hash(schema_path: str | None, explicit_hash: str | None) -> str | None:
    if explicit_hash:
        return explicit_hash.strip() or None
    if not schema_path:
        return None
    file = Path(schema_path)
    if not file.exists():
        return None
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    return digest


def run_metadata() -> dict[str, Any]:
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else None
    return {
        "repo": repo,
        "run_id": run_id,
        "run_url": run_url,
        "workflow": os.environ.get("GITHUB_WORKFLOW"),
        "job": os.environ.get("GITHUB_JOB"),
        "sha": os.environ.get("GITHUB_SHA"),
    }


def build_probe(args: argparse.Namespace) -> dict[str, Any]:
    current = now_utc()
    end = parse_dt(args.end_time) or current
    last_run = parse_dt(args.last_run_time) or end
    start = parse_dt(args.start_time)

    duration = to_float(args.duration_seconds)
    if duration is None and start is not None:
        duration = max(0.0, (end - start).total_seconds())

    status = str(args.status or "OK").upper().strip()
    if status not in ALLOWED_STATUSES:
        status = "WARN"

    max_date_value = args.max_date
    max_dt = parse_dt(max_date_value)
    lag_seconds = max(0.0, (current - max_dt).total_seconds()) if max_dt else None

    warnings = [item for item in args.warning if item.strip()]
    warnings.extend(parse_json_arg(args.warnings_json, []) if args.warnings_json else [])
    warnings = [str(item).strip() for item in warnings if str(item).strip()]

    checks_json = parse_json_arg(args.key_checks_json, [])
    key_checks = normalize_checks(checks_json)

    artifacts = parse_artifacts(args.artifact)
    artifacts.extend(parse_json_arg(args.artifacts_json, []) if args.artifacts_json else [])
    normalized_artifacts: list[dict[str, str]] = []
    for item in artifacts:
        if isinstance(item, dict):
            label = str(item.get("label", "artifact")).strip() or "artifact"
            url = str(item.get("url", "")).strip()
            if url:
                normalized_artifacts.append({"label": label, "url": url})

    meta = run_metadata()
    if meta.get("run_url") and not any(a["url"] == meta["run_url"] for a in normalized_artifacts):
        normalized_artifacts.append({"label": "workflow_run", "url": meta["run_url"]})

    probe = {
        "schema_version": "1.0",
        "status": status,
        "last_run_time": iso_utc(last_run),
        "duration_seconds": duration,
        "freshness": {
            "max_date": max_date_value,
            "lag_seconds": lag_seconds,
            "stale": None,
        },
        "row_counts": parse_row_counts(args.row_count),
        "schema_hash": schema_hash(args.schema_file, args.schema_hash),
        "key_checks": key_checks,
        "warnings": warnings,
        "artifact_links": normalized_artifacts,
        "meta": meta,
    }
    return probe


def write_probe(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit standardized ops/probe.json.")
    parser.add_argument("--output", default="ops/probe.json")
    parser.add_argument("--status", default="OK")
    parser.add_argument("--last-run-time")
    parser.add_argument("--start-time")
    parser.add_argument("--end-time")
    parser.add_argument("--duration-seconds")
    parser.add_argument("--max-date")
    parser.add_argument("--schema-file")
    parser.add_argument("--schema-hash")
    parser.add_argument("--row-count", action="append", default=[], help="name=value")
    parser.add_argument("--warning", action="append", default=[])
    parser.add_argument("--warnings-json")
    parser.add_argument("--key-checks-json", help="JSON string or @path to JSON file")
    parser.add_argument("--artifact", action="append", default=[], help="label=url")
    parser.add_argument("--artifacts-json")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    output_path = Path(args.output)
    try:
        probe = build_probe(args)
        write_probe(output_path, probe)
        print(f"Wrote probe: {output_path}")
        return 0
    except Exception as exc:
        if args.strict:
            raise
        fallback = {
            "schema_version": "1.0",
            "status": "FAIL",
            "last_run_time": iso_utc(now_utc()),
            "duration_seconds": None,
            "freshness": {"max_date": None, "lag_seconds": None, "stale": None},
            "row_counts": {},
            "schema_hash": None,
            "key_checks": [],
            "warnings": [f"Probe emitter failed: {exc}"],
            "artifact_links": [],
            "meta": run_metadata(),
        }
        run_url = fallback["meta"].get("run_url")
        if run_url:
            fallback["artifact_links"].append({"label": "workflow_run", "url": run_url})
        write_probe(output_path, fallback)
        print(f"Emitter error ignored (non-blocking): {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
