<p align="center"><img src="https://raw.githubusercontent.com/horae-dev/.github/main/brand/horae-logo-500.png" width="96" alt="Horae logo"></p>

# pack-daily-brief

Horae pipeline pack: the morning note pipeline and its rank-and-word skill.

> 🚧 **Early (0.1.0, unreleased).** The pipeline and the skill are here and pass their checks against the core's `main`. The wording of the note has **not been judged by the maintainer yet**, and the golden answers are hand-written, not recorded from a model (see [Wording quality](#wording-quality)). The source plugins it reads are not all released either (see [What each source needs](#what-each-source-needs)).

## What it does

Every morning the `daily-brief` pipeline reads your calendar, your open Obsidian tasks and your GitHub work, has **one bounded LLM call** rank it and word it, and writes the result into an owned block of today's assistant note, `Daily/<date>.assistant.md`:

```markdown
<!-- horae:begin daily-brief -->
Two things are overdue, two events today and a planning meeting tomorrow.

## Now

- Send the insurance form (due Nov 6)
- Work on acme-org/widgets #42: cache misses on cold start
- 09:30 Team stand-up
- 14:00 Dentist
- Book the train tickets

## Tomorrow

- All day: School holiday
- 10:00 Quarterly planning
- Review acme-org/api #7: export pagination fix
- Water the plants

## Later

- Answer the mention in acme-org/docs #3: install guide
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
steps:
  - collect:
      - calendar.events: { start: "{{date}}", days: 2, timezone: "{{timezone}}" }
      - obsidian.open_tasks: {}
      - github.review_requests: { today: "{{date}}" }
      - github.assigned: { today: "{{date}}", skip_covered: true }
      - github.mentions: { today: "{{date}}", skip_covered: true }
  - llm:
      skill: daily-brief/rank-and-word
      model: default
      budget: { usd: 0.05, tokens_out: 2000 }
      on_unchanged_input: skip
  - act:
      - obsidian.write_note: { path: "Daily/{{date}}.assistant.md", mode: replace-owned }
      - obsidian.write_tasks: { mode: tasks }
```

- `{{date}}` and `{{timezone}}` come from the run (the trigger's time zone, else `horae.yaml`'s), so the day is the run's and a replay sees the same one.
- **Unchanged input costs nothing**: a second run on the same data reuses the cached answer (no LLM call).
- If a source fails, the run is `degraded` and the `act` step is skipped: no note is written from partial data. If the model's note is refused (schema, or the action guard), nothing is written either.
- The `obsidian.write_tasks` entry is there for the GitHub sources: they propose one `task.create` per item, and an intent no entry takes fails the run. Drop the three `github.*` sources and that entry together if you don't use GitHub.

### The skill

**What the model sees** (the input view): for each source its name (`source`) and today's date (`params.today` / `params.start`); for tasks, the description, due and scheduled dates and priority; for GitHub items, the repository, number, title, due date and kind; for events, the title, start, end, all-day flag and status. **Not** links, fingerprints, note paths, event locations, descriptions or calendar names, nor any other parameter. Titles are declared `free_text`, so with a cloud model the core de-identifies them first with your vocabulary (schemas decision 47) and puts the real names back in the note.

**What it answers** (the output schema, at most about 29 KB): `summary` (200 characters), `ranked` (at most 20 items: `kind`, `when`, a `line` of 100 characters) and exactly one intent, `note.replace_owned_block` of block `daily-brief` (fixed by the schema) with `content` of at most 2,500 characters and no path. The prompt asks for plain Markdown (headings, bullets), no links, images, HTML, checkboxes or emoji. Whatever the model writes, the core's action guard refuses images, embeds, HTML, unsafe links and URLs that are not in the input, and the note writer refuses block markers.

**Titles are untrusted.** They come from other people (a pull request title) or from text you pasted. The prompt treats the input as data, and the `hostile-title` and `injected-embed` golden cases check both sides: a model that words an injected title plainly passes; one that obeys is stopped by the guard.

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

2. **The pipeline.** Copy `pipelines/daily-brief.yaml` into `<instance>/pipelines/` (the core does not install a pack's pipelines yet). Edit your copy freely: the trigger, the sources you have, the note path.
3. **A model alias** named `default` in `horae.yaml` (or change `model:` in your copy), for example `default: { backend: anthropic, model: <model>, secret: ANTHROPIC_API_KEY, price: {...} }`. With a price, the step's budget (`usd: 0.05`) is checked before each call.
4. **The source and sink plugins** below, each installed and pinned the same way.
5. Check with `horae doctor`, try `horae run daily-brief --dry-run` (shows the diff, writes nothing), then `horae run daily-brief`.

## What each source needs

| Entry | Plugin | Needs | Status |
|---|---|---|---|
| `calendar.events` | [plugin-calendar](https://github.com/horae-dev/plugin-calendar) | secret `CALENDAR_ICS_URLS` (your ICS links); hosts `calendar.google.com`, `calendar.proton.me` | in review ([plugin-calendar#6](https://github.com/horae-dev/plugin-calendar/pull/6)) |
| `obsidian.open_tasks` | [plugin-obsidian](https://github.com/horae-dev/plugin-obsidian) | the vault as `paths.read` in `horae.lock` | planned ([plugin-obsidian#4](https://github.com/horae-dev/plugin-obsidian/issues/4)) |
| `github.review_requests`, `github.assigned`, `github.mentions` | [plugin-github](https://github.com/horae-dev/plugin-github) 0.1.0 | secret `GITHUB_TOKEN` (fine-grained, read-only: pull requests, issues, metadata); host `api.github.com` | released |
| `obsidian.write_note` (act) | plugin-obsidian 0.1.0 | the vault as `paths.write` in `horae.lock`; intent `note.replace_owned_block` | released |
| `obsidian.write_tasks` (act) | plugin-obsidian | intents `task.create`, `task.update_status` | planned (plugin-obsidian#4); the entry name is this pack's assumption |

A source you don't have must be removed from your copy of the pipeline (an unknown tool fails the run before anything is called). The skill copes with any subset: what is missing is simply not in the note.

## Wording quality

The tests prove the pipeline runs end to end and that every golden answer is schema-valid and passes its case. They **cannot** say whether the note reads well. That is for the maintainer to judge, on real mornings:

- the golden answers in `evals/recorded/anthropic/` were **written by hand** to show the intended note, not produced by a model (their `model` is `claude-fixture-1`). Record real ones with a live eval (it spends money, asks first):

  ```sh
  horae eval daily-brief/rank-and-word -i <instance> --alias default --plugin-dir . --live --record --max-usd 0.50
  ```

  (only answers that pass their case are written), then read each note: ranking, tone, length, what was left out;
- prompt and schema changes are judged the same way: before and after notes of every case in the pull request.

The full eval suite (more mornings, wording checks, per-backend recordings) is [#3](https://github.com/horae-dev/pack-daily-brief/issues/3).

## Development

The pack has no Python code. The Python environment exists to check the pack's files with the core itself (`horae` from the core's `main`, the exact commit pinned in `uv.lock`):

```sh
uv sync
uv run pre-commit install                       # optional: ruff and gitleaks on every commit
uv lock --check                                 # uv.lock matches pyproject.toml
uv run ruff check && uv run ruff format --check # lint the tests and scripts
uv run mypy                                     # types (strict)
uv run pytest                                   # pack files, golden cases, pipeline end to end
uv run horae eval daily-brief/rank-and-word --plugin-dir .   # the golden cases, offline
uv run python scripts/sync_recordings.py --check             # no stale golden answer
```

- `tests/test_pack_files.py`: the manifest, pipeline and skill through the core's contracts and loaders (the pipeline also loaded into an instance as `horae run` reads it), and the output schema accepted by every backend's structured output.
- `tests/test_golden.py`: `horae eval` offline over every case (real render, production validator, action guard), none stale.
- `tests/test_pipeline_e2e.py`: the pipeline through the core's `Runner` with fixture plugins standing in for calendar, obsidian and github (the core's in-memory `FixturePlugins`), the golden case's answer replayed behind the real Anthropic render, and the note written to a temporary vault by the core's local sink. Also: a second run makes no LLM call, a dry run writes nothing, an obeyed injection writes nothing.
- `scripts/sync_recordings.py` refreshes the request digests of the **hand-written** answers after a prompt, schema or view change. It never rewrites a model's recording (any `model` but `claude-fixture-1`): it reports it stale, to be re-recorded.

A new golden case is a `evals/<case>.yaml` (the step input **before** the view: the `collect` output, `[{source, params, data}]`) and its answer in `evals/recorded/anthropic/<case>.json`.

## Contributing

This project is developed mostly by AI coding agents supervised by a human maintainer. Read [AGENTS.md](AGENTS.md) for the workflow (it applies to humans too), and the organization's [contributing guide](https://github.com/horae-dev/.github/blob/main/CONTRIBUTING.md) and [security policy](https://github.com/horae-dev/.github/blob/main/SECURITY.md).

## License

[Apache-2.0](LICENSE)
