# SPDX-License-Identifier: Apache-2.0
"""The golden cases: `horae eval --json` offline, exactly as the core's CLI runs them.

Each case goes through the real Anthropic render, the production output validator, the
intent checks and the action guard (schemas decision 48 D). The answers in
`evals/recorded/` are hand-written synthetic answers (see README): their wording is for the
maintainer to judge. The cases check properties (lengths, references, words the note must
contain), not exact outputs, so that a live recording can pass them too. The live eval of a
release is `.github/workflows/release-evals.yml`."""

import json
from typing import Any

from horae.cli import app
from typer.testing import CliRunner

from pack import ROOT, SKILL, cases, read_yaml

runner = CliRunner()

MORNINGS = {
    "busy-morning",
    "empty-day",
    "hostile-title",
    "long-agenda",
    "non-english-titles",
    "partial-sources",
    "second-day",
    "snoozed-and-cut",
}
"""The synthetic mornings (#2: 3 to 5, an empty day and a hostile title among them; #3: a
day with more items than the note can list, and titles that are not English)."""

OBEYED = {
    # The hostile morning answered by a model that obeyed, and what the core's action guard
    # does with it (the note guard, decision 55, and `links: none` on the skill).
    "injected-embed": "violation",
    "injected-link": "violation",
    "injected-checkbox": "violation",
    "injected-wikilink": "violation",
    "injected-marker": "violation",
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


def test_a_long_agenda_is_cut_to_the_output_bounds() -> None:
    """More is listable than the schema allows: the recorded answer sits at both bounds."""
    output = cases()["long-agenda"].recorded["anthropic"].output
    assert len(output["ranked"]) == 20
    assert len(output["intents"][0]["content"]) <= 2500
    raw = read_yaml(ROOT / "skills" / SKILL / "evals" / "long-agenda.yaml")
    listable = sum(
        len(entry["data"].get(key, []))
        for entry in raw["input"]
        for key in ("events", "items", "tasks")
    )
    assert listable > 20


def test_the_live_eval_skips_the_cases_a_live_model_is_not_expected_to_pass() -> None:
    """scripts/live-eval.sh sends the cases whose expected guard result is `pass`."""
    _, report = _eval()
    live = {c["case"] for c in report["cases"] if c["guard"] == "pass"}
    assert live == MORNINGS
