---
name: spend-limit
description: Show or set the DeepSeek spend limits (per day, per week, per worker) and show the pace of DeepSeek spend and of Claude's own plan.
argument-hint: "[<dollars> day|week|run [from now]]  |  last <how long>  |  off [day|week|run|stretch]"
disable-model-invocation: true
---

# /spend-limit

The user typed `/spend-limit $ARGUMENTS`. Typing the command is the user asking, so you may set, raise or remove a limit exactly as they typed it. Run the matching command below with the Bash tool, then show the status that comes back and add one plain sentence on what it means for the work.

The tool is `python "$HOME/.claude/skills/deepseek-agents/ds_spend.py"`. Limits cover every project on this computer.

| The user typed | Run |
|---|---|
| nothing, or `status` | `status` (it includes the DeepSeek balance and how long it lasts) |
| `balance` | `balance` |
| an amount and `day`, e.g. `2 day` or `$2 a day` | `set 2 --per day` |
| an amount and `week` | `set <n> --per week` |
| an amount and `run` (or "worker", "each") | `set <n> --per run`: each worker is told to wrap up at 75% and stopped at the cap |
| any of those plus "from now" (or "starting now") | add `--from-now`: spending before now doesn't count, and a weekly limit starts its weeks on today's weekday |
| `last <how long>` or "make my credit last ...", e.g. `last 2 weeks`, `last until Oct 9` | `stretch <2w, 10d, 36h, 1m, or a date as YYYY-MM-DD>`: each day gets an even share of the credit left, worked out again every midnight, as a pace target (never a hard limit). Add `--provider xiaomi` for MiMo's plan, or `--provider meta --credit <dollars>` for Muse. Turn a weekday or "end of the month" into a date first |
| `off` | `off`: removes every limit and any stretch (confirm in one line which ones were removed) |
| `off day`, `off week`, `off run`, `off stretch` | `off --per <that>` |

After any change, run `status` and show it.

With `status`, also show Claude's own pace. Read the plan with the get_usage tool (`mcp__ccd_session_mgmt__get_usage`; load it with ToolSearch if it is deferred), record it with `python "$HOME/.claude/skills/deepseek-agents/ds_claude.py" record '<its JSON>'`, and show the line it prints. Skip this where get_usage says plan limits don't apply.

An amount with no period, or anything else you can't map: show `status`, and say what the command accepts, for example `/spend-limit 20 week`, `/spend-limit 1.5 run`, `/spend-limit off day`.
