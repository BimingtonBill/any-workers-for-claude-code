"""Claude plan pacing: how fast a Claude session should work now, from the plan's usage windows.

On 2026-09-24/25 the DeepSeek limit held coders from 01:15, the OpenSkyrim sessions carried the work
themselves (35 Claude subagents, 81 edits) and the account hit its 5-hour limit, with the weekly one mostly used.
DeepSeek spend is paced (ds_spend.py); this paces Claude the same way, and lets each budget lean on the
other.

Claude Code can't read the plan windows from a script, but the desktop app gives every session a usage tool
(mcp__ccd_session_mgmt__get_usage). A session reads it and records it here; everything else works from the
latest reading. A reading only gets more optimistic with age (usage only grows until a reset), so an old one
is still a floor.

    python ds_claude.py record '<the get_usage JSON>'       or  record --window "5-hour" 40 2026-09-25T00:40Z
    python ds_claude.py status [--json]

Levels, from how far usage runs ahead of an even pace through each window (the same measure as DeepSeek's):
    0  on pace
    1  lean:  new work goes to DeepSeek workers first; Claude subagents only for what they can't do
    2  tight: no new Claude subagents but short lookups; don't take on work DeepSeek holds, queue it
    3  hold:  coordinate only (launch, review, integrate DeepSeek workers), and stop when DeepSeek is held too
"""
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import ds_common  # noqa: E402  (beside this file, in the harness and once installed)

LENGTH = {'five_hour': dt.timedelta(hours=5), 'week': dt.timedelta(days=7)}
HEAD_START = {'five_hour': 0.25, 'week': 0.1}   # a 5-hour window is bursty; a week should run even
LEVEL_AT = (1.0, 1.25, 1.5)
HOLD_USED = 90                                   # hold at this percent used, whatever the pace
# The week's last day: what isn't used by the reset is lost, so the weekly window stops holding back until 98%
# and sessions are told to spend the rest; the 5-hour window still paces bursts (the user's call, 2026-09-27).
SPEND_DOWN = dt.timedelta(hours=24)
SPEND_DOWN_HOLD = 98
STALE = dt.timedelta(minutes=60)
NAMES = {0: 'on pace', 1: 'lean', 2: 'tight', 3: 'hold'}
DO = {
    1: 'Send new work to DeepSeek workers first, and use Claude subagents only for what a worker can\'t do '
       '(smaller models for lookups).',
    2: 'Start no Claude subagents except short lookups, and don\'t take on work DeepSeek holds: queue it in the '
       'handoff for when either budget frees up.',
    3: 'Only coordinate: launch, review and integrate DeepSeek workers. If DeepSeek is held too, stop and queue '
       'the rest for after the reset.',
}


# One copy of each, in ds_common.py beside this file. parse_time gives UTC: every caller here compares, subtracts or
# prints through astimezone(), so the zone it carries does not matter.
spend_dir = ds_common.spend_dir
parse_time = ds_common.parse_time


def key(label):
    """'5-hour limit' -> five_hour, 'Weekly · all models' -> week, 'Weekly · Fable' -> week-fable."""
    low = label.lower()
    if '5-hour' in low or 'five' in low:
        return 'five_hour'
    if 'week' in low:
        model = re.split(r'[·:-]', low, maxsplit=1)[-1].strip() if re.search(r'[·:-]', low) else 'all models'
        return 'week' if model in ('all models', 'all', '') else 'week-' + re.sub(r'\W+', '-', model).strip('-')
    return None


def windows_from(usage):
    """The windows in a get_usage result (or its 'plan' part): {key: (percent used, resets at)}."""
    plan = usage.get('plan', usage) if isinstance(usage, dict) else {}
    out = {}
    for w in plan.get('windows') or []:
        k = key(str(w.get('label', '')))
        if k and w.get('percentUsed') is not None and w.get('resetsAt'):
            out[k] = (float(w['percentUsed']), parse_time(w['resetsAt']).isoformat())
    return out


def record(d, windows, now=None):
    now = (now or dt.datetime.now().astimezone()).astimezone()
    d.mkdir(parents=True, exist_ok=True)
    row = dict(at=now.isoformat(timespec='seconds'), windows={k: dict(used=u, resets=r) for k, (u, r) in windows.items()})
    # A usage reset mid-window (2026-09-25: the weekly window dropped from 88% to 0% with the same reset
    # time) starts the budget afresh from now, so the pace spreads it over what is left of the window.
    before = latest(d)
    for k, w in row['windows'].items():
        old = (before or {}).get('windows', {}).get(k) if before else None
        try:   # the same window: its reset time matches, give or take the milliseconds the tool adds
            same = abs(parse_time(old['resets']) - parse_time(w['resets'])) < dt.timedelta(minutes=5)
        except (TypeError, KeyError, ValueError):
            same = False
        if not same:
            continue
        if w['used'] < float(old.get('used') or 0) - 5:
            w['start'] = row['at']
        elif old.get('start'):
            w['start'] = old['start']
    with open(d / 'claude.jsonl', 'a', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps(row) + '\n')
    return row


def latest(d):
    try:
        lines = (d / 'claude.jsonl').read_text(encoding='utf-8').splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and isinstance(r.get('windows'), dict) and r.get('at'):
            return r
    return None


def window_pace(k, used, resets, now, start=None):
    """(level, ratio, on-pace-again time or None) for one window. A window past its reset is empty. `start`
    is when a mid-window usage reset began the budget afresh; the window then runs from there."""
    resets = parse_time(resets)
    if now >= resets:
        return 0, 0.0, None
    length = LENGTH['five_hour' if k == 'five_hour' else 'week']
    head = HEAD_START['five_hour' if k == 'five_hour' else 'week']
    begun = parse_time(start) if start else None
    start = begun if begun and resets - length < begun < resets else resets - length
    length = resets - start
    gone = max(0.0, min(1.0, (now - start) / length))
    share = used / 100.0
    ratio = share / min(1.0, gone + head)
    if k == 'week' and resets - now <= SPEND_DOWN:
        return (3 if used >= SPEND_DOWN_HOLD else 0), ratio, None
    level = 3 if used >= HOLD_USED else sum(ratio > t for t in LEVEL_AT)
    again = start + length * max(0.0, share - head) if level else None
    return level, ratio, (again if again and again < resets else None)


def status(d, now=None):
    """The pace now: level (None without a reading), the window behind it, per-window detail, the reading's
    age and whether it is stale."""
    now = (now or dt.datetime.now().astimezone()).astimezone()
    r = latest(d)
    out = dict(level=None, window=None, windows={}, age_min=None, stale=True, reading=r)
    if not r:
        return out
    age = now - parse_time(r['at'])
    out.update(age_min=int(age.total_seconds() // 60), stale=age > STALE, level=0)
    for k, w in r['windows'].items():
        try:
            level, ratio, again = window_pace(k, float(w['used']), w['resets'], now, w.get('start'))
        except (KeyError, TypeError, ValueError):
            continue
        reset = parse_time(w['resets'])
        out['windows'][k] = dict(used=0.0 if now >= reset else float(w['used']), resets=reset, level=level,
                                 ratio=ratio, again=again)
        # A per-model window only rules that model out; the pace comes from the 5-hour and all-models ones.
        if not k.startswith('week-') and level > out['level']:
            out.update(level=level, window=k)
    week = out['windows'].get('week')
    if week and 0 < (week['resets'] - now).total_seconds() <= SPEND_DOWN.total_seconds() and week['used'] < SPEND_DOWN_HOLD:
        out['spend_down'] = dict(left=100 - week['used'], hours=(week['resets'] - now).total_seconds() / 3600)
    return out


def level(d, now=None):
    """The level for other tools (ds_spend.py, the hook): 0 without a reading."""
    return status(d, now)['level'] or 0


def when(t, now):
    t = t.astimezone()
    return t.strftime('%H:%M') if t.date() == now.astimezone().date() else t.strftime('%a %H:%M')


def lines(d, now=None):
    """What to tell a session, in a few lines."""
    now = (now or dt.datetime.now().astimezone()).astimezone()
    s = status(d, now)
    read = ('Read the plan with the get_usage tool (mcp__ccd_session_mgmt__get_usage) and record it: '
            'python "%s" record \'<its JSON>\'' % Path(__file__).resolve().as_posix())
    if s['level'] is None:
        return ['Claude plan: no usage reading yet. ' + read]
    parts = []
    for k in ('five_hour', 'week') + tuple(k for k in s['windows'] if k.startswith('week-')):
        w = s['windows'].get(k)
        if w:
            parts.append('%s %d%% (resets %s)' % ({'five_hour': '5-hour', 'week': 'week'}.get(k, k.replace('week-', 'week ')),
                                                  w['used'], when(w['resets'], now)))
    head = 'Claude plan: %s: %s' % ('; '.join(parts), NAMES[s['level']])
    if s['level']:
        w = s['windows'][s['window']]
        head += ' (%s window, %.2fx an even pace%s)' % ('5-hour' if s['window'] == 'five_hour' else 'weekly', w['ratio'],
                                                        ', on pace again about %s if idle' % when(w['again'], now) if w['again'] else '')
    out = [head + '.']
    if s['level']:
        out.append(DO[s['level']])
    sd = s.get('spend_down')
    if sd and s['level'] < 2:
        out.append('The weekly allowance resets in %d hours with %d%% unused, and what is unused then is lost: spend it. '
                   'Take work on yourself and with Claude subagents rather than waiting on held DeepSeek workers, and use '
                   'higher effort for the hard parts. The 5-hour window still paces bursts.' % (round(sd['hours']), sd['left']))
    tight_models = [k.replace('week-', '') for k, w in s['windows'].items() if k.startswith('week-') and w['level'] >= 2]
    if tight_models:
        out.append('Avoid %s for subagents this week.' % ', '.join(tight_models))
    if s['stale']:
        out.append('This reading is %d min old, so usage is at least this. %s' % (s['age_min'], read))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('record', help='record a get_usage result (JSON) or --window entries')
    r.add_argument('json', nargs='?', help="get_usage output, or '-' to read it from stdin")
    r.add_argument('--window', nargs=3, action='append', metavar=('LABEL', 'PERCENT', 'RESETS_AT'))
    s = sub.add_parser('status')
    s.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    d = spend_dir()
    if a.cmd == 'record':
        windows = {}
        if a.json:
            try:
                windows = windows_from(json.loads(sys.stdin.read() if a.json == '-' else a.json))
            except ValueError as e:
                print('not JSON: %s' % e)
                return 2
        for label, pct, resets in a.window or []:
            if key(label):
                windows[key(label)] = (float(pct), parse_time(resets).isoformat())
        if not windows:
            print('no plan windows found (a get_usage result with status "not_applicable" has none: plan limits '
                  'do not apply, so there is nothing to pace)')
            return 2
        record(d, windows)
    if a.cmd == 'status' and a.json:
        st = status(d)
        print(json.dumps(dict(level=st['level'], name=NAMES.get(st['level']), window=st['window'], age_min=st['age_min'],
                              stale=st['stale'], windows={k: dict(used=w['used'], level=w['level'], ratio=round(w['ratio'], 2),
                                                                  resets=w['resets'].isoformat())
                                                          for k, w in st['windows'].items()})))
        return 0
    print('\n'.join(lines(d)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
