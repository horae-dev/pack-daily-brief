# SPDX-License-Identifier: Apache-2.0
"""The workflows' safety rules: what runs on a pull request and what can spend money.

Plain YAML checks, no GitHub: the live eval spends money and holds a model key, so it must run
only on a release tag, behind the `evals` environment, with the one write permission on a job
that has no key, and every action pinned to a commit."""

import re
from typing import Any

from pack import ROOT, read_yaml

WORKFLOWS = ROOT / ".github" / "workflows"
SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _workflow(name: str) -> dict[str, Any]:
    workflow = read_yaml(WORKFLOWS / name)
    assert isinstance(workflow, dict)
    workflow["on"] = workflow.pop(True, workflow.get("on"))  # YAML reads a bare `on` as True
    return workflow


def _steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for job in workflow["jobs"].values() for step in job["steps"]]


def test_the_offline_eval_runs_on_pull_requests_and_main_and_gates_the_summary() -> None:
    ci = _workflow("ci.yml")
    assert "pull_request" in ci["on"]
    assert ci["on"]["push"]["branches"] == ["main"]
    assert "eval" in ci["jobs"]["ci-ok"]["needs"]
    (gate,) = [s for s in ci["jobs"]["eval"]["steps"] if "horae eval" in s.get("run", "")]
    for condition in ("failed == 0", "errors == 0", "stale == 0", '.status == "pass"'):
        assert condition in gate["run"]
    assert "--live" not in gate["run"]


def test_the_live_eval_runs_only_on_release_tags_behind_the_evals_environment() -> None:
    release = _workflow("release-evals.yml")
    assert release["on"] == {"push": {"tags": ["v*"]}}
    assert release["permissions"] == {}
    evaluation = release["jobs"]["eval"]
    assert evaluation["environment"] == "evals"
    assert evaluation["permissions"] == {"contents": "read"}
    assert "ANTHROPIC_API_KEY" not in str(release["jobs"]["attach"])
    assert re.fullmatch(r"\d+(\.\d+)?", release["env"]["EVAL_MAX_USD"])


def test_only_the_attach_job_can_write_and_the_key_is_one_step_only() -> None:
    release = _workflow("release-evals.yml")
    writers = [
        n for n, j in release["jobs"].items() if "write" in j.get("permissions", {}).values()
    ]
    assert writers == ["attach"]
    with_key = [s for s in release["jobs"]["eval"]["steps"] if "ANTHROPIC_API_KEY" in str(s)]
    assert len(with_key) == 1
    assert "env" not in release["jobs"]["eval"]


def test_every_action_is_pinned_and_no_run_step_interpolates_an_expression() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for step in _steps(_workflow(path.name)):
            if "uses" in step:
                assert SHA.match(step["uses"]), (path.name, step["uses"])
            assert "${{" not in step.get("run", ""), (path.name, step.get("name"))


def test_the_live_eval_script_caps_the_spend_with_the_cores_flag() -> None:
    script = (ROOT / "scripts" / "live-eval.sh").read_text(encoding="utf-8")
    assert "--live --yes --max-usd" in script
    assert "--record" not in script
