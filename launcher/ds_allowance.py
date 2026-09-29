"""Pacing for a provider plan with a fixed allowance that resets, like Xiaomi's MiMo Token Plan.

    python ds_allowance.py status --provider xiaomi --tier token-plan     JSON for the launcher
    python ds_allowance.py lines                                          one line per plan, for status

No provider tells us the remaining allowance (MiMo has no balance endpoint: the console's usage page is the only
place it shows), so the harness counts what its own workers used, from the spend log, at the plan's published
rates. Work outside the harness with the same key (Xiaomi's MiMo Code, say) is not seen; `sync` corrects that
from the console's figure.

The allowance is spread evenly over the plan period with a small head start, the way the DeepSeek stretch
spreads credit: ahead of that pace the launcher says to ease off, and well ahead it holds new work on this
provider (Claude sends it to DeepSeek instead). In the last two days of the period nothing holds until 98% is
used: what is left at the renewal is lost.

The user's plan is in ~/.claude-deepseek/providers.json (DS_PROVIDERS_USER), for example
    {"plans": {"xiaomi/token-plan": {"plan": "standard", "renews": "2026-10-01"}}}
"""
import argparse
import calendar
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ds_common  # noqa: E402
import ds_providers  # noqa: E402

HEAD_START = 0.1
EASE_AT, HOLD_AT = 1.0, 1.5          # the ratio of used share to the even-pace share
SPEND_DOWN = dt.timedelta(days=2)
SPEND_DOWN_HOLD = 0.98


spend_dir = ds_common.spend_dir


def iso(text):
    """An ISO time as an aware datetime in local time (the plan period runs from local midnights)."""
    return ds_common.parse_time(text).astimezone()


def add_month(d, day):
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return d.replace(year=y, month=m, day=min(day, calendar.monthrange(y, m)[1]))


def period(renews, now):
    """(start, end) of the plan month that contains `now`, renewing on `renews`'s day of the month."""
    day = renews.day
    start = now.replace(day=min(day, calendar.monthrange(now.year, now.month)[1]), hour=0, minute=0, second=0, microsecond=0)
    if start > now:
        y, m = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
        start = start.replace(year=y, month=m, day=min(day, calendar.monthrange(y, m)[1]))
    return start, add_month(start, day)


def plan_for(provider, tier, settings=None):
    """The user's plan for a provider tier with an allowance: (budget, rates, renews, plan name), else None."""
    settings = ds_providers.user_settings() if settings is None else settings
    mine = (settings.get('plans') or {}).get('%s/%s' % (provider, tier))
    reg = (ds_providers.registry().get('providers') or {}).get(provider) or {}
    allowance = ((reg.get('tiers') or {}).get(tier) or {}).get('allowance')
    if not mine or not allowance:
        return None
    budget = mine.get('amount') or (allowance.get('plans') or {}).get(str(mine.get('plan', '')).lower())
    if not budget:
        return None
    renews = iso(mine.get('renews'))
    pace_until = None
    if mine.get('paceUntil'):     # `ds_spend.py stretch 3w --provider xiaomi`: spread it to then, not the renewal
        try:
            pace_until = iso(mine['paceUntil'])
        except ValueError:
            pass
    return dict(budget=float(budget), rates=allowance.get('rates') or {}, renews=renews, plan=mine.get('plan'),
                unit=allowance.get('unit', 'credits'), synced=mine.get('synced'), pace_until=pace_until)


def used(provider, tier, start, end, rates, d=None):
    """What this provider tier's runs used between start and end, at the plan's rates."""
    total = 0.0
    path = (d or spend_dir()) / 'spend.jsonl'
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return 0.0
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get('provider') != provider or r.get('tier') != tier:
            continue
        try:
            when = iso(r.get('ended') or r.get('started'))
        except ValueError:
            continue
        if start <= when < end:
            total += sum((r.get(k) or 0) * v for k, v in rates.items())
    return total


def status(provider, tier, now=None, d=None, settings=None):
    now = (now or dt.datetime.now().astimezone()).astimezone()
    p = plan_for(provider, tier, settings)
    if not p:
        return dict(configured=False)
    start, end = period(p['renews'], now)
    u = used(provider, tier, start, end, p['rates'], d)
    synced = p.get('synced') or {}
    try:   # a figure the user read off the console replaces the harness's own count up to that moment
        at = iso(synced['at'])
        if start <= at < end:
            u = float(synced['used']) + used(provider, tier, at, end, p['rates'], d)
    except (KeyError, TypeError, ValueError):
        pass
    share = u / p['budget']
    # The pace runs to the stretch's end when one is set inside this period; the period itself still ends (and the
    # allowance is used) at the renewal.
    goal = p['pace_until'] if p.get('pace_until') and start < p['pace_until'] < end else end
    gone = max(0.0, min(1.0, (now - start) / (goal - start)))
    ratio = share / min(1.0, gone + HEAD_START)
    spend_down = goal - now <= SPEND_DOWN
    if spend_down:
        level = 'hold' if share >= SPEND_DOWN_HOLD else 'ok'
    else:
        level = 'hold' if share >= 1 or ratio > HOLD_AT else 'ease' if ratio > EASE_AT else 'ok'
    # When an idle run of workers brings it back to pace: the time by which this share is the even-pace share.
    again = start + (goal - start) * max(0.0, share - HEAD_START) if level != 'ok' else None
    return dict(configured=True, plan=p['plan'], unit=p['unit'], budget=p['budget'], used=u, share=share,
                ratio=ratio, level=level, spend_down=spend_down, start=start.isoformat(timespec='minutes'),
                pace_until=goal.isoformat(timespec='minutes') if goal != end else None,
                resets=end.isoformat(timespec='minutes'), again=again.isoformat(timespec='minutes') if again and again < end else None)


def line(provider, tier, now=None, d=None):
    s = status(provider, tier, now, d)
    if not s.get('configured'):
        return ''
    name = ds_providers.short_name(provider) or provider
    resets = iso(s['resets']).strftime('%a %d %b')
    text = '%s %s (%s): %.1f%% of %.3gB %s used, renews %s: %s' % (
        name, tier, s['plan'], 100 * s['share'], s['budget'] / 1e9, s['unit'], resets,
        {'ok': 'on pace', 'ease': 'ahead of pace, easing off', 'hold': 'held, new work goes to DeepSeek'}[s['level']])
    if s.get('pace_until'):
        text = text.replace(', renews ', ', paced to last until %s, renews ' % iso(s['pace_until']).strftime('%a %d %b'), 1)
    if s['spend_down'] and s['level'] == 'ok':
        text += '; the period ends soon and what is unused is lost, so use it'
    return text


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    st = sub.add_parser('status'); st.add_argument('--provider', required=True); st.add_argument('--tier', required=True)
    sub.add_parser('lines')
    sy = sub.add_parser('sync', help='record the used figure the provider console shows')
    sy.add_argument('--provider', required=True); sy.add_argument('--tier', required=True)
    sy.add_argument('--used', type=float, required=True, help='allowance used so far this period, in its unit')
    a = ap.parse_args(argv)
    if a.cmd == 'status':
        print(json.dumps(status(a.provider, a.tier)))
        return 0
    if a.cmd == 'lines':
        for key in sorted((ds_providers.user_settings().get('plans') or {})):
            provider, _, tier = key.partition('/')
            text = line(provider, tier)
            if text:
                print(text)
        return 0
    if a.cmd == 'sync':
        settings = ds_providers.user_settings()
        plan = (settings.setdefault('plans', {})).get('%s/%s' % (a.provider, a.tier))
        if not plan:
            print('no plan is set for %s/%s' % (a.provider, a.tier))
            return 1
        plan['synced'] = dict(used=a.used, at=dt.datetime.now().astimezone().isoformat(timespec='seconds'))
        ds_common.write_json_atomic(ds_common.user_providers_path(), settings)
        print(line(a.provider, a.tier))
        return 0


if __name__ == '__main__':
    sys.exit(main())
