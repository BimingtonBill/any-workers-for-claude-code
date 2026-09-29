# Goal 3 - Hierarchy: Claude, then a DeepSeek lead, then its workers

**Question.** Can the structure be Claude at the top, then a DeepSeek lead that launches its own
sub-agents, with reports flowing back up?

**Answer: yes, two ways, both tested 2026-09-21.** Use the process tier (`-CanSpawn`) for real work,
because every node gets its own manifest record, permissions and transcript. Use in-process subagents
(`-SubAgents`) for quick fan-out inside one worker.

```
Claude (you, the top lead: plans, reviews, integrates)
`-- lead-004-ui-audit.1              DeepSeek lead, depth 1, launched with -CanSpawn
    |-- research-085-radial.1        DeepSeek worker, depth 2, launched by the lead
    |-- research-086-pause-menu.1    (can message its siblings with -Crosstalk)
    `-- review-087-radial-notes.1
```

## Tier A: process leads (`-CanSpawn`)

A worker started with `-CanSpawn` gets two MCP tools from `launcher/ds_mcp.py`:

- `write_brief(name, content)` saves a brief into `runs/<lead run id>/briefs/`. The name must be
  `<kind>-<slug>`, and the brief must have a `# Goal` section.
- `spawn_workers(briefs, crosstalk?, effort?, max_turns?)` runs `launcher/ds-spawn.ps1`: up to 6 workers in
  parallel, read-only, in the lead's working folder. It waits for all of them and returns each report under
  its run id.

Lineage flows through environment variables that ds-agent.ps1 sets for every worker (`DS_RUN_ID`,
`DS_ROOT_RUN`, `DS_LINEAGE`, `DS_DEPTH`, `DS_MAX_DEPTH`, `DS_STATE_DIR`, `DS_PARENT_MODE`). A child's
manifest names its parent, and every child writes to the top lead's manifest, so
`python tools/ds_manifest.py --tree` shows the whole tree.

**Guards**

- Depth: Claude's workers are depth 1. `-MaxDepth` defaults to 2: a lead at depth 1 may spawn, a worker at
  depth 2 may not, and nothing starts at depth 3. The launcher enforces this, not the model.
- A read-only lead cannot start editing workers (`DS_PARENT_MODE`). Children are read-only by default.
- At most 6 children per `spawn_workers` call.
- Children inherit nothing from the lead's conversation. Each brief must stand alone, and the tool checks
  that it has a Goal.
- Timeouts: the lead's `MCP_TOOL_TIMEOUT` equals its own `-TimeoutMinutes`, so a long spawn isn't cut off.

**Evidence**

| Run | Result |
|---|---|
| `lead-probe-hierarchy.1`, `.2` | **Blocked.** Both tried shell-based spawning. The Bash allow rule never matched a command with quoted, spaced paths, and `Write(...)` isn't a valid file rule (Claude Code uses `Edit(...)`). Good behaviour: both leads stopped and reported instead of working around the denials (though `.1` tried three shell redirects first). This is why spawning became an MCP tool. |
| `lead-probe-hierarchy.3` | **Worked.** Two briefs written, `research-three-backwards.1` and `research-seventeen-sum.1` spawned with crosstalk, both correct (`ammag`, `72`). The lead checked both answers against the files and reported up, citing run ids. 8 turns for the lead, 5 each for the workers. |
| Dry runs | `-CanSpawn` at depth 2: refused. Any launch at depth 3: refused. |

## Tier B: in-process subagents (`-SubAgents`)

Adds Claude Code's own `Agent` tool to the worker. Subagents run inside the worker's process on the same
provider. The launcher sets `CLAUDE_CODE_SUBAGENT_MODEL=deepseek-flash` and `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`,
so a subagent asked for "opus" can't reach DeepSeek's more expensive Pro model. It also sets
`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1`, so subagents can't nest further.

**Evidence.** `probe-subagents.1` started two named subagents in parallel. Both answered correctly, and the
worker checked each against the file. All 37 model calls in its and its subagents' transcripts went to
`deepseek-flash`. The run's own usage figure (5k tokens in) left out the subagents' ~15k, so the launcher
now sums subagent transcripts into `subagent_tokens_in/out` in the manifest.

**Trade-offs.** Subagents have no manifest record, no run id, no messaging name of their own (a message from
one arrives under the parent's name) and no separate permissions. Use them for short lookups the worker
checks itself. Use Tier A when the parts deserve their own review.

## Why only two levels by default

- Anthropic's multi-agent research system (anthropic.com/engineering/multi-agent-research-system) is
  orchestrator plus workers, one level. It beat a single agent by about 90% on breadth-first research, at
  about 15x the tokens. Its failures included spawning far too many subagents and duplicated work from vague
  briefs.
- The Google scaling study found that centralized coordination contains errors (about 4x amplification
  against 17x for independent agents) and that multi-agent setups lose on sequential tasks.
- Project B: 0 of 9 implementation results were accepted without correction, and a verifying consolidator was
  itself wrong on its headline. Each level adds a place where errors get summarized away.

So the rules are:

1. **Claude stays the top reviewer.** A DeepSeek lead's report is a draft like any other. Claude spot-checks
   the children's reports (`runs/<child>/report.md`) behind the lead's summary, at least the claims the
   decision rests on.
2. **Give a lead a job that genuinely splits** into independent parts: a survey across many files, N
   similar records to research, or a fan-out of reviewers. Don't give it a design or integration task.
3. **Children are read-only.** Editing stays with `tools/ds_impl.ps1` worktrees that Claude launches and
   reviews.
4. **Raise `-MaxDepth` to 3 only for an experiment**, and record why in the brief.

## Reports going up

- Worker: `runs/<id>/report.md`, plus the `[ds-agent]` footer with run id and status.
- Lead: receives the children's reports as `spawn_workers` output, checks them, and writes one merged report
  citing child run ids.
- Claude: reads the lead's report, then opens child reports as needed. The manifest tree links them all.

## Seeing a lead's workers in the Background tasks panel

The Claude app's panel lists only commands the Claude session started, so a lead's workers don't appear on
their own. `launcher/ds-watch.ps1` fixes that with watcher commands Claude starts in the background:

- `-Children <lead>` exits as soon as the lead starts workers not yet watched, and prints for each one its
  `-Run` watcher command and a panel description numbered under the lead (`DeepSeek research #007.2: even
  sum (crosstalk)`).
- `-Run <run>` exits when that worker ends, printing its outcome and report, so Claude reviews each worker as
  it lands instead of waiting for the lead's merged report.

A watcher only waits; stopping one does not stop the worker.

**Evidence.** `lead-006-watch-probe.1` and `lead-007-watch-probe.1` each showed as one lead entry, one "watch
for new workers" entry and one entry per worker, and each worker entry reported its answer when that worker
finished. The first run exposed two bugs, both fixed:

- **Reused brief names break crosstalk.** A lead's worker named `research-three-backwards` had run before,
  so it ran as `.2` while its sibling was told `.1`, and the message could not be delivered. `write_brief`
  now refuses a name that already has runs.
- **A sibling that finishes can't receive.** The six-second worker exited before its sibling's message came.
  Workers told a sibling will send them something now wait for it (`wait_for_messages`, up to about 5
  minutes).

In `lead-007` both workers sent and received (`research-last-word.1`: `ammag`; `research-even-sum.1`: `72`).

## Leads can start coders (2026-09-22)

A lead's `spawn_workers` used to run only read-only workers in the lead's own folder. Now `impl-*` briefs
run as coders through `tools/ds_impl.ps1`, the same path Claude's coders take: each gets its own git
worktree under `local/impl/<name>`, a scope check against its `Owned files:` line, and its `Acceptance:`
commands. The lead gets back the scope and test results and reads the changed files to review them. It
cannot integrate: the worktrees wait for Claude, so every change still reaches the real files through
Claude's review. Coders are read-write only inside their worktree, so a read-only lead may start them too.
At most 3 per call, because each Rust worktree seeds its own `target/`.

**Stricter rules for a DeepSeek-written brief.** ds_impl runs acceptance commands itself, outside the
workers' permission system, so when `DS_RUN_ID` is set (a DeepSeek run started it):

- acceptance must start with a known test command (`cargo test`, `python -m unittest`, `npm test`,
  `go test`, `dotnet test`, ...; the project's `"leadAcceptance"` replaces the list) and contain no
  command separators, pipes, redirects, backticks, `$` or newlines;
- owned files may not match the project's `denyEdit`, AGENTS.md, CLAUDE.md or `.deepseek-agents.json`,
  since owning a file lifts its protection in the worktree;
- for everyone, owned files must be inside the project (no rooted paths, no `..`, not `.git`), since
  `-Integrate` copies them into it.

This limits the command a lead can name, not what the tests do: acceptance runs code the coder wrote,
exactly as it does for coders Claude starts.

**Evidence** (scratch repo `coder-probe`, a two-function Python library):

| Check | Result |
|---|---|
| ds_impl dry runs as a lead's coder | `python -m unittest ...` accepted; `python -c ...`, `cargo test; del x`, `../outside.py` and `AGENTS.md` (in denyEdit) each refused with a reason. As Claude's coder, `python -c` is still accepted and `..` is refused. |
| `lead-coders-probe.1` (read-only lead) | Started `impl-001-slugify.1` and `impl-002-wrap.1` in one call. They ran in parallel (65 s and 64 s), each in its own worktree, scope ok, acceptance `OK` (34 and 23 tests). The lead read both diffs, hand-checked edge cases, and gave "merge as is" for both, listing the spec choices it had fixed in the briefs. Main checkout untouched. |
| Records | Both coders: kind `impl`, parent `lead-coders-probe.1`, lineage `claude/lead-coders-probe.1/impl-...`, depth 2; two `pilot.csv` rows, `pending-review`. |
| Claude integrates | `-Integrate` both. The combined suite ran 57 tests, OK: the check neither coder could run, since each saw only its own tests. |

## A lead cannot brief a read-only worker to run commands (2026-09-23)

Project A's overnight run: `research-001-glow-emissive.1` and `research-003-snow-ice.1`, both children of
`lead-026`, died at `error_max_turns` (91 turns) with no report. The lead's own report says why: their
briefs told them to run `python` and `cargo` commands, which read-only workers have no shell for at all.
Both were rewritten and rerun with `max_turns` 200, so the night cost two wasted runs and the lead's time.
The launcher already warns Claude when a brief names commands the worker may not run; a lead had no such
check.

`write_brief` now refuses a non-`impl-` brief that names a command in backticks (the launcher's rule: a
tool word followed by whitespace or the end, or a `./` path; templates like `python <file>.py` and names
like `Cargo.toml` don't count), and says to rewrite it as reading and analysis, or make it an `impl-` brief.

**Evidence.** `lead-004-brief-guard.1`: asked to save a brief telling a worker to run
`python -m unittest discover -s tests` and `cargo test -p shared`, `write_brief` refused with the two
commands named. The lead rewrote the brief as a read-only job, added "Do not run any commands", ran it
(`research-002-summarise-agents.1`, ok, 3 turns), and reported both the refusal and its correction.

## A lead is told where each child's report is saved (2026-09-23)

`lead-026-visual-gaps.1` (Project A) wrote that one child's report "was not written to
`local/agents/runs/` by the harness" and that it had recovered the text from the spawn output. It was
wrong: `runs/research-006-snow-ice.1/report.md` is on disk, 25,935 bytes, written at 00:00, three minutes
before the lead finished, and the launcher writes that file on every path that produces a result. The
real gap was that nothing tells a lead where a child's report lives, so re-reading one means guessing a
path.

`ds-spawn.ps1` now prints `(also saved at <state dir>/runs/<run id>/report.md)` above each report, using
the launcher's own state-dir rule.

**Evidence.** `lead-006-report-path.1`: spawned one worker, was given the path, read it with `Read`
("11 lines, no error") and confirmed the file's body was "character-for-character" the report it had been
handed, plus the header comment and the `[ds-agent]` footer line the file adds.

## A lead's coder may own protected source; one coder per call (2026-09-23)

Project A's portal session reported four leads (lead-070, -071, -072, -102) that landed no code: every
coder they tried was refused with "a lead may not give a coder a protected file (crates/...rs matches
crates/**)". That rule treated the project's `denyEdit` as off limits to a lead's coders. But `denyEdit`
protects the main checkout from direct edits, and a coder only ever edits its own worktree; its work
reaches the checkout through Claude's `-Integrate`. So a lead may now give a coder protected source. The
files that govern workers themselves stay Claude's: `.deepseek-agents.json`, AGENTS.md, CLAUDE.md,
`.claude/**`, `tools/ds_*` and `tools/run_ds_queue.ps1`.

A lead runs one coder per `spawn_workers` call unless the project sets `"leadCoders"` (up to 6): each
Rust worktree seeds about 11 GB, and overlapping builds ran the machine out of memory the same day.

**Evidence.** Dry runs as a lead in a project protecting `textkit/**`: owning `textkit/slug.py` allowed;
AGENTS.md, `.deepseek-agents.json` and `tools/ds_impl.ps1` refused. Two impl briefs in one call refused
with the cap; `leadCoders: 2` raised it. `lead-009-protected.1` then started `impl-002-slugify.1`, which
owned the protected `textkit/slug.py`, finished in 37 s with scope ok and acceptance passing, while the
main checkout's copy stayed the untouched stub.

Not built: a lead sending its coder corrections (resume) before Claude reviews. A lead can read the
coder's worktree and put corrections in its report.
