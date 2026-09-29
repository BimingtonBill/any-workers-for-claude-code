# Speed and consistency pass (2026-09-29)

Two read-only Claude reviews (analysis #075 speed, #076 consistency) found the costs below. Claude coders
#077-#081 and #083 then fixed them in three stages, each owning its own files. All figures are medians on the
user's machine: Python 3.13 (conda, about 30 ms bare start) and Windows PowerShell 5.1 (about 150 ms bare
start).

## What runs often, and what it cost

| Path | Runs | Before | After |
|---|---|---|---|
| ds_hook.py, a shell or prompt event that needs nothing | every Bash/PowerShell call, every prompt, every session | 76 ms | 29 ms (bare Python 28) |
| ds_steer.py deliver, worker PostToolUse | every worker tool call | 74 ms | 27 ms (`python -S`) |
| ds-watch.ps1 poll, 1.6 MB manifest | every 5 s per watcher | 765 ms | 428 ms, and no longer grows with the manifest |
| worker poll (watch + live), 50 MB transcript | every 15 s per worker | 672 ms | 106 ms (incremental from a saved offset) |
| launch dry run, 5.1, default / MiMo | every worker | 876 / 941 ms | 715 / 707 ms |
| ds-spawn stagger, 4 readers | every group launch | 3.2 s | 0.6 s |
| DeepSeek balance fetch | every DeepSeek launch | 760 ms cold | skipped when a reading is under 5 min old and above $2 |
| ds_spend plan(), 12k ledger rows | every launch and wait loop | 410 ms | 100 ms (one parse per process) |
| next_number on a 460 MB transcript | a misnamed helper | 1.2-2.9 s | 15 ms (last 2 MB) |

## How

- **Hook:** the fast path reads raw stdin, checks for the launch scripts and the budget words, and exits before
  it imports json, re or pathlib. A per-session marker file makes the Post-event notes a single `os.stat`
  when nothing is due; with nothing to say, they recheck at most every 5 minutes. ds_hook.py is a small entry
  script, and the rest sits in ds_hook_main.py, whose .pyc is cached. There is no PowerShell inside the hook
  (`ds_state.fallback_dir`).
- **Launch:** launcher/ds_prepare.py returns the provider, the allowance status, and the report notes and step
  budgets in one call. The spend check keeps its lock and claim. Every native call goes through
  `Invoke-Native`, so a stderr line under 5.1 no longer stops a launch. The claude.exe path is cached for a
  day.
- **Polls:** `ds_spend.py poll` does steer watch and spend live in one process. It reads only lines appended
  since the last poll, with running totals and message-id dedupe across polls. `record` still reads the whole
  transcript, so the recorded cost is exact.
- **Spend:** the ledger is cached per process, keyed on path, mtime, size and inode. holds.jsonl gets one
  'waited' row per run instead of one every 10 s. limits.json, providers.json, memory.json and audits.json are
  written atomically (`ds_common.write_json_atomic`).

## Consistency

- One exit-code table (ds-agent.ps1 header, SKILL.md): 0 ok, 1 the worker failed (its own code is kept as
  `worker_exit_code`), 2 bad arguments or setup, 3 timed out, 4 held by the budget or pace. ds-spawn and ds_impl
  pass 3 and 4 on, and ds_impl records a 4 as `held`.
- Shared helpers live in launcher/ds_common.py (`spend_dir`, `user_providers_path`, `parse_time`, `read_json`,
  `write_json_atomic`) and `ds_state.project_root`. The old names stay as aliases.
- Provider short names come from providers.json `short` (`ds_providers.short_name`), not from copies in the
  hook and ds_spend.
- `.deepseek-agents.json` is read with or without a BOM everywhere. Python .jsonl appends write LF.
- Messages that ran for any provider no longer say "DeepSeek". ds_cost.py prices each run by its provider.

## Proof

- Tests: 441 before, 509 after, including a launcher run against a stand-in claude.exe for every exit code.
- Real runs on MiMo (DeepSeek and Muse were held by the pace that day): probe-082-speed-pass.1 after stages 1
  and 3, and probe-084-poll.1 after stage 2.
- Not yet proven with a real launch: the balance reuse, since a DeepSeek launch was held until midnight.

## Left

- A poll still re-reads the ledger for the limits (about 100 ms at 600 rows, 160 ms at 12k). Caching the
  ledger totals in the poll state would remove that.
