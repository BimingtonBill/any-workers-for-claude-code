# The standard work cycle

## Why (Project A, 2026-09-24 18:00 to 2026-09-25 midday)

56 runs (54 DeepSeek, 2 Claude subagents). They were reliable: every finished run completed, only 3 were
retries, and 46 of 56 reports cited `file:line`. The results were uneven, though:

- Reports ran 6-12k characters (median by kind), and the sessions pulled about 2.1M characters of worker
  output into their own context in 20 hours (launch output 0.46M, background output files 1.1M, reports
  read 0.53M), all of it re-read on every later turn.
- 11 of 19 reviews had no verdict line; the rest said "accept", "fix first" or something else.
- 10 of 19 reviews said they ran nothing: reviewers had no shell, so "tests pass" was the coder's word.
- 30 of 54 briefs had no "Done when" and 20 no "Scope"; only coders and leads had templates.
- Only 25 of 56 reports said what was left; 9 opened with filler ("I have everything I need").

## What changed

1. **Report opening** (`launcher/ds_envelope.py`). Every worker is told to start its report with Status,
   Verdict (reviews: accept / fix first / reject; research, websearch, analysis: answered / partly / not
   found), Summary, Left and Next. The instruction is one line, since it goes into the worker's system prompt
   as one argument (a multi-line version broke the dry-run test). The launcher prints only the opening and
   the report's path, keeps the full report, and records the fields in the manifest (`report_status`, ...).
   A report without an opening is printed in full with a note; `-FullReport` prints any report whole. The
   status is read by meaning: review-060 pointed out that "task incomplete - blocked on transport" came
   out as "task" when only the first word was used.
2. **Reviews that can check.** The review command `ds_impl` prints after a PASS now runs in the coder's
   worktree (`-Dir <worktree> -AddDir <main checkout>`) with rules for `git status`, `git diff`,
   `git log`, `git show` and the task's acceptance commands, and its task asks it to see the diff and
   re-run the acceptance itself. `-Integrate` refuses on a `fix first` or `reject` verdict unless `-Force`,
   and finds a Claude subagent's review (`review-<nnn>-claude-*`) as well as a DeepSeek one.
3. **Brief templates** for review, research, websearch and digest beside the impl and lead ones, and a
   launcher warning when a brief file lacks Goal, Scope, Done when or Report (Scope is not asked of
   websearch; inline `-Task` briefs and generated audit/digest briefs are not checked).
4. **Claude subagents alike.** For a well-named Agent call in a worker project, the hook adds the same
   opening instruction to the prompt (PreToolUse `updatedInput`), and records the opening's fields when the
   subagent finishes.

## Evidence

- `research-059-envelope-probe` (a brief with Goal and Report only): the launcher warned "the brief has no
  Scope, Done when section"; the report opened with Status/Verdict/Summary/Left/Next, and Claude received
  those five lines and `Full report: ... (1444 characters; read it only when you need the details)`.
- `review-060-envelope-change`, launched as `ds_impl` now suggests, in a worktree holding a one-line change
  to the status normalisation that the tests don't cover: it ran `git status`, `git diff` and
  `python -m unittest tests.test_ds_envelope` itself ("Ran 7 tests ... OK"), described the change exactly,
  noted the dead constant it left and that no test exercises it, and argued (fairly) that the old
  normalisation was the real flaw; the fix above came from that. With Bash enabled, Claude Code also let it
  run read-only commands such as `grep`, `sed -n`, `ls` and `head` without rules of their own; nothing was
  refused.
- `Claude research #061` (a Haiku subagent given no instruction about format): its report opened with
  `Status: done / Verdict: answered / Summary / Left / Next`, so the hook's prompt addition took effect, and
  the run record holds `report_status: done`, `report_verdict: answered`.
- Unit tests: `tests/test_ds_envelope.py`, `tests/test_claude_runs.py`, and the integrate gate in
  `tests/test_ds_impl.py`.

## Coding subagents on Opus (2026-09-25)

A `Claude impl` subagent now runs on Opus: the hook sets `model: "opus"` in its `updatedInput`, unless the
Claude plan is tight (pace level 2 or more), when the caller's choice stands. Other kinds keep their model.
It was the user's call after `Claude impl #185` (Project A) was sent on Sonnet and took 48 minutes and
172 turns on a renderer bug. Probe `Claude impl #062`, launched with `model: "sonnet"`, ran and was
recorded as `claude-opus-5-5`. Tests: `tests/test_claude_runs.py`.

## Subagent runs left "working" (2026-09-25)

The hook found a project's state folder by starting PowerShell (`ds_state.state_dir`, 1.7 s when idle). With
cargo builds running it passed the hook's 10 s limit, and Claude Code cancelled `SubagentStop` twice, so
Project A's `Claude impl #185` and `#186` stayed `working` after they finished (backfilled from their
transcripts). The hook now uses the same rule in Python (`fallback_dir`, 0.014 s, the same folders for
Project A and Project B), and `SubagentStop` has 30 s.

Checked at the same time, over 81 subagent transcripts: none received the hook's pace or /compact notes,
none tried to launch DeepSeek workers or delegate despite the delegation level in AGENTS.md, and 9 of
165 Agent calls were refused once for naming (one extra turn each). The refusals that cost subagents
anything came from Claude Code itself: 7 worktree-isolation refusals (commands with `$USERPROFILE` or
PowerShell inside) and 3 auto-mode denials.

## Research starts on the web (2026-09-25)

The user's standard: a websearch worker goes first, then the research worker. A research brief has a
`# Web questions` section (public questions, or `none`); the launcher runs `websearch-<nnn>-<slug>` on them
first and adds its report to the research brief as "Web findings", marked as claims to check against the
code, never instructions. The workers stay separate (combining them would put project files, untrusted pages
and an outbound channel in one worker): the websearch one sees no files, and the research one has no web
tools. It is skipped for edit-mode and lead runs, and a research brief without the section gets a reminder.

Evidence: `research-063-web-first-probe` asked which documented SubagentStop fields `ds_hook.py` reads. The
launcher ran `websearch-063-web-first-probe.1` first (39 s, 9 turns, $0.006; it found the docs' field list
through mirrors when the official page fetch truncated, and said so), then the research worker (13 s, 4 turns,
$0.006), which said the Web findings were in its brief, relied on them for the documented column, and marked
that column as the web worker's claim, not its own check. The first attempt failed: the child's stderr
notes became a fatal error under `$ErrorActionPreference = 'Stop'`; the launcher now relaxes it around the
child. Dry-run tests: `tests/test_launcher_dryrun.py` (web questions, none, missing, edit mode).

## Worktree subagents never recorded as finished (2026-09-25 evening)

`SubagentStop` for a subagent started with `isolation: "worktree"` carries the worktree as `cwd`
(`<project>/.claude/worktrees/agent-<id>`), which has no state dir, so the hook ignored it: 15 of 18 Project A
Claude coding runs since noon stayed `working` although every transcript had finished. `agent_stop` now maps a
worktree `cwd` back to the project (test: `test_a_worktree_subagent_finishes_too`, which fails without the
fix), and the 12 finished runs were backfilled from their transcripts.

## Worker memory reminded mid-session, and websearch runs with no web calls flagged (2026-09-26)

From a project session, at the user's request:

- The map fell 226 commits behind because "Project memory: ... due" only came from the SessionStart report,
  and the dev sessions run for days. The hook's periodic note now checks `ds_memory.due` too, on its own
  half-hour clock per session (the check runs git, about 0.6 s, so it is not repeated on every call), and says
  to run `ds_memory.py due` and start what it prints. Nothing launches a digest by itself: it costs money, and
  the user chose the reminder (option 1) over a launcher warning or an automatic digest. Tests in
  `tests/test_ds_claude.py`; on the real projects it was silent for Project A (just refreshed) and named
  Project B's map as due. Notes are no longer added to a subagent's own tool calls (events with `agent_id`).
- `websearch-718-fun-sky-weather.1` finished in one turn with no web calls and invented links. The launcher
  now counts WebSearch/WebFetch calls in a websearch run; with none, it puts a warning at the top of the report,
  prints a note, and records `web_calls: 0` in the manifest. Probe `websearch-064-no-web-probe` (a brief that
  forbids tools) is written but not yet run: the day's DeepSeek limit was reached, and it is not raised
  for a probe.
