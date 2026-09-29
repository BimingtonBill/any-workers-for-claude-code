"""Project memory for workers: a map of the project, and pitfalls learned from reviews.

Every worker used to start by exploring the project from scratch, and re-reading context is 84% of
DeepSeek spend. Two short files, kept in <state dir>/memory/, go into every worker's instructions
instead (the launcher adds them):

  map.md       where things are: layout, entry points, key types, how to build and test, traps.
               Written by a digest worker; refreshed when the code has moved on.
  pitfalls.md  mistakes coders have made in this project before, as rules. Written by a digest worker
               from review reports, plus lines Claude adds with `note`. Given to coders, leads and
               reviewers.

    python ds_memory.py status                 how old each is, and whether a refresh is due
    python ds_memory.py brief map              write the brief for a map (new, or an update of the old
                                               one) and print the command that runs it
    python ds_memory.py brief pitfalls         the same for pitfalls, from reviews since the last update
    python ds_memory.py due                    brief whatever is due and print the commands that run it
                                               (what Claude runs at the start of a session)
    python ds_memory.py save map <run id>      keep that worker's report as the map (the launcher does
    python ds_memory.py save pitfalls <run id> this itself when a digest-map or digest-pitfalls run ends)
    python ds_memory.py note "<rule>"          add a pitfall now (Claude's own, from a review it did)

Run from the project folder. Nothing here calls DeepSeek; the printed command does.
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ds_state  # noqa: E402
from ds_state import state_dir as _state_dir  # noqa: E402

_dirs = {}


def state_dir(project):
    """The launcher's state-dir rule, asked once per project (it runs PowerShell) and per DS_STATE_DIR."""
    key = (str(Path(project).resolve()), os.environ.get('DS_STATE_DIR'))
    if key not in _dirs:
        _dirs[key] = _state_dir(project)
    return _dirs[key]

MAP_WORDS = 1500
MAX_PITFALLS = 12
STALE_COMMITS = 40
STALE_DAYS = 14
SKIP_DIRS = {'.git', 'target', 'node_modules', 'local', '__pycache__', '.venv', 'venv', 'dist', 'build', '.claude'}
SOURCE = {'.rs', '.py', '.ps1', '.ts', '.tsx', '.js', '.cs', '.cpp', '.c', '.h', '.hpp', '.go', '.java', '.lua', '.wgsl', '.glsl', '.hlsl'}


def git(project, *args):
    try:
        return subprocess.run(['git', '-C', str(project)] + list(args), capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def folder(project):
    d = Path(state_dir(project)) / 'memory'
    return d


def meta(d):
    try:
        return json.loads((d / 'memory.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


try:        # launcher/ds_common.py: in the harness beside tools/'s parent, installed in the skill folder
    ds_state.use_launcher()
    from ds_common import write_json_atomic  # noqa: E402
except (ImportError, AttributeError):     # copied alone beside an older launcher: a plain temp file and replace
    def write_json_atomic(path, data, indent=2):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name('%s.%d.tmp' % (path.name, os.getpid()))
        tmp.write_text(json.dumps(data, indent=indent) + '\n', encoding='utf-8')
        os.replace(str(tmp), str(path))


def save_meta(d, m):
    write_json_atomic(d / 'memory.json', m)


def launcher():
    here = Path(__file__).resolve().parent
    for p in (here.parent / 'launcher' / 'ds-agent.ps1', here.parent / 'ds-agent.ps1',
              Path.home() / '.claude' / 'skills' / 'deepseek-agents' / 'ds-agent.ps1'):
        if p.is_file():
            return p
    return 'ds-agent.ps1'


def runs_dir(project):
    return Path(state_dir(project)) / 'runs'


# --- The free part of a map: what the files themselves say ---

def skeleton(project):
    project = Path(project)
    tops, big = [], []
    for top in sorted(project.iterdir()):
        if top.name in SKIP_DIRS or top.name.startswith('.') and top.is_dir():
            continue
        if top.is_file():
            continue
        count = 0
        for root, dirs, files in os.walk(top):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for f in files:
                count += 1
                p = Path(root) / f
                if p.suffix in SOURCE:
                    try:
                        with open(p, encoding='utf-8', errors='replace') as fh:
                            big.append((sum(1 for _ in fh), str(p.relative_to(project)).replace('\\', '/')))
                    except OSError:
                        pass
        tops.append('%s/ (%d files)' % (top.name, count))
    big.sort(reverse=True)
    lines = ['Top-level folders: ' + ', '.join(tops) + '.',
             'Largest source files (lines): ' + ', '.join('%s %d' % (p, n) for n, p in big[:25]) + '.']
    cargo = project / 'Cargo.toml'
    if cargo.is_file():
        m = re.search(r'members\s*=\s*\[([^\]]*)\]', cargo.read_text(encoding='utf-8', errors='replace'), re.S)
        if m:
            lines.append('Cargo workspace members: ' + ', '.join(re.findall(r'"([^"]+)"', m.group(1))) + '.')
    pkg = project / 'package.json'
    if pkg.is_file():
        try:
            lines.append('npm scripts: ' + ', '.join(json.loads(pkg.read_text(encoding='utf-8')).get('scripts', {})) + '.')
        except ValueError:
            pass
    for doc in ('README.md', 'AGENTS.md', 'CONTEXT.md'):
        p = project / doc
        if p.is_file():
            heads = [h.strip('# ').strip() for h in p.read_text(encoding='utf-8', errors='replace').splitlines() if h.startswith('## ')]
            if heads:
                lines.append('%s sections: %s.' % (doc, '; '.join(heads[:20])))
    return '\n'.join('- ' + x for x in lines)


# --- Status ---

def status(project):
    d = folder(project)
    m = meta(d)
    out = {}
    mp = m.get('map') or {}
    if (d / 'map.md').is_file() and mp.get('commit'):
        behind = git(project, 'rev-list', '--count', '%s..HEAD' % mp['commit']) or '?'
        days = (dt.datetime.now() - dt.datetime.fromisoformat(mp['saved'])).days if mp.get('saved') else None
        stale = (behind.isdigit() and int(behind) >= STALE_COMMITS) or (days is not None and days >= STALE_DAYS)
        out['map'] = dict(commit=mp['commit'][:7], behind=behind, days=days, stale=stale)
    else:
        out['map'] = None
    pf = m.get('pitfalls') or {}
    count = len([x for x in (d / 'pitfalls.md').read_text(encoding='utf-8').splitlines() if x.startswith('- ')]) \
        if (d / 'pitfalls.md').is_file() else 0
    new_reviews = reviews_since(project, pf.get('since'))
    out['pitfalls'] = dict(count=count, since=pf.get('since'), new_reviews=len(new_reviews))
    return out


def running(project, label):
    """Whether a run of this label is under way, so a second session doesn't start the same digest."""
    rd = runs_dir(project)
    for mf in rd.glob('%s.*/manifest.json' % label) if rd.is_dir() else []:
        try:
            if json.loads(mf.read_text(encoding='utf-8-sig')).get('state') in ('submitted', 'working'):
                return True
        except ValueError:
            continue
    return False


def due(project):
    """What to refresh now: 'map' when there is none or it is stale, 'pitfalls' when enough reviews have
    come in (5 since the last update, or 3 when there is no list yet). Skips one already running."""
    s = status(project)
    out = []
    if (s['map'] is None or s['map']['stale']) and not running(project, 'digest-map'):
        out.append('map')
    p = s['pitfalls']
    if (p['new_reviews'] >= 5 or (p['count'] == 0 and p['new_reviews'] >= 3)) and not running(project, 'digest-pitfalls'):
        out.append('pitfalls')
    return out


def status_lines(project):
    s = status(project)
    lines = []
    if s['map'] is None:
        lines.append('map: none yet. Workers explore from scratch; `ds_memory.py brief map` writes one (a digest worker, a few cents).')
    else:
        m = s['map']
        lines.append('map: from %s, %s commits and %s days ago%s' % (
            m['commit'], m['behind'], m['days'], '; due for a refresh (`ds_memory.py brief map`)' if m['stale'] else ''))
    p = s['pitfalls']
    lines.append('pitfalls: %d%s; %d review report(s) since%s' % (
        p['count'], ' (last update %s)' % p['since'][:10] if p['since'] else '', p['new_reviews'],
        ' - enough for an update (`ds_memory.py brief pitfalls`)' if p['new_reviews'] >= 5 else ''))
    return lines


# --- Reviews ---

def reviews_since(project, since):
    out = []
    rd = runs_dir(project)
    if not rd.is_dir():
        return out
    for mf in rd.glob('*/manifest.json'):
        try:
            m = json.loads(mf.read_text(encoding='utf-8-sig'))
        except ValueError:
            continue
        if m.get('kind') not in ('review', 'critic') or m.get('state') != 'completed' or not m.get('ended'):
            continue
        if since and m['ended'] <= since:
            continue
        rep = Path(m.get('report') or '')
        if rep.is_file():
            out.append((m['ended'], m['run_id'], rep))
    return sorted(out)


def report_body(path):
    text = Path(path).read_text(encoding='utf-8', errors='replace')
    lines = [x for x in text.splitlines() if not x.startswith('<!-- ') and not x.startswith('[ds-agent] run=')]
    body = '\n'.join(lines).strip()
    # The standard report opening (Status / Summary / Left / Next) is for the lead, not the saved map or pitfalls:
    # kept, it rode into every worker brief (an Project A session, 2026-09-28).
    env = _envelope()
    parsed = env.parse(body) if env else None
    if parsed and parsed.get('head') and parsed['head'] in body:
        body = body.replace(parsed['head'], '', 1).strip()
    return body


def _envelope():
    """launcher/ds_envelope.py: beside tools/ once installed, in launcher/ in the harness."""
    here = Path(__file__).resolve().parent
    for folder_ in (here.parent / 'launcher', here.parent):
        if (folder_ / 'ds_envelope.py').is_file():
            sys.path.insert(0, str(folder_))
            try:
                import ds_envelope
                return ds_envelope
            except ImportError:
                return None
    return None


# --- Briefs ---

def brief_map(project):
    d = folder(project)
    m = meta(d)
    head = git(project, 'rev-parse', 'HEAD')
    old = (d / 'map.md').read_text(encoding='utf-8') if (d / 'map.md').is_file() else None
    parts = ['# Goal', 'Write a map of this project that a worker can read in two minutes before starting a task, so it '
             'knows where things are without exploring. Every worker will get this map with its brief.', '',
             '# Context', 'What the files themselves say (counted by a script, so trust the numbers):', skeleton(project)]
    if old and (m.get('map') or {}).get('commit'):
        since = m['map']['commit']
        parts += ['', 'This is an update. The current map (from commit %s):' % since[:7], '', old, '',
                  'Commits since then:', git(project, 'log', '--oneline', '-60', '%s..HEAD' % since) or '(none)', '',
                  'Files changed since then:', '\n'.join(git(project, 'diff', '--stat=100', since, 'HEAD').splitlines()[-60:]) or '(none)',
                  '', 'Keep what is still true, fix what changed, drop what no longer exists.']
    parts += ['', '# Scope', 'Read-only. Read entry points, module roots and key types as needed: do not read every file, '
              'and skip local/, target/, node_modules and generated data.', '',
              '# Done when', 'The map covers, in at most %d words:' % MAP_WORDS,
              '- what the project is, in two lines;',
              '- the layout: each top-level folder, crate or package in one line, saying what lives there;',
              '- where the main flows start: entry points, main loops and the key types, with file:line;',
              '- how to build and test: the exact commands, and which are slow;',
              '- conventions a change must follow (registration, naming, config, docs);',
              '- traps: things that look one way but are another.',
              'Every file:line you cite exists at HEAD.', '',
              '# Report', 'The map itself, as Markdown, and nothing else: your report is saved as the map.']
    d.mkdir(parents=True, exist_ok=True)
    path = d / 'map-brief.md'
    path.write_text('\n'.join(parts) + '\n', encoding='utf-8')
    m['map_pending'] = head
    save_meta(d, m)
    return path, 'digest-map'


def brief_pitfalls(project):
    d = folder(project)
    m = meta(d)
    since = (m.get('pitfalls') or {}).get('since')
    reviews = reviews_since(project, since)
    old = (d / 'pitfalls.md').read_text(encoding='utf-8') if (d / 'pitfalls.md').is_file() else '(none yet)'
    budget, blocks = 40000, []
    for ended, rid, rep in reversed(reviews):   # newest first, until the budget is used
        body = report_body(rep)[:2500]
        if budget - len(body) < 0:
            break
        budget -= len(body)
        blocks.append('### %s (%s)\n%s' % (rid, ended[:10], body))
    pilot = Path(state_dir(project)) / 'pilot.csv'
    failed = []
    if pilot.is_file():
        for line in pilot.read_text(encoding='utf-8', errors='replace').splitlines()[1:]:
            cells = line.split(',')
            if len(cells) > 9 and cells[9] and cells[9] not in ('pending-review', 'accepted', 'integrated', 'held') and (not since or cells[0] > since[:19]):
                failed.append('%s: %s %s' % (cells[1], cells[9], ','.join(cells[12:]).strip()))
    parts = ['# Goal', 'Turn what reviews found in this project into a short list of pitfalls: rules every coder should know '
             'before starting, so the same mistakes stop coming back.', '',
             '# Context', 'The current pitfalls:', '', old, '',
             'Review reports since %s (%d, newest first):' % (since[:10] if since else 'the start', len(reviews)), ''] + blocks
    if failed:
        parts += ['', 'Coder tasks that were not accepted (task: outcome notes):'] + failed[-40:]
    parts += ['', '# Scope', 'Read-only. You may open cited files to check that a finding still applies to the code.', '',
              '# Done when', 'At most %d pitfalls. Each is a rule a coder can follow ("register a new system in X", '
              'not "be careful"), in one or two lines, with one example run id. Recurring mistakes come first; a one-off '
              'goes in only if it was costly. Keep existing pitfalls that still hold and drop ones the code no longer has.' % MAX_PITFALLS, '',
              '# Report', 'The list itself, as Markdown bullets starting "- ", and nothing else: your report is saved as the pitfalls list.']
    d.mkdir(parents=True, exist_ok=True)
    path = d / 'pitfalls-brief.md'
    path.write_text('\n'.join(parts) + '\n', encoding='utf-8')
    m['pitfalls_pending'] = reviews[-1][0] if reviews else since
    save_meta(d, m)
    return path, 'digest-pitfalls', len(reviews)


def command(project, path, label):
    return ('powershell -NoProfile -ExecutionPolicy Bypass -File "%s" -Kind digest -Mode read -Effort high -Dir "%s" '
            '-Label %s -TaskFile "%s"' % (launcher(), Path(project).resolve(), label, path))


def save(project, which, run_id):
    d = folder(project)
    m = meta(d)
    rep = runs_dir(project) / run_id / 'report.md'
    if not rep.is_file():
        raise SystemExit('no report at %s' % rep)
    body = report_body(rep)
    if len(body) < 200:
        raise SystemExit('that report is too short to be a %s (%d characters); not saved' % (which, len(body)))
    now = dt.datetime.now().isoformat(timespec='seconds')
    d.mkdir(parents=True, exist_ok=True)
    if which == 'map':
        (d / 'map.md').write_text(body + '\n', encoding='utf-8')
        m['map'] = dict(commit=m.pop('map_pending', None) or git(project, 'rev-parse', 'HEAD'), saved=now, run_id=run_id)
    else:
        notes = [x for x in ((d / 'pitfalls.md').read_text(encoding='utf-8').splitlines() if (d / 'pitfalls.md').is_file() else [])
                 if '(Claude, ' in x]
        kept = [x for x in notes if x not in body]
        (d / 'pitfalls.md').write_text(body + ('\n' + '\n'.join(kept) if kept else '') + '\n', encoding='utf-8')
        m['pitfalls'] = dict(since=m.pop('pitfalls_pending', None) or now, saved=now, run_id=run_id)
    save_meta(d, m)
    return d / ('%s.md' % which)


def note(project, text):
    d = folder(project)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / 'pitfalls.md', 'a', encoding='utf-8') as fh:
        fh.write('- %s (Claude, %s)\n' % (text.strip().rstrip('.'), dt.date.today().isoformat()))
    return d / 'pitfalls.md'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--project', default='.')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status')
    sub.add_parser('due')
    b = sub.add_parser('brief'); b.add_argument('which', choices=('map', 'pitfalls'))
    s = sub.add_parser('save'); s.add_argument('which', choices=('map', 'pitfalls')); s.add_argument('run_id')
    n = sub.add_parser('note'); n.add_argument('text')
    a = ap.parse_args(argv)
    project = Path(a.project).resolve()
    if a.cmd == 'status':
        print('\n'.join(status_lines(project)))
    elif a.cmd == 'due':
        todo = due(project)
        if not todo:
            print('project memory is up to date')
        for which in todo:
            if which == 'map':
                path, label = brief_map(project)
            else:
                path, label, _ = brief_pitfalls(project)
            print('%s is due. Start it now, in the background (panel: "DeepSeek digest #<nnn>: %s"); the launcher saves it when it ends:\n  %s'
                  % (which, 'project map' if which == 'map' else 'pitfalls', command(project, path, label)))
    elif a.cmd == 'brief' and a.which == 'map':
        path, label = brief_map(project)
        print('wrote %s\nrun it in the background (panel: "DeepSeek digest #<nnn>: project map"); the launcher saves it when it ends:\n  %s'
              % (path, command(project, path, label)))
    elif a.cmd == 'brief':
        path, label, count = brief_pitfalls(project)
        print('wrote %s from %d review report(s)\nrun it in the background (panel: "DeepSeek digest #<nnn>: pitfalls"); the launcher saves it when it ends:\n  %s'
              % (path, count, command(project, path, label)))
    elif a.cmd == 'save':
        print('saved %s' % save(project, a.which, a.run_id))
    else:
        print('added to %s' % note(project, a.text))
    return 0


if __name__ == '__main__':
    sys.exit(main())
