"""Nudges for a running worker, the moment it starts to drift, instead of a hard stop later.

The launcher calls `watch` every 15 seconds while a worker runs. It reads the worker's transcript and,
the first time a sign of drift appears, queues a short message in the run folder (nudge.txt):

  steps     100, 150 and 200 steps with tools: cost grows with every step, since each re-reads the
            whole context (the costliest OpenSkyrim coders ran 220-300 steps)
  context   200k and 350k tokens of context
  refused   the same kind of shell command refused 3 times: that form isn't allowed, so stop retrying
  sleep     a sleep of 2 minutes or more (coders slept up to 9 minutes on background builds)

  budget    past 1.5 and 2 times the steps its kind usually takes (the step budget), when that comes
            before the fixed counts: a review usually takes about 11 steps, so one at 90 is far off
            track while the fixed nudges would still say nothing
  time      TIME_AT (75%) of the run's -TimeoutMinutes gone: finish the change in hand, run the checks and
            report now (a DOA MiMo coder was killed at its 90 minutes after writing its files but before
            reporting, 2026-09-29)
The worker's own PostToolUse hook runs `deliver` after every tool call: it hands any queued message
to the worker as added context and clears it, so the worker sees it before its next step. Every nudge
is also kept in steer.json, which tools/ds_morning.py reports.

The step budget is learned: ds_spend.py records each finished run's steps, and `budget` gives the median
of the kind's last 30 runs (DEFAULT_BUDGET until there are 5). The launcher tells the worker its budget up
front. Each `watch` also writes progress.json in the run folder (steps, tool uses, context, what it is
doing now), so Claude can see how a running worker is going without reading its output: `progress`.

    python ds_steer.py watch --transcript <file> --since <ISO time> --run-dir <folder> [--budget N]
                             [--deadline <ISO time> --timeout-minutes N]
    python ds_steer.py deliver --run-dir <folder>
    python ds_steer.py budget --kind impl [--spend-dir <folder>]
    python ds_steer.py progress [--state-dir <folder>]      the running workers, one line each
"""
import os
import sys


def _nothing_to_deliver(argv):
    """True when this is `deliver --run-dir <folder>` and that folder holds no nudge.txt. The hook runs after
    every tool call of every worker, so this is answered before argparse and the other imports: 74 ms a call
    otherwise, about 7 s over a 100-step worker. Anything unusual (another form of the arguments) is left to
    the full path."""
    if len(argv) != 4 or argv[1] != 'deliver' or argv[2] != '--run-dir':
        return False
    return not os.path.exists(os.path.join(argv[3], 'nudge.txt'))


if __name__ == '__main__' and _nothing_to_deliver(sys.argv):
    os._exit(0)   # nothing written, so nothing to flush; skipping interpreter shutdown saves ~2 ms more

import argparse
import datetime as dt
import json
import re
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import ds_common  # noqa: E402  (beside this file, in the harness and once installed)

STEPS = (100, 150, 200)
CONTEXT = (200_000, 350_000)
REFUSED_AFTER = 3
SLEEP_AT = 120
TIME_AT = 0.75         # of the run's timeout
# Steps a kind usually takes, until the spend record holds 5 runs of it with their steps.
DEFAULT_BUDGET = dict(review=20, critic=20, websearch=20, digest=40, probe=15, selftest=15, advisor=40,
                      research=60, analysis=60, lead=60, impl=80)
BUDGET_RUNS = 30
BUDGET_MAX = 80        # never tell a worker ~100 steps is normal: briefs should finish well under 100

DENIED = ('Permission for this tool use was denied', 'requires approval', 'was blocked')


parse_time = ds_common.parse_time


SIGNAL_MARKS = ('"tool_use"', '"tool_result"', '"usage"')


class Signals:
    """The running counts behind signals(), fed one transcript entry at a time. state() and Signals(state) carry
    them between processes, so a poll (ds_spend.py poll) reads only the lines added since the last one."""

    def __init__(self, state=None):
        s = state or {}
        self.steps = set(s.get('steps') or ())
        self.context = s.get('context') or 0
        self.longest_sleep = s.get('sleep') or 0
        self.slept = s.get('slept') or 0
        self.commands = dict(s.get('commands') or {})
        self.refused = dict(s.get('refused') or {})
        self.tools = s.get('tools') or 0
        self.now = s.get('now') or ''

    @staticmethod
    def wants(line):
        return any(m in line for m in SIGNAL_MARKS)

    def feed(self, e):
        """One parsed transcript entry, already known to be in this launch (at or after `since`)."""
        msg = e.get('message') or {}
        us = msg.get('usage')
        if isinstance(us, dict):
            self.context = (us.get('input_tokens') or 0) + (us.get('cache_read_input_tokens') or 0) + \
                           (us.get('cache_creation_input_tokens') or 0)
        for c in msg.get('content') or []:
            if not isinstance(c, dict):
                continue
            if c.get('type') == 'tool_use':
                self.steps.add(msg.get('id') or c.get('id'))
                self.tools += 1
                self.now = describe_tool(c.get('name'), c.get('input') or {})
                if c.get('name') == 'Bash':
                    cmd = (c.get('input') or {}).get('command') or ''
                    self.commands[c.get('id')] = cmd
                    m = re.match(r'\s*sleep\s+(\d+)', cmd)
                    if m:
                        self.longest_sleep = max(self.longest_sleep, int(m.group(1)))
                        self.slept += int(m.group(1))
            elif c.get('type') == 'tool_result' and c.get('is_error') and c.get('tool_use_id') in self.commands:
                body = c.get('content')
                body = body if isinstance(body, str) else json.dumps(body)
                if any(d in body for d in DENIED):
                    words = re.findall(r'[\w./-]+', self.commands[c['tool_use_id']])
                    # "cargo fmt", "git grep", "python -c" are forms of their own; cd's argument is a path.
                    head = ' '.join(words[:2]) if len(words) > 1 and words[0] in ('git', 'cargo', 'python', 'npm') else (words[0] if words else '?')
                    self.refused[head] = self.refused.get(head, 0) + 1

    def result(self):
        return dict(steps=len(self.steps), context=self.context, refused=dict(self.refused), sleep=self.longest_sleep,
                    slept=self.slept, tools=self.tools, now=self.now)

    def state(self):
        return dict(steps=list(self.steps),
                    context=self.context, sleep=self.longest_sleep, slept=self.slept, commands=self.commands,
                    refused=self.refused, tools=self.tools, now=self.now)


def signals(transcript, since=None):
    """Steps with tools, latest context size, refused shell commands by their first word, and the
    longest sleep, in this launch of the worker."""
    if not transcript or not os.path.exists(transcript):
        return dict(steps=0, context=0, refused={}, sleep=0, slept=0, tools=0, now='')
    sig = Signals()
    with open(transcript, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if not Signals.wants(line):
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if since and e.get('timestamp') and parse_time(e['timestamp']) < since:
                continue
            sig.feed(e)
    return sig.result()


def describe_tool(name, inp):
    """'Bash cargo test -p portal', 'Read src/door.rs', 'Grep load_door': what a tool call is doing."""
    detail = (inp.get('command') or inp.get('file_path') or inp.get('pattern') or inp.get('query') or inp.get('url')
              or inp.get('description') or '')
    detail = ' '.join(str(detail).split())
    if inp.get('file_path'):
        detail = '/'.join(Path(detail).parts[-2:])
    return ('%s %s' % (name, detail)).strip()[:80]


def budgets(spend_dir=None, kinds=()):
    """budget() for every kind at once, from one read of the spend record: each of `kinds`, each kind in
    DEFAULT_BUDGET and each kind recorded. The launcher's one start-up call (ds_prepare.py) uses this."""
    d = Path(spend_dir) if spend_dir else ds_common.spend_dir()
    seen = {}
    try:
        with open(d / 'spend.jsonl', encoding='utf-8') as fh:
            for line in fh:
                if '"steps"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                # Trivial runs (harness probes, failed starts) would drag the median down to a few steps.
                if (isinstance(r, dict) and isinstance(r.get('kind'), str) and isinstance(r.get('steps'), int)
                        and r['steps'] >= 5 and (r.get('cost') or 0) >= 0.02):
                    seen.setdefault(r['kind'], []).append(r['steps'])
    except OSError:
        pass
    out = {}
    for kind in set(kinds) | set(DEFAULT_BUDGET) | set(seen):
        runs = sorted(seen.get(kind, [])[-BUDGET_RUNS:])
        if len(runs) < 5:
            out[kind] = DEFAULT_BUDGET.get(kind, 60)
            continue
        mid = runs[len(runs) // 2] if len(runs) % 2 else (runs[len(runs) // 2 - 1] + runs[len(runs) // 2]) / 2
        out[kind] = min(BUDGET_MAX, max(10, int(-(-mid // 5) * 5)))
    return out


def budget(kind, spend_dir=None):
    """The steps a worker of this kind usually takes: the median of its last BUDGET_RUNS real recorded runs
    (5+ steps, 2+ cents), rounded up to 5, between 10 and BUDGET_MAX, once there are 5; else DEFAULT_BUDGET."""
    return budgets(spend_dir, (kind,))[kind]


def nudges(sig, fired, step_budget=None):
    """The messages due now, as (key, text), skipping any already sent."""
    out = []
    if step_budget:
        # Relative to what this kind usually takes, where that comes before the fixed counts below.
        for mult, key in ((1.5, 'budget150'), (2.0, 'budget200')):
            at = int(step_budget * mult)
            if sig['steps'] >= at and at < STEPS[0] and key not in fired:
                out.append((key, (
                    'You are at %d steps; tasks like this usually take about %d. Finish the part you are on and '
                    'write your report now: what is done, what is left, and what you found.' if mult < 2 else
                    'You are at %d steps, twice the %d this kind of task usually takes. Stop here and report: what '
                    'is done, what is left, and what you found. Claude will brief the rest separately.')
                    % (sig['steps'], step_budget)))
    for n in STEPS:
        if sig['steps'] >= n and 'steps%d' % n not in fired:
            # Firmer than it was: a coder read "finish the part you are on" as leave to carry on (2026-09-25).
            text = {100: 'You have taken 100 steps. Stop exploring now. Finish only the change already in hand, then write your report: what is done, what is left, and what you found. Every further step re-reads everything so far and costs more than the last.',
                    150: 'You are at 150 steps. Wrap up now: stop exploring, finish or back out the change in hand, and report what is done and what is left.',
                    200: 'You are at 200 steps, well past what this task should take. Report now with what you have; Claude will split the rest into smaller tasks.'}[n]
            out.append(('steps%d' % n, text))
    for n in CONTEXT:
        if sig['context'] >= n and 'context%d' % n not in fired:
            out.append(('context%d' % n, 'Your context is %dk tokens and every step re-reads all of it. Read nothing more in full: search, or read only the lines you need, and head for your report.' % (n // 1000)))
    for head, count in sig['refused'].items():
        key = 'refused:' + head
        if count >= REFUSED_AFTER and key not in fired:
            out.append((key, 'Shell commands starting "%s" have been refused %d times: that form is not allowed here and retrying will not change it. Use a form from your list of allowed commands (no cd, no chains, no python -c), or write a small script and run it.' % (head, count)))
    if sig['sleep'] >= SLEEP_AT and 'sleep' not in fired:
        out.append(('sleep', 'Don\'t sleep for minutes at a time: a sleep cannot end early when a build finishes. Run builds and tests in the foreground with the Bash timeout raised (up to 600000 ms); for a longer job, check on it about once a minute.'))
    return out


def time_nudge(deadline, minutes, fired, now=None):
    """The wrap-up message once TIME_AT of the run's timeout has gone, as (key, text), or None. `deadline` is when
    the launcher stops the worker and `minutes` this launch's timeout."""
    if not deadline or not minutes or 'time' in fired:
        return None
    now = now or dt.datetime.now().astimezone()
    left = (deadline - now).total_seconds() / 60
    if left > (1 - TIME_AT) * minutes:
        return None
    left = max(1, int(round(left)))
    return ('time', 'You have used most of your time: you will be stopped in about %d minute%s. Finish the change in '
                    'hand, run the checks and write your report now: what is done, what is left, and what you found. '
                    'Start nothing new.' % (left, '' if left == 1 else 's'))


def watch(transcript, since, run_dir, step_budget=None, sig=None, deadline=None, minutes=None):
    """Queue the nudges now due and write progress.json. `sig` is signals() worked out already (ds_spend.py poll
    keeps them up to date from the new lines only); without it the whole transcript is read. With `deadline` and
    `minutes` (the run's timeout), the time nudge too."""
    run_dir = Path(run_dir)
    state_file = run_dir / 'steer.json'
    try:
        state = json.loads(state_file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {'nudges': []}
    fired = {n['key'] for n in state['nudges']}
    if sig is None:
        sig = signals(transcript, since)
    write_progress(run_dir, sig, since, step_budget)
    due = nudges(sig, fired, step_budget)
    # Once per launch, not per run: a run resumed after a timeout has a new deadline to be warned about.
    timed = any(n.get('key') == 'time' and since and n.get('at') and parse_time(n['at']) >= since for n in state['nudges'])
    late = time_nudge(deadline, minutes, {'time'} if timed else set())
    if late:
        due.append(late)
    if not due:
        return []
    now = dt.datetime.now().astimezone().isoformat(timespec='seconds')
    with open(run_dir / 'nudge.txt', 'a', encoding='utf-8') as fh:
        for key, text in due:
            fh.write(text + '\n')
    state['nudges'] += [dict(key=k, at=now, steps=sig['steps'], context=sig['context'], text=t) for k, t in due]
    state_file.write_text(json.dumps(state, indent=1), encoding='utf-8')
    return due


def write_progress(run_dir, sig, since, step_budget=None):
    """progress.json: how the running worker is going, for Claude and `progress`."""
    now = dt.datetime.now().astimezone()
    try:
        secs = int((now - since).total_seconds()) if since else None
        row = dict(updated=now.isoformat(timespec='seconds'), seconds=secs, steps=sig['steps'], tools=sig['tools'],
                   context=sig['context'], now=sig['now'], budget=step_budget)
        tmp = Path(run_dir) / 'progress.json.tmp'
        tmp.write_text(json.dumps(row), encoding='utf-8')
        os.replace(str(tmp), str(Path(run_dir) / 'progress.json'))
    except (OSError, TypeError):
        pass


def progress_lines(state_dir):
    """One line per running worker in this state dir, from its progress.json."""
    out = []
    for mf in sorted(Path(state_dir).glob('runs/*/manifest.json')):
        try:
            m = json.loads(mf.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            continue
        if m.get('state') != 'working' or m.get('provider') == 'claude':
            continue
        try:
            p = json.loads((mf.parent / 'progress.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            out.append('%s: starting' % m['run_id'])
            continue
        mins, secs = divmod(p.get('seconds') or 0, 60)
        over = ' (budget %d)' % p['budget'] if p.get('budget') else ''
        out.append('%s: %dm%02ds, %d steps%s, %d tool uses, context %dk, now: %s' % (
            m['run_id'], mins, secs, p.get('steps', 0), over, p.get('tools', 0), (p.get('context') or 0) // 1000,
            p.get('now') or '-'))
    return out


def deliver(run_dir):
    """Hand any queued nudge to the worker as added context (PostToolUse hook), then clear it."""
    f = Path(run_dir) / 'nudge.txt'
    try:
        text = f.read_text(encoding='utf-8').strip()
        f.unlink()
    except OSError:
        return None
    if not text:
        return None
    return json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse',
                                              'additionalContext': 'Note from the launcher: ' + text}})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    w = sub.add_parser('watch')
    w.add_argument('--transcript', required=True); w.add_argument('--since', required=True); w.add_argument('--run-dir', required=True)
    w.add_argument('--budget', type=int, help="the steps this worker's kind usually takes")
    w.add_argument('--deadline', help='when the launcher stops the worker (ISO time), for the time nudge')
    w.add_argument('--timeout-minutes', type=int, help="the run's whole timeout, for the time nudge")
    d = sub.add_parser('deliver')
    d.add_argument('--run-dir', required=True)
    b = sub.add_parser('budget')
    b.add_argument('--kind', required=True); b.add_argument('--spend-dir')
    p = sub.add_parser('progress')
    p.add_argument('--state-dir', help='default: this folder\'s state dir (tools/ds_state.py)')
    a = ap.parse_args(argv)
    if a.cmd == 'watch':
        for key, _ in watch(a.transcript, parse_time(a.since), a.run_dir, a.budget,
                            deadline=parse_time(a.deadline) if a.deadline else None, minutes=a.timeout_minutes):
            print(key)
        return 0
    if a.cmd == 'budget':
        print(budget(a.kind, a.spend_dir))
        return 0
    if a.cmd == 'progress':
        sd = a.state_dir
        if not sd:
            here = Path(__file__).resolve().parent
            sys.path[:0] = [str(here / 'tools'), str(here.parent / 'tools')]
            import ds_state
            sd = ds_state.state_dir(Path.cwd())
        print('\n'.join(progress_lines(sd)) or 'No workers running.')
        return 0
    out = deliver(a.run_dir)
    if out:
        sys.stdout.write(out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
