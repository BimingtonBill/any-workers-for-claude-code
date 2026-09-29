# Tracks: keeping coders, read-only workers and Claude busy at once

Measured in Project A, 2026-09-22 to 24 (three Claude sessions, ~3,000 Claude tool calls, 138 worker
runs, local transcripts and manifests only):

- Only one worker ran for 59% of the time any worker ran; 55 of Claude's 80 launch batches were a
  single worker. 78 of the 121 runs Claude started were coders, which are rightly serial (an ~11 GB
  build each, the machine's commit limit, build freezes during a long asset conversion). But during
  that conversion every worker ran one after another, all coders: read-only work, which builds
  nothing, was never run beside them.
- 78 coders, 8 review workers: Claude reviewed nearly every diff itself.
- The largest idle stretches with a worker running ended with the user's reply, after messages such
  as "One question is still waiting on you" (108 minutes with nothing started).

Changes: SKILL.md now plans three tracks (coders, read-only workers, Claude's own part), says one coder
at a time is not one worker at a time, starts a review of each coder as it lands, and keeps unblocked
work going while a question waits. `ds_impl.ps1` prints, after an accepted coder, a ready review command
to start with the next coder.

`run_ds_queue.ps1` was left alone: it launched 14 times in these sessions against 291 `ds_impl.ps1`
launches, so a read-only lane there would not have changed the night.

## Evidence

`review-035-probe-review`: the review command printed by `ds_impl.ps1`'s own lines, run against a
worktree whose brief asked only for a docstring on `label()` in `launcher/ds_spend.py`, and whose diff
also changed `HEAD_START` from 0.2 to 0.5. Verdict "reject", citing `ds_spend.py:52`, the base value,
the effect on pacing and the tests it would fail; the docstring itself judged fine. 108 s, $0.03. A
read-mode reviewer has no shell, so it compared the files by reading and computed the test outcomes by
hand, and said so.

## Claude subagents named and recorded like workers (2026-09-25)

Project A's panel showed "Build field notes (impl-183)" beside "DeepSeek research #184: ...": a Claude
subagent that had taken over DeepSeek impl #183. The user asked for that to be the standard. In a project
that runs workers, `ds_hook.py` now refuses an Agent call unless its description reads
`Claude <kind> #<nnn>: <what>`, and gives the corrected name: the task's own number when the description
names a DeepSeek task id, else the next after the highest among the session's last 20 numbered entries
(a hook test's "#999" early in the harness session had pushed a plain maximum to #1000). It records each
subagent in the manifest (provider `claude`, run id `<kind>-<nnn>-claude-<slug>.<attempt>`): at
PostToolUse, finished at once for a foreground subagent, and at SubagentStop for a background one.

What Claude Code sends was captured first with a logging hook: PostToolUse carries `agentId`,
`resolvedModel` and `status` ("completed" or "async_launched"); SubagentStop carries `agent_id`,
`agent_transcript_path` and `last_assistant_message`, and for a foreground subagent it arrives before
PostToolUse. Live probes in this project: an unformatted Agent call was refused with
"Claude <kind> #056: ..."; `Claude probe #056` (foreground) and `#057` (background) were both recorded
`completed` with model claude-haiku-4-5, 1 turn, about 37-39k tokens in, and their final text as the
report; `ds_manifest.py --tree` lists them as `[claude probe]`, and the morning report counts them.
