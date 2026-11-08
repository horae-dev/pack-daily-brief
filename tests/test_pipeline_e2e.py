# SPDX-License-Identifier: Apache-2.0
"""`pipelines/daily-brief.yaml` end to end, with fixture plugins and a recorded LLM answer.

What is real (the core, pinned in uv.lock): the instance loader, the `Runner` (lock, store,
LLM cache, usage ledger, apply and journal), the engine (planning, templating, routing, the
action guard, de-identification), this pack's skill loaded by `DirectorySkills`, the Anthropic
backend's `render` and the production output validator (`ModelBackends`), and the built-in
local vault sink (`LocalFileSink`) that writes the note.

What is a fixture:

- the sources: the core's in-memory `FixturePlugins` (`horae.engine.testing`) stand in for
  plugin-calendar, plugin-obsidian and plugin-github under their own tool names, serving the
  `data` of a golden case's input (the github ones also propose their `task.create`
  intents, as the real plugin does). Their manifests below copy the real ones where they
  exist and assume the planned ones elsewhere (`obsidian.open_tasks`, `obsidian.write_tasks`);
- the model: the provider backend's `send` returns the golden case's recorded answer
  instead of calling the API (as `horae eval` does offline); nothing leaves the host;
- the tasks sink: the core's in-memory `FixtureSink`.

Since the case's input is what this pipeline collects on that day, the request the run
renders must be the one the recording was made for (same input, prompt and schema digests).
"""

import asyncio
import shutil
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from horae.apply import LocalFileSink, StaticSinks
from horae.apply.testing import FixtureSink
from horae.config import Instance, load_instance
from horae.contracts import RunRecord
from horae.contracts.evals import RecordedResponse
from horae.contracts.instance import ModelAlias
from horae.engine import ToolResult
from horae.engine.testing import FixturePlugins
from horae.evals import LoadedCase
from horae.llm.backends import FACTORIES
from horae.llm.base import CompletionResult, ModelBackends, ProviderBackend
from horae.runner import Ports, Runner
from horae.secrets import SecretResolver
from horae.skills import DirectorySkills
from horae.store import Store

from pack import PIPELINE, PIPELINES, ROOT, cases, manifest

NOTE = "Daily/{day}.assistant.md"
BEGIN, END = "<!-- horae:begin daily-brief -->", "<!-- horae:end daily-brief -->"

# Stand-ins for the source and sink plugins, by namespace. github's provides and permissions
# are plugin-github 0.1.0's; calendar's are plugin-calendar's (horae-dev/plugin-calendar#6);
# obsidian's sink is plugin-obsidian 0.1.0's `write_note`, plus the planned `open_tasks`
# source and tasks sink (horae-dev/plugin-obsidian#4).
STAND_INS: dict[str, dict[str, Any]] = {
    "calendar": {
        "provides": {"sources": ["calendar.events"]},
        "permissions": {"network": ["calendar.google.com"], "secrets": ["CALENDAR_ICS_URLS"]},
    },
    "obsidian": {
        "provides": {
            "sources": ["obsidian.open_tasks"],
            "sinks": ["obsidian.write_note", "obsidian.write_tasks"],
        },
        "permissions": {
            "intents": [
                "note.create",
                "note.append_section",
                "note.replace_owned_block",
                "task.create",
                "task.update_status",
            ]
        },
    },
    "github": {
        "provides": {"sources": ["github.review_requests", "github.assigned", "github.mentions"]},
        "permissions": {
            "network": ["api.github.com"],
            "secrets": ["GITHUB_TOKEN"],
            "intents": ["task.create"],
        },
    },
}
VERBS = {"review_requested": "Review", "assigned": "Work on", "mentioned": "Answer mention in"}


def _manifest(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "version": "0.1.0", "horae": ">=0,<0.2", **spec}


def _tasks(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The github plugin's `task.create` proposals for its items (title shape of its README;
    the real plugin also escapes the title with the SDK)."""
    verb = VERBS[data["kind"]]
    return [
        {
            "type": "task.create",
            "title": f"{verb} {item['repo']}\\#{item['number']}: {item['title']}",
            "fingerprint": item["fingerprint"],
            "due": item["due"],
        }
        for item in data["items"]
    ]


def fixture_plugins(morning: list[dict[str, Any]]) -> FixturePlugins:
    """Every source of the pipeline serves its entry of the golden case's input."""
    served = {entry["source"]: entry for entry in morning}
    plugins = FixturePlugins()

    def handler(ref: str) -> Any:
        def serve(params: dict[str, Any], input: Any) -> ToolResult:
            entry = served[ref]
            assert params == entry["params"], f"{ref}: the pipeline's params changed"
            intents = _tasks(entry["data"]) if ref.startswith("github.") else []
            return ToolResult(data=entry["data"], intents=intents)

        return serve

    for name, spec in STAND_INS.items():
        sources = spec["provides"].get("sources", [])
        plugins.add(_manifest(name, spec), {ref: handler(ref) for ref in sources})
    plugins.add(manifest())
    return plugins


class Recorded:
    """A provider backend that renders with the real Anthropic backend and answers with a
    recorded result (what `horae eval` does offline). It counts what it was asked to send."""

    def __init__(self, inner: ProviderBackend, result: CompletionResult) -> None:
        self._inner, self._result = inner, result
        self.sent: list[Mapping[str, Any]] = []

    @property
    def name(self) -> Any:
        return self._inner.name

    @property
    def may_write_cache(self) -> bool:
        return getattr(self._inner, "may_write_cache", False) is True

    def render(self, completion: Any) -> dict[str, Any]:
        return self._inner.render(completion)

    async def send(self, payload: Mapping[str, Any], timeout_s: float) -> CompletionResult:
        self.sent.append(payload)
        return self._result

    async def aclose(self) -> None:
        await self._inner.aclose()


def recorded_model(
    instance: Instance, recorded: RecordedResponse
) -> tuple[ModelBackends, list[Recorded]]:
    built: list[Recorded] = []
    result = CompletionResult(recorded.output, recorded.usage, recorded.stop_reason)

    def factory(name: str, spec: ModelAlias, secrets: SecretResolver) -> ProviderBackend:
        backend = Recorded(FACTORIES[spec.backend](name, spec, secrets), result)
        built.append(backend)
        return backend

    backends = ModelBackends(
        models=instance.config.models,
        secrets=instance.secret_resolver(environ={}),
        factories={"anthropic": factory},
    )
    return backends, built


@pytest.fixture
def instance(tmp_path: Path) -> Instance:
    """An instance with the pack's pipeline copied in, as the README says to install it."""
    root = tmp_path / "instance"
    (root / "pipelines").mkdir(parents=True)
    shutil.copy(PIPELINES / f"{PIPELINE}.yaml", root / "pipelines")
    (root / "horae.yaml").write_text(
        "timezone: Europe/Paris\n"
        "state_dir: state\n"
        "models:\n"
        "  default: { backend: anthropic, model: claude-fixture-1, secret: ANTHROPIC_API_KEY }\n",
        encoding="utf-8",
    )
    return load_instance(root)


@pytest.fixture
def store(instance: Instance) -> Iterator[Store]:
    with Store.open(instance.store_path) as opened:
        yield opened


class Morning:
    """One run of the pipeline on a golden case's day."""

    def __init__(self, instance: Instance, store: Store, vault: Path, case: str) -> None:
        loaded: LoadedCase = cases()[case]
        self.recorded = loaded.recorded["anthropic"]
        self.day = loaded.case.input[0]["params"]["start"]
        self.input = loaded.case.input
        self.plugins = fixture_plugins(self.input)
        self.llm, self.backends = recorded_model(instance, self.recorded)
        self.tasks = FixtureSink()
        vault.mkdir(exist_ok=True)
        (vault / "Daily").mkdir(exist_ok=True)
        self.note = vault / NOTE.format(day=self.day)
        sinks = StaticSinks(
            {"obsidian.write_note": LocalFileSink(vault), "obsidian.write_tasks": self.tasks}
        )
        ports = Ports(
            self.plugins,
            self.plugins,
            self.llm,
            sinks=sinks,
            skills=DirectorySkills({manifest().name: ROOT}),
        )
        # 05:30 in Paris, the trigger's time.
        clock = datetime.fromisoformat(f"{self.day}T04:30:00+00:00").astimezone(UTC)
        self.runner = Runner(instance, store, ports, clock=lambda: clock)

    def run(self, **kwargs: Any) -> RunRecord:
        return asyncio.run(self.runner.run(PIPELINE, **kwargs))

    @property
    def sent(self) -> int:
        return sum(len(backend.sent) for backend in self.backends)


def _block(text: str) -> str:
    assert text.count(BEGIN) == 1
    assert text.count(END) == 1
    return text.split(BEGIN, 1)[1].split(END, 1)[0]


@pytest.mark.parametrize("case", ["busy-morning", "empty-day", "hostile-title"])
def test_the_morning_note_block_is_written(
    instance: Instance, store: Store, tmp_path: Path, case: str
) -> None:
    morning = Morning(instance, store, tmp_path / "vault", case)
    record = morning.run()

    assert record.status == "ok", [s.error for s in record.steps]
    # Every source was called once, in the pipeline's order, with its rendered params.
    assert morning.plugins.refs == [entry["source"] for entry in morning.input]
    # One request, for exactly the input, prompt and schema the golden answer was made for.
    assert morning.sent == 1
    (call,) = record.llm
    assert call.skill_digest == morning.recorded.skill_digest
    request = call.request
    assert isinstance(request, dict)
    assert request["input_sha256"] == morning.recorded.request.input_sha256
    assert request["system_sha256"] == morning.recorded.request.system_sha256
    assert request["schema_sha256"] == morning.recorded.request.schema_sha256

    # The note: today's assistant note, only the owned block, with the model's words.
    content = morning.recorded.output["intents"][0]["content"]
    assert morning.note.exists()
    assert _block(morning.note.read_text(encoding="utf-8")).strip() == content.strip()
    (note,) = [c for c in record.changes if c.target == NOTE.format(day=morning.day)]
    proposed = record.intents[note.intent]
    assert proposed.proposed_by == "daily-brief/rank-and-word"
    assert proposed.applied_by == "obsidian.write_note"

    # The github items became tasks through the tasks entry; the model proposed none.
    expected = [
        i for e in morning.input if e["source"].startswith("github.") for i in _tasks(e["data"])
    ]
    assert len(morning.tasks.tasks) == len({t["fingerprint"] for t in expected})


def test_an_unchanged_morning_makes_no_second_llm_call(
    instance: Instance, store: Store, tmp_path: Path
) -> None:
    """The $0 path (PLAN.md §2.2): the same input on the same day reuses the cached answer."""
    morning = Morning(instance, store, tmp_path / "vault", "busy-morning")
    assert morning.run().status == "ok"
    before = morning.note.read_bytes()
    again = morning.run()
    assert again.status in ("ok", "noop")
    assert morning.sent == 1
    assert again.llm[0].cached is True
    assert morning.note.read_bytes() == before


def test_a_dry_run_writes_nothing(instance: Instance, store: Store, tmp_path: Path) -> None:
    morning = Morning(instance, store, tmp_path / "vault", "busy-morning")
    record = morning.run(dry_run=True)
    assert record.status == "ok"
    assert not morning.note.exists()
    assert morning.tasks.tasks == {}


def test_an_obeyed_injection_never_reaches_the_vault(
    instance: Instance, store: Store, tmp_path: Path
) -> None:
    """The model obeyed a hostile title (`injected-embed`): the action guard refuses the
    whole step, the act step is skipped and no note is written."""
    morning = Morning(instance, store, tmp_path / "vault", "injected-embed")
    record = morning.run()
    assert record.status == "degraded"
    llm = next(step for step in record.steps if step.kind == "llm")
    assert llm.status == "degraded"
    assert llm.error is not None
    assert llm.error.kind == "guard_violation"
    assert any(v.kind == "guard" for v in record.violations)
    assert not morning.note.exists()
