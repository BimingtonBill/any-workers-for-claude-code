"""Where worker tokens go, from the worker transcripts on disk. Local only: it costs nothing to run.

    python tools/ds_cost.py <project folder> [<project folder> ...]

Prints the token split (cache reads, fresh input, output), spend by kind and effort, and the costliest
runs. Each project's runs are found in its state dir (tools/ds_state.py: DS_STATE_DIR, the project's
"stateDir", else <project>/local/agents ...). Tokens and prices come from launcher/ds_spend.py, the same
reckoning the spend limits use: each run is priced for the provider and tier in its manifest (DeepSeek's
list rates for runs that name none). The prices are list rates, assumed; the shares are what matter. See
docs/design/cost.md.
"""
import collections
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# ds_spend.py (and ds_providers.py, which it imports for other providers' prices) sit in launcher/ in the
# harness, and beside tools/ once installed as part of the skill.
for _folder in (HERE.parent / 'launcher', HERE.parent):
    if (_folder / 'ds_spend.py').is_file():
        sys.path.insert(0, str(_folder))
        break
import ds_spend  # noqa: E402
import ds_state  # noqa: E402

KEYS = ('cache_read', 'fresh_in', 'cache_write', 'out')


def api_calls(transcript):
    """API messages with usage in a transcript and its subagents', each once: the count beside
    ds_spend.usage's totals (a message without an id is counted where it appears)."""
    if not transcript or not os.path.exists(transcript):
        return 0
    files = [Path(transcript)]
    sub = Path(transcript).with_suffix('') / 'subagents'
    if sub.is_dir():
        files += sorted(sub.glob('*.jsonl'))
    seen, calls = set(), 0
    for f in files:
        with open(f, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    msg = json.loads(line).get('message') or {}
                except (ValueError, AttributeError):
                    continue
                mid = msg.get('id')
                if not isinstance(msg.get('usage'), dict) or (mid and mid in seen):
                    continue
                if mid:
                    seen.add(mid)
                calls += 1
    return calls


def runs_dirs(projects):
    """Each project's runs folder, by the launcher's state-dir rule, once each (DS_STATE_DIR sends every
    project to the same one)."""
    out = []
    for proj in projects:
        try:
            runs = Path(ds_state.state_dir(Path(proj))) / 'runs'
        except Exception:
            runs = Path(ds_state.fallback_dir(Path(proj))) / 'runs'
        if runs.is_dir() and runs.resolve() not in [r.resolve() for _, r in out]:
            out.append((proj, runs))
    return out


def collect(projects, spend=None):
    """One row per run with a transcript: its tokens, API calls, prices and cost."""
    spend = spend or ds_spend.spend_dir()
    rows = []
    for proj, runs in runs_dirs(projects):
        for rd in runs.iterdir():
            mf = rd / 'manifest.json'
            if not mf.is_file():
                continue
            try:
                m = json.loads(mf.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                continue
            if not isinstance(m, dict):
                continue
            t = m.get('transcript')
            if not t or not os.path.exists(t):
                continue
            u = ds_spend.usage(t)
            price = ds_spend.run_prices(spend, m.get('provider') or 'deepseek', m.get('tier') or 'standard')
            rows.append(dict(project=Path(proj).name, run=rd.name, kind=m.get('kind'), effort=m.get('effort'),
                             calls=api_calls(t), price=price, cost=ds_spend.cost(u, price), **u))
    return rows


def report(rows, projects):
    tot = collections.Counter()
    part = collections.Counter()    # dollars per token class
    for r in rows:
        for k in KEYS:
            tot[k] += r.get(k, 0)
            part[k] += r.get(k, 0) * r['price'].get(k, 0) / 1e6
    total_cost = sum(r['cost'] for r in rows)
    if not rows or not total_cost:
        print('no worker runs with usage found under %s (runs live in each project\'s state dir: '
              '<project>/local/agents/runs, or the one the launcher chose)' % (', '.join(projects) or 'the folders given'))
        return
    print('runs with transcripts: %d, API calls: %d' % (len(rows), sum(r['calls'] for r in rows)))
    print('tokens (M): cache-read %.1f | fresh-in %.1f | cache-write %.1f | out %.1f' % tuple(tot[k] / 1e6 for k in KEYS))
    print('share of cost: ' + ', '.join('%s %.0f%%' % (k, 100 * part[k] / total_cost) for k in ('cache_read', 'fresh_in', 'cache_write', 'out'))
          + '  (total ~$%.2f)' % total_cost)

    by = collections.defaultdict(lambda: [0, 0.0, 0, 0])
    for r in rows:
        b = by[(r['kind'], r['effort'])]
        b[0] += 1; b[1] += r['cost']; b[2] += r['out']; b[3] += r['calls']
    print('\nkind/effort      runs   cost$  avg-out-k  avg-calls')
    for (k, e), (n, c, o, calls) in sorted(by.items(), key=lambda x: -x[1][1])[:10]:
        print('%-10s %-5s %4d  %6.2f  %8.0f  %8.0f' % (k, e, n, c, o / n / 1000, calls / n))

    print('\ncostliest runs:')
    for r in sorted(rows, key=lambda r: r['cost'], reverse=True)[:6]:
        print('  $%.3f  %-44s %-8s calls=%-4d out=%dk cache-read=%dk' % (r['cost'], r['run'][:44], r['effort'], r['calls'], r['out'] // 1000, r['cache_read'] // 1000))


def main(argv=None):
    projects = sys.argv[1:] if argv is None else argv
    report(collect(projects), projects)
    return 0


if __name__ == '__main__':
    sys.exit(main())
