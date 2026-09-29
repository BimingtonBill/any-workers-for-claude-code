"""Standing audits: a checklist per area of the project, re-checked only where the code has changed,
with findings kept as numbered items until a fix closes them. Plus baselines to diff against.

Reviews used to start from a fresh brief every time. Here each area of the project (the converter's ESM
parser, the renderer, the launcher ...) has a standing checklist in <state dir>/memory/checklists/<area>.md:
the invariants a change there must keep, each with the test that guards it (or "unguarded"), and a security
section. An audit is a review worker that checks that list against the code, concentrating on what changed
since the area's last audit, and reports only findings it can confirm. Findings go into
memory/findings.jsonl with an ID (ESM-20260925-03) and stay open until an audit or Claude closes them;
new invariants an audit discovers are added to the checklist.

    python ds_audit.py init                     brief a digest worker to propose areas and first checklists
    python ds_audit.py status                   each area: paths, last audit, files changed since
    python ds_audit.py due                      brief an audit for every area whose code changed since its
                                                last audit (or never had one), and print the commands
    python ds_audit.py findings [--all] [--area A]   open findings (--all: closed ones too)
    python ds_audit.py close <id> [--note "..."]     mark a finding fixed
    python ds_audit.py save checklists <run id>      (the launcher does these two itself when
    python ds_audit.py save audit <area> <run id>     a digest-checklists or audit-<area> run ends)
    python ds_audit.py baseline record <name> -- <command ...>   keep a command's output as a baseline
    python ds_audit.py baseline check <name>    run it again and show what moved (exit 1 if anything did)

Run from the project folder. Nothing here calls DeepSeek; the printed commands do.
"""
import argparse
import datetime as dt
import difflib
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ds_memory  # noqa: E402
from ds_memory import git, runs_dir, report_body, launcher  # noqa: E402

SEVERITIES = ('high', 'medium', 'low')
SECURITY = ('untrusted input (game files, archives, network, command-line arguments) reaching parsers, '
            'allocation sizes or paths; anything that listens on a port or opens a debug channel; file writes '
            'outside the project or its output folders; unsafe blocks and FFI; panics or unbounded work on bad '
            'input; secrets in code or logs')


def mem(project):
    return ds_memory.folder(project)


def checklists_dir(project):
    return mem(project) / 'checklists'


def audits_meta(project):
    try:
        return json.loads((mem(project) / 'audits.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_audits_meta(project, m):
    ds_memory.write_json_atomic(mem(project) / 'audits.json', m)


# --- Checklists ---

def parse_checklist(text):
    """(area, [path globs], body) from a checklist file with a small front matter block."""
    area, paths, body = None, [], text
    m = re.match(r'---\s*\n(.*?)\n---\s*\n?(.*)', text, re.S)
    if m:
        body = m.group(2)
        for line in m.group(1).splitlines():
            k, _, v = line.partition(':')
            if k.strip() == 'area':
                area = v.strip()
            elif k.strip() == 'paths':
                paths = [p.strip() for p in v.split(',') if p.strip()]
    return area, paths, body.strip()


def areas(project):
    out = {}
    d = checklists_dir(project)
    for f in sorted(d.glob('*.md')) if d.is_dir() else []:
        area, paths, body = parse_checklist(f.read_text(encoding='utf-8', errors='replace'))
        out[area or f.stem] = dict(file=f, paths=paths, body=body)
    return out


def write_checklist(project, area, paths, body):
    d = checklists_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    f = d / ('%s.md' % area)
    f.write_text('---\narea: %s\npaths: %s\n---\n\n%s\n' % (area, ', '.join(paths), body.strip()), encoding='utf-8')
    return f


def slug(area):
    return re.sub(r'[^a-z0-9]+', '-', area.lower()).strip('-')


def changed_files(project, since, paths):
    """Files under the area's paths changed between `since` and HEAD; None when never audited."""
    if not since:
        return None
    # git() gives '' on any failure; a commit that is gone (rebased away, or a state dir copied to another
    # clone) must read as "audit again", not "nothing changed" (review-049).
    if subprocess.run(['git', '-C', str(project), 'cat-file', '-e', '%s^{commit}' % since],
                      capture_output=True).returncode != 0:
        return None
    names = git(project, 'diff', '--name-only', '%s..HEAD' % since).splitlines()
    return [n for n in names if any(fnmatch.fnmatch(n, p) for p in paths)]


# --- Findings ---

def _ledger_lines(project):
    f = mem(project) / 'findings.jsonl'
    return f.read_text(encoding='utf-8', errors='replace').splitlines() if f.is_file() else []


def ledger(project):
    rows = []
    for line in _ledger_lines(project):
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def write_ledger(project, rows):
    """Rewrite the ledger from `rows`, keeping any line that doesn't parse (a hand edit, a truncated write)
    at the end rather than deleting it, through a temp file of this process's own (review-049)."""
    import os
    f = mem(project) / 'findings.jsonl'
    kept = []
    for line in _ledger_lines(project):
        try:
            json.loads(line)
        except ValueError:
            if line.strip():
                kept.append(line)
    tmp = f.with_name('findings.%d.tmp' % os.getpid())
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:     # LF, like every .jsonl the harness writes
        fh.write(''.join(json.dumps(r) + '\n' for r in rows) + ''.join(x + '\n' for x in kept))
    tmp.replace(f)


class ledger_lock:
    """One read-modify-write of the ledger at a time: audits briefed together by `due` can end together."""
    def __init__(self, project):
        self.lock = mem(project) / 'findings.lock'
        self.fd = None

    def __enter__(self):
        import os, time
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(300):
            try:
                self.fd = os.open(str(self.lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.lock.stat().st_mtime > 60:   # left by a killed process
                        self.lock.unlink()
                except OSError:
                    pass
                time.sleep(0.1)
        return self

    def __exit__(self, *exc):
        import os
        if self.fd is not None:
            os.close(self.fd)
            try:
                self.lock.unlink()
            except OSError:
                pass


def open_findings(project, area=None):
    return [r for r in ledger(project) if r.get('status') == 'open' and (area is None or r.get('area') == area)]


def area_code(area, others=()):
    """A short code for IDs: the initials of a multi-word area (worker-launch-hook: WLH), else the name.
    When another area has the same initials (converter-parsers, converter-pipeline), two letters a word
    (COPA, COPI) keep them apart."""
    def initials(name, n):
        words = [w for w in re.split(r'[^A-Za-z0-9]+', name) if w]
        return (''.join(w[:n] for w in words) if len(words) > 1 else (words[0] if words else 'AREA')).upper()[:8]
    code = initials(area, 1)
    if any(o != area and initials(o, 1) == code for o in others):
        code = initials(area, 2)
    return code


def new_id(rows, area, others=()):
    prefix = '%s-%s-' % (area_code(area, others), dt.date.today().strftime('%Y%m%d'))
    n = 1 + max([int(r['id'].rsplit('-', 1)[1]) for r in rows if r['id'].startswith(prefix)] or [0])
    return '%s%02d' % (prefix, n)


def close(project, fid, note=None, by=None):
    with ledger_lock(project):
        rows = ledger(project)
        hit = [r for r in rows if r['id'] == fid]
        if not hit:
            raise SystemExit('no finding %s' % fid)
        hit[0].update(status='fixed', closed=dt.datetime.now().isoformat(timespec='seconds'), closed_by=by or 'Claude', note=note)
        write_ledger(project, rows)
    return hit[0]


# --- Briefs ---

def brief_init(project):
    d = mem(project)
    mp = d / 'map.md'
    pf = d / 'pitfalls.md'
    reviews = ds_memory.reviews_since(project, None)[-8:]
    parts = ['# Goal', 'Propose the areas of this project that deserve a standing audit, and write the first '
             'checklist for each: the invariants a change in that area must keep, so a reviewer can check them '
             'every time the code there changes.', '',
             '# Context',
             'The project map:' if mp.is_file() else 'There is no project map yet; read the layout yourself.',
             mp.read_text(encoding='utf-8') if mp.is_file() else '', '',
             'Mistakes found in reviews here before:' if pf.is_file() else '',
             pf.read_text(encoding='utf-8') if pf.is_file() else '', '',
             'Recent review reports (for what tends to break):', '']
    for ended, rid, rep in reviews:
        parts.append('### %s\n%s\n' % (rid, report_body(rep)[:1500]))
    parts += ['', '# Scope', 'Read-only. Read the code each checklist item names: every item must be anchored on real '
              'symbols (a function, type, constant or file:line) and say which test guards it, as the exact command '
              '(e.g. `cargo test -p converter esm::`), or "unguarded". Find the guarding tests with Grep.', '',
              '# Done when',
              '3 to 8 areas that together cover the code that matters most (not every folder). Each checklist has '
              '5 to 15 items under "## Invariants", 2 to 5 under "## Security" (for that area: %s), and optionally '
              '"## Known weak spots". No item you have not confirmed in the code.' % SECURITY, '',
              '# Report', 'Only the areas, each in exactly this shape (the tool splits your report on these lines):',
              '', '=== area: <short-name>', 'paths: <glob>, <glob>   (repo-relative, e.g. crates/converter/src/esm/**)',
              '<the checklist, as Markdown>', '', 'Paths are plain globs separated by commas: no {a,b} braces, no commas inside a path. '
              'Nothing else before the first "=== area:" line.']
    d.mkdir(parents=True, exist_ok=True)
    path = d / 'checklists-brief.md'
    path.write_text('\n'.join(parts) + '\n', encoding='utf-8')
    return path, 'digest-checklists'


def brief_audit(project, area):
    a = areas(project)[area]
    meta = audits_meta(project)
    last = (meta.get(area) or {}).get('commit')
    head = git(project, 'rev-parse', 'HEAD')
    known = open_findings(project, area)
    parts = ['# Goal', 'Audit the "%s" area of this project against its standing checklist, and report only what you '
             'can confirm in the code at HEAD.' % area, '',
             '# Context', 'Area paths: %s' % ', '.join(a['paths']), '', 'The checklist (%s):' % a['file'].name, '', a['body'], '']
    if known:
        parts += ['Findings already recorded for this area and still open. Do not report them again; if one is now '
                  'fixed, say so with a FIXED line:'] + ['- %s [%s] %s: %s' % (r['id'], r['severity'], r['where'], r['summary']) for r in known] + ['']
    if last:
        files = changed_files(project, last, a['paths'])
        parts += ['Last audited at commit %s. Changed in this area since then (%d files):' % (last[:7], len(files)),
                  '\n'.join(files[:80]) or '(none)', '',
                  'Commits touching it:', git(project, 'log', '--oneline', '-40', '%s..HEAD' % last, '--', *a['paths']) or '(none)', '',
                  'Concentrate on what changed, but a checklist item the changes could have broken is in scope wherever it lives.']
    else:
        parts += ['This is the first audit of this area: check the whole checklist.']
    parts += ['', '# Scope', 'Read-only. Rules:',
              '- Anchor every finding on a symbol and a file:line you have read, and confirm it with Grep before reporting it.',
              '- Drop anything you cannot confirm. No speculation, no style comments, no "consider refactoring".',
              '- Always check the security dimension for this area: %s.' % SECURITY, '',
              '# Done when', 'Every checklist item is marked ok, broken or unchecked (with why), every open finding above is '
              'rechecked, and each finding is confirmed in the code.', '',
              '# Report', 'Exactly these sections:', '',
              '## Checklist', 'One line per item: `ok|broken|unchecked - <item> - <evidence file:line>`', '',
              '## Findings', 'One per line, exactly: `FINDING | high|medium|low | <path>:<line> | <what is wrong and which invariant it breaks>`',
              '(no lines if there are none)', '',
              '## Fixed', 'One per line: `FIXED | <finding id> | <evidence>` for open findings above that are now fixed.', '',
              '## Checklist additions', 'Bullets: invariants worth checking next time that the checklist lacks, each anchored and with its guarding test or "unguarded".']
    d = mem(project)
    path = d / ('audit-%s-brief.md' % slug(area))
    path.write_text('\n'.join(parts) + '\n', encoding='utf-8')
    meta.setdefault(area, {})['pending'] = head
    save_audits_meta(project, meta)
    return path, 'audit-%s' % slug(area)


def command(project, path, label, kind='review'):
    return ('powershell -NoProfile -ExecutionPolicy Bypass -File "%s" -Kind %s -Mode read -Effort high -Dir "%s" '
            '-Label %s -TaskFile "%s"' % (launcher(), kind, Path(project).resolve(), label, path))


def running(project, label):
    return ds_memory.running(project, label)


def due(project):
    out = []
    meta = audits_meta(project)
    for area, a in areas(project).items():
        if running(project, 'audit-%s' % slug(area)):
            continue
        last = (meta.get(area) or {}).get('commit')
        files = changed_files(project, last, a['paths'])
        if files is None or files:
            out.append((area, files))
    return out


# --- Saving what workers report ---

def save_checklists(project, run_id, replace=False):
    body = report_body(runs_dir(project) / run_id / 'report.md')
    blocks = re.split(r'(?m)^=== area:\s*', body)[1:]
    if not blocks:
        raise SystemExit('no "=== area:" sections in %s; nothing saved' % run_id)
    written, kept = [], []
    have = areas(project)
    for block in blocks:
        first, _, rest = block.partition('\n')
        area = slug(first.strip())
        m = re.match(r'\s*paths:\s*(.+)\n', rest)
        paths = [p.strip().strip('`') for p in m.group(1).split(',') if p.strip()] if m else []
        text = rest[m.end():] if m else rest
        if not paths or len(text.strip()) < 100:
            continue
        if area in have and not replace:
            kept.append(area)
            continue
        write_checklist(project, area, paths, text)
        written.append(area)
    return written, kept


def parse_audit(text):
    findings, fixed, additions = [], [], []
    section = None
    for line in text.splitlines():
        if line.startswith('## '):
            section = line[3:].strip().lower()
            continue
        m = re.match(r'\s*`?FINDING\s*\|\s*(high|medium|low)\s*\|\s*([^|]+?)\s*\|\s*(.+?)`?\s*$', line, re.I)
        if m:
            findings.append(dict(severity=m.group(1).lower(), where=m.group(2).strip('` '), summary=m.group(3).strip()))
            continue
        m = re.match(r'\s*`?FIXED\s*\|\s*([A-Z0-9]+-\d{8}-\d+)\s*(?:\|\s*(.+?))?`?\s*$', line)
        if m:
            fixed.append((m.group(1), (m.group(2) or '').strip()))
            continue
        if section and section.startswith('checklist additions') and line.strip().startswith(('- ', '* ')):
            additions.append(line.strip()[2:].strip())
    return findings, fixed, additions


def save_audit(project, area, run_id):
    have = areas(project)
    if area not in have:
        match = [a for a in have if slug(a) == area]
        if match:
            area = match[0]
        # No checklist by that name any more (renamed or deleted): still file what the audit found, rather
        # than lose a finished audit (review-049); only the checklist additions have nowhere to go.
    rep = runs_dir(project) / run_id / 'report.md'
    text = report_body(rep)
    if '## Checklist' not in text and 'FINDING' not in text:
        raise SystemExit('%s does not look like an audit report; nothing saved' % run_id)
    findings, fixed, additions = parse_audit(text)
    with ledger_lock(project):
        return _file_audit(project, area, run_id, have, findings, fixed, additions)


def _file_audit(project, area, run_id, have, findings, fixed, additions):
    rows = ledger(project)
    now = dt.datetime.now().isoformat(timespec='seconds')
    added = []
    for f in findings:
        if any(r['area'] == area and r['status'] == 'open' and r['where'] == f['where'] and r['summary'] == f['summary'] for r in rows):
            continue
        f.update(id=new_id(rows, area, list(have)), area=area, status='open', found=now, run_id=run_id)
        rows.append(f)
        added.append(f['id'])
    closed = []
    for fid, evidence in fixed:
        for r in rows:
            if r['id'] == fid and r['status'] == 'open':
                r.update(status='fixed', closed=now, closed_by=run_id, note=evidence)
                closed.append(fid)
    mem(project).mkdir(parents=True, exist_ok=True)
    write_ledger(project, rows)
    if additions and area in have:
        f = have[area]['file']
        body = f.read_text(encoding='utf-8').rstrip()
        if '## Added by audits' not in body:
            body += '\n\n## Added by audits'
        body += '\n' + '\n'.join('- %s (%s)' % (x, run_id) for x in additions)
        f.write_text(body + '\n', encoding='utf-8')
    meta = audits_meta(project)
    entry = meta.setdefault(area, {})
    entry.update(commit=entry.pop('pending', None) or git(project, 'rev-parse', 'HEAD'), at=now, run_id=run_id)
    save_audits_meta(project, meta)
    return added, closed, len(additions)


# --- Baselines ---

def baseline_dir(project):
    return mem(project) / 'baselines'


def baseline_record(project, name, cmd):
    out = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=str(project), shell=False)
    d = baseline_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    (d / ('%s.txt' % name)).write_text(out.stdout, encoding='utf-8')
    (d / ('%s.json' % name)).write_text(json.dumps(dict(cmd=cmd, commit=git(project, 'rev-parse', 'HEAD'),
                                                        at=dt.datetime.now().isoformat(timespec='seconds'), exit=out.returncode)), encoding='utf-8')
    return len(out.stdout.splitlines()), out.returncode


def baseline_check(project, name):
    d = baseline_dir(project)
    meta = json.loads((d / ('%s.json' % name)).read_text(encoding='utf-8'))
    old = (d / ('%s.txt' % name)).read_text(encoding='utf-8').splitlines()
    new = subprocess.run(meta['cmd'], capture_output=True, text=True, encoding='utf-8', errors='replace',
                         cwd=str(project)).stdout.splitlines()
    diff = list(difflib.unified_diff(old, new, 'baseline (%s, %s)' % (meta['commit'][:7], meta['at'][:10]), 'now', lineterm='', n=1))
    return diff, meta


# --- Status lines for the morning report ---

def status_lines(project):
    have = areas(project)
    if not have:
        return ['audits: no standing checklists yet (`ds_audit.py init` proposes them)']
    todo = due(project)
    opened = open_findings(project)
    high = sum(1 for r in opened if r.get('severity') == 'high')
    return ['audits: %d area(s), %d due for an audit%s; %d open finding(s)%s' % (
        len(have), len(todo), ' (%s)' % ', '.join(a for a, _ in todo) if todo else '', len(opened),
        ', %d high' % high if high else '')]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--project', default='.')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('init')
    sub.add_parser('status')
    sub.add_parser('due')
    f = sub.add_parser('findings'); f.add_argument('--all', action='store_true'); f.add_argument('--area')
    c = sub.add_parser('close'); c.add_argument('id'); c.add_argument('--note')
    s = sub.add_parser('save'); s.add_argument('what', choices=('checklists', 'audit')); s.add_argument('rest', nargs='+')
    s.add_argument('--replace', action='store_true', help='checklists: overwrite areas that already have one')
    b = sub.add_parser('baseline'); b.add_argument('action', choices=('record', 'check')); b.add_argument('name')
    b.add_argument('command', nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    project = Path(a.project).resolve()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    if a.cmd == 'init':
        path, label = brief_init(project)
        print('wrote %s\nrun it in the background (panel: "DeepSeek digest #<nnn>: audit checklists"); the launcher saves '
              'the checklists when it ends:\n  %s' % (path, command(project, path, label, 'digest')))
    elif a.cmd == 'status':
        meta = audits_meta(project)
        for area, x in areas(project).items():
            last = (meta.get(area) or {}).get('commit')
            files = changed_files(project, last, x['paths'])
            print('%-20s %-50s %s' % (area, ', '.join(x['paths'])[:50],
                                      'never audited' if files is None else 'audited at %s, %d file(s) changed since' % (last[:7], len(files))))
        print('\n'.join(status_lines(project)))
    elif a.cmd == 'due':
        todo = due(project)
        if not todo:
            print('no audits due' if areas(project) else 'no standing checklists yet: run `ds_audit.py init` first')
        for area, files in todo:
            path, label = brief_audit(project, area)
            print('%s: %s. Start it in the background (panel: "DeepSeek review #<nnn>: audit %s"); the launcher saves its findings:\n  %s'
                  % (area, 'never audited' if files is None else '%d file(s) changed since its last audit' % len(files), area, command(project, path, label)))
    elif a.cmd == 'findings':
        rows = [r for r in ledger(project) if (a.all or r['status'] == 'open') and (not a.area or r['area'] == a.area)]
        for r in sorted(rows, key=lambda r: (SEVERITIES.index(r['severity']) if r['severity'] in SEVERITIES else 9, r['id'])):
            print('%-20s %-6s %-6s %s: %s' % (r['id'], r['status'], r['severity'], r['where'], r['summary']))
        if not rows:
            print('no findings')
    elif a.cmd == 'close':
        r = close(project, a.id, a.note)
        print('closed %s: %s' % (r['id'], r['summary']))
    elif a.cmd == 'save' and a.what == 'checklists':
        written, kept = save_checklists(project, a.rest[0], a.replace)
        print('saved checklists: %s%s' % (', '.join(written) or 'none', '; kept existing: %s' % ', '.join(kept) if kept else ''))
    elif a.cmd == 'save':
        if len(a.rest) != 2:
            ap.error('save audit <area> <run id>')
        added, closed, adds = save_audit(project, a.rest[0], a.rest[1])
        print('audit of %s saved: %d new finding(s)%s, %d closed, %d checklist addition(s)' % (
            a.rest[0], len(added), ' (%s)' % ', '.join(added) if added else '', len(closed), adds))
    elif a.action == 'record':
        cmd = a.command[1:] if a.command[:1] == ['--'] else a.command
        if not cmd:
            ap.error('baseline record <name> -- <command ...>')
        lines, code = baseline_record(project, a.name, cmd)
        print('baseline %s: %d line(s) kept (exit %d)' % (a.name, lines, code))
    else:
        diff, meta = baseline_check(project, a.name)
        if not diff:
            print('baseline %s: nothing moved since %s' % (a.name, meta['commit'][:7]))
            return 0
        print('\n'.join(diff[:200]))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
