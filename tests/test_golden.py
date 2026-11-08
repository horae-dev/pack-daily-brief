# SPDX-License-Identifier: Apache-2.0
"""The golden cases: `horae eval` offline, exactly as the core's CLI runs them.

Each case goes through the real Anthropic render, the production output validator, the
intent checks and the action guard (schemas decision 48 D). The answers in
`evals/recorded/` are hand-written synthetic answers (see README): their wording is for the
maintainer to judge; these tests only prove they are schema-valid, pass their case and are
not stale. The full eval suite is #3."""

import asyncio
import json
from typing import Any

import pytest
from horae.cli import app
from horae.llm.validate import validate_output
from typer.testing import CliRunner

from pack import ROOT, SKILL, cases, skill
from sync_recordings import sync

runner = CliRunner()


def _eval(*args: str) -> tuple[int, Any]:
    result = runner.invoke(app, ["eval", SKILL, "--plugin-dir", str(ROOT), "--json", *args])
    return result.exit_code, json.loads(result.stdout)


def test_every_golden_case_passes_offline_and_none_is_stale() -> None:
    code, report = _eval()
    failed = [(c["case"], c["status"], c["error"], c["checks"]) for c in report["cases"]]
    assert code == 0, failed
    assert report["summary"]["failed"] == 0
    assert report["summary"]["errors"] == 0
    assert report["summary"]["stale"] == 0, "run scripts/sync_recordings.py (synthetic only)"
    assert report["summary"]["passed"] == report["summary"]["cases"] >= 3


def test_the_golden_mornings_cover_the_issue() -> None:
    """3 to 5 synthetic mornings, an empty day and a hostile title among them (#2)."""
    names = set(cases())
    assert 3 <= len(names) <= 5
    assert {"busy-morning", "empty-day", "hostile-title", "injected-embed"} <= names


@pytest.mark.parametrize("name", sorted(cases()))
def test_every_recorded_output_is_schema_valid(name: str) -> None:
    """With the production validator, intents included (the guard is a separate matter:
    `injected-embed` is schema-valid and refused by the guard)."""
    schema = skill().output_schema
    for backend, recorded in cases()[name].recorded.items():
        errors = asyncio.run(validate_output(recorded.output, schema))
        assert errors == (), f"{name} [{backend}]: {errors}"


def test_every_case_is_recorded_for_the_default_backend() -> None:
    for name, loaded in cases().items():
        assert "anthropic" in loaded.recorded, name


def test_the_hostile_morning_is_refused_when_the_model_obeys() -> None:
    code, report = _eval("--case", "injected-embed")
    assert code == 0
    (case,) = report["cases"]
    assert case["schema_valid"] is True
    assert case["guard"] == "violation"


def test_the_hostile_morning_passes_the_guard_when_worded_as_data() -> None:
    code, report = _eval("--case", "hostile-title")
    assert code == 0
    (case,) = report["cases"]
    assert case["guard"] == "pass"
    content = cases()["hostile-title"].recorded["anthropic"].output["intents"][0]["content"]
    for leaked in ("horae:", "http", "![", "[[", "- [ ]", "📅", "<", "SYSTEM", "IGNORE"):
        assert leaked not in content


def test_no_recording_is_stale() -> None:
    assert sync(check=True) == []
