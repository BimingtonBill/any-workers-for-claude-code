# Any Workers for Claude Code

An add-on for [Claude Code](https://claude.com/claude-code) that gives Claude a team of cheap helpers.
Claude stays in charge: it splits a job up, hands pieces to DeepSeek AI "workers" that run in the background
on your computer, keeps working on its own part, and checks every result before using it. The heavy reading
and drafting happens on DeepSeek, so your Claude plan lasts longer. It works in any project: a website, a
Python script, a game, a Rust or .NET app.

A typical worker task costs a few cents and a big one up to about 20 cents, so a $5 DeepSeek top-up goes a
long way.

Workers run on DeepSeek by default. Meta's Muse Spark and Xiaomi's MiMo can run workers too (`-Provider meta`,
`-Provider xiaomi`, with their own keys, limits and pacing); see `docs/design/providers.md`. Formerly "DeepSeek
agents for Claude Code".

*An independent community project, not made or endorsed by Anthropic, DeepSeek, Meta or Xiaomi.*

## Install

You need a **Windows** computer, the **Claude desktop app** ([download](https://claude.ai/download)) with a
plan that includes Claude Code, **Python 3** and **Git**, and a **DeepSeek account** with a little credit:
sign up at [platform.deepseek.com](https://platform.deepseek.com), add credit under **Top up**, and create a
key under **API keys**.

Open the Claude desktop app, go to the **Code** tab, start a session, and paste this in:

```text
Please install "Any Workers for Claude Code" for me.

1. Download https://github.com/BimingtonBill/any-workers-for-claude-code/releases/latest/download/any-workers-for-claude-code.zip into a new folder called "deepseek-agents-setup" in my Downloads folder, and unzip it there.
2. Read setup.ps1 in the unzipped folder so you know what it does, then run it:
   powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1
   It installs the skills and checks for Python and Claude Code. If I haven't saved a DeepSeek API key yet, it opens a separate window where I type the key myself.
3. Tell me in plain words what it reported and what I need to do next.

Never ask me to type or paste my DeepSeek API key into this chat.
```

Say yes when Claude asks permission to download and run things. When a window asks for your **DeepSeek API
key**, paste it there (nothing shows as you paste; that's normal) and press Enter. The key never goes into
the chat, which would save it; the window stores it privately on your computer. Then **close and reopen the
Claude app.**

To install by hand instead, download the zip from the
[latest release](https://github.com/BimingtonBill/any-workers-for-claude-code/releases/latest), unzip it,
double-click **`install.cmd`**, and reopen the Claude app.

## Using it

Ask Claude in plain words, in any project:

- *"Use DeepSeek workers to find every place this project reads its settings file."*
- *"Have a DeepSeek web searcher find the latest version of this library and what changed."*
- *"Get a DeepSeek coder to add a median function to stats.js, with tests, while you look at the bug."*
- *"Get a DeepSeek worker to review the change you just made."*

Workers show up in Claude's **Background tasks** panel with names like
`DeepSeek research #004: find where the game loads save files`. Three commands help too: `/deepseek-agents`
switches it on for a session, `/delegation` sets how much Claude hands off, and `/spend-limit` shows and sets
the budget.

## How it works

![How a job flows through Claude and the DeepSeek workers](docs/flowchart.svg)

There are four kinds of worker: **web searchers** look things up online, **readers** research, review and
analyse, **coders** write code in their own copy of your project, and **leads** run a small team for a big
job. Everything comes back to Claude, which is the only one that changes your files. Every job follows the
same steps:

- **Research starts on the web**, then a reader checks the answers against your code.
- **Every report opens with the same five lines** (done or not, verdict, summary, what's left, what's next),
  so Claude reads those and opens the full report only when it needs to.
- **Reviews re-run the tests themselves**, and a change a review says needs fixing isn't brought in until it is.
- **It learns as it goes**: every worker gets a short map of your project, mistakes that reviews catch become
  a pitfalls list coders read first, and a worker that starts to drift is told to wrap up.

([`docs/flowchart.html`](docs/flowchart.html) is an interactive version: download it and open it in your browser.)

## How much Claude hands off

A delegation level from **1 to 5** sets how much work goes to DeepSeek. Higher uses less of your Claude plan,
but more work is first done by DeepSeek, which is cheaper and less reliable, so Claude spends its time
checking instead.

| Level | What Claude does |
|:---:|---|
| **1** | Everything itself; DeepSeek only when you ask. |
| **2** | Hands off research, reading and reviews; writes all code itself. |
| **3** | *The default.* As 2, plus small, self-contained pieces of code. |
| **4** | Plans, checks and fits the pieces together; DeepSeek does most exploring and coding. *Good for bigger projects.* |
| **5** | Only manages: every task goes to DeepSeek unless it needs you. |

At every level Claude talks to you, makes the design decisions, handles anything security-sensitive, and
checks every piece of DeepSeek work before using it. Change it by telling Claude (*"set DeepSeek delegation
to 4 for this project"*) or with `/delegation 4`.

## Budgets

There's no limit until you set one, and each provider has its own. Tell Claude in any session, and it applies everywhere:

- *"Limit DeepSeek to $2 a day."*: a daily or weekly limit, like Claude's own usage limits.
- *"Make my DeepSeek credit last two weeks."*: each day gets an even share of what's left.
- *"Stop any DeepSeek worker at $1.50."*: a cap per worker, which keeps what it had found.

Spending is paced like cruise control: when it runs ahead, workers ease off step by step before the limit is
reached. A worker that won't fit doesn't start: Claude runs the same work as a Sonnet helper instead. Claude tells you when your
credit is very low. Spend figures are estimates at DeepSeek's list prices; the DeepSeek dashboard is the real
bill.

Claude paces **its own** plan the same way: ahead of pace it hands more to DeepSeek, near a limit it only
coordinates, and in the last day before the weekly reset it's told to use up what's left rather than lose it.
It suggests `/compact` when a conversation is 70% full, since every message re-reads the whole conversation.

## Is it safe?

- **Workers only see the project you point them at**, never your Claude account, chats or other folders.
- **Your DeepSeek key stays on your computer**, saved for your Windows account only.
- **Coders work in a separate copy** of your project and can only change the files they were given; Claude
  reviews the changes before bringing them in.
- **Web content is treated as unverified.** Web searchers can't see your project, and a worker that has read
  the web can only change a separate copy.
- **If a job needs a program you don't have**, Claude asks you to install it rather than working around it.
- **It adds a hook to Claude Code** (in `~/.claude/settings.json`, backed up first). It names workers and
  Claude's own helpers in the Background tasks panel and records them, runs Claude's helpers on Sonnet 5.5 unless
  a session names another model, and adds short notes for Claude about budgets. It changes
  nothing else. Install with `tools/install-skill.ps1 -NoHooks` to skip it, or delete the entries that
  mention `ds_hook.py` to remove it.

## If something goes wrong

| You see | What to do |
|---|---|
| `DEEPSEEK_API_KEY is not set` | Run `install.cmd` again; it reopens the key window. |
| `DeepSeek rejected DEEPSEEK_API_KEY (401)` | The key is wrong or deleted. Create a new one at platform.deepseek.com and run `install.cmd` again. |
| `balance is too low` / `402` | Add credit at [platform.deepseek.com/top_up](https://platform.deepseek.com/top_up). |
| `Claude Code was not found` | Install the Claude desktop app and open its Code tab once. |
| `it needs Python` | `winget install Python.Python.3.12`, then restart Claude. |
| A worker seems stuck | Ask Claude: *"Show me the running DeepSeek workers and stop the stuck one."* |

## For the technically curious

- `skill/`: the skills that teach Claude to lead workers, the `/delegation` and `/spend-limit` commands, and the hook.
- `launcher/`: `ds-agent.ps1` starts a worker as a headless Claude Code process on DeepSeek's
  Anthropic-compatible API, with its own permissions and a run record; plus spend limits and pacing.
- `tools/`: coding tasks in isolated git worktrees (`ds_impl.ps1`), run status, project memory, audits,
  and checkers.
- `templates/`: a task template for each kind of worker.
- `docs/design/`: how each part works, with the test evidence behind it.

## License

MIT. See [`LICENSE`](LICENSE).
