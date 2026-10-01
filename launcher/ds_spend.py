"""DeepSeek spend limits, per day and per week, the way Claude's usage limits work: a worker does not
start when the limit is used up or when what it usually costs would not fit, and a running worker is
stopped when the limit is reached. The limit resets at local midnight (day) or Monday midnight (week;
set --from-now counts only spending from that moment and starts each week on that weekday).

Spending is also paced, so the limit is rarely reached at all. The budget is spread evenly over the day
or week, plus a head start of 20% of the limit. While spending stays within that pace, nothing changes.
When it runs ahead, workers ease off in steps (see EASE):
  1. lower effort (max becomes high), a note to finish in few steps, delegation one level down;
  2. effort low, and one worker at a time (a new one waits for others to finish, up to 20 minutes);
  3. as 2, and no coders or leads until spending is back on pace;
  4. at twice the pace, no new workers at all until it catches up.
A stretch (`stretch 3w`) makes each day's even share of the credit the pace target, not a hard limit: spending
past it is never refused or stopped, only paced, and what is overspent comes out of the following days' shares,
worked out again every midnight. Only limits the user sets with `set` are hard. Each provider has its own
limits, pace and stretch (`--provider meta|xiaomi`); MiMo's plan is paced by ds_allowance.py.
While Claude's own plan is tight (ds_claude.py, level 2 or more), the head start grows by
CLAUDE_TIGHT_HEAD, so DeepSeek takes more of the work; the limits themselves never move.

    python ds_spend.py status                  what has been spent, what is left, when it resets
    python ds_spend.py set 2 --per day         limit DeepSeek spend to $2 a day (--per week for a week,
                                               --from-now to ignore what was spent before now)
    python ds_spend.py set 1.5 --per run       stop any one worker once it has cost $1.50 (a nudge to wrap
                                               up comes first, at 75%); ds-agent.ps1 -MaxCost overrides it
    python ds_spend.py stretch 2w              make the DeepSeek credit last two weeks (also 10d, 36h, 1m, or a
                                               date: 2026-10-09); `stretch off` stops it
    python ds_spend.py off [--per day|week|run|stretch]  remove a limit (all of them when --per is left out)
    python ds_spend.py backfill <project> ...  add past runs from those projects to the spend record

ds-agent.ps1 calls the rest itself: check (before a worker starts), poll (every 15 seconds while it
runs: `live` and ds_steer.py `watch` in one process, reading only the transcript lines added since the
last poll) and record (when it ends, from the whole transcript). `live` and `watch` still work alone. Everything lives in one folder shared by every project, so a limit
covers all of them: DS_SPEND_DIR, else ~/.claude-deepseek/spend. It holds limits.json, spend.jsonl (one
line per finished run) and live/ (what each running worker has spent so far).

Costs are worked out from the token counts in each worker's transcript at DeepSeek's list prices
(PRICE below; override with "prices" in limits.json). They come out higher than the DeepSeek dashboard
(roughly a third higher in the one comparison made, 2026-09-24), so limits act a little early; the
dashboard is the real bill.

The DeepSeek balance: the launcher fetches it before every worker and passes it to `check`, which keeps
the latest in balance.json. `status` and `balance` fetch it fresh (the key is read from DEEPSEEK_API_KEY,
this process's or the saved user variable, and never printed), and say how long it lasts at the last
week's rate of spending. The morning report and the SessionStart hook use the saved value, and warn
when it runs low.
"""
import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import ds_common  # noqa: E402  (beside this file, in the harness and once installed)

# DeepSeek list prices, US dollars per million tokens.
PRICE = dict(cache_read=0.028, fresh_in=0.28, cache_write=0.28, out=0.42)

# What a run of each kind cost on average over 233 runs in two projects (2026-09, tools/ds_cost.py),
# used until the spend record holds a few runs of that kind.
DEFAULT_ESTIMATE = dict(impl=0.70, lead=0.60, analysis=0.40, research=0.25, digest=0.25, review=0.15,
                        critic=0.15, advisor=0.15, websearch=0.05, selftest=0.02, probe=0.02)
FALLBACK_ESTIMATE = 0.30
PERIODS = ('day', 'week')
WARN_AT = 0.8          # say so once a limit is this far used
RUN_NUDGE_AT = 0.75    # tell a worker to wrap up once it has used this much of the cap per worker
REFUSED = 3            # exit code for "over the limit"

# Pacing. The share of a limit that is "on pace" at a moment is the share of the window gone by, plus
# HEAD_START of the limit (so the first job of the day isn't held back). EASE_AT are the ratios of
# (spent + this worker's estimate) to that allowance where each easing step begins.
HEAD_START = 0.2
CLAUDE_TIGHT_HEAD = 0.2   # extra head start while Claude's plan is tight (ds_claude.py level 2+): lean on DeepSeek
EASE_AT = (1.0, 1.25, 1.5, 2.0)
EXPENSIVE = ('impl', 'lead')
EASE = {1: 'lower effort, short jobs, delegation one level down',
        2: 'effort low, one worker at a time, delegation one level down',
        3: 'effort low, one worker at a time, no coders or leads, delegation one level down',
        # Step 4 came with pacing-only budgets (2026-09-29): with no hard limit behind the pace, spending twice
        # the even pace holds every new worker until the pace catches up.
        4: 'no new workers until spending is back on pace'}


# One copy of each, in ds_common.py beside this file; the names stay here because other files and tests use them.
spend_dir = ds_common.spend_dir
read_json = ds_common.read_json
write_json_atomic = ds_common.write_json_atomic
parse_time = ds_common.parse_time


def raw_limits(d):
    """limits.json as saved."""
    lim = read_json(d / 'limits.json', {})
    return lim if isinstance(lim, dict) else {}


# Each provider has its own limits and pace (the user, 2026-09-29: a MiMo review on its prepaid plan was refused
# because DeepSeek's daily limit was used up). DeepSeek's are limits.json's top-level keys, as before; another
# provider's are under "providers": {"meta": {"day": 3}}. Its runs are the ledger rows with that "provider"
# (DeepSeek's rows have none). A provider with no limits set has none; the per-worker cap ("run") is shared.
PROVIDER_NAMES = {'deepseek': 'DeepSeek', 'xiaomi': 'MiMo', 'meta': 'Muse'}   # when providers.json can't say


def prov_key(provider):
    return (provider or 'deepseek').lower()


def prov_name(provider):
    """The provider's short name for messages ("MiMo", "Muse"): providers.json's "short" (ds_providers.short_name),
    else PROVIDER_NAMES."""
    key = prov_key(provider)
    try:
        import ds_providers
        name = ds_providers.short_name(key)
        if name and name != key:
            return name
    except Exception:
        pass
    return PROVIDER_NAMES.get(key, provider)


def row_provider(r):
    return prov_key(r.get('provider'))


def limits(d, now=None, provider='deepseek'):
    """The limits in force for a provider. DeepSeek: limits.json, with the daily limit lowered to today's share
    of the credit while a stretch is set (stretch_day); 'stretchDay' then holds that share."""
    lim = raw_limits(d)
    if prov_key(provider) != 'deepseek':
        own = (lim.get('providers') or {}).get(prov_key(provider)) or {}
        out = {p: own[p] for p in PERIODS + ('run',) if own.get(p)}
        if 'run' not in out and lim.get('run'):
            out['run'] = lim['run']
        share = provider_stretch_day(d, own, prov_key(provider), now)
        if share is not None:
            out['stretchDay'], out['stretch'] = share, own['stretch']
            if not out.get('day') or share < out['day']:
                out['day'], out['dayIsShare'] = share, True
        return out
    share = stretch_day(d, lim, now)
    if share is not None:
        lim['stretchDay'] = share
        if not lim.get('day') or share < lim['day']:
            lim['day'], lim['dayIsShare'] = share, True
    return lim


# --- Stretch: make the credit last until a date ---

def midnight(t):
    t = t.astimezone()
    return dt.datetime(t.year, t.month, t.day).astimezone()


def stretch_day(d, lim=None, now=None):
    """Today's share of the credit while a stretch is set: the balance as it was at midnight, spread evenly
    over the days left until the stretch ends. The balance at midnight is the last saved balance plus what
    was spent between midnight and that reading (or minus what was spent since, for a reading from before
    midnight). None without a stretch, after it ends, or before any balance is known."""
    lim = raw_limits(d) if lim is None else lim
    st = lim.get('stretch')
    if not isinstance(st, dict) or not st.get('until'):
        return None
    b = last_balance(d)
    if not b:
        return None
    return even_share(d, 'deepseek', b['usd'], b['at'], st['until'], now)


def even_share(d, provider, usd, at, until, now=None):
    """Today's even share of a provider's credit: `usd` as read at `at`, moved to midnight by what the provider's
    runs spent in between, spread over the days left until `until`. None once `until` has passed."""
    now = (now or dt.datetime.now().astimezone()).astimezone()
    until = parse_time(until)
    if now >= until:
        return None
    day = midnight(now)
    at = parse_time(at)
    between = sum(r.get('cost') or 0 for r, t in ended_rows(d, provider) if t and min(day, at) <= t < max(day, at))
    at_midnight = usd + (between if at >= day else -between)
    days = max((until - day).total_seconds() / 86400, 1 / 24)
    left = max(0.0, at_midnight)
    return round(left / days if days >= 1 else left, 2)     # under a day to go: all of it today


def provider_stretch_day(d, own, provider, now=None):
    """Today's share for another provider's stretch: {"until", "credit", "at"} under its limits. It has no balance
    endpoint, so the credit is what the user read off its console when the stretch was set."""
    st = own.get('stretch')
    if not isinstance(st, dict) or not st.get('until') or st.get('credit') is None or not st.get('at'):
        return None
    return even_share(d, provider, float(st['credit']), st['at'], st['until'], now)


def soft(lim, period):
    """A stretch's daily share is a pace target, not a hard limit (the user, 2026-09-29: "rely on pacing only"):
    spending past it is not refused or stopped; the pace eases off and, well ahead, holds new work, and whatever
    is overspent comes out of the following days' shares, which are worked out again every midnight."""
    return period == 'day' and bool(lim.get('dayIsShare'))


def parse_until(text, now=None):
    """'2w', '10d', '36h', '1m' (30 days), '3 weeks', or a date ('2026-10-09': the end of that day) -> the
    moment the credit should last until."""
    import re
    now = (now or dt.datetime.now().astimezone()).astimezone()
    m = re.match(r'^\s*(\d+(?:\.\d+)?)\s*(h|hours?|d|days?|w|weeks?|m|mo|months?)\s*$', text.strip().lower())
    if m:
        n, unit = float(m.group(1)), m.group(2)[0]
        hours = n * {'h': 1, 'd': 24, 'w': 24 * 7, 'm': 24 * 30}[unit]
        return now + dt.timedelta(hours=hours)
    try:
        t = dt.datetime.fromisoformat(text.strip())
    except ValueError:
        raise ValueError('say how long, like 2w, 10d, 36h or 1m, or give a date like 2026-10-09')
    if len(text.strip()) <= 10:          # a date alone: to the end of that day
        t = t + dt.timedelta(days=1)
    return t if t.tzinfo else t.astimezone()


def prices(d):
    p = dict(PRICE)
    p.update(raw_limits(d).get('prices') or {})
    return p


def run_prices(d, provider='deepseek', tier='standard'):
    """Prices for one run: DeepSeek's (with the user's overrides in limits.json), or another provider's from
    launcher/providers.json. Each provider's dollars count against its own limits."""
    if not provider or provider == 'deepseek':
        return prices(d)
    try:
        import ds_providers
        p = ds_providers.prices(provider, tier)
    except Exception:
        p = None
    return p or prices(d)


# --- Costs from a transcript ---

class Usage:
    """The running token totals behind usage(), fed one transcript entry at a time, each API message once (by
    its id). state() and Usage(state) carry them between processes for `poll`."""

    def __init__(self, state=None):
        s = state or {}
        self.u = dict(cache_read=0, fresh_in=0, cache_write=0, out=0)
        self.u.update(s.get('usage') or {})
        self.seen = set(s.get('seen') or ())

    def feed(self, e, since=None):
        msg = e.get('message') or {}
        us, mid = msg.get('usage'), msg.get('id')
        if not isinstance(us, dict) or (mid and mid in self.seen):
            return
        if since and e.get('timestamp') and parse_time(e['timestamp']) < since:
            return
        if mid:
            self.seen.add(mid)
        u = self.u
        u['fresh_in'] += us.get('input_tokens') or 0
        u['cache_read'] += us.get('cache_read_input_tokens') or 0
        u['cache_write'] += us.get('cache_creation_input_tokens') or 0
        u['out'] += us.get('output_tokens') or 0

    def state(self):
        return dict(usage=dict(self.u), seen=sorted(self.seen))


def transcript_files(transcript):
    """A worker transcript and its in-process subagents' transcripts, in the order usage() reads them."""
    files = [Path(transcript)]
    sub = Path(transcript).with_suffix('') / 'subagents'
    if sub.is_dir():
        files += sorted(sub.glob('*.jsonl'))
    return files


def usage(transcript, since=None):
    """Token totals in a worker transcript (and its in-process subagents' transcripts), once per API
    message, counting only messages at or after `since` (a resumed run's earlier launches are already
    recorded)."""
    acc = Usage()
    if not transcript or not os.path.exists(transcript):
        return acc.u
    for f in transcript_files(transcript):
        with open(f, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                acc.feed(e, since)
    return acc.u


def _new_lines(path, offset):
    """The complete lines of `path` after byte `offset`, and the offset after the last of them. A line the worker
    is still writing (no newline yet) is left for the next read."""
    lines = []
    with open(path, 'rb') as fh:
        fh.seek(offset)
        for raw in fh:
            if not raw.endswith(b'\n'):
                break
            offset += len(raw)
            lines.append(raw)
    return lines, offset


def poll_counts(transcript, since, run_dir, steer=True):
    """(usage totals, signals or None) for a running worker, reading only what was added to its transcripts since
    the last poll. The byte offset of each file and the running counts are kept in <run_dir>/poll.json; a new
    launch (another `since`), another transcript, or a file that shrank starts the count again from the top."""
    import ds_steer
    state_file = Path(run_dir) / 'poll.json'
    key = dict(since=since.isoformat(), transcript=str(transcript))
    state = read_json(state_file, None)
    if not isinstance(state, dict) or state.get('key') != key:
        state = dict(key=key, files={})
    offsets = state.get('files') or {}
    try:
        files = transcript_files(transcript)
        if any(os.path.getsize(f) < offsets.get(str(f), 0) for f in files):
            state, offsets = dict(key=key, files={}), {}
    except OSError:
        pass
    acc = Usage(state)
    sig = ds_steer.Signals(state.get('signals')) if steer else None
    for n, f in enumerate(files):
        try:
            lines, offsets[str(f)] = _new_lines(f, offsets.get(str(f), 0))
        except OSError:
            continue
        main = n == 0 and sig is not None
        for raw in lines:
            if main:
                if b'"tool_use"' not in raw and b'"tool_result"' not in raw and b'"usage"' not in raw:
                    continue
            elif b'"usage"' not in raw:
                continue
            try:
                e = json.loads(raw.decode('utf-8', 'replace'))
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            if main:     # checked against `since` once here, for both counts
                if since and e.get('timestamp') and parse_time(e['timestamp']) < since:
                    continue
                sig.feed(e)
                acc.feed(e)
            else:
                acc.feed(e, since)
    state = dict(key=key, files=offsets, **acc.state())
    if sig is not None:
        state['signals'] = sig.state()
    try:
        write_json_atomic(state_file, state, indent=None)
    except OSError:
        pass
    return acc.u, (sig.result() if sig is not None else None)


def cost(u, price=PRICE):
    return sum(u.get(k, 0) * p for k, p in price.items()) / 1e6


# --- Windows ---

def window(period, now=None, d=None):
    """Start and end of the current day or week, in local time. A week runs Monday to Monday, or from
    the weekday in limits.json "weekStartsOn" (0 Monday .. 6 Sunday). A window that "countFrom" falls
    inside starts there instead, so spending before a limit was set with --from-now is not counted and
    the pace spreads the budget over what is left of that window."""
    now = (now or dt.datetime.now().astimezone()).astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == 'week':
        first = int(raw_limits(d).get('weekStartsOn') or 0) if d else 0
        start -= dt.timedelta(days=(start.weekday() - first) % 7)
        end = start + dt.timedelta(days=7)
    else:
        end = start + dt.timedelta(days=1)
    # Rebuild from the date so a daylight-saving change doesn't shift midnight by an hour.
    start = dt.datetime(start.year, start.month, start.day).astimezone()
    end = dt.datetime(end.year, end.month, end.day).astimezone()
    since = raw_limits(d).get('countFrom') if d else None
    if since:
        since = parse_time(since).astimezone()
        if start < since < end:
            start = since
    return start, end


# spend.jsonl is parsed once per process while it is unchanged. A launch's plan() read it six times and status
# nine, parsing every row's time each time: 0.4 s a launch at 12,000 rows (2026-09-29). Keyed on the file's path,
# modification time, size and inode, so an appended or rewritten file is read again.
_LEDGER = {}


def _parse_ended(r):
    try:
        return parse_time(r['ended']) if r.get('ended') else None
    except (TypeError, ValueError):
        return None


def ended_rows(d, provider=None):
    """(row, its 'ended' as an aware UTC datetime or None) for the recorded runs: every provider's, or one's.
    The rows are shared with the cache: read them, don't change them."""
    path = os.path.abspath(str(Path(d) / 'spend.jsonl'))
    try:
        st = os.stat(path)
    except OSError:
        _LEDGER.pop(path, None)
        return []
    key = (st.st_mtime_ns, st.st_size, st.st_ino)
    hit = _LEDGER.get(path)
    if not hit or hit['key'] != key:
        rows = []
        try:
            with open(path, encoding='utf-8') as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    rows.append((r, _parse_ended(r) if isinstance(r, dict) else None))
        except OSError:
            return []
        hit = _LEDGER[path] = dict(key=key, rows=rows, by={})
    if provider is None:
        return hit['rows']
    p = prov_key(provider)
    if p not in hit['by']:
        hit['by'][p] = [x for x in hit['rows'] if isinstance(x[0], dict) and row_provider(x[0]) == p]
    return hit['by'][p]


def ledger(d, provider=None):
    """The recorded runs: every provider's, or one provider's. The rows are shared with the cache (ended_rows):
    read them, don't change them."""
    return [r for r, _ in ended_rows(d, provider)]


def pid_alive(pid):
    if not pid:
        return False
    if os.name == 'nt':
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def live(d, exclude=None, provider=None):
    """What running workers have spent so far (every provider's, or one provider's). A file whose launcher is
    gone is a leftover (the run was recorded, or killed before it could be): drop it."""
    out = {}
    folder = d / 'live'
    if not folder.is_dir():
        return out
    for f in folder.glob('*.json'):
        r = read_json(f, None)
        if not r or r.get('run_id') == exclude:
            continue
        if not pid_alive(r.get('pid')):
            try:
                f.unlink()
            except OSError:
                pass
            continue
        if provider is not None and row_provider(r) != prov_key(provider):
            continue
        out[r['run_id']] = r
    return out


def spent(d, period, now=None, exclude=None, provider='deepseek'):
    """One provider's recorded runs that ended in this window plus what its running workers have spent."""
    start, _ = window(period, now, d)
    done = sum(r.get('cost') or 0 for r, t in ended_rows(d, provider) if t and t >= start)
    running = sum(r.get('cost') or 0 for r in live(d, exclude, provider).values())
    return done + running


def estimate(d, kind, provider='deepseek'):
    """What a run of this kind usually costs on this provider: the mean of its last 20 recorded runs once there
    are 3, else the default table (DeepSeek's costs)."""
    # Newest by end time, not file order: a backfill appends whole projects one after another.
    rows = sorted(((r, t) for r, t in ended_rows(d, provider) if r.get('kind') == kind and r.get('cost') and t),
                  key=lambda x: x[1])
    costs = [r['cost'] for r, _ in rows][-20:]
    if len(costs) >= 3:
        return sum(costs) / len(costs)
    return DEFAULT_ESTIMATE.get(kind, FALLBACK_ESTIMATE)


def resets(period, now=None, d=None):
    now = (now or dt.datetime.now().astimezone()).astimezone()
    _, end = window(period, now, d)
    left = end - now
    h, m = divmod(int(left.total_seconds()) // 60, 60)
    when = end.strftime('%H:%M') if period == 'day' else end.strftime('%a %H:%M')
    return '%s (in %s)' % (when, ('%dd %dh' % (h // 24, h % 24)) if h >= 24 else '%dh %dm' % (h, m))


def label(period):
    return 'today' if period == 'day' else 'this week'


# --- Commands ---

def check(d, kind, balance=None, now=None, provider='deepseek'):
    """(ok, lines) for starting a worker of this kind on this provider now: only its own limits count."""
    lim = limits(d, now, provider)
    est = estimate(d, kind, provider)
    name = prov_name(provider)
    flag = '' if prov_key(provider) == 'deepseek' else ' --provider %s' % prov_key(provider)
    lines = []
    for period in PERIODS:
        cap = lim.get(period)
        if not cap or soft(lim, period):
            continue
        used = spent(d, period, now, provider=provider)
        left = cap - used
        if left <= 0:
            return False, ['%s spend limit reached: $%.2f of $%.2f %s. It resets at %s. Do this work '
                           'as a Claude subagent on Sonnet 5.5, on another provider, or ask the user to raise the limit (python ds_spend.py '
                           'set <dollars> --per %s%s).' % (name, used, cap, label(period), resets(period, now, d), period, flag)]
        if est > left:
            return False, ['A %s worker on %s usually costs about $%.2f, and only $%.2f of the $%.2f %s limit is '
                           'left (resets at %s). Do this work yourself, give it to a cheaper kind or another provider, '
                           'or ask the user to raise the limit.' % (kind, name, est, left, cap, 'daily' if period == 'day'
                                                                    else 'weekly', resets(period, now, d))]
        if (used + est) / cap >= WARN_AT:
            lines.append('%d%% of the %s %s limit will be used once this worker is done ($%.2f + about '
                         '$%.2f of $%.2f); it resets at %s.' % (100 * (used + est) / cap, 'daily' if period ==
                         'day' else 'weekly', name, used, est, cap, resets(period, now, d)))
    if balance is not None and prov_key(provider) == 'deepseek' and est > balance:
        return False, ['Your DeepSeek balance is $%.2f and a %s worker usually costs about $%.2f. Ask the user '
                       'to top up at platform.deepseek.com, or do this work yourself.' % (balance, kind, est)]
    return True, lines


def holds(d, since):
    """Launches the limits or the pace refused or made wait since `since`: (refused, waited), each a list
    of rows, one per run (a waiting run checks every 10 s, so it is counted once)."""
    refused, waited = [], {}
    try:
        lines = (d / 'holds.jsonl').read_text(encoding='utf-8').splitlines()
    except OSError:
        return [], []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict) or not r.get('at') or parse_time(r['at']) < since:
            continue
        if r.get('held') == 'refused':
            refused.append(r)
        else:
            waited.setdefault(r.get('run_id'), r)
    return refused, list(waited.values())


def waited_logged(d, run_id):
    """Whether holds.jsonl already has a 'waited' row for this run. A waiting launcher checks every 10 s, and a
    row per check made 1,599 rows in five days (2026-09-29); holds() counts a run once anyway. A refused launch
    is still logged every time."""
    if not run_id:
        return False
    needle = json.dumps(run_id)
    try:
        with open(d / 'holds.jsonl', encoding='utf-8') as fh:
            for line in fh:
                if needle not in line or 'waited' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get('run_id') == run_id and r.get('held') != 'refused':
                    return True
    except OSError:
        pass
    return False


def claude_level(d, now=None):
    """The Claude plan's pace level (ds_claude.py beside this file; 0 without it or without a reading)."""
    try:
        import ds_claude
        return ds_claude.level(d, now)
    except Exception:
        return 0


# --- The DeepSeek balance ---

LOW_DAYS = 2        # warn when the balance lasts fewer days than this at the last week's rate (was 7: with a
                    # week-long stretch that warned every session; the user wants it only when very low, 2026-09-27)
LOW_USD = 2.0       # or when it is below this, whatever the rate


def api_key():
    key = os.environ.get('DEEPSEEK_API_KEY')
    if not key and sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as k:
                key = winreg.QueryValueEx(k, 'DEEPSEEK_API_KEY')[0]
        except OSError:
            key = None
    return key or None


def fetch_balance(timeout=10):
    """(usd, available) from DeepSeek's /user/balance, or None when there is no key or the call fails."""
    import urllib.request
    key = api_key()
    if not key:
        return None
    req = urllib.request.Request('https://api.deepseek.com/user/balance',
                                 headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode('utf-8'))
    except Exception:
        return None
    usd = next((i for i in body.get('balance_infos') or [] if i.get('currency') == 'USD'), None)
    if not usd:
        return None
    return float(usd.get('total_balance') or 0), body.get('is_available') is not False


def save_balance(d, usd, available=True, now=None):
    now = (now or dt.datetime.now().astimezone()).astimezone()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / 'balance.json.tmp'
    tmp.write_text(json.dumps(dict(at=now.isoformat(timespec='seconds'), usd=round(usd, 4), available=available)),
                   encoding='utf-8')
    os.replace(str(tmp), str(d / 'balance.json'))


def last_balance(d):
    b = read_json(d / 'balance.json', None)
    return b if isinstance(b, dict) and isinstance(b.get('usd'), (int, float)) and b.get('at') else None


def daily_rate(d, now=None):
    """Dollars a day to expect: the last 7 days' average, but no more than the limits allow."""
    return rate_basis(d, now)[0]


def rate_basis(d, now=None):
    """(dollars a day, how it was found): the last 7 days' average, or the daily limit (or a seventh of the
    weekly one) when that is lower, since spending can't run faster than the limits."""
    now = (now or dt.datetime.now().astimezone()).astimezone()
    since = now - dt.timedelta(days=7)
    rate = sum(r.get('cost') or 0 for r, t in ended_rows(d, 'deepseek') if t and t >= since) / 7
    lim = limits(d, now)
    caps = [(lim['day'], ("today's $%.2f share of the stretch" if lim.get('dayIsShare') else 'the $%.2f daily limit used in full')
             % lim['day'])] if lim.get('day') else []
    if lim.get('week'):
        caps.append((lim['week'] / 7, 'the $%.2f weekly limit used in full' % lim['week']))
    cap = min(caps) if caps else None
    if cap and cap[0] < rate:
        return cap
    return rate, "the last week's rate"


def balance_line(d, usd, now=None, at=None):
    """'DeepSeek balance: $12.34, about 11 days at the last week's rate ($1.10 a day).' plus its age when
    it is a saved value, and a top-up note when it runs low."""
    now = (now or dt.datetime.now().astimezone()).astimezone()
    rate, how = rate_basis(d, now)
    line = 'DeepSeek balance: $%.2f' % usd
    if at:
        mins = int((now - parse_time(at)).total_seconds() // 60)
        line += ' (%s)' % ('checked %d min ago' % mins if mins < 120 else 'checked %s' % parse_time(at).astimezone().strftime('%a %H:%M'))
    days = usd / rate if rate > 0 else None
    if days is not None:
        line += ', about %s at %s ($%.2f a day)' % ('%.0f days' % days if days >= 2 else '%.0f hours' % (days * 24), how, rate)
    line += '.'
    if low(usd, days):
        line += ' Running low: tell the user it needs a top-up at platform.deepseek.com.'
    return line


def low(usd, days):
    return usd < LOW_USD or (days is not None and days < LOW_DAYS)


def balance_warning(d, now=None):
    """The saved balance as one line when it runs low, else ''. No network."""
    b = last_balance(d)
    if not b:
        return ''
    rate = daily_rate(d, now)
    if not low(b['usd'], b['usd'] / rate if rate > 0 else None):
        return ''
    return balance_line(d, b['usd'], now, at=b['at'])


def pace(d, kind, now=None, provider='deepseek'):
    """How far a provider's spending is ahead of an even pace: ease 0 (on pace) to 3, the period behind it, and
    when a worker of this kind fits the pace again (None when only the reset will do)."""
    now = (now or dt.datetime.now().astimezone()).astimezone()
    lim = limits(d, now, provider)
    est = estimate(d, kind, provider)
    best = dict(ease=0, period=None, fits_at=None)
    # While Claude's plan is tight, DeepSeek may run further ahead of an even pace; its limits don't move.
    head = HEAD_START + (CLAUDE_TIGHT_HEAD if claude_level(d, now) >= 2 else 0)
    for period in PERIODS:
        cap = lim.get(period)
        if not cap:
            continue
        start, end = window(period, now, d)
        gone = (now - start) / (end - start)
        need = spent(d, period, now, provider=provider) + est
        ratio = need / (cap * min(1.0, gone + head))
        ease = sum(ratio > t for t in EASE_AT)
        if ease > best['ease']:
            fits = start + (end - start) * max(0.0, need / cap - head)
            best = dict(ease=ease, period=period, fits_at=fits if fits < end else None)
    return best


def plan(d, kind, balance=None, now=None, lineage='', waited=False, provider='deepseek'):
    """Whether and how a worker of this kind starts now on this provider: its hard limits (check), then its
    pace. Returns ok, lines (what to tell Claude), ease, effort_cap ('high', 'low' or None) and wait (other
    workers on the same provider are running and this one should wait for them)."""
    ok, lines = check(d, kind, balance, now, provider)
    out = dict(ok=ok, lines=lines, ease=0, effort_cap=None, wait=False)
    both = ("Claude's plan is ahead of its pace too (ds_claude.py status), so don't do this yourself: split off "
            'what a research or review worker can do and queue the rest for when either budget frees up.')
    if not ok:
        lim = limits(d, now, provider)
        if (lim.get('stretchDay') is not None and lim['day'] == lim['stretchDay']
                and spent(d, 'day', now) + estimate(d, kind) > lim['day']):
            lines.append("Today's daily limit is today's share of the credit, spread to last until %s at the "
                         "user's request; the share is worked out again at midnight." % parse_time(
                             lim['stretch']['until']).astimezone().strftime('%a %d %b'))
        if claude_level(d, now) >= 2:
            lines.append('But ' + both)
        return out
    p = pace(d, kind, now, provider)
    ease = out['ease'] = p['ease']
    if not ease:
        return out
    lim = limits(d, now, provider)
    ahead = prov_name(provider) + ' spending is ahead of an even pace for %s' % (
        "today's share of the credit, spread to last until %s" % parse_time(lim['stretch']['until']).astimezone().strftime(
            '%a %d %b') if soft(lim, p['period']) else 'the %s limit' % ('daily' if p['period'] == 'day' else 'weekly'))
    if ease >= 4 or (ease >= 3 and kind in EXPENSIVE):
        when = ('it fits the pace again at %s' % p['fits_at'].astimezone().strftime('%H:%M' if p['period'] == 'day' else '%a %H:%M')
                if p['fits_at'] else 'it fits again after the reset at %s' % resets(p['period'], now, d))
        out.update(ok=False, lines=['%s, so %s wait (%s). %s' % (
            ahead, 'all new workers' if ease >= 4 else '%s workers' % kind, when, both if claude_level(d, now) >= 2 else
            'Run it as a Claude subagent on Sonnet 5.5 instead (Opus if the work needs judgment), or wait.' if ease >= 4 else
            'Run it as a Claude subagent on Sonnet 5.5 instead (Opus if the work needs judgment), split off the parts a research or review worker can do, or wait.')])
        return out
    out['effort_cap'] = 'high' if ease == 1 else 'low'
    mine = set(filter(None, lineage.split('/')))
    others = [r for r in live(d, provider=provider).values() if r.get('run_id') not in mine]
    out['wait'] = ease >= 2 and bool(others) and not waited
    lines.append('%s: easing off (%s).' % (ahead, EASE[ease]))
    return out


def lowered(level, d, now=None):
    """The delegation level to work at: one lower while spending runs ahead of pace (never below 1)."""
    return max(1, level - 1) if pace(d, 'research', now)['ease'] else level


class locked:
    """Hold spend_dir/pace.lock, so workers starting at the same moment (a lead's spawn_workers) take
    turns to check the pace and claim their place, instead of all seeing no one running."""
    def __init__(self, d):
        self.lock = d / 'pace.lock'
        self.fd = None

    def __enter__(self):
        import time
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(150):
            try:
                self.fd = os.open(str(self.lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.lock.stat().st_mtime > 30:   # left by a killed process
                        self.lock.unlink()
                except OSError:
                    pass
                time.sleep(0.1)
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            try:
                self.lock.unlink()
            except OSError:
                pass


def run_cap_nudge(run_dir, spent_so_far, cap):
    """Queue one wrap-up nudge for the worker (delivered by its ds_steer.py hook) at RUN_NUDGE_AT of the
    cap per worker, and note it in steer.json with the others."""
    run_dir = Path(run_dir)
    state_file = run_dir / 'steer.json'
    try:
        state = json.loads(state_file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {'nudges': []}
    if any(n.get('key') == 'cost' for n in state['nudges']):
        return False
    text = ('You have cost $%.2f of the $%.2f this task may cost, and each step costs more than the last. Wrap up now: '
            'finish or back out the change in hand and report what is done and what is left, or you will be stopped '
            'at $%.2f.' % (spent_so_far, cap, cap))
    with open(run_dir / 'nudge.txt', 'a', encoding='utf-8') as fh:
        fh.write(text + '\n')
    state['nudges'].append(dict(key='cost', at=dt.datetime.now().astimezone().isoformat(timespec='seconds'), text=text))
    state_file.write_text(json.dumps(state, indent=1), encoding='utf-8')
    return True


def over(d, run_id, now=None, provider='deepseek'):
    """The first of a provider's limits that its running workers and recorded runs together have now reached,
    or None."""
    lim = limits(d, now, provider)
    for period in PERIODS:
        cap = lim.get(period)
        if cap and not soft(lim, period) and spent(d, period, now, provider=provider) >= cap:
            return period, cap
    return None


def provider_lines(d, now=None):
    """One line for each other provider that has limits, spending this week or a plan: what it spent against its
    own limits, and its plan's allowance (launcher/ds_allowance.py, e.g. MiMo's Token Plan)."""
    plans = {}
    try:
        import ds_allowance
        for key in sorted((ds_allowance.ds_providers.user_settings().get('plans') or {})):
            text = ds_allowance.line(*key.split('/', 1), d=d)
            if text:
                plans.setdefault(prov_key(key.split('/', 1)[0]), []).append(text)
    except Exception:
        pass
    seen = {row_provider(r) for r in ledger(d)} | set((raw_limits(d).get('providers') or {})) | set(plans)
    out = []
    for prov in sorted(seen - {'deepseek'}):
        lim = limits(d, now, prov)
        parts = []
        for period in PERIODS:
            used = spent(d, period, now, provider=prov)
            parts.append(('%s $%.2f of $%.2f' % (label(period), used, lim[period])) if lim.get(period)
                         else '%s $%.2f' % (label(period), used))
        has_limit = any(lim.get(x) for x in PERIODS)
        if not has_limit and prov not in plans and not spent(d, 'week', now, provider=prov):
            continue
        text = ', '.join(parts) + ('' if has_limit else ', no dollar limit')
        if lim.get('stretchDay') is not None:
            text += ' (budget $%.2f spread to last until %s; the daily figure is a pace target)' % (
                float(lim['stretch']['credit']), parse_time(lim['stretch']['until']).astimezone().strftime('%a %d %b'))
        if has_limit:
            ease = pace(d, 'research', now, prov)['ease']
            text += '; ' + ('ahead of an even pace, easing off' if ease else 'on pace')
        out.append('%-9s %s' % (prov_name(prov) + ':', text))
        out += ['  plan:    ' + t for t in plans.get(prov, [])]
    return out


def stretch_line(d, now=None):
    """'stretch: credit spread to last until Fri 9 Oct 09:00: $1.18 today (balance $20.00)', or ''."""
    now = (now or dt.datetime.now().astimezone()).astimezone()
    st = raw_limits(d).get('stretch')
    if not isinstance(st, dict) or not st.get('until'):
        return ''
    until = parse_time(st['until']).astimezone()
    when = until.strftime('%a %d %b %H:%M')
    if now >= until:
        return 'stretch:  ended %s (`stretch off` clears it, or set a new one)' % when
    share = stretch_day(d, now=now)
    if share is None:
        return 'stretch:  to last until %s, waiting for a balance reading (the next worker or `balance` fetches it)' % when
    days = (until - midnight(now)).total_seconds() / 86400
    day = raw_limits(d).get('day')
    # Name the limit that binds and how to lift it: "a tighter daily limit is also set" left a lower daily cap
    # binding under a larger stretch share with no word on what to do (2026-09-26).
    tighter = (' - but the $%.2f/day limit is lower and applies today; `ds_spend.py off --per day` lifts it so the '
               'stretch share governs' % day) if day and day < share else ''
    return 'stretch:  credit spread to last until %s: $%.2f for today, %.1f days left%s' % (when, share, days, tighter)


def plan_stretch(provider, text, now):
    """A stretch for a provider on a plan with an allowance (MiMo's Token Plan): its pacing (ds_allowance.py) spreads
    the allowance to that moment instead of the plan's renewal. Returns the message, or None without such a plan."""
    import ds_allowance
    settings = ds_allowance.ds_providers.user_settings()
    keys = [k for k in (settings.get('plans') or {}) if k.split('/', 1)[0] == provider]
    if not keys:
        return None
    off = text.strip().lower() in ('off', 'stop', 'none')
    until = None if off else parse_until(text, now)
    for k in keys:
        if off:
            settings['plans'][k].pop('paceUntil', None)
        else:
            settings['plans'][k]['paceUntil'] = until.isoformat(timespec='seconds')
    write_json_atomic(ds_common.user_providers_path(), settings)
    lines = [ds_allowance.line(*k.split('/', 1)) for k in keys]
    head = ('%s is paced over its plan period again.' % prov_name(provider) if off else
            "%s's plan allowance is now spread to last until %s." % (prov_name(provider), until.astimezone().strftime('%a %d %b %H:%M')))
    return '\n'.join([head] + [l for l in lines if l])


def stretch_cmd(d, text, now=None, provider='deepseek', credit=None):
    now = (now or dt.datetime.now().astimezone()).astimezone()
    provider = prov_key(provider)
    if provider != 'deepseek':
        try:
            msg = plan_stretch(provider, text, now) if credit is None else None
        except ValueError as e:
            print(str(e))
            return 2
        if msg:
            print(msg)
            return 0
        lim = raw_limits(d)
        own = lim.setdefault('providers', {}).setdefault(provider, {})
        if text.strip().lower() in ('off', 'stop', 'none'):
            own.pop('stretch', None)
        else:
            try:
                until = parse_until(text, now)
            except ValueError as e:
                print(str(e))
                return 2
            old = own.get('stretch') or {}
            if credit is None and old.get('credit') is None:
                print('%s has no balance the harness can read: give the dollars to spend by then (its prepaid credit, or a budget for a billed-after account), '
                      'e.g. `stretch 3w --provider %s --credit 20`.' % (prov_name(provider), provider))
                return 2
            own['stretch'] = dict(until=until.isoformat(timespec='seconds'), set=now.isoformat(timespec='seconds'),
                                  credit=round(credit, 2) if credit is not None else old['credit'],
                                  at=now.isoformat(timespec='seconds') if credit is not None else old['at'])
        if not own:
            lim['providers'].pop(provider, None)
        if not lim.get('providers'):
            lim.pop('providers', None)
        d.mkdir(parents=True, exist_ok=True)
        write_json_atomic(d / 'limits.json', lim)
        share = limits(d, now, provider).get('stretchDay')
        print('%s: %s' % (prov_name(provider), 'no longer stretched' if 'stretch' not in own else
                          'budget spread to last until %s: $%.2f for today, as a pace target' % (
                              until.strftime('%a %d %b %H:%M'), share or 0)))
        return 0
    lim = raw_limits(d)
    if text.strip().lower() in ('off', 'stop', 'none'):
        lim.pop('stretch', None)
        d.mkdir(parents=True, exist_ok=True)
        write_json_atomic(d / 'limits.json', lim)
        print('The credit is no longer stretched; the other limits stay as they were.')
        return 0
    try:
        until = parse_until(text, now)
    except ValueError as e:
        print(str(e))
        return 2
    if until <= now:
        print('That is already past.')
        return 2
    fresh = fetch_balance()
    if fresh:
        save_balance(d, *fresh, now=now)
    d.mkdir(parents=True, exist_ok=True)
    lim['stretch'] = dict(until=until.isoformat(timespec='seconds'), set=now.isoformat(timespec='seconds'))
    write_json_atomic(d / 'limits.json', lim)
    print('DeepSeek spending is now spread so your credit lasts until %s: each day gets an even share of what '
          'is left, worked out again every day, and the usual pacing spreads each share through the day.'
          % until.astimezone().strftime('%a %d %b %H:%M'))
    line = stretch_line(d, now)
    if line:
        print(line)
    if not fresh and not last_balance(d):
        print('The balance could not be read yet, so nothing is held back until a worker launch reads it.')
    return 0


def show_balance(d, given=None, offline=False):
    """The balance line for status: the given value, else a fresh fetch, else the saved one with its age."""
    if given is not None:
        return balance_line(d, given)
    fresh = None if offline else fetch_balance()
    if fresh:
        save_balance(d, *fresh)
        return balance_line(d, fresh[0])
    b = last_balance(d)
    return balance_line(d, b['usd'], at=b['at']) if b else None


def live_stop(d, a, c):
    """`live` for a running worker that has cost `c` so far: note it in live/<run id>.json, queue the wrap-up nudge
    at RUN_NUDGE_AT of its cap, and say why it must stop now (its cap, or a spend limit used up), else None."""
    (d / 'live').mkdir(parents=True, exist_ok=True)
    (d / 'live' / ('%s.json' % a.run_id)).write_text(
        json.dumps(dict(run_id=a.run_id, pid=a.pid, cost=c, kind=a.kind, provider=prov_key(a.provider))), encoding='utf-8')
    cap = a.run_cap if a.run_cap is not None else limits(d, provider=a.provider).get('run')
    if cap and c >= cap:
        return 'this worker has cost $%.2f, reaching the $%.2f cap per worker; brief what is left as smaller tasks' % (c, cap)
    if cap and a.run_dir and c >= RUN_NUDGE_AT * cap:
        run_cap_nudge(a.run_dir, c, cap)
    hit = over(d, a.run_id, provider=a.provider)
    if hit:
        return 'the %s %s spend limit ($%.2f) is used up; it resets at %s' % (
            'daily' if hit[0] == 'day' else 'weekly', prov_name(a.provider), hit[1], resets(hit[0], None, d))
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--dir', help='the spend folder (default: DS_SPEND_DIR or ~/.claude-deepseek/spend)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('status'); s.add_argument('--balance', type=float)
    s.add_argument('--offline', action='store_true', help='use the saved balance instead of asking DeepSeek')
    s = sub.add_parser('balance', help='the DeepSeek balance and how long it lasts at the last week\'s rate')
    s.add_argument('--offline', action='store_true'); s.add_argument('--json', action='store_true')
    s = sub.add_parser('set'); s.add_argument('dollars', type=float); s.add_argument('--per', choices=PERIODS + ('run',), default='day')
    s.add_argument('--provider', default='deepseek', help="another provider's own limit (meta, xiaomi); the default is DeepSeek's")
    s.add_argument('--from-now', action='store_true', help='count only spending from now on; with --per week, weeks '
                   'also start on today\'s weekday instead of Monday')
    s = sub.add_parser('off'); s.add_argument('--per', choices=PERIODS + ('run', 'stretch'))
    s.add_argument('--provider', default='deepseek')
    s = sub.add_parser('stretch', help='make the DeepSeek credit last until then: 2w, 10d, 36h, 1m, a date, or off')
    s.add_argument('until')
    s.add_argument('--provider', default='deepseek', help='another provider: MiMo paces its plan to that date; Muse needs --credit')
    s.add_argument('--credit', type=float, help="dollars to spend by then: the provider's prepaid credit, or a budget (Meta bills after use)")
    s = sub.add_parser('check'); s.add_argument('--kind', required=True); s.add_argument('--balance', type=float)
    s.add_argument('--provider', default='deepseek', help="the worker's provider: only its own limits and pace count")
    s.add_argument('--json', action='store_true', help='print the plan (pace included) as JSON')
    s.add_argument('--run-id'); s.add_argument('--pid', type=int)
    s.add_argument('--lineage', default='', help="the worker's lineage (claude/<parent>/...): its ancestors never make it wait")
    s.add_argument('--claim', action='store_true', help='when it may start, mark it running (needs --run-id and --pid)')
    s.add_argument('--waited', action='store_true', help='it has waited long enough; start even if others run')
    for name in ('live', 'record', 'poll'):
        s = sub.add_parser(name)
        s.add_argument('--run-id', required=True); s.add_argument('--transcript', required=True)
        s.add_argument('--since', required=True, help='when this launch started (ISO time)')
        s.add_argument('--pid', type=int, help='the launcher process')
        s.add_argument('--kind'); s.add_argument('--effort'); s.add_argument('--project')
        s.add_argument('--provider', default='deepseek'); s.add_argument('--tier', default='standard')
        s.add_argument('--out-total', type=int, default=0, help="record: the result's output total, when the transcript lacks it")
        s.add_argument('--run-dir', help='live: the run folder, for the wrap-up nudge')
        s.add_argument('--run-cap', type=float, help='live: this run\'s cap in dollars (-MaxCost), instead of the "run" limit')
        if name == 'poll':
            s.add_argument('--budget', type=int, help="the steps this worker's kind usually takes (ds_steer.py watch)")
            s.add_argument('--no-steer', action='store_true', help='the spend part only, no nudges')
            s.add_argument('--deadline', help='when the launcher stops the worker (ISO time), for the time nudge')
            s.add_argument('--timeout-minutes', type=int, help="this launch's timeout, for the time nudge")
    s = sub.add_parser('backfill'); s.add_argument('projects', nargs='+')
    a = ap.parse_args(argv)
    d = Path(a.dir) if a.dir else spend_dir()

    if a.cmd == 'set' and prov_key(a.provider) != 'deepseek':
        if a.dollars <= 0 or a.per == 'run' or a.from_now:
            ap.error('another provider takes a daily or weekly limit of more than 0 (the per-worker cap and --from-now are shared)')
        d.mkdir(parents=True, exist_ok=True)
        lim = raw_limits(d)
        lim.setdefault('providers', {}).setdefault(prov_key(a.provider), {})[a.per] = round(a.dollars, 2)
        write_json_atomic(d / 'limits.json', lim)
        print('%s spend is now limited to $%.2f a %s, across all projects; the other providers keep their own limits.' % (
            prov_name(a.provider), a.dollars, a.per))
        return 0
    if a.cmd == 'set':
        if a.dollars <= 0:
            ap.error('a limit must be more than 0; use "off" to remove one')
        d.mkdir(parents=True, exist_ok=True)
        lim = raw_limits(d); lim[a.per] = round(a.dollars, 2)
        if a.from_now:
            lim['countFrom'] = dt.datetime.now().astimezone().isoformat(timespec='seconds')
            if a.per == 'week':
                lim['weekStartsOn'] = dt.date.today().weekday()
        write_json_atomic(d / 'limits.json', lim)
        print('DeepSeek spend is now limited to $%.2f a %s, across all projects%s.' % (
            a.dollars, 'worker' if a.per == 'run' else a.per, ', counting from now' + (' (weeks start on %s)' % dt.date.today().strftime('%A')
                                                        if a.per == 'week' else '') if a.from_now else ''))
        return 0
    if a.cmd == 'stretch':
        return stretch_cmd(d, a.until, provider=a.provider, credit=a.credit)
    if a.cmd == 'off' and prov_key(a.provider) != 'deepseek':
        lim = raw_limits(d)
        own = (lim.get('providers') or {}).get(prov_key(a.provider)) or {}
        for period in ([a.per] if a.per else PERIODS):
            own.pop(period, None)
        if not own:
            (lim.get('providers') or {}).pop(prov_key(a.provider), None)
        if not lim.get('providers'):
            lim.pop('providers', None)
        if d.is_dir():
            write_json_atomic(d / 'limits.json', lim)
        print('%s limits now: %s' % (prov_name(a.provider), ', '.join('$%.2f a %s' % (own[p], p) for p in PERIODS if own.get(p)) or 'none'))
        return 0
    if a.cmd == 'off':
        lim = raw_limits(d)
        for period in ([a.per] if a.per else PERIODS + ('run', 'stretch')):
            lim.pop(period, None)
        if d.is_dir():
            write_json_atomic(d / 'limits.json', lim)
        print('Limits now: %s' % (', '.join('$%.2f a %s' % (lim[p], 'worker' if p == 'run' else p) for p in PERIODS + ('run',) if lim.get(p)) or 'none'))
        return 0
    if a.cmd == 'status':
        print('DeepSeek:')
        lim = limits(d)
        for period in PERIODS:
            used = spent(d, period)
            cap = lim.get(period)
            if cap:
                pct = min(used / cap, 1)
                bar = '#' * round(20 * pct) + '-' * (20 - round(20 * pct))
                print('%-9s [%s] %3d%%  $%.2f of $%.2f%s, resets %s' % (label(period), bar, 100 * used / cap, used, cap,
                      " (today's share: a pace target, not a limit)" if soft(lim, period) else '', resets(period, None, d)))
            else:
                print('%-9s $%.2f spent, no limit' % (label(period), used))
        p = pace(d, 'research')
        if any(lim.get(x) for x in PERIODS):
            print('pace:     %s' % ('ahead of an even pace, easing off: ' + EASE[p['ease']] if p['ease'] else 'on pace'))
        if lim.get('run'):
            print('per worker: stopped at $%.2f, told to wrap up at $%.2f' % (lim['run'], RUN_NUDGE_AT * lim['run']))
        if stretch_line(d):
            print(stretch_line(d))
        running = live(d, provider='deepseek')
        if running:
            print('running:  %d worker(s), $%.2f so far' % (len(running), sum(r.get('cost') or 0 for r in running.values())))
        b = show_balance(d, a.balance, a.offline)
        if b:
            print('balance:  ' + b.replace('DeepSeek balance: ', ''))
        for line in provider_lines(d):
            print(line)
        print('(estimated from transcripts at list prices; the DeepSeek dashboard has the real bill)')
        return 0
    if a.cmd == 'balance':
        fresh = None if a.offline else fetch_balance()
        if fresh:
            save_balance(d, *fresh)
        b = last_balance(d)
        if a.json:
            rate = daily_rate(d)
            print(json.dumps(dict(usd=b['usd'], at=b['at'], available=b.get('available', True), fresh=bool(fresh),
                                  per_day=round(rate, 4), days=round(b['usd'] / rate, 1) if rate > 0 else None) if b else None))
        else:
            print(balance_line(d, b['usd'], at=None if fresh else b['at']) if b else
                  'DeepSeek balance: unknown (no key in DEEPSEEK_API_KEY, or DeepSeek did not answer, and none saved yet).')
        return 0
    if a.cmd == 'check':
        if a.balance is not None:     # the launcher's fetch: keep it, so status and the reports can show it
            try:
                save_balance(d, a.balance)
            except OSError:
                pass
        if not a.json:
            ok, lines = check(d, a.kind, a.balance, provider=a.provider)
            for line in lines:
                print(line)
            return 0 if ok else REFUSED
        with locked(d):
            out = plan(d, a.kind, a.balance, lineage=a.lineage, waited=a.waited, provider=a.provider)
            if a.claim and (not out['ok'] or (out['wait'] and not waited_logged(d, a.run_id))):
                with open(d / 'holds.jsonl', 'a', encoding='utf-8', newline='\n') as fh:
                    fh.write(json.dumps(dict(at=dt.datetime.now().astimezone().isoformat(timespec='seconds'),
                                             run_id=a.run_id, kind=a.kind, provider=prov_key(a.provider), held='refused' if not out['ok'] else 'waited',
                                             ease=out['ease'], why=' '.join(out['lines'])[:300])) + '\n')
            if out['ok'] and not out['wait'] and a.claim and a.run_id and a.pid:
                (d / 'live').mkdir(parents=True, exist_ok=True)
                (d / 'live' / ('%s.json' % a.run_id)).write_text(
                    json.dumps(dict(run_id=a.run_id, pid=a.pid, cost=0, kind=a.kind, provider=prov_key(a.provider))), encoding='utf-8')
        print(json.dumps(out))
        return 0 if out['ok'] else REFUSED
    if a.cmd == 'poll':
        # `live` and ds_steer.py `watch` in one process, reading only the transcript lines added since the last
        # poll (poll_counts). Prints one JSON line: the nudges queued, the cost so far, and why to stop, if so.
        if not a.run_dir:
            ap.error('poll needs --run-dir')
        since = parse_time(a.since)
        steer = not a.no_steer
        u, sig = poll_counts(a.transcript, since, a.run_dir, steer=steer)
        fired = []
        if steer:
            import ds_steer
            fired = [k for k, _ in ds_steer.watch(a.transcript, since, a.run_dir, a.budget, sig=sig,
                                                  deadline=parse_time(a.deadline) if a.deadline else None,
                                                  minutes=a.timeout_minutes)]
        c = cost(u, run_prices(d, a.provider, a.tier))
        stop = live_stop(d, a, c)
        print(json.dumps(dict(nudged=fired, cost=round(c, 5), stop=stop)))
        return REFUSED if stop else 0
    if a.cmd in ('live', 'record'):
        u = usage(a.transcript, parse_time(a.since))
        if a.out_total > u['out']:
            u['out'] = a.out_total
        c = cost(u, run_prices(d, a.provider, a.tier))
        (d / 'live').mkdir(parents=True, exist_ok=True)
        lf = d / 'live' / ('%s.json' % a.run_id)
        if a.cmd == 'live':
            stop = live_stop(d, a, c)
            if stop:
                print(stop)
                return REFUSED
            return 0
        row = dict(run_id=a.run_id, kind=a.kind, effort=a.effort, project=a.project, started=a.since,
                   **({} if a.provider == 'deepseek' else dict(provider=a.provider, tier=a.tier)),
                   ended=dt.datetime.now().astimezone().isoformat(timespec='seconds'), cost=round(c, 5), **u)
        try:                  # the steps it took, for the step budget of its kind (ds_steer.budget)
            import ds_steer
            row['steps'] = ds_steer.signals(a.transcript, parse_time(a.since))['steps']
        except Exception:
            pass
        with open(d / 'spend.jsonl', 'a', encoding='utf-8', newline='\n') as fh:
            fh.write(json.dumps(row) + '\n')
        try:
            lf.unlink()
        except OSError:
            pass
        print('$%.3f' % c)
        return 0
    if a.cmd == 'backfill':
        d.mkdir(parents=True, exist_ok=True)
        have = {r.get('run_id') for r in ledger(d)}
        added, total = 0, 0.0
        with open(d / 'spend.jsonl', 'a', encoding='utf-8', newline='\n') as fh:
            for proj in a.projects:
                runs = Path(proj) / 'local' / 'agents' / 'runs'
                for mf in sorted(runs.glob('*/manifest.json')) if runs.is_dir() else []:
                    m = read_json(mf, {})
                    if not m.get('run_id') or m['run_id'] in have or not m.get('ended') or not m.get('transcript'):
                        continue
                    u = usage(m['transcript'])
                    c = cost(u, prices(d))
                    if not c:
                        continue
                    fh.write(json.dumps(dict(run_id=m['run_id'], kind=m.get('kind'), effort=m.get('effort'),
                                             project=m.get('project'), started=m.get('started'), ended=m['ended'],
                                             cost=round(c, 5), backfilled=True, **u)) + '\n')
                    have.add(m['run_id']); added += 1; total += c
        print('added %d past run(s), $%.2f' % (added, total))
        return 0


if __name__ == '__main__':
    sys.exit(main())
