<p align="center"><img src="https://raw.githubusercontent.com/horae-dev/.github/main/brand/horae-logo-500.png" width="96" alt="Horae logo"></p>

# pack-daily-brief

Horae pipeline pack: the morning note pipeline and its rank-and-word skill.

> 🚧 **Early (0.1.0, unreleased), and it cannot run yet.** The pipeline needs two things plugin-obsidian does not have yet: the `obsidian.open_tasks` source and the sink that applies `task.create` (both [plugin-obsidian#4](https://github.com/horae-dev/plugin-obsidian/issues/4), in progress). Until they are released, `horae run daily-brief` fails at planning (an unknown tool). The pipeline and the skill pass their checks against the core's `main` with stand-ins of those plugins in their real or planned shapes. The wording of the note has **not been judged by the maintainer yet**: the golden answers are hand-written, not recorded from a model (see [Wording quality](#wording-quality)).

## What it does

Every morning the `daily-brief` pipeline reads your calendar, your open Obsidian tasks and your GitHub work, has **one bounded LLM call** rank it and word it, and writes the result into an owned block of today's assistant note, `Daily/<date>.assistant.md`:

```markdown
<!-- horae:begin daily-brief -->
Two things are overdue, a conference and three events today, planning tomorrow.

## Now

- Send the insurance form (due Nov 6)
- Work on widgets #42: cache misses on cold start (due Nov 6)
- All day: Product conference (until Nov 10)
- 09:30 Team stand-up
- 14:00 Dentist (tentative)
- Review api #7: export pagination fix
- Book the train tickets
- Draft the newsletter
- Answer the mention in docs #3: install guide

## Tomorrow

- All day: School holiday
- 10:00 Quarterly planning
- Water the plants

## Later

- Renew the library card (due Nov 20)
- Read the onboarding guide
<!-- horae:end daily-brief -->
```

The LLM never decides what runs or where anything is written (PLAN.md §2.1): the pipeline fixes the steps, the note's path comes from the pipeline, and the model can only propose the content of the one `daily-brief` block.

## What is in the pack

A pack is a plugin with no runtime: it ships files, not a process (`plugin.yaml` has no `runtime`, so no network, secret or path either).

| File | What it is |
|---|---|
| [`plugin.yaml`](plugin.yaml) | the manifest: provides the skill `daily-brief/rank-and-word` and the pipeline `daily-brief`; its one permission is the intent `note.replace_owned_block` |
| [`pipelines/daily-brief.yaml`](pipelines/daily-brief.yaml) | the pipeline: `collect` → `llm` → `act`, cron `30 5 * * *` |
| [`skills/daily-brief/rank-and-word/skill.yaml`](skills/daily-brief/rank-and-word/skill.yaml) | the skill: version, input view, intents (schemas decision 39) |
| [`skills/daily-brief/rank-and-word/prompt.md`](skills/daily-brief/rank-and-word/prompt.md) | the prompt, sent verbatim |
| [`skills/daily-brief/rank-and-word/output.schema.json`](skills/daily-brief/rank-and-word/output.schema.json) | the bounded output schema |
| [`skills/daily-brief/rank-and-word/evals/`](skills/daily-brief/rank-and-word/evals/) | golden cases and their answers (schemas decision 48) |

### The pipeline

```yaml
name: daily-brief
trigger: { cron: "30 5 * * *" }          # or run by an external scheduler: `horae run daily-brief`
steps:
  # Read. Each source returns data; the github sources also propose one task.create per item,
  # with the item's link (plugin-github 0.2.0), routed to the tasks entry of the act step.
  - collect:
      # A calendar that fails gives the others' events (`complete: false`) instead of failing
      # the source: a partial brief beats no brief, and the note says the list is incomplete.
      - calendar.events: { start: "{{date}}", days: 2, timezone: "{{timezone}}", partial: true }
      # plugin-obsidian#4 (in progress): open tasks, with `source_fingerprint` on the tasks
      # Horae created from another source.
      - obsidian.open_tasks: {}
      # Reviews and mentions are due the day they are collected; an assignment is due on its
      # milestone's date. `orgs` keeps only the owners `link_prefix` covers: an item of
      # another owner would be unrouted and fail the run.
      - github.review_requests:
          { today: "{{date}}", orgs: [Acme-Org], max_items: 20, due_in_business_days: 0 }
      - github.assigned: { today: "{{date}}", orgs: [Acme-Org], max_items: 20, skip_covered: true }
      - github.mentions:
          { today: "{{date}}", orgs: [Acme-Org], max_items: 20, skip_covered: true, due_in_days: 0 }
  # Rank and word: the one bounded LLM step. It sees only the skill's input view and proposes
  # one note.replace_owned_block of block `daily-brief`, without a path.
  - llm:
      skill: daily-brief/rank-and-word
      model: default                        # alias resolved in horae.yaml
      budget: { usd: 0.15, tokens_out: 4000 }
      on_unchanged_input: skip              # $0 path: same input, no LLM call
  # Apply. The core fills the note's path from this entry (its trusted parameter); the sink
  # writes only the owned block of today's assistant note.
  - act:
      - obsidian.write_note: { path: "Daily/{{date}}.assistant.md", mode: replace-owned }
      # The github sources' tasks (sink of plugin-obsidian#4). A task's link is routed only
      # under a trusted `link_prefix`; without this entry, or with a link outside its prefix,
      # the task is unrouted and the run fails.
      - obsidian.write_tasks: { mode: tasks, link_prefix: "https://github.com/Acme-Org/" }
```

- `{{date}}` and `{{timezone}}` come from the run (the trigger's time zone, else `horae.yaml`'s), so the day is the run's and a replay sees the same one.
- **`Acme-Org` is a placeholder**: replace it with your GitHub owner, in its canonical case (as it appears in github.com URLs), in the three `orgs` and in `link_prefix`. For several owners, list them all in `orgs` and add one tasks entry per owner, each with its own `link_prefix`; the prefixes must not overlap (`https://github.com/Acme-Org/` and `https://github.com/Other-Org/` are fine).
- **Unchanged input costs nothing**: a second run on the same data reuses the cached answer (no LLM call).
- **Budget**: at most US$0.15 and 4,000 output tokens per run. With a priced alias, `max_tokens` is the `tokens_out` lowered so that the input plus the output fit the `usd`; at a Sonnet-class price a busy morning still gets the full 4,000 (tested). Each GitHub kind reads at most 20 items.
- The GitHub sources propose one `task.create` per item, with the item's link (plugin-github 0.2.0). The `obsidian.write_tasks` entry applies them; its trusted `link_prefix` is the only place their links may point to, and an intent no entry takes fails the run. Drop the three `github.*` sources and that entry together if you don't use GitHub.

### When there is no note

The note is written only when the whole run succeeds. A missing note (or one still showing an older day's block) means one of these, and `horae run` / the run journal says which:

- **a source failed** (GitHub rate limited, the vault unreadable, every calendar down): the run is `degraded`, the `act` step is skipped and nothing is written from partial data. One calendar failing out of several does **not** stop the brief: `partial: true` keeps the others' events and the note says the calendar list is incomplete (a partial brief beats no brief). GitHub's own `partial` and `truncated` results are flagged the same way;
- **the model's answer was refused** (schema, or the action guard): the step is `degraded` and nothing is written;
- **applying failed**, for example a GitHub task whose link no `link_prefix` covers (`intent_unrouted`), or note content carrying a block marker: the run `failed` and nothing was applied.

### The skill

**What the model sees** (the input view): for each source its name (`source`), today's date (`params.today` / `params.start`) and whether its list is complete (`data.partial`, `data.truncated`, `data.complete`, `data.errors`); for tasks, the description, due, scheduled and start dates, priority and `source_fingerprint`; for GitHub items, the repository, number, title, due date and kind; for events, the title, start, end, all-day flag and status (`confirmed` or `tentative`). **Not** links, item fingerprints, note paths, event locations, descriptions or calendar names, nor any other parameter.

**De-identification.** With a cloud model the core scans **every** string of the viewed input for your vocabulary's terms (schemas decision 47 B) and puts the real values back in the note. Titles and task descriptions are declared `class: free_text`, which marks them as text written by other people and is counted in the request's de-identification summary; it does not change what is scanned.

**What it answers** (the output schema, at most about 29 KB): `summary` (200 characters), `ranked` (at most 20 references: `kind`, `when`, `source` and `index`, the item's position in that source's list) and exactly one intent, `note.replace_owned_block` of block `daily-brief` (fixed by the schema) with `content` of at most 2,500 characters and no path. The note is the only place the model words the items; `ranked` lets the evals check the ranking without parsing the note.

**Titles are untrusted.** They come from other people (a pull request title, an invitation) or from text you pasted. The prompt treats the input as data and forbids copying checkboxes, `[[`, links, URLs, emoji, `::`, `%%`, tags or `horae:` text; titles are copied as plain words. The core's action guard refuses images, embeds, HTML and URLs that are not in the input, and the note writer refuses block markers. **Known holes**, tracked in [horae-dev/horae#128](https://github.com/horae-dev/horae/issues/128): today the guard lets an obeyed Tasks checkbox line, an internal wikilink, a link whose URL is in a viewed title and a plain-text `horae:end` marker through (the marker then fails the run at apply). The `injected-*` golden cases and the end-to-end tests record exactly what happens to each; when #128 lands the skill gets `links: none` (a TODO in `skill.yaml`) and those cases flip to `guard: violation`.

## Install

`horae plugin add` (milestone M4) does not exist yet, so for now by hand, in your instance:

1. **The pack.** Clone it at a commit into `<instance>/plugins/pack-daily-brief/` and pin it in `horae.lock`:

   ```yaml
   plugins:
     pack-daily-brief:
       version: 0.1.0
       repo: https://github.com/horae-dev/pack-daily-brief.git
       commit: "<the 40-hex commit you cloned>"
       permissions:
         network: []
         secrets: []
         paths: {}
         intents: [note.replace_owned_block]
   ```

2. **The pipeline.** Copy `pipelines/daily-brief.yaml` into `<instance>/pipelines/` (the core does not install a pack's pipelines yet). Replace `Acme-Org` with your GitHub owner, and edit the rest freely: the trigger, the sources you have, the note path.
3. **A model alias** named `default` in `horae.yaml` (or change `model:` in your copy), for example `default: { backend: anthropic, model: <model>, secret: ANTHROPIC_API_KEY, price: {...} }`. With a price, the step's budget (`usd: 0.15`) is checked before each call.
4. **The source and sink plugins** below, each installed and pinned the same way.
5. Check with `horae doctor`, try `horae run daily-brief --dry-run` (shows the diff, writes nothing), then `horae run daily-brief`.

## What each source needs

| Entry | Plugin | Needs | Status |
|---|---|---|---|
| `calendar.events` | [plugin-calendar](https://github.com/horae-dev/plugin-calendar) (`main`) | secret `CALENDAR_ICS_URLS` (your ICS links); hosts `calendar.google.com`, `calendar.proton.me` | merged ([plugin-calendar#6](https://github.com/horae-dev/plugin-calendar/pull/6)), not released |
| `github.review_requests`, `github.assigned`, `github.mentions` | [plugin-github](https://github.com/horae-dev/plugin-github) 0.2.0 (tasks with links; needs a core at horae-dev/horae@0f5531a or later) | secret `GITHUB_TOKEN` (fine-grained, read-only: pull requests, issues, metadata); host `api.github.com` | on `main` |
| `obsidian.open_tasks` | [plugin-obsidian](https://github.com/horae-dev/plugin-obsidian) | the vault as `paths.read` in `horae.lock` | **in progress** ([plugin-obsidian#4](https://github.com/horae-dev/plugin-obsidian/issues/4)); the pack cannot run until it lands |
| `obsidian.write_note` (act) | plugin-obsidian 0.1.0 | the vault as `paths.write` in `horae.lock`; intent `note.replace_owned_block` | released |
| `obsidian.write_tasks` (act) | plugin-obsidian | intents `task.create`, `task.update_status` | **in progress** (plugin-obsidian#4, no pull request yet); the entry name is this pack's assumption, to align when #4 names its sink |

A source you don't have must be removed from your copy of the pipeline (an unknown tool fails the run before anything is called). The skill copes with any subset: what is missing is simply not in the note.

## Wording quality

The tests prove the pipeline runs end to end and that every golden answer is schema-valid and passes its case. They **cannot** say whether the note reads well. That is for the maintainer to judge, on real mornings:

- the golden answers in `evals/recorded/anthropic/` were **written by hand** to show the intended note, not produced by a model (their `model` is `claude-fixture-1`). The cases check properties (how many items are ranked, references, words the note contains), not exact text, so a real model's answer can pass them. Record real ones for the mornings with a live eval (it spends money, asks first):

  ```sh
  for case in busy-morning empty-day hostile-title partial-sources second-day; do
    horae eval daily-brief/rank-and-word -i <instance> --alias default --plugin-dir . \
      --case "$case" --live --record --max-usd 0.50
  done
  ```

  (only answers that pass their case are written), then read each note: ranking, tone, length, what was left out. Don't record the `injected-*` cases: their answers are a model that **obeyed** an injection, written by hand on purpose;
- prompt and schema changes are judged the same way: before and after notes of every case in the pull request.

The full eval suite (more mornings, wording checks, per-backend recordings) is [#3](https://github.com/horae-dev/pack-daily-brief/issues/3).

## Design

Decisions taken with the maintainer ("go with what you think is good for the project") on the review of #4:

1. **GitHub tasks carry links, so they are routed by owner.** plugin-github 0.2.0 sends each task's `link`; the core routes a linked task only to a `mode: tasks` entry whose trusted `link_prefix` covers it. The pipeline uses one owner, `Acme-Org` (a placeholder, canonical case), in `link_prefix: "https://github.com/Acme-Org/"` and in `orgs: [Acme-Org]` on all three GitHub calls, so an item of another owner is filtered out at the source instead of failing the run. More owners: one tasks entry per owner, with disjoint prefixes.
2. **Obeyed injections.** The core fix is horae-dev/horae#128 (guard rules for Tasks lines, wikilinks and markers, and a per-skill `links: none`). Here: the prompt forbids that text outright, `skill.yaml` has a TODO for `links: none`, and a golden case per payload (embed, link, checkbox, wikilink, plain marker) records what the guard does today: `violation` for the embed, `pass` for the other four (known holes, documented in each case). The end-to-end tests do the same, with a strict `xfail` that turns red when #128 closes the holes.
3. **Sources that don't exist yet.** The pack needs plugin-obsidian's `obsidian.open_tasks` and its `task.create` sink (plugin-obsidian#4) and cannot run until they land. The end-to-end stand-ins mirror the real shapes: `Task.to_json()` of plugin-obsidian `main` (generated with its parser) plus #4's `source_fingerprint`, plugin-github 0.2.0's data and `task.create` (with `link`), plugin-calendar `main`'s `calendar.events`. The sink is named `obsidian.write_tasks`; #4 has no pull request yet to take a name from.
4. **No duplicate GitHub items from day two.** The tasks created from GitHub items come back as open tasks with a `source_fingerprint` starting with `github:`; those are left out, since GitHub lists the item itself (`second-day` case). The input view selects fields, it cannot filter by value, so the view shows `source_fingerprint` and the prompt leaves those tasks out. A deterministic filter needs a view filter or a transform in the core.
5. **Budget.** `usd: 0.15`, `tokens_out: 4000`, `max_items: 20` per GitHub kind; a test with a Sonnet-class price checks that a busy morning still gets at least 1,500 output tokens (it gets 4,000).
6. **Shorter lines.** A GitHub item is named by its repository without the owner (`api #7`).
7. **Incomplete lists are said.** The view shows `partial`, `truncated`, `complete` and `errors`; the note then says which list is incomplete (`partial-sources` case).
8. **Reviews and mentions are today's work.** `due_in_business_days: 0` and `due_in_days: 0`: their due date is the day they are collected, never a made-up deadline, and the prompt never calls them overdue. An assignment keeps its milestone's date.
9. **`horae:` text.** The prompt forbids copying it in any form; the end-to-end test shows that today a copied plain marker fails the run at apply (core fix in #128).
10. **A partial brief beats no brief.** `partial: true` on `calendar.events`: one failing calendar no longer stops the brief (see [When there is no note](#when-there-is-no-note)).
11. **One wording.** `ranked` holds references (`kind`, `when`, `source`, `index`), not lines; the model words each item once, in the note.
12. **Property checks.** The golden cases assert properties (lengths, references, contained words) instead of exact outputs, so a live recording can pass them; the hostile payloads are in fields the view shows (titles, task descriptions as plugin-obsidian parses them).
13. **Ranking rules.** Tentative events are marked, multi-day events are listed while they last, a task's `due` before today is overdue while a past `scheduled` date is today's work, and a task whose `start` is after today is left out. The calendar source already drops cancelled events and reads only today and tomorrow, so the prompt has no rule for those.
14. **CI uses only public core interfaces.** Staleness is checked from `horae eval --json` (`summary.stale`); `scripts/sync_recordings.py` (private eval internals) is a manual helper, neither run nor type-checked in CI.
15. **Earlier choices kept.** The model writes the whole block (no core transform renders `ranked`); no `transform` step (no plugin provides one); no `notify` step (the digest belongs to `pack-reminders`); the day comes from the sources' trusted params (`{{date}}`), since prompts have no template syntax.

### Core gaps

- **Installing a pack**: nothing copies a pack's `provides.pipelines` into an instance (M4, `horae plugin add`).
- **`horae run` with a recorded LLM**: the end-to-end test drives the `Runner` in Python with a replaying backend; a public replay backend would let packs test through the CLI.
- **Fixture plugins under real tool names**: the test uses `horae.engine.testing.FixturePlugins`; the built-in `fixture-source` only provides `fixture-source.serve`.
- **No value filter in the input view** (decision 4).
- **The guard holes** of horae-dev/horae#128 (decision 2).
- **`horae eval --record` writes only passing answers**, and the digest helpers used by `scripts/sync_recordings.py` are private.

## Development

The pack has no Python code. The Python environment exists to check the pack's files with the core itself (`horae` from the core's `main`, the exact commit pinned in `uv.lock`):

```sh
uv sync
uv run pre-commit install                       # optional: ruff and gitleaks on every commit
UV_LOCKED=1 uv lock --check                     # uv.lock matches pyproject.toml
uv run ruff check && uv run ruff format --check # lint the tests and scripts
uv run mypy                                     # types (strict)
uv run pytest                                   # pack files, golden cases, pipeline end to end
uv run horae eval daily-brief/rank-and-word --plugin-dir .          # the golden cases, offline
uv run horae eval daily-brief/rank-and-word --plugin-dir . --json   # summary.stale must be 0
```

- `tests/test_pack_files.py`: the manifest, pipeline and skill through the core's contracts and loaders (the pipeline also loaded into an instance as `horae run` reads it), the output schema accepted by every backend's structured output, and the pipeline's parameters (owner, bounds, due offsets, `partial`).
- `tests/test_golden.py`: `horae eval --json` offline over every case (real render, production validator, action guard): all pass, none stale, and the guard's result on each obeyed injection.
- `tests/test_pipeline_e2e.py`: the pipeline through the core's `Runner` with fixture plugins standing in for calendar, obsidian and github (the core's in-memory `FixturePlugins`), the golden case's answer replayed behind the real Anthropic render, and the note written to a temporary vault by the core's local sink. Also: linked GitHub tasks routed by `link_prefix`, an unlisted owner filtered by `orgs` (and failing the run without it), a second run makes no LLM call, a dry run writes nothing, the budget at a Sonnet-class price, and what each obeyed injection does today.
- `scripts/sync_recordings.py` (manual helper, not run in CI): refreshes the request digests of the **hand-written** answers after a prompt, schema or view change. It never rewrites a model's recording (any `model` but `claude-fixture-1`): it reports it stale, to be re-recorded.

A new golden case is a `evals/<case>.yaml` (the step input **before** the view: the `collect` output, `[{source, params, data}]`) and its answer in `evals/recorded/anthropic/<case>.json`.

## Contributing

This project is developed mostly by AI coding agents supervised by a human maintainer. Read [AGENTS.md](AGENTS.md) for the workflow (it applies to humans too), and the organization's [contributing guide](https://github.com/horae-dev/.github/blob/main/CONTRIBUTING.md) and [security policy](https://github.com/horae-dev/.github/blob/main/SECURITY.md).

## License

[Apache-2.0](LICENSE)
