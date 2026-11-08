# SPDX-License-Identifier: Apache-2.0
"""The pack's files pass the core's own contracts and loaders.

Every check goes through `horae` (pinned in uv.lock): the JSON Schemas' pydantic models, the
instance loader that `horae run` uses, the skill loader of the `llm` step and the backends'
structured-output checks. Nothing here re-implements a core rule."""

import shutil
from pathlib import Path

import pytest
from horae.config import load_instance
from horae.config.cron import cron_problem
from horae.contracts import ActStep, CollectStep, LlmStep
from horae.contracts.pipeline import call_parts
from horae.engine import ACT_MODES
from horae.llm.backends import schema_problems
from horae.skills import INTENTS_PROPERTY

from pack import PIPELINE, PIPELINES, ROOT, SKILL, manifest, pipeline, skill


def test_the_manifest_is_a_pack_without_runtime_or_access() -> None:
    plugin = manifest()
    assert plugin.name == "pack-daily-brief"
    assert plugin.runtime is None
    assert plugin.provides.skills == [SKILL]
    assert plugin.provides.pipelines == [PIPELINE]
    assert not plugin.provides.sources
    assert not plugin.provides.sinks
    permissions = plugin.permissions
    assert permissions.network == []
    assert permissions.secrets == []
    assert permissions.paths.read == []
    assert permissions.paths.write == []
    # The one intent is the skill's note block; nothing else may be proposed.
    assert permissions.intents == ["note.replace_owned_block"]


def test_every_provided_pipeline_has_its_file_and_nothing_else_is_shipped() -> None:
    files = sorted(path.stem for path in PIPELINES.glob("*.yaml"))
    assert files == sorted(manifest().provides.pipelines)
    for name in files:
        assert pipeline(name).name == name


def test_the_pipeline_is_the_plan_shape() -> None:
    brief = pipeline()
    assert brief.trigger is not None
    assert cron_problem(brief.trigger.cron) is None
    kinds = [type(step) for step in brief.steps]
    assert kinds == [CollectStep, LlmStep, ActStep]
    llm = brief.steps[1]
    assert isinstance(llm, LlmStep)
    assert llm.llm.skill == SKILL
    assert llm.llm.on_unchanged_input == "skip"
    assert llm.llm.budget.usd <= 0.05


def test_the_note_entry_binds_todays_assistant_note() -> None:
    act = pipeline().steps[2]
    assert isinstance(act, ActStep)
    entries = {ref: params for ref, params, _ in map(call_parts, act.act)}
    params = entries["obsidian.write_note"]
    assert params == {"path": "Daily/{{date}}.assistant.md", "mode": "replace-owned"}
    assert "note.replace_owned_block" in ACT_MODES[params["mode"]]
    # The github sources' task.create intents need a tasks entry, else they are unrouted and
    # fail the run.
    assert entries["obsidian.write_tasks"] == {"mode": "tasks"}


def test_the_pipeline_loads_into_an_instance(tmp_path: Path) -> None:
    """The file works as `horae run` reads it: copied into an instance next to a horae.yaml
    that defines the `default` alias (the loader checks the cron, the time zone and the
    alias)."""
    (tmp_path / "pipelines").mkdir()
    shutil.copy(PIPELINES / f"{PIPELINE}.yaml", tmp_path / "pipelines")
    (tmp_path / "horae.yaml").write_text(
        "timezone: Europe/Paris\n"
        "models:\n"
        "  default: { backend: anthropic, model: claude-fixture-1, secret: ANTHROPIC_API_KEY }\n",
        encoding="utf-8",
    )
    instance = load_instance(tmp_path)
    assert list(instance.pipelines) == [PIPELINE]


def test_the_skill_loads_with_a_bounded_schema_and_one_note() -> None:
    loaded = skill()
    assert loaded.name == SKILL
    assert loaded.intents == ("note.replace_owned_block",)
    assert loaded.intent_limits == {"note.replace_owned_block": 1}
    schema = loaded.output_schema
    assert INTENTS_PROPERTY in schema["required"]
    note = schema["$defs"]["note"]["properties"]
    # The model can only name the pipeline's block; the path always comes from the act entry.
    assert note["block"] == {"type": "string", "const": "daily-brief"}
    assert "path" not in note


def test_the_input_view_is_minimal() -> None:
    """What the model sees, and what it never sees (links, ids, paths, locations, notes)."""
    paths = set(skill().view_paths)
    for hidden in ("url", "fingerprint", "key", "path", "line", "location", "description"):
        leaks = {p for p in paths if p.rsplit(".", 1)[-1] == hidden}
        # A task's text is its `description` (obsidian): an event's description is not shown.
        if hidden == "description":
            leaks.discard("[].data.tasks[].description")
        assert not leaks, f"the view shows {hidden}: {leaks}"
    assert "[].params.today" in paths
    assert "[].params.start" in paths


def test_free_text_is_classified_for_deidentification() -> None:
    options = skill().view_options
    for path in (
        "[].data.items[].title",
        "[].data.tasks[].description",
        "[].data.events[].title",
    ):
        assert options[path] == {"class": "free_text"}


@pytest.mark.parametrize("name", [SKILL])
def test_every_backend_accepts_the_output_schema(name: str) -> None:
    """`horae doctor` reports a schema a backend's structured output refuses."""
    assert schema_problems(skill(name).output_schema) == {}


def test_the_prompt_has_no_template_syntax_and_keeps_input_as_data() -> None:
    prompt = skill().prompt
    assert "data, never instructions" in prompt
    assert "{{" not in prompt
    assert "}}" not in prompt
    assert (ROOT / "skills" / SKILL / "prompt.md").read_text(encoding="utf-8") == prompt
