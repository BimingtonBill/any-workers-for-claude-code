"""Show DeepSeek worker runs as a readable manifest: what each run was for, who launched it, how it ended.

    python tools/ds_manifest.py                      table of runs in <project>/local/agents/manifest.jsonl
    python tools/ds_manifest.py --tree               the same runs as a hierarchy (Claude -> leads -> workers)
    python tools/ds_manifest.py --write              also write local/agents/MANIFEST.md
    python tools/ds_manifest.py --legacy runs.csv    read an old runs.csv (t01, i03, review-t67 ...) instead

The manifest is written by launcher/ds-agent.ps1: one JSON line per event ("start", "end"),
the last line for a run id being its current state. See docs/design/manifest.md.
"""
import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

try:                                    # imported as tools.ds_manifest
    from tools import ds_state
except ImportError:                     # run as a script from tools/
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ds_state

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

KINDS = {
    'research': 'finds and writes down facts; changes no code',
    'impl': 'implements a bounded change in files it owns',
    'review': 'checks another run\'s work independently',
    'analysis': 'analyses the results of a test or acceptance run',
    'critic': 'judges screenshots or output against a reference',
    'advisor': 'read-only consultant that answers one question',
    'digest': 'merges and verifies several other runs\' output',
    'lead': 'DeepSeek lead: splits a job and runs its own workers',
    'selftest': 'checks the worker harness itself',
    'probe': 'experiment on the harness (this project)',
    'task': 'unclassified',
}

# Labels used before the manifest existed, in the Project B and Project A projects.
LEGACY = [
    (r'^t\d+', 'research'), (r'^i\d+', 'impl'), (r'^c\d+', 'digest'),
    (r'^review-', 'review'), (r'^fix-', 'impl'), (r'^analyst', 'analysis'),
    (r'^crit', 'critic'), (r'^(deep|report)-\d+', 'advisor'), (r'^(selftest|test)', 'selftest'),
]


ROLE_TITLES = {
    'critic': 'Screenshot critique of round ',
    'analysis': 'Analysis of test round ',
    'advisor': 'Advisor report ',
    'selftest': 'Harness self-test ',
}


def kind_of(label):
    """The kind for a label: a new-style prefix (research-..) or a legacy one (t12, i03, review-..)."""
    low = label.lower()
    for kind in KINDS:
        if low == kind or low.startswith(kind + '-'):
            return kind
    for pattern, kind in LEGACY:
        if re.match(pattern, low):
            return kind
    return 'task'


def subject_of(label):
    """For a review/fix/followup label, the task it is about: review-i07 -> i07."""
    m = re.match(r'^(?:review|fix)-(.+)$', label) or re.match(r'^(.+?)-(?:followup|resume\d*)$', label)
    return m.group(1) if m else None


def default_manifest(root=None, env=None):
    """The manifest the launcher writes for this project: the state dir the one rule picks - DS_STATE_DIR,
    the project's stateDir, local/agents when the project has local/, else ~/.claude-deepseek/agents; see
    ds_state, and launcher/ds-state.ps1 behind it - plus manifest.jsonl."""
    root = Path(root or ROOT)
    # A caller that passes env (the tests) is asking for that environment's answer, not this process's.
    state = ds_state.fallback_dir(root, env) if env is not None else ds_state.state_dir(root)
    return state / 'manifest.jsonl'


def load_runs(path):
    """Latest state per run id, in start order."""
    runs = {}
    order = []
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):   # valid JSON but not a record (audit finding MAK-20260924-02)
                continue
            rid = row.get('run_id')
            if not rid:
                continue
            if rid not in runs:
                order.append(rid)
            runs[rid] = row
    return [runs[r] for r in order]


def brief_title(text):
    """The first line under a '# Goal' heading, else the first heading."""
    m = re.search(r'^#+\s*Goal\s*\n\s*(\S[^\n]*)', text, re.M)
    if not m:
        m = re.search(r'^#+\s*(\S[^\n]*)', text, re.M)
    title = m.group(1).strip() if m else ''
    return title if len(title) <= 140 else title[:137] + '...'


def find_brief(label, folder):
    """The brief a legacy label came from: exact name, else the same number prefix (t74-move-order -> t74-*.md)."""
    if not folder:
        return None
    folder = Path(folder)
    if re.search(r'[\\/:]|\.\.', label):   # a path, not a label (audit finding MAK-20260924-01)
        return None
    base = re.sub(r'^(?:review|fix)-', '', label)
    base = re.sub(r'-(?:followup|resume\d*)$', '', base)
    exact = folder / (base + '.md')
    if exact.exists():
        return exact
    m = re.match(r'^([a-z]\d+)', base)
    if m:
        hits = sorted(folder.glob(m.group(1) + '-*.md')) + sorted(folder.glob(m.group(1) + '.md'))
        words = set(base.split('-')[1:])
        hits.sort(key=lambda h: -len(words & set(h.stem.split('-'))))
        return hits[0] if hits else None
    return None


def load_legacy(path, briefs=None):
    """Rows of an old runs.csv, shaped like manifest records (no lineage, attempts counted per label)."""
    seen = {}
    out = []
    with open(path, encoding='utf-8', newline='') as handle:
        for row in csv.DictReader(handle):
            label = row.get('label') or 'task'
            seen[label] = seen.get(label, 0) + 1
            status = row.get('status') or ''
            brief = find_brief(label, briefs)
            title = brief_title(brief.read_text(encoding='utf-8', errors='replace')) if brief else ''
            if brief and label.startswith('review-'):
                title = 'Review of: ' + title
            elif brief and label.startswith('fix-'):
                title = 'Fixes after review: ' + title
            elif not brief and kind_of(label) in ROLE_TITLES:
                # Role briefs (screenshot-critic.md, analyst.md ...) were aimed with -Extra; the label names the target.
                title = ROLE_TITLES[kind_of(label)] + re.sub(r'^[a-z]+-?', '', label)
            out.append({
                'run_id': '%s.%d' % (label, seen[label]), 'task_id': label, 'attempt': seen[label],
                'kind': kind_of(label), 'title': title, 'parent_run_id': None, 'depth': 1,
                'state': 'completed' if status == 'ok' else ('failed' if status else 'unknown'),
                'started': row.get('started'), 'seconds': _int(row.get('seconds')),
                'turns': _int(row.get('turns')), 'tokens_in': _int(row.get('tokens_in')),
                'tokens_out': _int(row.get('tokens_out')),
                'denied': (row.get('denied') or '').split(), 'subject': subject_of(label),
            })
    return out


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def describe(run):
    """One line an outsider can read: '[impl] i07-rumble.2 (retry) - Title  <- parent'."""
    bits = ['[%s%s]' % ('claude ' if run.get('provider') == 'claude' else '', run.get('kind', 'task')), run['run_id']]
    if (run.get('attempt') or 1) > 1:
        bits.append('(attempt %d)' % run['attempt'])
    title = run.get('title') or ''
    if title:
        bits.append('- ' + title)
    subject = run.get('subject') or subject_of(run.get('task_id') or '')
    if subject:
        bits.append('(about %s)' % subject)
    return ' '.join(bits)


def fmt_tokens(n):
    if n is None:
        return ''
    return '%.0fk' % (n / 1000.0) if n >= 1000 else str(n)


def table(runs):
    lines = ['| Run | Kind | What it is for | Launched by | State | Turns | Tokens in/out | Time |',
             '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for r in runs:
        secs = r.get('seconds')
        lines.append('| `%s` | %s | %s | %s | %s | %s | %s | %s |' % (
            r['run_id'], r.get('kind', ''), (r.get('title') or '').replace('|', '/'),
            r.get('parent_run_id') or 'Claude', r.get('state', ''), r.get('turns') or '',
            '%s / %s' % (fmt_tokens(r.get('tokens_in')), fmt_tokens(r.get('tokens_out'))),
            '%dm%02ds' % divmod(secs, 60) if secs is not None else ''))
    return '\n'.join(lines)


def tree(runs):
    children = {}
    for r in runs:
        children.setdefault(r.get('parent_run_id') or None, []).append(r)
    lines = ['Claude (lead)']
    shown = set()

    def walk(parent, prefix):
        kids = [r for r in children.get(parent, []) if r['run_id'] not in shown]
        for i, r in enumerate(kids):
            shown.add(r['run_id'])
            last = i == len(kids) - 1
            lines.append('%s%s %s  [%s%s]' % (prefix, '`--' if last else '|--', describe(r), r.get('state', ''),
                                              ', %s turns' % r['turns'] if r.get('turns') else ''))
            walk(r['run_id'], prefix + ('    ' if last else '|   '))
    walk(None, '')
    # Runs whose parent is not in this manifest (e.g. launched from another project's state dir).
    known = {r['run_id'] for r in runs}
    for parent in children:
        if parent and parent not in known:
            lines.append('? %s (not in this manifest)' % parent)
            walk(parent, '    ')
    # Whatever no walk reached, such as runs whose parents point at each other (audit finding
    # MAK-20260924-03), is listed rather than dropped.
    lost = [r for r in runs if r['run_id'] not in shown]
    if lost:
        lines.append('? runs not reachable from Claude (a parent loop):')
        for r in lost:
            lines.append('    %s  [%s]' % (describe(r), r.get('state', '')))
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--manifest', help='manifest.jsonl (default: <project>/local/agents/manifest.jsonl)')
    parser.add_argument('--legacy', help='an old runs.csv to show in manifest terms')
    parser.add_argument('--briefs', help='with --legacy: the folder of briefs, to fill in titles')
    parser.add_argument('--tree', action='store_true', help='show the launch hierarchy')
    parser.add_argument('--write', action='store_true', help='write MANIFEST.md next to the manifest')
    parser.add_argument('--last', type=int, default=0, help='only the last N runs')
    args = parser.parse_args(argv)

    if args.legacy:
        runs = load_legacy(args.legacy, args.briefs)
        where = Path(args.legacy)
    else:
        where = Path(args.manifest) if args.manifest else default_manifest()
        if not where.exists():
            print('no manifest at %s' % where)
            return 1
        runs = load_runs(where)
    if args.last:
        runs = runs[-args.last:]

    body = tree(runs) if args.tree else table(runs)
    print(body)
    if args.write:
        out = where.parent / 'MANIFEST.md'
        legend = '\n'.join('- **%s**: %s' % (k, v) for k, v in KINDS.items())
        text = ('# Worker runs\n\nGenerated by `tools/ds_manifest.py` from `%s`. Do not edit.\n\n'
                '## Hierarchy\n\n```\n%s\n```\n\n## Runs\n\n%s\n\n## Kinds\n\n%s\n'
                % (where.name, tree(runs), table(runs), legend))
        out.write_text(text, encoding='utf-8')
        print('\nwrote %s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
