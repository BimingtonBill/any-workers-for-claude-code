# The delegation level (1-5)

A single number saying how much work Claude hands to DeepSeek workers: 1 = only when asked, 5 = everything.
The scale and its rules live in one place, `tools/ds_delegation.py` (`--table` prints them), and the
skill's SKILL.md repeats the table.

| Level | Name | In short |
|:---:|---|---|
| 1 | Only when asked | Claude does everything unless the user asks for a worker. |
| 2 | Research and review | Research, big reads, reviews and run analyses go to workers; Claude writes all code. |
| 3 | Balanced (default) | As 2, plus bounded self-contained implementation and anything briefable with a clear check. |
| 4 | Claude manages | Claude plans, briefs, reviews and integrates; workers explore and do most coding. |
| 5 | Everything | Claude only orchestrates; every task goes to a worker. |

## Why 1-5 and not 0-10

The first version had eleven levels. In use, neighbouring levels differed by a sentence Claude could not
reliably act on (5 vs 6, 7 vs 8, 9 vs 10), and the user found the scale more than it needed to be. The
five levels are the old ones merged in pairs: 0-2 -> 1, 3-4 -> 2, 5-6 -> 3, 7-8 -> 4, 9-10 -> 5.

Level 0 used to be enforced: the launcher refused to start a worker without `-Force`. That is gone. "Off"
and "only when asked" behaved the same in practice, since a worker launched at the user's request needed
`-Force` anyway. `-Force` is still accepted and recorded as `forced` in the manifest.

## Setting it

```
python tools/ds_delegation.py                      # effective level here, and where it comes from
python tools/ds_delegation.py --set 4 --agents-md  # this project, plus the rule block in its AGENTS.md
python tools/ds_delegation.py --set 2 --global     # your default for every project
DS_DELEGATION_LEVEL=1                              # this shell only, overrides everything
```

Precedence: `DS_DELEGATION_LEVEL`, then `"delegationLevel"` in the project's `.deepseek-agents.json`, then
`~/.claude-deepseek/config.json`, then 3.

**Old settings keep working.** The 0-10 value was stored as `"delegation"` (and `DS_DELEGATION`). A new key
was needed because old values 1-5 would otherwise be read as the wrong level. Where no `delegationLevel` is
set, an old `delegation` value at the same place is mapped with the table above. `--set` writes
`delegationLevel` and removes the old key.

## Why AGENTS.md matters

The level steers **Claude**, not the workers, and Claude follows only what it sees. The skill carries the
table, but it loads only when workers come up. Without the AGENTS.md block, a project set to 4 behaves
like 1 until someone mentions DeepSeek. `--agents-md` writes a generated block (between
`<!-- ds-delegation:begin -->` and `<!-- ds-delegation:end -->`) under the file's first heading, and replaces
it on the next `--set`. Everything else in AGENTS.md is left alone.

## What is enforced, and what is guidance

- **Recorded:** every run's manifest has `delegation` (the level at launch, 1-5) and `forced`.
- **Guidance:** the levels describe what Claude delegates. No launcher can judge whether a task "could be
  briefed". The rules are written so Claude can apply them in the moment.
- **Never scaled down:** at every level Claude keeps the conversation with the user, design decisions,
  integration, security-sensitive work, and review of every worker result it acts on. Project B's record
  (0 of 9 implementations accepted without correction) is why the review floor doesn't move with the dial.

## Where the existing projects sit

Project B and Project A were set to 7 on the old scale, which is 4 ("Claude manages").
