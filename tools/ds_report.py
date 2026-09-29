"""Which kinds of delegated work pay for themselves? Summarise the delegation records.

  python tools/ds_report.py                 # local/agents/runs.csv and pilot.csv
  python tools/ds_report.py --json          # the same summary, for another tool
  python tools/ds_report.py --runs R --pilot P

runs.csv    started,label,session,status,turns,tokens_in,tokens_out,seconds,denied
pilot.csv   started,task,type,base,status,scope_ok,checks_passed,checks_total,seconds,
            outcome,reviewer,lead_minutes,notes

Delegating is worth it only if it reduces the lead's total work, so the report answers one
question per task type: how much came back accepted without correction, what the worker cost
in minutes and tokens, and what the lead then spent on it. Lead effort that was never
recorded is unknown, and the report says so instead of counting it as zero - a total that
quietly assumed zero would make delegation look free. A value projected from a partial record
is marked with a leading '~'.
"""
import os
import argparse
import csv
import json
import math
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from ds_state import project_root  # noqa: E402  (beside this file, in the harness and installed)
except ImportError:     # this tool copied alone into a project's tools/: DS_PROJECT or the folder above tools/
    def project_root(tools_dir):
        return Path(os.environ.get('DS_PROJECT') or Path(tools_dir).resolve().parent).resolve()


def _project_root():
    """The project these tools act on (ds_state.project_root)."""
    return project_root(Path(__file__).resolve().parent)


ROOT = _project_root()
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ds_state  # noqa: E402  (beside this file, in the harness and in the installed skill)


def _state_dir(root=ROOT, env=None):
    """Where the launcher keeps runs.csv for this project, by the one copy of the rule
    (tools/ds_state.py, and behind it launcher/ds-state.ps1). A caller that passes env wants the answer for
    that environment rather than this process's, which only the tests do."""
    if env is not None:
        return ds_state.fallback_dir(root, env)
    return ds_state.state_dir(root)


DEFAULT_RUNS = _state_dir() / 'runs.csv'
DEFAULT_PILOT = _state_dir() / 'pilot.csv'

UNTYPED = '(untyped)'

# ds_impl.ps1 writes 'pending-review' or 'failed'; the lead later records what the review
# cost. Only these spellings are classified - an outcome nobody recognises is reported as it
# stands rather than guessed at, because a guess would move the acceptance rate.
OUTCOME_KINDS = {
    'accepted': ('accepted', 'accept', 'accepted-clean', 'accepted-no-correction',
                 'accepted-without-correction'),
    'corrected': ('corrected', 'revised', 'accept-with-correction', 'accepted-with-correction',
                  'accepted-after-correction'),
    'rejected': ('rejected', 'reject', 'failed', 'fail', 'failed-checks'),
    'pending': ('pending', 'pending-review', 'unreviewed', 'awaiting-review', 'in-review',
                'not-reviewed'),
}
TRUE_WORDS = ('true', '1', 'yes', 'y')
FALSE_WORDS = ('false', '0', 'no', 'n')
COUNTS = ('accepted', 'corrected', 'rejected', 'pending', 'other')


def read_csv(path):
    """The rows of a record file as dicts with stripped keys.

    A missing, empty or header-only file is an empty list, not an error: the records grow as
    workers run, so the first report of a session may have almost nothing to read. Every row
    comes back with the same keys, short rows padded, so a reader never has to guard a lookup.

    The header row is required, and both records always have one because the runner writes
    them: without it the first row would be read as the column names and every later row would
    lose its fields to lookups that find nothing. Nothing here guesses at a missing header.

    Fields past the header are dropped, so an unquoted comma in the last column (the free-text
    `notes`) truncates that field instead of shifting the ones before it, which are read by name.
    """
    path = Path(path)
    if not path.is_file():
        return []
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        columns = [(name, name.strip()) for name in (reader.fieldnames or []) if name.strip()]
        rows = []
        for raw in reader:
            row = {clean: (raw.get(name) or '').strip() for name, clean in columns}
            if any(row.values()):
                rows.append(row)
        return rows


def _text(row, key):
    return (row.get(key) or '').strip()


def _number(row, key):
    """A numeric field as a float, or None when it is blank or not a number."""
    text = _text(row, key)
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    # 'nan' and 'inf' parse as floats and would poison every sum they reach.
    return value if math.isfinite(value) else None


def _flag(row, key):
    """A yes/no field. PowerShell writes True/False; a hand-edit may write 1/0.

    None means the value is missing or unrecognised - unknown, which is not the same as
    False and must not be counted as a clean scope check.
    """
    text = _text(row, key).lower()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    return None


def _normalise(value):
    text = (value or '').strip().lower()
    for separator in ('_', ' ', '/'):
        text = text.replace(separator, '-')
    while '--' in text:
        text = text.replace('--', '-')
    return text


def _outcome_kind(value):
    """accepted/corrected/rejected/pending, 'blank' when nothing was written, else 'other'."""
    text = _normalise(value)
    if not text:
        return 'blank'
    for kind, words in OUTCOME_KINDS.items():
        if text in words:
            return kind
    return 'other'


def _task_name(row, index):
    return _text(row, 'task') or _text(row, 'label') or 'row %d' % (index + 1)


def missing_outcomes(pilot):
    """The tasks whose outcome or reviewer is still blank, in the order they were recorded.

    A blank reviewer counts too: the run recorded itself as `pending-review`, so the review
    has not happened yet. These are the entries the record needs before it can say whether
    the task paid for itself.
    """
    missing = []
    for index, row in enumerate(pilot or []):
        if not _text(row, 'outcome') or not _text(row, 'reviewer'):
            name = _task_name(row, index)
            if name not in missing:
                missing.append(name)
    return missing


def _attribute(runs, pilot):
    """Match runs to pilot rows by label == task, each run at most once.

    The implementation runner launches every worker with -Label <task name>, so the label is
    the join key. Two rows for the same task (a re-run) would both match the same runs; the
    first row claims them, so no run is counted twice.
    """
    by_label = {}
    for run in runs:
        by_label.setdefault(_text(run, 'label').casefold(), []).append(run)
    claimed = set()
    entries = []
    for index, row in enumerate(pilot):
        name = _task_name(row, index)
        matched = []
        for run in by_label.get(name.casefold(), []):
            if id(run) in claimed:
                continue
            claimed.add(id(run))
            matched.append(run)
        entries.append((row, matched))
    unattributed = [run for run in runs if id(run) not in claimed]
    return entries, unattributed


def _group(name, entries, extra_runs=()):
    """One task type, or the whole record. `entries` are (pilot row, matched runs) pairs."""
    tasks = len(entries)
    matched_runs = [run for _, runs in entries for run in runs]
    runs = list(extra_runs) + matched_runs

    counts = dict.fromkeys(COUNTS, 0)
    outcomes = {}
    open_tasks = []
    blank_outcomes = 0
    scope_failures = scope_unrecorded = 0
    checks_passed = checks_total = checks_unrecorded = 0
    worker_seconds = worker_tasks = 0
    lead = 0.0
    lead_tasks = 0
    tasks_without_runs = 0

    for index, (row, row_runs) in enumerate(entries):
        value = _text(row, 'outcome')
        kind = _outcome_kind(value)
        if kind == 'blank':
            # Nobody wrote an outcome yet. That is not "unclassified" - the value is missing,
            # not unrecognised - and it is not a rejection either, so it is its own count.
            blank_outcomes += 1
        else:
            counts[kind] += 1
            outcomes[value] = outcomes.get(value, 0) + 1
        if kind in ('blank', 'pending'):
            open_tasks.append(_task_name(row, index))

        scope = _flag(row, 'scope_ok')
        if scope is False:
            scope_failures += 1
        elif scope is None:
            scope_unrecorded += 1

        passed = _number(row, 'checks_passed')
        total = _number(row, 'checks_total')
        if passed is not None:
            checks_passed += int(passed)
        if total is not None:
            checks_total += int(total)
        else:
            checks_unrecorded += 1

        seconds = _number(row, 'seconds')
        if seconds is not None:
            worker_seconds += seconds
            worker_tasks += 1

        minutes = _number(row, 'lead_minutes')
        if minutes is not None:
            lead += minutes
            lead_tasks += 1

        if not row_runs:
            tasks_without_runs += 1

    tokens_in = tokens_out = 0
    tokens_unrecorded = 0
    run_minutes = 0.0
    run_turns = 0
    denied = 0.0
    denied_runs = 0
    denied_tools = {}
    statuses = {}
    for run in runs:
        in_tokens = _number(run, 'tokens_in')
        out_tokens = _number(run, 'tokens_out')
        if in_tokens is None and out_tokens is None:
            tokens_unrecorded += 1
        else:
            tokens_in += int(in_tokens or 0)
            tokens_out += int(out_tokens or 0)
        seconds = _number(run, 'seconds')
        if seconds is not None:
            run_minutes += seconds / 60.0
        turns = _number(run, 'turns')
        if turns is not None:
            run_turns += int(turns)
        # The launcher writes the names of the tools a run was denied ("Bash Glob"); older logs wrote a
        # count. Count names as one denied tool each, and tally which tools they were.
        number = _number(run, 'denied')
        if number is not None:
            denied += number
            if number:
                denied_runs += 1
        elif _text(run, 'denied'):
            names = _text(run, 'denied').replace(',', ' ').split()
            denied += len(names)
            denied_runs += 1
            for tool in names:
                denied_tools[tool] = denied_tools.get(tool, 0) + 1
        status = _text(run, 'status') or '(unrecorded)'
        statuses[status] = statuses.get(status, 0) + 1

    decided = counts['accepted'] + counts['corrected'] + counts['rejected']
    rate = counts['accepted'] / decided if decided else None
    lead_per_task = lead / lead_tasks if lead_tasks else None
    # A projection exists only where the record is incomplete; where it is complete the sum
    # above is already the answer, and printing both would invite reading one as a measurement.
    projected = lead_per_task * tasks if lead_per_task is not None and lead_tasks < tasks else None
    return {
        'type': name,
        'tasks': tasks,
        'decided': decided,
        'accepted': counts['accepted'],
        'corrected': counts['corrected'],
        'rejected': counts['rejected'],
        'pending': counts['pending'],
        'outcome_unrecorded': blank_outcomes,
        'unclassified': counts['other'],
        'acceptance_rate': rate,
        'outcomes': outcomes,
        'open_tasks': open_tasks,
        'scope_failures': scope_failures,
        'scope_unrecorded': scope_unrecorded,
        'checks_passed': checks_passed,
        'checks_total': checks_total,
        'checks_unrecorded': checks_unrecorded,
        'worker_minutes': worker_seconds / 60.0 if worker_tasks else None,
        'worker_minutes_tasks': worker_tasks,
        'worker_minutes_unrecorded': tasks - worker_tasks,
        'lead_minutes': lead if lead_tasks else None,
        'lead_minutes_tasks': lead_tasks,
        'lead_minutes_unrecorded': tasks - lead_tasks,
        'lead_minutes_per_task': lead_per_task,
        'lead_minutes_projected': projected,
        'tokens_in': tokens_in,
        'tokens_out': tokens_out,
        'tokens': tokens_in + tokens_out,
        'tokens_unrecorded_runs': tokens_unrecorded,
        'tasks_without_runs': tasks_without_runs,
        'runs': len(runs),
        'unattributed_runs': len(list(extra_runs)),
        'run_minutes': run_minutes,
        'run_turns': run_turns,
        'denied': denied,
        'denied_runs': denied_runs,
        'denied_tools': denied_tools,
        'statuses': statuses,
    }


def summarise(runs, pilot):
    """The delegation records as one summary: per task type, then the whole record.

    Runs are matched to tasks by `label == task`. A run whose label matches no task (the
    research workers have no pilot row) still costs money, so it stays in the totals and is
    reported separately as unattributed.
    """
    runs = list(runs or [])
    pilot = list(pilot or [])
    entries, unattributed = _attribute(runs, pilot)

    # `type` is written by hand, so its spelling drifts ('Research', 'research '). Grouping is
    # on the normalised key, the same treatment an outcome gets: one logical type must not
    # split into two blocks and halve the numbers it is being judged by.
    by_type = {}
    for row, matched in entries:
        kind = _normalise(_text(row, 'type')) or UNTYPED
        by_type.setdefault(kind, []).append((row, matched))

    types = {name: _group(name, rows) for name, rows in by_type.items()}
    overall = _group('all', entries, extra_runs=unattributed)

    notes = []
    if unattributed:
        notes.append('%d run(s) match no task: their minutes and tokens are in the total,'
                     ' their outcome is not measured' % len(unattributed))
    if overall['tasks_without_runs']:
        notes.append('%d task(s) have no run record: their tokens are unknown'
                     % overall['tasks_without_runs'])
    if overall['tokens_unrecorded_runs']:
        notes.append('%d run(s) record no tokens: unknown, not zero'
                     % overall['tokens_unrecorded_runs'])
    if overall['scope_unrecorded']:
        notes.append('%d task(s) have no scope check recorded: unknown, not a pass'
                     % overall['scope_unrecorded'])
    if overall['lead_minutes_unrecorded']:
        notes.append('lead effort is recorded for %d of %d task(s): the other %d are unknown,'
                     ' never counted as zero' % (overall['lead_minutes_tasks'], overall['tasks'],
                                                 overall['lead_minutes_unrecorded']))
    return {
        'tasks': len(pilot),
        'runs': len(runs),
        'types': types,
        'overall': overall,
        'unattributed': _group('unattributed', [], extra_runs=unattributed),
        'missing_outcomes': missing_outcomes(pilot),
        'notes': notes,
    }


def _minutes(value):
    return '%.1f min' % value


def _tokens(value):
    if value >= 1e6:
        return '%.1fM' % (value / 1e6)
    if value >= 1e5:
        return '%.0fk' % (value / 1e3)
    if value >= 1e3:
        return '%.1fk' % (value / 1e3)
    return '%d' % value


def _rate(group):
    if group['acceptance_rate'] is None:
        return 'acceptance n/a (nothing decided yet)'
    return 'acceptance %d/%d = %d%%' % (group['accepted'], group['decided'],
                                         round(100 * group['acceptance_rate']))


def _counts(group):
    return ('accepted %d, corrected %d, rejected %d, pending %d, no outcome %d, unclassified %d'
            % (group['accepted'], group['corrected'], group['rejected'], group['pending'],
               group['outcome_unrecorded'], group['unclassified']))


def _worker_line(group):
    if group['worker_minutes'] is None:
        return ('  worker    unknown: no time recorded for any of %d task(s)'
                % group['tasks'])
    return '  worker    %s over %d of %d task(s)' % (_minutes(group['worker_minutes']),
                                                     group['worker_minutes_tasks'],
                                                     group['tasks'])


def _lead_line(group):
    """The lead's own effort. Never printed as a bare number when part of it is unrecorded."""
    if group['lead_minutes'] is None:
        return ('  lead      unknown: no lead minutes recorded for any of %d task(s)'
                % group['tasks'])
    text = '  lead      %s recorded for %d of %d task(s)' % (_minutes(group['lead_minutes']),
                                                             group['lead_minutes_tasks'],
                                                             group['tasks'])
    if group['lead_minutes_unrecorded']:
        text += '; %d unknown' % group['lead_minutes_unrecorded']
    if group['lead_minutes_projected'] is not None:
        text += ('; ~%s projected for all %d'
                 % (_minutes(group['lead_minutes_projected']), group['tasks']))
    return text


def _block(name, group):
    lines = ['%-16s %d task(s), %d run(s)' % (name, group['tasks'], group['runs'])]
    lines.append('  outcome   ' + _counts(group) + ' - ' + _rate(group))
    if group['checks_total']:
        checks = '%d/%d passed' % (group['checks_passed'], group['checks_total'])
    elif group['checks_passed']:
        checks = '%d passed (no total recorded)' % group['checks_passed']
    else:
        checks = 'not recorded'
    checks += ', scope failures %d, scope not recorded %d' % (group['scope_failures'],
                                                              group['scope_unrecorded'])
    if group['checks_unrecorded']:
        checks += ', checks not recorded for %d task(s)' % group['checks_unrecorded']
    lines.append('  checks    ' + checks)
    lines.append(_worker_line(group))
    lines.append(_lead_line(group))
    if group['denied_tools']:
        tally = ', '.join('%s %d' % kv for kv in sorted(group['denied_tools'].items(), key=lambda kv: (-kv[1], kv[0])))
        denied = '%d with denied tools (%s)' % (group['denied_runs'], tally)
    else:
        denied = '%d denied' % int(group['denied'])
    text = '%d run(s), %d turns, %s, %s in + %s out tokens' % (
        group['runs'], group['run_turns'], denied,
        _tokens(group['tokens_in']), _tokens(group['tokens_out']))
    if group['tasks_without_runs']:
        text += ' (%d task(s) with no run record)' % group['tasks_without_runs']
    lines.append('  runs      ' + text)
    return lines


def format_report(summary):
    """A compact text report: one block per task type, then the total."""
    if not summary.get('tasks') and not summary.get('runs'):
        return 'delegation report: nothing recorded yet.'
    overall = summary['overall']
    head = 'delegation report: %d task(s), %d run(s)' % (summary['tasks'], summary['runs'])
    if overall['unattributed_runs']:
        head += ' (%d with no task record)' % overall['unattributed_runs']
    lines = [head, '']
    for name in sorted(summary['types']):
        lines += _block(name, summary['types'][name])
    lines += _block('TOTAL', overall)
    lines.append('  note      acceptance is accepted / (accepted + corrected + rejected); a')
    lines.append('            task still awaiting review is in neither, and corrected is not')
    lines.append('            accepted.')
    lines.append('  note      unrecorded lead effort is unknown, not zero; a "~" marks a'
                 ' projection from')
    lines.append('            the tasks that do have a record, not a measurement.')
    for note in summary.get('notes', []):
        lines.append('  note      ' + note)
    if summary.get('missing_outcomes'):
        lines.append('  todo      outcome or reviewer still blank: %s'
                     % ', '.join(summary['missing_outcomes']))
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--runs', default=str(DEFAULT_RUNS), help='worker run record')
    parser.add_argument('--pilot', default=str(DEFAULT_PILOT), help='pilot task record')
    parser.add_argument('--json', action='store_true', help='print the summary as JSON')
    args = parser.parse_args(argv)

    runs_path, pilot_path = Path(args.runs), Path(args.pilot)
    if not runs_path.is_file() and not pilot_path.is_file():
        print('no delegation records yet: %s and %s are both missing' % (runs_path, pilot_path))
        return 0
    summary = summarise(read_csv(runs_path), read_csv(pilot_path))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(format_report(summary))
    return 0


if __name__ == '__main__':
    sys.exit(main())
