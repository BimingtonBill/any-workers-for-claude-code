# Goal 1 - Clarity: the run manifest

**Problem.** In the Project B and Project A projects a worker was known by a label such as `t72`, `i07`, `c01`,
`review-t67`, `crit124` or `resume-4f3a9c1e`. Nothing outside the lead's head said what `t72` was for, that
there were two different t72s, that `t81b` was a rerun of `t81`, or which run a `resume-*` continued.
Failed and timed-out runs were never logged at all (see `docs/lessons.md`).

**Decision.** Every run gets one manifest record. It is written by the launcher itself, so it can't be
forgotten, and it borrows its field names from the two standards for this: OpenTelemetry's GenAI agent spans
(agent id/name, conversation id, parent span) and A2A's task states. It is a local file, not a telemetry export.

## Names

| Thing | Format | Example | Rule |
|---|---|---|---|
| **Kind** | one word | `research`, `websearch`, `impl`, `review`, `analysis`, `critic`, `advisor`, `digest`, `lead`, `selftest`, `probe` | Says what sort of work it is. See the table below. |
| **Brief / task id** | `<kind>-<nnn>-<slug>.md` | `research-083-console-camera.md` | New briefs start with their kind. Take the number from the filesystem (`Glob`) right before writing. |
| **Run id** | `<task id>.<attempt>` | `research-083-console-camera.2` | `.1` is the first run of that brief and a fresh rerun is the next number. A `-Resume` continues the run it resumes: same run id, one more in its `resumes` count. There are no more `t81b` labels; `resume-<hex>` appears only when resuming a session the manifest has no record of. |
| **Title** | one sentence | "Why the console camera re-centres behind the moving character" | Taken from the first line under `# Goal` unless `-Title` is given. Written for an outsider. |
| **Lineage** | path | `claude/lead-004-ui-audit.1/research-085-radial.1` | Who launched whom, from Claude down. |

The run id is also the worker's **session name** (`claude --name`), so the name you see in the manifest is
the name other workers use to message it (see `crosstalk.md`).

Legacy names still work: the launcher and `tools/ds_manifest.py` map `t##` to research, `i##` to impl,
`c##` to digest, `review-` to review, `fix-` to impl, `analyst-` to analysis, `crit*` to critic,
`deep-#`/`report-#` to advisor, and `selftest`/`test*` to selftest. A label that matches nothing is kind `task`.

### Kinds

| Kind | What it does | Mode |
|---|---|---|
| research | finds and writes down facts; changes no code | read, or edit for notes only |
| impl | implements a bounded change in files it owns (`tools/ds_impl.ps1`) | edit, in a worktree |
| review | checks another run's work independently | read |
| analysis | analyses a finished test or acceptance run | read |
| critic | judges screenshots or output against a reference | read |
| advisor | read-only consultant on one question (the advisor-loop skill, `skill/advisor-loop/SKILL.md`) | read |
| digest | merges and verifies several other runs' output | read |
| websearch | searches and reads the web; no project files at all (`docs/design/websearch-injection.md`) | web only |
| lead | a DeepSeek lead that splits a job and runs its own workers (`hierarchy.md`) | read + spawn |
| selftest | checks the worker harness itself | edit (scratch) |
| probe | an experiment on the harness (this project) | any |
| task | fallback for a label that matches no kind | any |

The Mode column is convention: the launcher does not tie `-Kind` to `-Mode`.

## Record

`<stateDir>/runs/<run id>/manifest.json` holds the latest state. `<stateDir>/manifest.jsonl` gets one line per
event: `submit` (before the worker starts), `start`, `end`, and again `submit`/`start`/`end` for each resume. A
crash between them leaves a visible `submitted` or `working` record.
`runs/<run id>/` also keeps `brief.md` (the brief as sent; the launcher's own instructions are added on top as a
system prompt and are not in this file), `brief-resume-<n>.md` for each resume's follow-up, and `report.md`.
The report holds every turn's final text: a sibling's message that arrives after the report starts another
turn, and that turn is appended under a separator rather than replacing the report. A resume appends a
`## Resume <n>` section. A run that timed out or was cancelled has no `report.md`.
Alongside the manifest the launcher keeps `<stateDir>/<label>.json` while a run is live (its pid and the
`taskkill` line to stop it), `<stateDir>/runs.csv` (one row per run, for `ds_report.py`), and for a lead
`runs/<run id>/briefs/` (the briefs it wrote for its workers).

```json
{
  "schema": "ds-run/1",
  "run_id": "research-085-radial.1", "task_id": "research-085-radial", "attempt": 1,
  "kind": "research", "title": "Which console function draws the radial menu's ring",
  "project": "Project B", "dir": "C:\\...\\Project B",
  "parent_run_id": "lead-004-ui-audit.1", "root_run_id": "lead-004-ui-audit.1",
  "lineage": "claude/lead-004-ui-audit.1/research-085-radial.1", "depth": 2,
  "state": "completed",
  "mode": "read", "model": "deepseek-flash[1m]", "effort": "high", "effort_requested": "high", "max_turns": 80,
  "crosstalk": true, "subagents": false, "can_spawn": false, "delegation": 4, "forced": false,
  "brief": "...\\runs\\lead-004-ui-audit.1\\briefs\\research-085-radial.md",
  "resumed_session": null, "resumes": 0,
  "session_id": "...", "pid": 1234, "transcript": "...jsonl",
  "started": "...", "ended": "...", "seconds": 512,
  "turns": 88, "tokens_in": 91000, "tokens_out": 70000, "denied": ["Bash"],
  "report": "...\\runs\\research-085-radial.1\\report.md", "error": null
}
```

Fields to know:
- `effort` is what DeepSeek ran; `effort_requested` is what was asked (`medium` runs as `high`, `xhigh` as `max`;
  see `effort.md`).
- `delegation` is the project's delegation level at launch (1-5; runs before the change recorded 0-10), and `forced` says the launch was given `-Force`.
- `turns` and the token counts are totals across every turn and every resume of the run. `tokens_in` is
  uncached input.
- A `-SubAgents` run also carries `subagents_run`, `subagent_tokens_in` and `subagent_tokens_out`, which are 0
  until the run ends.

`state` follows A2A's task states where they fit: `submitted`, `working`, `completed`, `failed`,
`timed_out`, `canceled`. A record still `working` with no live pid means the launcher itself was killed. A
`timed_out` run keeps what the worker had written: a headless run prints nothing until it ends, so the
launcher rebuilds a partial `report.md` from the transcript and marks it partial (probe
`websearch-014-partial.1`). The partial can be thin, since it holds only the text the worker wrote between
tool calls.

## Reading it

```
python tools/ds_manifest.py                # table: run, kind, what it is for, launched by, state, turns, tokens, time
python tools/ds_manifest.py --tree         # hierarchy: Claude -> leads -> workers
python tools/ds_manifest.py --write        # also writes <stateDir>/MANIFEST.md
python tools/ds_manifest.py --legacy <runs.csv> --briefs <tasks/deepseek>   # an old project's log in the same terms
```

Run it from the project folder, or set `DS_PROJECT`. It reads the same manifest the launcher writes: `DS_STATE_DIR` if
set, else the project's `stateDir` from `.deepseek-agents.json`, else `local/agents`; `--manifest` overrides.

## Crashes and dead launchers (2026-09-24)

The Project A night of 2026-09-23 showed two gaps. `research-542` finished its work, opened one more
large image and died with exit code 1: the partial-report rescue ran only for timeouts, so its report
was lost. `impl-152`'s launcher died (the machine ran out of memory) and the run stayed `working` for
ever, so a watcher would have waited on it indefinitely.

- A crash now keeps a partial report too (`Save-PartialReport`, shared with the timeout path), and the
  launcher captures the worker's error output and puts its last line in the manifest's `error`.
- Every launch closes runs whose launcher died: a state file (`<state dir>/<label>.json`, removed by
  every launcher that ends normally) whose worker process is gone. Its manifest becomes `canceled`, with
  an `end` event in `manifest.jsonl`, and the state file is removed.

Evidence: `probe-034-crash` was killed 13 s in (taskkill of the worker process). The launcher printed
"kept the partial output" and "What it had written is in ...report.md"; the report starts "*(partial:
the worker crashed (exit code 1) before it could report ...)*" followed by the worker's part 1. The same
launch found a planted `probe-033-orphan` (state file with pid 999999, manifest `working`), printed
"closed probe-033-orphan.1 as canceled", and its manifest reads `canceled`, "its launcher stopped
without recording an end (found by probe-034-crash.1); the worker process 999999 is gone".

Two instructions were added to every worker's prompt from the same night: run builds and tests in the
foreground with the Bash timeout raised (coders slept 3-9 minutes at a time waiting on background
builds; `impl-525` slept about 37 of its 46 minutes), and stop once the task is done (`research-542`
crashed on an extra check it began because it "had budget"). These are guidance; the next night's runs
will show whether they hold.
