# Claude pacing

## Why

DeepSeek spend is paced (`spend-limit.md`); Claude's own plan was not. On the night of 2026-09-24/25 the
$20/week DeepSeek limit held coders from 01:15, and the Project A sessions did the work themselves, as the
skill told them to: 35 Claude subagents at 80-180k tokens each, 81 edits of their own, 72 commits. The
account hit its 5-hour limit, and by morning most of the weekly all-models window was used with nearly four days
left. The DeepSeek limit didn't reduce the work; it moved it onto the scarcer budget. Long sessions added to
it: every turn re-reads the conversation, and this harness session was at 737k tokens.

## What it does

`launcher/ds_claude.py` keeps the latest reading of the plan's windows in the spend folder
(`claude.jsonl`) and turns it into a level, using the same measure as DeepSeek pacing: usage against an
even pace through the window plus a head start (25% for the bursty 5-hour window, 10% for the week).

| Level | When | What Claude does |
|---|---|---|
| on pace | ratio at most 1 | works as usual |
| lean | ratio over 1 | DeepSeek workers first; Claude subagents only for what they can't do, smaller models for lookups |
| tight | over 1.25 | no new Claude subagents but short lookups; doesn't take on work DeepSeek holds, queues it |
| hold | over 1.5, or 90% used | only coordinates DeepSeek workers; if DeepSeek is held too, hands over and stops until a reset |

The 5-hour and all-models windows set the level; a per-model window (Weekly · Fable) only says to avoid that
model.

**The two budgets cover for each other.** While Claude is tight or worse, DeepSeek pacing gets 20% more head
start (`CLAUDE_TIGHT_HEAD` in `ds_spend.py`), so DeepSeek takes more of the work. Its limits never move.
When DeepSeek refuses a worker and Claude is tight too, the refusal says to queue the work rather than do
it yourself.

**Where the reading comes from.** No script can read the plan windows. The desktop app gives every session a
tool that can (`mcp__ccd_session_mgmt__get_usage`), so the skill has the session call it and record the JSON:
at session start, before a fan-out, and about hourly in long runs. A reading gets more optimistic as it
ages, since usage only grows until a reset, so an old one still sets a floor. A window past its reset
counts as empty.

**Reminders.** The hook (`ds_hook.py`) adds the pace at session start, and after Agent calls and shell
commands at most every half hour a session, when the pace is off, the reading is missing, or it is over an
hour old. It also says when the session's own context passes 400k tokens, and suggests /compact at the next quiet
moment (the user compacts rather than starting new sessions).

## Evidence

- A real reading on 2026-09-25 (the 5-hour window barely used, the weekly one mostly used with nearly four
  days left) comes out **tight**: 1.49x an even pace, on pace again a few days later if idle.
- The hook, run as the real script against a temp spend folder, printed the pace after an Agent call. A
  second call in the same session was silent, and SessionStart in a folder with no workers still printed the
  pace.
- Probes with a temp spend folder, $5.20 of a $10 daily limit spent at 08:45, and `-Effort max`:
  - `probe-050` with no Claude reading was paced to effort high ("effort max runs as high while DeepSeek
    spending is ahead of pace").
  - `probe-051`, after the reading above was recorded, ran at effort max, as asked, with no easing line: the extra head start put DeepSeek back on pace. The only difference between the two launches was the recorded Claude reading.
- Unit tests: `tests/test_ds_claude.py`.
