# Provider-independent workers: DeepSeek plus Muse Spark 1.3

Research fork, started 2026-09-27. Goal: workers that can run on more than one model provider, chosen per
task, starting with DeepSeek (today's only provider) and Meta's Muse Spark 1.3.

## Muse Spark 1.3: what is verified

From `websearch-065-muse-spark-api.1` (harness run), with the two load-bearing sources opened directly:

- **Maker and date.** Meta (Meta Superintelligence Labs), released about 2 September 2026, served by the Meta
  Model API. (Worker's sources: techmonitor.ai, thestandard.com.hk.)
- **Claude Code works directly, no proxy.** Meta's own guide, dev.meta.ai/docs/coding-agents (opened
  2026-09-27):
  ```
  ANTHROPIC_BASE_URL=https://api.meta.ai            (no /v1: the client appends /v1/messages)
  ANTHROPIC_AUTH_TOKEN=<Meta Model API key>          (bearer token, not ANTHROPIC_API_KEY)
  ANTHROPIC_MODEL, ANTHROPIC_DEFAULT_OPUS/SONNET/HAIKU_MODEL, CLAUDE_CODE_SUBAGENT_MODEL = muse-spark-1.3
  ENABLE_TOOL_SEARCH=true
  ```
  Setting every alias stops Claude Code falling back to a Claude model. The Messages API is stateless; Claude
  Code keeps the conversation on its side, as it does for DeepSeek.
- **Formats.** `/v1/messages`, `/v1/chat/completions` and `/v1/responses`; text, image, video and PDF input;
  tool calling; a 1,048,576-token context window. (LiteLLM's day-0 post, docs.litellm.ai/blog/muse_spark_1_3,
  opened 2026-09-27.)
- **Pricing** (LiteLLM's post; Meta's pricing page is behind sign-in): standard $1.25 per million input,
  $4.25 output, $0.15 cached input. A "contributor" tier at $0.10 / $0.20 lets Meta train on the prompts
  and is rate-limited (about 100 requests a minute against 3,000). **Worker prompts carry project code, so only
  the standard tier fits unless the user decides otherwise.**
- **Not verified:** rate limits, eligibility for individual developers and for Australia, max output tokens,
  and whether a balance endpoint exists (DeepSeek's balance check has no known Meta equivalent).

For scale: DeepSeek Flash costs about a tenth of Muse Spark's standard price per token, so Muse Spark suits
the work where quality matters more than volume.

## What is DeepSeek-specific in the harness today

- `launcher/ds-agent.ps1`: the DeepSeek base URL, `DEEPSEEK_API_KEY`, the model `deepseek-flash[1m]`, effort
  levels mapped to DeepSeek's (low, high, max).
- `launcher/ds_spend.py`: DeepSeek list prices, the balance check against api.deepseek.com, stretch and pacing
  from that one balance.
- `tools/set-deepseek-key.ps1`, `setup.ps1`: one key, saved as `DEEPSEEK_API_KEY`.
- Names everywhere: "DeepSeek <kind> #nnn" in the panel, the hook's house format, the skill's wording.

## Plan

1. **A provider registry** (`providers.json`): per provider its base URL, the environment variable holding
   the key, how it authenticates, its model names, prices (input, output, cached), context window, and an
   optional balance check. DeepSeek's entry reproduces today's behaviour exactly.
2. **`-Provider` in the launcher**, defaulting to `deepseek`, so every existing command and project keeps
   working. The run record gains `provider`, and spend is priced from the registry.
3. **Keys without the chat.** A general key window (`set-provider-key.ps1 -Provider meta`) like DeepSeek's,
   saving `META_MODEL_API_KEY` for the Windows account; never typed into a chat.
4. **Probe on real work**, cheapest first: one research run and one small coder in a fresh project on Muse
   Spark, then the same brief on DeepSeek, comparing turns, cost, and a review of both.
5. **Routing** once the probes say something: which kinds go to which provider, and a **cross-provider
   review** (a coder on one provider reviewed by the other), which should catch more than a review by the same
   model.
6. **Budgets per provider**, with pacing, since Muse Spark has no known balance check: a spend limit is then the
   only guard.

## The contributor tier, sanctioned per project

The idea: the contributor tier could be acceptable for an open-source project if it were properly sanctioned to
stay in that project. Take an open-source game-engine project whose repository is public (Apache-2.0), but whose workers can also read the game install and the converted assets (the publisher's data), the
cargo registry, shared build folders (all `readOnlyDirs`), and `local/` (captures, screenshots, logs, handoffs,
notes). Only the tracked code is public. So:

1. **Opt-in lives in the user's own settings** (`~/.claude-deepseek/providers.json`, a list of project folders
   per provider), never in the project's `.deepseek-agents.json`: a committed config, from a pull request for
   example, must not be able to opt the user's machine into a training tier.
2. **A contributor run sees only the tracked code.** The launcher drops `readOnlyDirs`, refuses `-AddDir`, and
   denies reads of `local/**`, build output (`target/**`, `node_modules/**` ...) and secret-looking files
   (`.env*`, `*.key`, `*.pem`). Coders already work in worktrees of tracked files only.
3. **The tier follows what the task can read**, not what the brief promises: a run that needs any of those
   folders goes to the standard tier or to DeepSeek, automatically.
4. **Briefs carry only public information** (a skill rule), every contributor run records `tier: contributor`,
   and the morning report lists them.
5. To verify with a key: how Meta applies the tier (per key, per account or per request), which decides
   whether this needs one key or two.

## Needed from the user

- A Meta Model API key (dev.meta.ai), on the standard tier, saved through a key window, never pasted into chat.
- A Muse Spark budget to start with (a small daily limit is enough for the probes).

## Xiaomi MiMo-V2.6-Pro (researched 2026-09-27)

From `websearch-067-mimo.1`, with the endpoint, prices and thinking rule checked in Xiaomi's own
`mimo.mi.com/llms-full.txt`:

- **Model.** Released about 22 September 2026; MIT-licensed open weights; sparse MoE, 42B active; 1M context,
  128K output; text, image, video and audio input. Artificial Analysis Intelligence Index 46, the top
  open-weight model there (DeepSeek V4.1 Flash 39, Muse Spark 1.3 48).
- **Claude Code directly:** `ANTHROPIC_BASE_URL=https://api.xiaomimimo.com/anthropic`, key `sk-...` as
  `ANTHROPIC_AUTH_TOKEN`, model `mimo-v2.6-pro` (also `mimo-v2.6-flash`), every alias pinned to it.
- **Price (overseas, USD per million):** $0.435 input, $0.87 output, $0.0036 cached input; flash $0.14 / $0.28.
  About DeepSeek Flash's price, for a model that scores higher.
- **Limits:** 100 requests a minute and 10M tokens a minute per model per account.
- **Data:** for non-mainland users Xiaomi is the processor, with servers in Europe and Singapore. The privacy
  policy reportedly says prompts aren't used for training without consent (search summary only; not opened).
- **Risk to test first:** with thinking on, Xiaomi requires `reasoning_content` to be passed back on every
  earlier assistant turn that made tool calls, and Claude Code is not on its list of tested agents. A community
  report says Claude Code then fails with HTTP 400 `Param Incorrect` in multi-turn tool loops. Workers start
  fresh, so turning thinking off from the first turn may avoid it; one live session with a key settles it.
- **Practitioner reports** (anecdotal): better than 2.5 for code, weaker on large tasks (invented function
  names), slow first token. Small, well-scoped briefs, which is how workers run anyway.

## Built (2026-09-27)

- **`launcher/providers.json`**: DeepSeek (exactly today's settings), Meta (`meta`, Muse Spark 1.3, standard and
  contributor tiers) and Xiaomi (`xiaomi`, MiMo-V2.6-Pro, thinking off). Prices, key variable, auth style, model
  and small model, effort levels, output cap per provider.
- **`launcher/ds_providers.py`**: resolves a provider (and tier) for the launcher; the contributor tier only for a
  project listed in the user's own `~/.claude-deepseek/providers.json` (`DS_PROVIDERS_USER` overrides the path),
  where a coder's worktree counts as its project.
- **Launcher `-Provider` / `-Tier`** (or `DS_PROVIDER`), default deepseek. The worker's environment comes from
  the registry: base URL, `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN`, every model alias pinned, effort only
  where the provider lists levels, `MAX_THINKING_TOKENS=0` where thinking is off, extra settings (Meta's
  `ENABLE_TOOL_SEARCH`). The balance check runs only for a provider with one (DeepSeek). The run record and the
  spend row carry the provider and tier; spend is priced from the registry (DeepSeek keeps its own prices and
  the user's overrides). Each provider has its own limits since 2026-09-29 (below).
- **Contributor tier fence.** The worker runs in a fresh detached worktree of HEAD under `local/contrib/`
  (removed afterwards; leftovers swept after 12 hours), so untracked files, `local/` and build output are not
  there; the project's `readOnlyDirs` are dropped and `-AddDir` is refused. A coder from `ds_impl` keeps its
  own worktree, which is already tracked files only. Run records still go to the project's state dir.
- **`tools/set-provider-key.ps1 -Provider <p> [-Tier <t>]`**: a key window like DeepSeek's; it checks the key
  with a one-token request and saves it for the Windows user.
- **`ds_impl.ps1 -Provider/-Tier`** pass through to the coder.
- Tests: `tests/test_providers.py` (registry, opt-in, pricing, launcher dry runs for DeepSeek, Xiaomi, a missing
  key, and the contributor fence). The existing 412 still pass.

Not yet proven with a real Meta or Xiaomi run: that needs keys. Open questions for those first runs: whether
Meta accepts Claude Code's effort setting, how its tier is chosen, and whether MiMo works with thinking on.
Limits counted all providers together at first; see "Separate limits per provider" below.

## First real runs on MiMo (2026-09-27)

With a MiMo **Token Plan**, its key (`tp-...`) works only at the Token Plan addresses, and the key
window found it at Singapore (`token-plan-sgp.xiaomimimo.com/anthropic`), saved that in the user's settings and
made the plan MiMo's default tier. (Its first attempt was the pay-as-you-go `sk-` key, which every Token Plan
address refused with 401 `invalid_key`; the pay-as-you-go account had no credit, 402.)

| Run | Provider | Turns | Result |
|---|---|---:|---|
| `research-069-deepseek-smoke.1` | DeepSeek | 8 | correct, with `path:line` |
| `research-069-mimo-smoke.2` | MiMo (Token Plan) | 9 | correct, with `path:line`; also found the user-settings address override |
| `research-069-mimo-thinking.1` | MiMo, thinking allowed | 9 | correct |
| `impl-001-median.1` (earlier) | DeepSeek coder | 10 | acceptance PASS |
| `impl-002-median-mimo.1` | MiMo coder | 7 | scope ok, `npm test` PASS; clean code, sorts a copy, tests odd, even (unsorted input) and empty |

**Thinking works.** Both MiMo research runs contain thinking blocks between tool calls (5 and 6) and made 8
tool calls each with no `reasoning_content` 400: Claude Code passes MiMo's thinking back correctly, and
`MAX_THINKING_TOKENS=0` did not stop MiMo thinking anyway. So `"thinking": false` is gone from MiMo's entry;
`DS_PROVIDER_THINKING=1` remains for testing another provider that has it.

## Pacing MiMo's Token Plan (2026-09-27)

No balance or usage API: `mimo.mi.com/llms-full.txt` lists none, the usual balance paths return 404 on the Token
Plan host, and responses carry no quota headers (only the request's own `usage`). The console's usage page is the
only place the figure shows.

The official plan page (mimo.mi.com/docs/en-US/price/token-plan) gives: Lite $6 / 4.1B credits, **Standard $16 /
11B**, Pro $50 / 38B, Max $100 / 82B a month, resetting monthly; `mimo-v2.6-pro` costs 300 credits per fresh input
token, 600 per output token, 2.5 per cached input token (flash 100 / 200 / 2); when credits run out the service is
suspended until the renewal. 300 credits at $16 / 11B is exactly pay-as-you-go's $0.435 a million: the plan is
prepaid credit that expires monthly, not a discount.

- **`launcher/ds_allowance.py`** counts what the harness's own MiMo runs used this plan month, from the spend log at
  the plan's rates (pro rates for everything, which errs safe), and paces it like the DeepSeek stretch: an even
  share of the month with a 10% head start; over it the launcher notes "ease off", and at 1.5x the even share (or
  the whole allowance) it holds new MiMo work (exit 4, "run it on DeepSeek instead"). In the last two days before
  the renewal nothing holds until 98%: what is left is lost.
- The user's plan is in `~/.claude-deepseek/providers.json` (`"plans": {"xiaomi/token-plan": {"plan":
  "standard", "renews": "2026-10-01"}}`, the purchase date is the renewal day). `ds_spend.py status` shows
  a `plan:` line. `ds_allowance.py sync --provider xiaomi --tier token-plan --used <credits>` records the console's
  figure, which then replaces the harness's own count up to that moment (other tools using the same key are
  invisible otherwise).
- **Output tokens were missing.** MiMo's transcript records `output_tokens: 0` on every message; only the result
  has the total. The launcher now passes the result's output total to `ds_spend.py record` (`--out-total`), which
  keeps the larger. `probe-070-mimo-allowance.1` recorded 542 output tokens where the earlier runs showed 0.
- Tests: `tests/test_allowance.py` (the plan month, credits at the plan's rates, easing and holding ahead of pace,
  the spend-down, a console sync, no plan recorded).

## DeepSeek against MiMo on real work (2026-09-27)

Same briefs, same checkouts, DeepSeek V4.1 Flash (effort high) against MiMo-V2.6-Pro (Token Plan), plus Claude
Opus 5.5 on the design task.

| Test | DeepSeek | MiMo | Claude |
|---|---|---|---|
| Review A: impl-176 (`8529b5e`), known defect "a door waits for ever on a failed cell" | found it, + 3 minor; **5.3 min**, 55 turns, $0.15 | found it, same fix, + 3 similar; **21.6 min**, 49 turns | |
| Review B: `c5cdad9..edacdfc` engine, 6 known issues | 5 proved issues, none of the known six (a different, valid reading); **4.0 min**, $0.19 | **refused by Xiaomi's content filter** after 23 turns, 5.8 min ("considered high risk") | |
| Visual: 7 of the user's annotated screenshots | 3 hits, 2 partial, 1 miss (doubled doors); **2.3 min**, $0.02 | **timed out at 15 min**: 1-5 min of thinking per step, and it reported images "stripped from context" and a re-read file returning a different image | |
| Design: portal-demo settings screen, one HTML file | clean, cohesive, everything on one screen; **4.9 min**, $0.09 | polished, with descriptions; slider arrows reversed, "unsaved changes" shown on load; **15.4 min** | most ambitious (live portal preview, real rebinding), settings list low-contrast; **17.8 min** |

MiMo matched DeepSeek's review quality but was 1.5-4x slower, could not be trusted with images through Claude
Code, and its provider filter refused ordinary engine code (now recorded as a failed run). The whole comparison
used 2.0% of the Standard plan.

## First Muse Spark runs (2026-09-28)

- The key check asked for 1 output token; Meta requires at least 16 (400), now 16.
- **`ENABLE_TOOL_SEARCH=true`, from Meta's own Claude Code guide, breaks workers:** every request failed with
  400 "Deferred tools require tools.tool_search". Workers use few tools, so it is now `false` for Meta.
- `research-069-muse-smoke.2` (standard tier): correct, 8 turns, 0.3 min, $0.048 (DeepSeek: 8 turns, 0.3 min).
- `review-071-a-muse.2` (standard tier): found the known defect (the door that waits for ever on a missing cell,
  "a visual hole becomes a progression block") and three lesser issues overlapping DeepSeek's and MiMo's; **3.0 min,
  14 turns, $0.41**, against DeepSeek's 5.3 min, 55 turns, $0.15 and MiMo's 21.6 min. At contributor prices the
  same run would cost about $0.03.

## Muse Spark on the rest of the tests (2026-09-28)

The open-source project was opted in to the contributor tier by its owner, in their own settings.

| Test | DeepSeek | MiMo | Muse |
|---|---|---|---|
| Review B (46 commits, 6 known issues) | 5 other proved issues, fix first; 4.0 min, $0.19 | refused by its filter | **accept**, 3 nits only (stale docstring, a log level, a tour note); 5.4 min, $0.037 (contributor) |
| Screenshots (7, the user's notes) | 3 hits, 2 partial, 1 miss; 2.3 min | timed out | **5 hits, 1 partial** (saw the doubled doors but did not call them a fault); 2.5 min, $0.26 (standard) |
| Design (settings screen) | clean, plainest; 4.9 min | polished, 2 bugs; 15.4 min | tidy and readable, focus ring on one tab while another's content shows; 1.6 min, $0.13 (standard) |

Muse is fast and cheap and the best at screenshots, but as a sole reviewer of a large change it would have passed
real bugs. The skill makes it Project A's contributor-tier worker for research, analysis, audits and second
reviews beside DeepSeek's; screenshots go to its standard tier; coders stay on DeepSeek until it is tried as one.

## Separate limits per provider (2026-09-29)

The DeepSeek daily limit ($10) was used up at noon and the launcher refused a MiMo review (exit 4) that ran on the
prepaid Token Plan; Muse's dollars ($2.61 that day) also counted against DeepSeek's limit. The user asked for
"different limits and pacing" per provider. Now:

- DeepSeek's limits stay limits.json's top-level keys (stretch and balance included). Another provider's are under
  `"providers": {"meta": {"day": 3}}`, set with `ds_spend.py set 3 --per day --provider meta` and removed with
  `off --provider meta`. A provider without limits has none; the per-worker cap (`run`) is shared.
- `spent`, `estimate`, `pace`, `over` and the "wait for the others" rule count only the provider's own ledger rows
  (DeepSeek's rows carry no `provider`) and live workers (live files now record the provider). The stretch share
  and the balance rate count DeepSeek's rows only.
- MiMo's Token Plan keeps its credit pacing (ds_allowance.py); `status` prints a line per provider with its plan.
- The launcher passes `--provider` to `check` for any provider but DeepSeek.

Probe (`experiments/briefs/probe-074-provider-limits.md`): a copy of the spend folder with DeepSeek's daily limit
at $0.50 against $7.30 spent. Dry runs: DeepSeek "a real launch would be refused: DeepSeek spend limit reached";
MiMo and Muse "within limits". A real MiMo run (probe-074-provider-limits.1) then started, answered in 15 s and
was recorded with `"provider": "xiaomi"`. Tests: 7 new in tests/test_ds_spend.py; 437 pass.
