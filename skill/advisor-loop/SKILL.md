---
name: advisor-loop
description: Run DeepSeek advisors while you keep working - a deep 12-15 minute researcher that settles one hard question at a time with evidence from code/data (cycles continuously), and a quick 3-4 minute round reviewer run once per test round that lists what is not verified yet. Use when the user asks for advisors, a consultant, "a second opinion that keeps running", inspiration/top-down review during a long iteration, or runs /advisor-loop.
---

# Advisor loop: a deep researcher that cycles, plus a quick review per round

Two read-only DeepSeek workers (via the `deepseek-agents` skill's `ds-agent.ps1`) run in the background and hand you reports. You read each one **critically**, act on what is good, keep a shared situation file current, and relaunch. You never wait on them; they overlap your own work.

- **Deep advisor:** cycles continuously, **one current question at a time**.
- **Quick advisor:** a **round review**, launched once per test round after you record the round's results. It lists verification gaps.

## What the first long run taught (Project B console UI port, 2026-09-19: 7 deep and 14 quick reports)

- **The deep advisor pays for itself.** Three of its seven answers became fixes, among them a state the code wrote as floats while the engine read int32.
  - It re-answered the same question three times in a row while the question list lagged behind the work.
  - Hence: exactly one "Current deep question", and a list of answered questions it must build on.
- **The quick advisor, run every 3 minutes, produced 14 reports in about 40 minutes.**
  - After about 5 reports it mostly repeated itself.
  - About half of its claims about engine internals were wrong.
  - Its "this was never tested" warnings were right: they predicted 2 of the user's 4 later bug reports.
  - Hence: run it once per round, framed as a verification-gap review, with engine claims labelled as leads.

## Files (in the project)

- **`local/advisor/situation.md`** (or any git-ignored path) is **the only input you maintain**. Keep it short, because a long, historical file makes the advisors repeat stale ideas. Archive old content instead of letting it grow. Sections:
  - `## Now`: the round, what is being worked on next, the install state.
  - `## Current deep question`: **one** question, with pointers (addresses, files, what is already implemented). Write "none" when you have none.
  - `## Verified working`: each item with how it was verified.
  - `## Unverified or known broken`: the quick reviewer's main input.
  - `## Answered deep questions`: a one-line answer each, plus the report name.
  - `## Settled / do not re-suggest`: checked facts and advisor claims that turned out wrong.
  - `## Leads for later`: candidate questions that are not current.
- **Briefs:** `tasks/advisor.md` (quick) and `tasks/advisor-deep.md` (deep); templates below.
- **Reports:** `local/advisor/report-<n>.md` and `local/advisor/deep-<n>.md`.

## Launch (in the background)

```bash
# deep: ~12-15 min, relaunch as each one lands
powershell -NoProfile -ExecutionPolicy Bypass -File "<skills>/deepseek-agents/ds-agent.ps1" -TaskFile "tasks/advisor-deep.md" -Dir "<project>" -Label deep-<n> -Mode read -Effort high -MaxTurns 70 -TimeoutMinutes 18 > local/advisor/deep-<n>.md 2>&1
# quick: ~3-4 min, once per round
powershell -NoProfile -ExecutionPolicy Bypass -File "<skills>/deepseek-agents/ds-agent.ps1" -TaskFile "tasks/advisor.md" -Dir "<project>" -Label report-<n> -Mode read -Effort high -MaxTurns 30 -TimeoutMinutes 6 > local/advisor/report-<n>.md 2>&1
```

Use the Bash tool with `run_in_background: true`; you are notified when each finishes. Read the report with `tail -n +2`, because the first line is a model notice.

## The deep cycle (continuous)

1. Before each launch, make sure "Current deep question" holds exactly one question that serves the work you are doing now or next, with pointers. Never relaunch with a question that has already been answered.
2. When a report lands:
   - Check its citations mechanically first if the project has a checker (here: `python tools/check_citations.py local/advisor/deep-<n>.md`), then read it critically and verify one cheap claim with a grep or a single test before acting.
   - Move the question to "Answered deep questions" with a one-line answer and the report name.
   - Tell the user in 3-5 bullets what you accept, what is wrong or stale and why, and what is new.
3. Set the next current question: often the report's proposed one, if it serves the current work, or one from "Leads for later". Then relaunch. If nothing is worth a deep question, write "none" and pause the deep advisor.

## The quick round review (once per round)

1. After a test round, record its results in "Now", "Verified working" and "Unverified or known broken", then launch one quick review.
2. When it lands, act on at most the top gaps. Put each rejected claim under "Settled / do not re-suggest" so it does not come back. Tell the user in 3-5 bullets.
3. Typical failure modes to catch:
   - advice based on facts you already overturned;
   - a deliberate behaviour misread as a bug;
   - claims about names or addresses that are wrong (verify one before acting).

## Quick brief template (`tasks/advisor.md`)

> You review the project once per test round, right after the lead records the results. Read-only. **At most ~20 tool calls, then answer.** Your value is catching what is not verified and what a player will hit, not engine internals: label any engine-internal claim a "lead" with a one-step check, and do not repeat the situation file's "Settled" list. Read `local/advisor/situation.md` first, then the goal and the newest test results, then only what you need. Report **≤400 words**:
> - verification gaps (thin evidence; never exercised), ranked by how soon a player hits them;
> - top-down view (one paragraph);
> - risks of the latest changes;
> - up to 3 concrete test ideas in the project's test harness;
> - one question.

## Deep brief template (`tasks/advisor-deep.md`)

> You are a senior researcher consulting for the lead. Read-only. Budget **~60 tool calls**; stale after ~15 minutes. Answer **only** the situation file's "Current deep question" (stop if it is "none"). Build on "Answered deep questions" and "Settled"; never re-derive them without new instruction-level evidence. Settle the question from primary evidence (code, decompiled exports, data, existing research); quote addresses and `file:line`; mark verified vs inferred vs guess. Report **≤900 words**:
> - question and answer with confidence;
> - evidence chain;
> - an implementation recipe concrete enough to code from;
> - how to verify with the project's test harness;
> - what is still unknown;
> - a proposed next deep question with pointers.
>
> No general project advice.

## Tips

- Advisors have `Read`, `Grep` and `Glob` (since 2026-09-20; before that the launcher's bare mode left them with `Read` alone). Reports from earlier runs that say a folder could not be enumerated are stale, not a real limit.
- The deep advisor needs *answerable* questions with pointers (addresses, files). Vague questions produce vague essays.
- Advisors are read-only: they never edit, build or run anything. You implement and verify.
- Record accepted findings in the project's docs, not only in chat; the next session starts from the docs.
