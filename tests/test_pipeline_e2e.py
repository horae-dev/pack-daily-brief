# SPDX-License-Identifier: Apache-2.0
"""`pipelines/daily-brief.yaml` end to end, with fixture plugins and a recorded LLM answer.

What is real (the core, pinned in uv.lock): the instance loader, the `Runner` (lock, store,
LLM cache, usage ledger, apply and journal), the engine (planning, templating, routing with
`link_prefix`, the action guard, de-identification), this pack's skill loaded by
`DirectorySkills`, the Anthropic backend's `render` and the production output validator
(`ModelBackends`), and the built-in local vault sink (`LocalFileSink`) that writes the note.

What is a fixture:

- the sources: the core's in-memory `FixturePlugins` (`horae.engine.testing`) stand in for
  plugin-calendar, plugin-obsidian and plugin-github under their own tool names, serving the
  `data` of a golden case's input, in the real shapes (plugin-calendar main, Task.to_json of
  plugin-obsidian main's `open_tasks`, generated with its code, plugin-github 0.2.0). The
  github ones apply their `orgs` filter, as the real search does, and propose one
  `task.create` per item with its link, as plugin-github 0.2.0 does. Their manifests copy the
  real ones (plugin-obsidian main's `obsidian.open_tasks` and its tasks sink
  `obsidian.write_task`);
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
OWNER = "Acme-Org"

# Stand-ins for the source and sink plugins, by namespace: github's provides and permissions
# are plugin-github 0.2.0's, calendar's plugin-calendar main's; obsidian's is
# plugin-obsidian main's (`plugin.yaml`: the `open_tasks` source, the `write_note` and
# `write_task` sinks).
STAND_INS: dict[str, dict[str, Any]] = {
    "calendar": {
        "version": "0.1.0",
        "provides": {"sources": ["calendar.events"]},
        "permissions": {
            "network": ["calendar.google.com", "calendar.proton.me"],
            "secrets": ["CALENDAR_ICS_URLS"],
        },
    },
    "obsidian": {
        "version": "0.2.0",
        "provides": {
            "sources": ["obsidian.open_tasks"],
            "sinks": ["obsidian.write_note", "obsidian.write_task"],
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
        "version": "0.2.0",
        "provides": {"sources": ["github.review_requests", "github.assigned", "github.mentions"]},
        "permissions": {
            "network": ["api.github.com"],
            "secrets": ["GITHUB_TOKEN"],
            "intents": ["task.create"],
        },
    },
}
VERBS = {"review_requested": "Review", "assigned": "Work on", "mentioned": "Answer mention in"}

UNLISTED = {
    "fingerprint": "github:pr:PR_kwDOSYNTH0099",
    "type": "pr",
    "repo": "Other-Org/tools",
    "number": 99,
    "title": "Bump the linter",
    "url": "https://github.com/Other-Org/tools/pull/99",
    "due": None,
}
"""A GitHub item of an owner the pipeline does not list in `orgs` (nor in `link_prefix`)."""


def _manifest(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "horae": ">=0,<0.2", **spec}


def _link(item: Mapping[str, Any]) -> str:
    """plugin-github 0.2.0 builds the link from the repository and the number."""
    kind = "pull" if item["type"] == "pr" else "issues"
    return f"https://github.com/{item['repo']}/{kind}/{item['number']}"


def _tasks(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """plugin-github 0.2.0's `task.create` proposals for its items (`intents.task_create`:
    the title escaped, the item's fingerprint, due date and link)."""
    verb = VERBS[data["kind"]]
    proposals = []
    for item in data["items"]:
        task = {
            "type": "task.create",
            "title": f"{verb} {item['repo']}\\#{item['number']}: {item['title']}",
            "fingerprint": item["fingerprint"],
            "link": _link(item),
        }
        if item["due"] is not None:
            task["due"] = item["due"]
        proposals.append(task)
    return proposals


def _in_orgs(item: Mapping[str, Any], orgs: list[str] | None) -> bool:
    """GitHub's `org:` qualifier: owner names compare case-insensitively."""
    owner = str(item["repo"]).split("/", 1)[0].lower()
    return orgs is None or owner in {org.lower() for org in orgs}


def fixture_plugins(
    morning: list[dict[str, Any]], *, extra_items: list[dict[str, Any]] | None = None
) -> FixturePlugins:
    """Every source of the pipeline serves its entry of the golden case's input. GitHub also
    holds `extra_items` for every kind, which the `orgs` filter leaves out or not."""
    served = {entry["source"]: entry for entry in morning}
    plugins = FixturePlugins()

    def handler(ref: str) -> Any:
        def serve(params: dict[str, Any], input: Any) -> ToolResult:
            entry = served[ref]
            assert params == entry["params"], f"{ref}: the pipeline's params changed"
            if not ref.startswith("github."):
                return ToolResult(data=entry["data"])
            data = dict(entry["data"])
            found = [*data["items"], *(extra_items or [])]
            data["items"] = [item for item in found if _in_orgs(item, params.get("orgs"))]
            return ToolResult(data=data, intents=_tasks(data))

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


SONNET_PRICE = (
    "price: { input_per_mtok: 3, output_per_mtok: 15, cache_read_per_mtok: 0.3,"
    " cache_write_per_mtok: 3.75 }"
)
"""A Sonnet-class price (US dollars per million tokens)."""


def make_instance(root: Path, *, price: str | None = None) -> Instance:
    """An instance with the pack's pipeline copied in, as the README says to install it."""
    (root / "pipelines").mkdir(parents=True)
    shutil.copy(PIPELINES / f"{PIPELINE}.yaml", root / "pipelines")
    alias = "backend: anthropic, model: claude-fixture-1, secret: ANTHROPIC_API_KEY"
    if price is not None:
        alias += f", {price}"
    (root / "horae.yaml").write_text(
        f"timezone: Europe/Paris\nstate_dir: state\nmodels:\n  default: {{ {alias} }}\n",
        encoding="utf-8",
    )
    return load_instance(root)


@pytest.fixture
def instance(tmp_path: Path) -> Instance:
    return make_instance(tmp_path / "instance")


@pytest.fixture
def store(instance: Instance) -> Iterator[Store]:
    with Store.open(instance.store_path) as opened:
        yield opened


class Morning:
    """One run of the pipeline on a golden case's day."""

    def __init__(
        self,
        instance: Instance,
        store: Store,
        vault: Path,
        case: str,
        *,
        extra_items: list[dict[str, Any]] | None = None,
        without_orgs: bool = False,
    ) -> None:
        loaded: LoadedCase = cases()[case]
        self.recorded = loaded.recorded["anthropic"]
        self.day = loaded.case.input[0]["params"]["start"]
        self.input = loaded.case.input
        if without_orgs:  # what the sources are called with by a pipeline without `orgs`
            for entry in self.input:
                entry["params"].pop("orgs", None)
        self.plugins = fixture_plugins(self.input, extra_items=extra_items)
        self.llm, self.backends = recorded_model(instance, self.recorded)
        self.tasks = FixtureSink()
        vault.mkdir(exist_ok=True)
        (vault / "Daily").mkdir(exist_ok=True)
        self.note = vault / NOTE.format(day=self.day)
        sinks = StaticSinks(
            {"obsidian.write_note": LocalFileSink(vault), "obsidian.write_task": self.tasks}
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

    def github_tasks(self) -> list[dict[str, Any]]:
        """The `task.create`s the github stand-ins proposed (the case's items)."""
        return [
            task
            for entry in self.input
            if entry["source"].startswith("github.")
            for task in _tasks(entry["data"])
        ]


def _block(text: str) -> str:
    assert text.count(BEGIN) == 1
    assert text.count(END) == 1
    return text.split(BEGIN, 1)[1].split(END, 1)[0]


WRITTEN = ["busy-morning", "empty-day", "hostile-title", "partial-sources", "second-day"]


@pytest.mark.parametrize("case", WRITTEN)
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

    # The github items became linked tasks through the tasks entry (their links are under
    # its link_prefix); the model proposed none.
    expected = {t["fingerprint"]: t["link"] for t in morning.github_tasks()}
    assert set(morning.tasks.tasks) == set(expected)
    for fingerprint, link in expected.items():
        assert link.startswith(f"https://github.com/{OWNER}/")
        assert link in morning.tasks.tasks[fingerprint]


def test_an_unlisted_owner_is_filtered_out_by_orgs(
    instance: Instance, store: Store, tmp_path: Path
) -> None:
    """GitHub also has an item of another owner: `orgs` keeps it out of the data and of the
    tasks, so the run does not fail on a link no `link_prefix` covers."""
    morning = Morning(instance, store, tmp_path / "vault", "busy-morning", extra_items=[UNLISTED])
    record = morning.run()
    assert record.status == "ok", [s.error for s in record.steps]
    # The collected input is still the case's (the request digests match the recording).
    assert record.llm[0].request["input_sha256"] == morning.recorded.request.input_sha256
    assert UNLISTED["fingerprint"] not in morning.tasks.tasks
    assert len(morning.tasks.tasks) == len(morning.github_tasks()) == 3


def test_without_orgs_an_unlisted_owner_fails_the_run(tmp_path: Path) -> None:
    """Why the pipeline sets `orgs`: the same item, collected, is a task whose link no
    `link_prefix` covers, and the run fails (`intent_unrouted`) rather than guess."""
    root = tmp_path / "instance"
    make_instance(root)
    path = root / "pipelines" / f"{PIPELINE}.yaml"
    text = path.read_text(encoding="utf-8")
    assert text.count("orgs: [Acme-Org], ") == 3
    path.write_text(text.replace("orgs: [Acme-Org], ", ""), encoding="utf-8")
    instance = load_instance(root)
    with Store.open(instance.store_path) as store:
        morning = Morning(
            instance,
            store,
            tmp_path / "vault",
            "empty-day",
            extra_items=[UNLISTED],
            without_orgs=True,
        )
        record = morning.run()
    assert record.status == "failed"
    collect = record.steps[0]
    assert collect.error is not None
    assert collect.error.kind == "intent_unrouted", collect.error
    assert morning.tasks.tasks == {}
    assert not morning.note.exists()


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


def test_the_budget_leaves_room_for_a_busy_morning_at_a_sonnet_price(tmp_path: Path) -> None:
    """With a priced alias, `max_tokens` is the step's `tokens_out` lowered so that the input
    bound plus the output fit its `usd`: a busy morning must still get room to answer."""
    instance = make_instance(tmp_path / "instance", price=SONNET_PRICE)
    with Store.open(instance.store_path) as store:
        morning = Morning(instance, store, tmp_path / "vault", "busy-morning")
        record = morning.run()
    assert record.status == "ok", [s.error for s in record.steps]
    request = record.llm[0].request
    assert isinstance(request, dict)
    assert request["max_tokens"] >= 1500, request["max_tokens"]


def test_an_obeyed_embed_never_reaches_the_vault(
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
    assert morning.tasks.tasks == {}


@pytest.mark.xfail(
    strict=True,
    reason="known hole, horae-dev/horae#128: the guard lets these through today",
)
@pytest.mark.parametrize("case", ["injected-link", "injected-checkbox", "injected-wikilink"])
def test_an_obeyed_link_checkbox_or_wikilink_never_reaches_the_vault(
    instance: Instance, store: Store, tmp_path: Path, case: str
) -> None:
    """What #128 must make true. Strict: when the core refuses them, these pass, the xfail
    fails, and the marker (and the cases' `guard:`) must be updated."""
    morning = Morning(instance, store, tmp_path / "vault", case)
    record = morning.run()
    assert record.status == "degraded"
    assert not morning.note.exists()


def test_an_obeyed_plain_marker_fails_the_run_at_apply_today(
    instance: Instance, store: Store, tmp_path: Path
) -> None:
    """Known hole, horae-dev/horae#128: the model copied the plain-text `horae:end` marker of a
    title (`injected-marker`). The guard passes it; the note writer then refuses the block,
    so no note is written. #128 moves the refusal to the guard, so that only the step
    degrades (and then this test, and the case's `guard: pass`, change)."""
    morning = Morning(instance, store, tmp_path / "vault", "injected-marker")
    record = morning.run()
    assert record.status == "failed"
    assert record.error is not None
    assert record.error.startswith("plan_invalid"), record.error
    assert "content_has_marker" in record.error
    assert not morning.note.exists()
    assert morning.tasks.tasks == {}
