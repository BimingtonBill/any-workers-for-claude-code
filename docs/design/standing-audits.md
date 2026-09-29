# Standing audits

Idea borrowed (structure only; the repository has no licence) from ByroRedux, a heavily agent-driven Rust
engine that keeps ~50 standing `audit-*` skills, one per subsystem, re-run only where the code changed,
with findings filed as tracked issues. Triaged in docs/todo.md items 10, 11, 12 and 15.

`tools/ds_audit.py`, all state in `<state dir>/memory/`:

- `checklists/<area>.md`: front matter `area` and `paths` (repo-relative globs), then "## Invariants"
  (each anchored on symbols, with its guarding test command or "unguarded"), "## Security", and "## Added
  by audits". `init` briefs a digest worker (label `digest-checklists`) to propose 3-8 areas; its report,
  split on `=== area:` lines, becomes the files (existing areas are kept unless `--replace`).
- `due`: an area is due when it has never been audited or when `git diff --name-only <last>..HEAD`
  touches its paths. Each due area gets a brief (label `audit-<area>`, kind review, read-only) with the
  checklist, the open findings for the area, and the files and commits since the last audit.
- Audit reports have fixed sections: `## Checklist` (ok/broken/unchecked per item), `## Findings` (`FINDING
  | severity | path:line | text`), `## Fixed` (`FIXED | id | evidence`), `## Checklist additions`. The
  launcher runs `save audit` when an `audit-*` run ends: new findings get IDs (`<AREA>-<yyyymmdd>-<nn>`)
  in `findings.jsonl` (deduplicated against open ones), FIXED lines close findings, additions are appended
  to the checklist, and the area's audited commit moves to the brief's HEAD.
- Rules in every audit brief: anchor on a symbol and file:line, confirm with Grep, drop what can't be
  confirmed, no speculation or style; and always a security dimension (untrusted input, listening ports
  and debug channels, writes outside the project, unsafe/FFI, panics on bad input, secrets). The last
  comes from ByroRedux's own open HIGH issue: an unauthenticated debug port with file access, on by
  default, left open despite heavy agent-driven auditing.
- `baseline record <name> -- <command>` / `baseline check <name>`: a command's output kept with its
  commit, and a unified diff against a fresh run (exit 1 when something moved). A cheap "did anything move"
  check for analysis workers.
- The morning report shows areas, audits due and open findings (with the high count).

Evidence: `tests/test_ds_audit.py` (checklists saved and not overwritten; due only where code changed;
findings numbered, deduplicated, closed by FIXED lines and by hand; a non-audit report refused; baselines
diff). The first real checklists for this repository were written by a Claude subagent working from the
`init` brief, in the report format, and saved with `save checklists`. A DeepSeek audit run through the
launcher's auto-save is still to be proven (held overnight on 2026-09-24 by the weekly spend pace, to leave
Project A's coders the budget).

## Review and first real audits (2026-09-24, overnight)

- Claude subagents wrote this repository's first checklists from the `init` brief (7 areas, 8-12 invariants
  each, all anchored with their guarding tests) and audited two areas; `save checklists` and `save audit`
  filed them. spend-limit-and-pace: 13 ok, 1 unchecked, no findings, 5 checklist additions.
  worker-launch-hook: one medium finding (WORKERLA-20260924-01: `install()` dropped whole hook groups),
  fixed the same night and closed with `ds_audit.py close`.
- `review-049-overnight` (DeepSeek, $0.04, 144 s; pacing ran it at effort low, asked for high) found 11
  problems in the night's changes. Fixed: a vanished audited commit read as "nothing changed"; a shared temp
  file and no lock on the findings ledger; an operator-precedence bug in the morning report's audit line; a
  finished audit discarded when its area has no checklist; unparseable ledger lines deleted on rewrite; a
  checklist in another encoding breaking the morning report; `install()` failing on malformed hook groups and
  writing settings.json in place; ds_impl's changed-since-base guard failing open when the base is gone.
  Left (noted): commas inside path globs or task names.

## First full rounds (2026-09-24, overnight)

- This repository: 7 areas audited (4 by Claude subagents, 3 by DeepSeek), then the 5 areas the fixes
  touched re-audited by DeepSeek (8 cents in total). 23 findings in all, 1 high (HAC-20260924-01:
  spawn_workers launched any .md on disk as a brief) and 2 medium; every one fixed with a regression test,
  or closed with the reason it isn't a bug (WLP-20260924-02). The launcher's auto-save handled every
  DeepSeek run: findings filed and numbered, checklists grown, areas marked audited at the brief's HEAD.
- Project A: `init` proposed 8 areas ($0.045), and the first audit of each ($0.23 for all 8) filed 22
  findings, 7 of them medium: acceptance scripts pinned to schemas the converter no longer writes, an
  integration report that passes regardless of its own issues, the engine opening the converted database
  read-write, a whole-file bytecheck on every cache lookup. Two were in harness tools Project A copies
  (HT-20260924-04/05), fixed at the source. The others were handed to Project A's sessions.
- Re-audits find more than first audits: the second pass of this repository's areas found 12 new issues
  the first had not. Stopping after two rounds was a judgment call: the second round's findings were
  smaller than the first's.
