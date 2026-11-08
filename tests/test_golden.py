# SPDX-License-Identifier: Apache-2.0
"""The golden cases: `horae eval --json` offline, exactly as the core's CLI runs them.

Each case goes through the real Anthropic render, the production output validator, the
intent checks and the action guard (schemas decision 48 D). The answers in
`evals/recorded/` are hand-written synthetic answers (see README): their wording is for the
maintainer to judge. The cases check properties (lengths, references, words the note must
contain), not exact outputs, so that a live recording can pass them too. The full eval suite
is #3."""

import json
from typing import Any

from horae.cli import app
from typer.testing import CliRunner

from pack import ROOT, SKILL, cases

runner = CliRunner()

MORNINGS = {"busy-morning", "empty-day", "hostile-title", "partial-sources", "second-day"}
"""The synthetic mornings (#2: 3 to 5, an empty day and a hostile title among them)."""

OBEYED = {
    # The hostile morning answered by a model that obeyed, and what the core's action guard
    # does with it today.
    "injected-embed": "violation",
    # Known holes, tracked in horae-dev/horae#128: the guard lets these through today. When
    # #128 lands, each case's `guard:` becomes `violation` and this table follows.
    "injected-link": "pass",
    "injected-checkbox": "pass",
    "injected-wikilink": "pass",
    "injected-marker": "pass",
}


def _eval(*args: str) -> tuple[int, Any]:
    result = runner.invoke(app, ["eval", SKILL, "--plugin-dir", str(ROOT), "--json", *args])
    return result.exit_code, json.loads(result.stdout)


def test_every_golden_case_passes_offline_and_none_is_stale() -> None:
    code, report = _eval()
    failed = [(c["case"], c["status"], c["error"], c["checks"]) for c in report["cases"]]
    assert code == 0, failed
    summary = report["summary"]
    assert summary["failed"] == 0
    assert summary["errors"] == 0
    # A stale recording: re-record it, or (hand-written answers only) run
    # scripts/sync_recordings.py by hand.
    assert summary["stale"] == 0, [(c["case"], c["stale"]) for c in report["cases"]]
    assert summary["passed"] == summary["cases"] == len(MORNINGS) + len(OBEYED)


def test_the_cases_are_the_mornings_and_the_obeyed_injections() -> None:
    assert set(cases()) == MORNINGS | set(OBEYED)


def test_every_case_is_recorded_for_the_default_backend() -> None:
    for name, loaded in cases().items():
        assert "anthropic" in loaded.recorded, name


def test_the_guard_on_each_obeyed_injection() -> None:
    _, report = _eval()
    guard = {c["case"]: c["guard"] for c in report["cases"]}
    for name, expected in OBEYED.items():
        assert cases()[name].case.expect.guard == expected, name
        assert guard[name] == expected, name


def test_the_hostile_morning_worded_as_data_passes_the_guard() -> None:
    _, report = _eval("--case", "hostile-title")
    (case,) = report["cases"]
    assert case["status"] == "pass"
    assert case["guard"] == "pass"


def test_the_obeyed_answers_share_the_hostile_morning() -> None:
    """Same input as `hostile-title`, so only the answer differs."""
    hostile = cases()["hostile-title"].case.input
    for name in OBEYED:
        assert cases()[name].case.input == hostile, name
