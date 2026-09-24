"""Validate the GitHub Actions export workflow parses and is wired correctly."""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "export.yml"


def _load():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers(data):
    # PyYAML parses the bare key ``on`` as the boolean True (YAML 1.1), so the
    # triggers live under either key depending on quoting.
    return data.get("on", data.get(True))


def test_workflow_yaml_parses():
    data = _load()
    assert isinstance(data, dict)
    assert "jobs" in data and "export" in data["jobs"]


def test_workflow_has_nightly_schedule():
    triggers = _triggers(_load())
    assert "schedule" in triggers
    crons = [c["cron"] for c in triggers["schedule"]]
    assert "0 13 * * *" in crons


def test_workflow_keeps_manual_dispatch_inputs():
    triggers = _triggers(_load())
    inputs = triggers["workflow_dispatch"]["inputs"]
    for name in ("mode", "format", "since", "sub_sport", "tz", "upload", "weight_lb"):
        assert name in inputs
    assert inputs["tz"]["default"] == "America/Los_Angeles"


def test_export_steps_run_on_schedule():
    # Scheduled runs have no ``mode`` input, so export steps must gate on
    # ``mode != 'diagnose'`` (not ``== 'export'``) to fire on a schedule.
    steps = _load()["jobs"]["export"]["steps"]
    by_name = {s.get("name"): s for s in steps}
    for name in ("Restore export ledger", "Export sessions", "Upload activity files"):
        assert "diagnose" in by_name[name]["if"] and "!=" in by_name[name]["if"]


def test_export_step_env_defaults_and_since_floor():
    steps = _load()["jobs"]["export"]["steps"]
    export = next(s for s in steps if s.get("name") == "Export sessions")
    env = export["env"]
    assert env["TZ_NAME"] == "${{ inputs.tz || 'America/Los_Angeles' }}"
    assert env["FORMAT"] == "${{ inputs.format || 'fit' }}"
    assert env["SUB_SPORT"] == "${{ inputs.sub_sport || 'bouldering' }}"
    # Upload on every scheduled run.
    assert "github.event_name == 'schedule'" in env["UPLOAD"]
    # since floor defaults to the repo variable else 2026-09-24.
    assert env["UPLOAD_SINCE"] == "${{ vars.UPLOAD_SINCE || '2026-09-24' }}"
    # The run script applies the floor when no explicit since is given.
    assert 'SINCE="${SINCE_INPUT:-$UPLOAD_SINCE}"' in export["run"]


def test_ledger_cache_key_is_stable_not_run_id():
    steps = _load()["jobs"]["export"]["steps"]
    cache = next(s for s in steps if s.get("name") == "Restore export ledger")
    key = cache["with"]["key"]
    assert "github.run_id" not in key
    assert "kilter-ledger-" in key
    assert "kilter-ledger-" in cache["with"]["restore-keys"]
