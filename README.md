<p align="center"><img src="https://raw.githubusercontent.com/horae-dev/.github/main/brand/horae-logo-500.png" width="96" alt="Horae logo"></p>

# pack-daily-brief

Horae pipeline pack: the morning note pipeline and its rank-and-word skill.

> 🚧 **Early (0.1.0, unreleased).** The plugins the pipeline needs are merged (plugin-obsidian's `obsidian.open_tasks` source and `obsidian.write_task` sink, [plugin-obsidian#4](https://github.com/horae-dev/plugin-obsidian/issues/4)), see [What each source needs](#what-each-source-needs) for what is released. The pipeline and the skill pass their checks against the core's `main`, with stand-ins of the plugins in their real shapes (plugin-obsidian's data is the output of its own code). The wording of the note has **not been judged by the maintainer yet**: the golden answers are hand-written, not recorded from a model (see [Wording quality](#wording-quality)).

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
      # Open tasks of the vault (plugin-obsidian), by due date, with `source_fingerprint` on
      # the tasks Horae created from another source. `limit` bounds what the model sees; the
      # source says how many it left out (`more`) and the note then says the list is incomplete.
      - obsidian.open_tasks: { limit: 100 }
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
      # The github sources' tasks (plugin-obsidian's task sink, which appends them to its
      # owned `Tasks.assistant.md`; `note:` changes the file). A task's link is routed only
      # under a trusted `link_prefix`; without this entry, or with a link outside its prefix,
      # the task is unrouted and the run fails.
      - obsidian.write_task: { mode: tasks, link_prefix: "https://github.com/Acme-Org/" }
```

- `{{date}}` and `{{timezone}}` come from the run (the trigger's time zone, else `horae.yaml`'s), so the day is the run's and a replay sees the same one.
- **`Acme-Org` is a placeholder**: replace it with your GitHub owner, in its canonical case (as it appears in github.com URLs), in the three `orgs` and in `link_prefix`. For several owners, list them all in `orgs` and add one tasks entry per owner, each with its own `link_prefix`; the prefixes must not overlap (`https://github.com/Acme-Org/` and `https://github.com/Other-Org/` are fine).
- **Unchanged input costs nothing**: a second run on the same data reuses the cached answer (no LLM call).
- **Budget**: at most US$0.15 and 4,000 output tokens per run. With a priced alias, `max_tokens` is the `tokens_out` lowered so that the input plus the output fit the `usd`; at a Sonnet-class price a busy morning still gets the full 4,000 (tested). Each GitHub kind reads at most 20 items.
- The GitHub sources propose one `task.create` per item, with the item's link (plugin-github 0.2.0). The `obsidian.write_task` entry applies them (it appends them to plugin-obsidian's own `Tasks.assistant.md`); its trusted `link_prefix` is the only place their links may point to, and an intent no entry takes fails the run. Drop the three `github.*` sources and that entry together if you don't use GitHub.

### When there is no note

The note is written only when the whole run succeeds. A missing note (or one still showing an older day's block) means one of these, and `horae run` / the run journal says which:

- **a source failed** (GitHub rate limited, the vault unreadable, every calendar down): the run is `degraded`, the `act` step is skipped and nothing is written from partial data. One calendar failing out of several does **not** stop the brief: `partial: true` keeps the others' events and the note says the calendar list is incomplete (a partial brief beats no brief). GitHub's own `partial` and `truncated` results are flagged the same way;
- **the model's answer was refused** (schema, or the action guard): the step is `degraded` and nothing is written;
- **applying failed**, for example note content carrying a block marker: the run `failed` and nothing was applied.

### The skill

**What the model sees** (the input view): for each source its name (`source`), today's date (`params.today` / `params.start`) and whether its list is complete (`data.partial`, `data.truncated`, `data.complete`, `data.errors`); for tasks, the description, due, scheduled and start dates, priority and `source_fingerprint`; for GitHub items, the repository, number, title, due date and kind; for events, the title, start, end, all-day flag and status (`confirmed` or `tentative`). **Not** links, item fingerprints, note paths, event locations, descriptions or calendar names, nor any other parameter.

**De-identification.** With a cloud model the core scans **every** string of the viewed input for your vocabulary's terms (schemas decision 47 B) and puts the real values back in the note. Titles and task descriptions are declared `class: free_text`, which marks them as text written by other people and is counted in the request's de-identification summary; it does not change what is scanned.

**What it answers** (the output schema, at most about 29 KB): `summary` (200 characters), `ranked` (at most 20 references: `kind`, `when`, `source` and `index`, the item's position in that source's list) and exactly one intent, `note.replace_owned_block` of block `daily-brief` (fixed by the schema) with `content` of at most 2,500 characters and no path. The note is the only place the model words the items; `ranked` lets the evals check the ranking without parsing the note.

**Titles are untrusted.** They come from other people (a pull request title, an invitation) or from text you pasted. The prompt treats the input as data and forbids copying checkboxes, `[[`, links, URLs, emoji, `::`, `%%`, tags or `horae:` text; titles are copied as plain words. The core's action guard refuses images, embeds, HTML and URLs that are not in the input, and the note writer refuses block markers. Since the core's guard decision 55, the guard also refuses Tasks checkboxes and signifiers, wikilinks, `::`, `%%`, block markers and `#tags` in the note, and the skill sets `links: none` so that no link or URL passes, even one that is in a viewed title. The `injected-*` golden cases and the end-to-end tests record that each payload is refused (`guard: violation`).

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
| `obsidian.open_tasks` | [plugin-obsidian](https://github.com/horae-dev/plugin-obsidian) `main` (#4) | the vault as the single `paths.write` entry in `horae.lock` (the plugin takes one vault path, read or write) | merged ([plugin-obsidian#8](https://github.com/horae-dev/plugin-obsidian/pull/8)), not released |
| `obsidian.write_note` (act) | plugin-obsidian | the vault as `paths.write` in `horae.lock`; intent `note.replace_owned_block` | merged, not released |
| `obsidian.write_task` (act, `mode: tasks`) | plugin-obsidian `main` (#4) | the same vault path; intents `task.create`, `task.update_status`; appends to the owned `Tasks.assistant.md` (`note:` to change it) | merged ([plugin-obsidian#8](https://github.com/horae-dev/plugin-obsidian/pull/8)), not released; `link_prefix` is checked by the core, the plugin ignores it |

A source you don't have must be removed from your copy of the pipeline (an unknown tool fails the run before anything is called). The skill copes with any subset: what is missing is simply not in the note.

## Wording quality

The tests prove the pipeline runs end to end and that every golden answer is schema-valid and passes its case. They **cannot** say whether the note reads well. That is for the maintainer to judge, on real mornings:

- the golden answers in `evals/recorded/anthropic/` were **written by hand** to show the intended note, not produced by a model (their `model` is `claude-fixture-1`). The cases check properties (how many items are ranked, references, words the note contains), not exact text, so a real model's answer can pass them. Record real ones for the mornings with a live eval (it spends money, asks first):

  ```sh
  for case in busy-morning empty-day hostile-title long-agenda non-english-titles \
      partial-sources second-day snoozed-and-cut; do
    horae eval daily-brief/rank-and-word -i <instance> --alias default --plugin-dir . \
      --case "$case" --live --record --max-usd 0.50
  done
  ```

  (only answers that pass their case are written), then read each note: ranking, tone, length, what was left out. Don't record the `injected-*` cases: their answers are a model that **obeyed** an injection, written by hand on purpose;
- prompt and schema changes are judged the same way: before and after notes of every case in the pull request.

## Evals

- **Offline, on every pull request and push to `main`** (`ci.yml`, job *Golden cases*): `horae eval --json` over every case. No network, no secret, no cost. The job fails on a failed or errored case, on a stale recording (`horae eval` itself only warns) and when no case ran. The 13 cases are 8 synthetic mornings (an empty day, a busy one, a day with more items than the note can list, non-English titles, incomplete sources, a hostile one) and 5 where a model that **obeyed** an injection is refused by the action guard. They assert properties only (counts, references, words), never exact text.
- **Live, on a release tag `v*`** (`release-evals.yml`): `scripts/live-eval.sh` sends each case that is expected to pass the guard (not the `injected-*` ones) once to the real model with `horae eval --live`, under one spend cap (`EVAL_MAX_USD`, enforced by the core's `--max-usd`: a case whose worst case does not fit what is left is refused). The merged JSON report is attached to the GitHub release as `live-eval-report-<tag>.json`, also when a case failed. A tag push creates no release: the workflow makes a **draft** one when there is none, for you to publish. Never runs on a pull request.

**What the maintainer must create** (the repository's settings are not touched by the workflows):

1. An environment named **`evals`** (Settings > Environments) with a **required reviewer**, and deployment tags restricted to `v*`.
2. In that environment, the secret **`ANTHROPIC_API_KEY`** (an environment secret, not a repository one; a key with its own spend limit is best).
3. Optionally edit `EVAL_MODEL`, `EVAL_PRICE_IN`, `EVAL_PRICE_OUT` (US dollars per million tokens) and `EVAL_MAX_USD` in `release-evals.yml`.
4. Optionally make *CI OK* a required status check on `main`.

To run it by hand: `ANTHROPIC_API_KEY=... EVAL_MODEL=... EVAL_PRICE_IN=3 EVAL_PRICE_OUT=15 EVAL_MAX_USD=1 EVAL_OUT=report.json RUNNER_TEMP=$(mktemp -d) scripts/live-eval.sh`.

## Design

Decisions taken with the maintainer ("go with what you think is good for the project") on the review of #4:

1. **GitHub tasks carry links, so they are routed by owner.** plugin-github 0.2.0 sends each task's `link`; the core routes a linked task only to a `mode: tasks` entry whose trusted `link_prefix` covers it. The pipeline uses one owner, `Acme-Org` (a placeholder, canonical case), in `link_prefix: "https://github.com/Acme-Org/"` and in `orgs: [Acme-Org]` on all three GitHub calls, so an item of another owner is filtered out at the source instead of failing the run. More owners: one tasks entry per owner, with disjoint prefixes.
2. **Obeyed injections.** The prompt forbids that text outright; the core's note guard (decision 55) refuses it and `skill.yaml` sets `links: none`. A golden case per payload (embed, link, checkbox, wikilink, plain marker) expects `guard: violation`, and the end-to-end tests check that the step degrades and no note is written.
3. **Stand-ins of the plugins.** The end-to-end tests do not start the plugins; the stand-ins mirror the real shapes: plugin-obsidian `main`'s `obsidian.open_tasks` output (the golden cases' data was **generated by running its code** on a vault of the cases' task lines, with horae-sdk 0.3 and the Horae tasks written by its `task.create` sink), plugin-github 0.2.0's data and `task.create` (with `link`), plugin-calendar `main`'s `calendar.events`. The tasks sink is `obsidian.write_task`. The pack asks for `limit: 100` tasks; the source's `more`, `output_cut`, `withheld` and `hidden` are viewed, and the note says the tasks list is incomplete when any of them reports tasks left out (`snoozed-and-cut` case). Snoozed tasks (a `⏰` reminder in the future) are left out unless due today; `edited` is not viewed.
4. **No duplicate GitHub items from day two.** The tasks created from GitHub items come back as open tasks with a `source_fingerprint` starting with `github:`; those are left out, since GitHub lists the item itself (`second-day` case). The input view selects fields, it cannot filter by value, so the view shows `source_fingerprint` and the prompt leaves those tasks out. A deterministic filter needs a view filter or a transform in the core.
5. **Budget.** `usd: 0.15`, `tokens_out: 4000`, `max_items: 20` per GitHub kind; a test with a Sonnet-class price checks that a busy morning still gets at least 1,500 output tokens (it gets 4,000).
6. **Shorter lines.** A GitHub item is named by its repository without the owner (`api #7`).
7. **Incomplete lists are said.** The view shows `partial`, `truncated`, `complete` and `errors`; the note then says which list is incomplete (`partial-sources` case).
8. **Reviews and mentions are today's work.** `due_in_business_days: 0` and `due_in_days: 0`: their due date is the day they are collected, never a made-up deadline, and the prompt never calls them overdue. An assignment keeps its milestone's date.
9. **`horae:` text.** The prompt forbids copying it in any form; a copied plain marker is refused by the core's guard (`injected-marker` case).
10. **A partial brief beats no brief.** `partial: true` on `calendar.events`: one failing calendar no longer stops the brief (see [When there is no note](#when-there-is-no-note)).
11. **One wording.** `ranked` holds references (`kind`, `when`, `source`, `index`), not lines; the model words each item once, in the note.
12. **Property checks.** The golden cases assert properties (lengths, references, contained words) instead of exact outputs, so a live recording can pass them; the hostile payloads are in fields the view shows (titles, task descriptions as plugin-obsidian parses them).
13. **Ranking rules.** Tentative events are marked, multi-day events are listed while they last, a task's `due` before today is overdue while a past `scheduled` date is today's work, and a task whose `start` is after today is left out. The calendar source already drops cancelled events and reads only today and tomorrow, so the prompt has no rule for those.
14. **CI uses only public core interfaces.** Staleness is checked from `horae eval --json` (`summary.stale`); `scripts/sync_recordings.py` (a manual helper on the public `horae.evals.request_digest`) is not run in CI.
15. **Earlier choices kept.** The model writes the whole block (no core transform renders `ranked`); no `transform` step (no plugin provides one); no `notify` step (the digest belongs to `pack-reminders`); the day comes from the sources' trusted params (`{{date}}`), since prompts have no template syntax.

### Core gaps

- **Installing a pack**: nothing copies a pack's `provides.pipelines` into an instance (M4, `horae plugin add`).
- **Fixture plugins under real tool names**: the test uses `horae.engine.testing.FixturePlugins`; the built-in `fixture-source` only provides `fixture-source.serve`.
- **No value filter in the input view** (decision 4).
- **`horae eval --record` writes only passing answers.**

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
- `tests/test_workflows.py`: the workflows' rules (offline eval gated on the summary; the live eval only on `v*` tags, behind `evals`, with the one write permission on a job without the key; actions pinned; no expression in `run:`).
- `tests/test_pipeline_e2e.py`: the pipeline through the core's `Runner` with fixture plugins standing in for calendar, obsidian and github (the core's in-memory `FixturePlugins`), the golden case's answer replayed by the core's public `ReplayBackend` behind the real Anthropic render, and the note written to a temporary vault by the core's local sink. Also: linked GitHub tasks routed by `link_prefix`, an unlisted owner filtered by `orgs` (and failing the run without it), a second run makes no LLM call, a dry run writes nothing, the budget at a Sonnet-class price, and what each obeyed injection does today.
- `scripts/sync_recordings.py` (manual helper, not run in CI): refreshes the request digests of the **hand-written** answers after a prompt, schema or view change. It never rewrites a model's recording (any `model` but `claude-fixture-1`): it reports it stale, to be re-recorded.

A new golden case is a `evals/<case>.yaml` (the step input **before** the view: the `collect` output, `[{source, params, data}]`) and its answer in `evals/recorded/anthropic/<case>.json`.

## Contributing

This project is developed mostly by AI coding agents supervised by a human maintainer. Read [AGENTS.md](AGENTS.md) for the workflow (it applies to humans too), and the organization's [contributing guide](https://github.com/horae-dev/.github/blob/main/CONTRIBUTING.md) and [security policy](https://github.com/horae-dev/.github/blob/main/SECURITY.md).

## License

[Apache-2.0](LICENSE)
