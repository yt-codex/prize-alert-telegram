"""Tests for ops probe emission contract."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EMIT_PROBE_PATH = REPO_ROOT / "ops" / "emit_probe.py"


def _load_emit_probe_module():
    spec = importlib.util.spec_from_file_location("emit_probe", EMIT_PROBE_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _args(**overrides):
    payload = {
        "workload_outcome": "success",
        "start_time": None,
        "end_time": "2026-02-27T12:00:00Z",
        "duration_seconds": "10",
        "warning": [],
        "artifact": [],
    }
    payload.update(overrides)
    return argparse.Namespace(**payload)


def _runtime_report():
    return {
        "status": "OK",
        "last_run_time": "2026-02-27T11:59:50Z",
        "duration_seconds": 10.0,
        "freshness": {"max_date": "2026-02-27T11:00:00Z", "lag_seconds": 3600.0},
        "row_counts": {
            "symbols_monitored": 1,
            "prices_fetched": 1,
            "alerts_generated": 0,
            "alerts_sent": 0,
            "alerts_failed": 0,
        },
        "schema_hash": "abc123",
        "key_checks": [
            {"name": "config_valid", "status": "OK", "detail": "ok"},
            {"name": "price_fetch_success_rate", "status": "OK", "detail": "ok"},
            {"name": "freshness_within_threshold", "status": "OK", "detail": "ok"},
            {"name": "rules_evaluated", "status": "OK", "detail": "ok"},
            {"name": "telegram_send_success_rate", "status": "OK", "detail": "ok"},
            {"name": "state_persisted", "status": "OK", "detail": "ok"},
        ],
        "warnings": [],
    }


def test_build_probe_contract_ok(monkeypatch):
    emit_probe = _load_emit_probe_module()
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_SHA", "deadbeef")
    monkeypatch.setenv("GITHUB_WORKFLOW", "Scheduled Ops Probe")
    monkeypatch.setenv("GITHUB_JOB", "run-and-emit-probe")

    probe = emit_probe.build_probe(_runtime_report(), _args(), None)

    assert probe["status"] == "OK"
    assert probe["last_run_time"] == "2026-02-27T11:59:50Z"
    assert probe["freshness"]["max_date"] == "2026-02-27T11:00:00Z"
    assert probe["meta"]["run_id"] == "123"
    assert probe["meta"]["run_url"] == "https://github.com/owner/repo/actions/runs/123"
    assert any(item["label"] == "workflow_run" for item in probe["artifact_links"])


def test_build_probe_escalates_fail_on_workload_failure():
    emit_probe = _load_emit_probe_module()
    probe = emit_probe.build_probe(
        _runtime_report(),
        _args(workload_outcome="failure"),
        None,
    )
    assert probe["status"] == "FAIL"
    assert any("workload outcome: failure" in warning for warning in probe["warnings"])
