"""The DeepSeek worker hook, all but its fast path: ds_hook.py beside this file reads the event, answers the events
that need nothing, and passes the rest to main() here. See ds_hook.py for what the hook does.

Kept out of ds_hook.py so that Python compiles it once into __pycache__ instead of on every tool call (8.5 ms of a
40 ms event, 2026-09-29). Settings.json still runs ds_hook.py.
"""
import os
import sys
import time

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:     # loaded by its path, as a test does
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
# The fast path's helpers and settings, one copy, in ds_hook.py.
from ds_hook import NOTE_EVERY, RECHECK, SCRIPTS, _due_marker, _field, _spend_dir, quiet  # noqa: E402,F401

import json  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402


def _adapter_dir():
    """The tools folder holding ds_state.py, the Python door to the launcher's state-dir rule. In the
    harness this hook is skill/deepseek-agents/ds_hook.py, with tools/ two folders up; installed as part
    of the skill it sits beside tools/ instead."""
    here = Path(__file__).resolve().parent
    for folder in (here.parents[1] / 'tools', here / 'tools'):
        if (folder / 'ds_state.py').is_file():
            return folder
    return None


_ADAPTER = _adapter_dir()
if _ADAPTER is not None:
    sys.path.insert(0, str(_ADAPTER))


def _ds_state():
    """tools/ds_state.py, imported when first needed (it brings subprocess); None when missing or broken, since
    a broken adapter must not stop the hook: the tool call goes through."""
    if _ADAPTER is None:
        return None
    try:
        import ds_state
        return ds_state
    except ImportError:
        return None


WATCH = Path(__file__).resolve().parent / 'ds-watch.ps1'
CODING_KINDS = ('impl',)  # Claude subagents that write code (they run on Sonnet 5.5 like the rest, since 2026-09-29)
COMPACT_AT = 0.7         # suggest /compact once a session's context is this full (the user's call, 2026-09-27)
TAIL = 2000000           # bytes of a session transcript read for its recent task numbers


def worker_state(cwd):
    """The project's state dir when the project runs workers (the dir has runs/ and belongs to this
    project), else None: elsewhere Agent calls are left alone."""
    ds_state = _ds_state()
    if ds_state is None or not cwd:
        return None
    try:
        # The rule in Python: state_dir() starts PowerShell (1.7 s idle), and under a cargo build that passed
        # the hook's 10 s limit, so Project A's impl #185 was never recorded as finished (2026-09-25).
        sd = Path(ds_state.fallback_dir(Path(cwd))).resolve()
        root = Path(cwd).resolve()
    except Exception:
        return None
    ours = root in sd.parents or (root / '.deepseek-agents.json').is_file()
    return sd if ours and (sd / 'runs').is_dir() else None


def next_number(transcript, sd):
    """The next task number: after the highest #nnn among the last RECENT entries this session gave a
    DeepSeek worker or Claude subagent (each session keeps its own sequence; only recent ones, so one odd
    number long ago, like a hook test's #999, doesn't set it), else after the highest in the project's runs.
    Only the transcript's last TAIL bytes are read: the whole of a 464 MB one took 2.9 s of the hook's 10 s."""
    try:
        with open(transcript, 'rb') as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - TAIL))
            text = fh.read().decode('utf-8', errors='replace')
        seen = [int(n) for n in _numbered().findall(text)][-RECENT:]
    except (OSError, TypeError, ValueError):
        seen = []
    if not seen:
        seen = [int(m.group(1)) for m in (re.match(r'^[a-z]+-(\d{3})-', p.name) for p in (sd / 'runs').iterdir()) if m]
    return '%03d' % ((max(seen) if seen else 0) + 1)


def coding_model(kind, asked):
    """Sonnet for a Claude subagent that names no model, coders included. The user's call, 2026-09-29: Claude
    Sonnet 5.5 (Intelligence Index 56 against Opus 5.5's 58 and DeepSeek V4.1 Flash's 40; Terminal-Bench 4.0
    64% against Opus's 60%) takes over Claude's reading, review and design helpers and coding. It replaced
    Opus for coders, which had been set on 2026-09-25 after impl #185 on Sonnet 5 spent 48 minutes and 172
    turns on a renderer bug. A caller that names a model keeps it. The user's rule (2026-09-29, from the Project B session, where a hard reverse-engineering job
    had been sent to Opus): Sonnet 5.5 unless Sonnet has already failed at the task; Opus only after that, or when the
    user asks. None
    means leave the model alone."""
    if asked:
        return None
    return 'sonnet'


def agent_pre(event):
    """Refuse an Agent call in a worker project unless its panel description is in the house format."""
    sd = worker_state(event.get('cwd'))
    tool_input = event.get('tool_input') or {}
    desc = str(tool_input.get('description') or '').strip()
    if sd is None:
        return
    named = CLAUDE_PANEL.match(desc)
    if named:
        # The same report opening as DeepSeek workers, so both kinds of report read the same way.
        env = _launcher_module('ds_envelope')
        prompt = str(tool_input.get('prompt') or '')
        updated = dict(tool_input)
        if env and 'Status: done | partial | blocked' not in prompt:
            updated['prompt'] = prompt.rstrip() + '\n\n' + env.note(named.group(1))
        model = coding_model(named.group(1), tool_input.get('model'))
        if model:
            updated['model'] = model
        if updated != tool_input:
            print(json.dumps({'hookSpecificOutput': {
                'hookEventName': 'PreToolUse',
                'permissionDecision': 'allow',
                'updatedInput': updated,
            }}))
        return
    ref = TASK_REF.search(desc)
    if ref:      # "Build field notes (impl-183)": Claude taking over a DeepSeek task keeps its number
        kind, number = ref.group(1), ref.group(2)
        what = re.sub(r'\s*[(\[]?\s*%s\s*[)\]]?\s*' % re.escape(ref.group(0)), ' ', desc).strip(' :-') or 'what it does'
    else:
        kind = {'Explore': 'research', 'Plan': 'analysis'}.get(str(tool_input.get('subagent_type')), '<kind>')
        number, what = next_number(event.get('transcript_path'), sd), desc or 'what it does'
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': (
            'In this project Claude subagents are named like the workers, in one numbered sequence, so the '
            'panel reads as one team: "Claude %s #%s: %s" (kind: %s). A task taken over from a worker '
            'keeps its number. Run the same call again with that description.' % (kind, number, what, ', '.join(KINDS))),
    }}))


def _slug(text):
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:40].strip('-') or 'task'


def _transcript_totals(path):
    """(tokens in, tokens out, turns) from a subagent transcript, each API message once."""
    tin = tout = 0
    seen = set()
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    msg = json.loads(line).get('message') or {}
                except ValueError:
                    continue
                us, mid = msg.get('usage'), msg.get('id')
                if not isinstance(us, dict) or mid in seen:
                    continue
                seen.add(mid)
                tin += sum(us.get(k) or 0 for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'))
                tout += us.get('output_tokens') or 0
    except (OSError, TypeError):
        return None, None, None
    return tin, tout, len(seen)


def _write_run(sd, m, event_name):
    import datetime as dt
    folder = sd / 'runs' / m['run_id']
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / 'manifest.json.tmp'
    tmp.write_text(json.dumps(m, indent=2), encoding='utf-8')
    os.replace(str(tmp), str(folder / 'manifest.json'))
    line = dict(event=event_name, at=dt.datetime.now().strftime('%Y-%m-%dT%H:%M:%S'), **m)
    with open(sd / 'manifest.jsonl', 'a', encoding='utf-8', newline='\n') as fh:     # LF, as ds-agent.ps1 writes it
        fh.write(json.dumps(line) + '\n')


def _finish(sd, m, report, transcript):
    import datetime as dt
    now = dt.datetime.now()
    m.update(state='completed', ended=now.strftime('%Y-%m-%dT%H:%M:%S'),
             seconds=int((now - dt.datetime.fromisoformat(m['started'])).total_seconds()), transcript=transcript)
    tin, tout, turns = _transcript_totals(transcript)
    if turns:
        m.update(tokens_in=tin, tokens_out=tout, turns=turns)
    env = _launcher_module('ds_envelope')
    parsed = env.parse(report or '') if env else None
    for f in ('status', 'verdict', 'summary', 'left', 'next'):
        if parsed and parsed.get(f):
            m['report_' + f] = parsed[f]
    m['report'] = str(sd / 'runs' / m['run_id'] / 'report.md')
    Path(m['report']).parent.mkdir(parents=True, exist_ok=True)
    Path(m['report']).write_text(report or '', encoding='utf-8')
    _write_run(sd, m, 'end')


def _index(sd, agent_id=None, run_id=None):
    """Agent id -> run id, in <state dir>/claude-agents.json; with run_id, add that entry."""
    path = sd / 'claude-agents.json'
    try:
        idx = json.loads(path.read_text(encoding='utf-8'))
        idx = idx if isinstance(idx, dict) else {}
    except (OSError, ValueError):
        idx = {}
    if run_id:
        idx[agent_id] = run_id
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_text(json.dumps(idx, indent=1), encoding='utf-8')
        os.replace(str(tmp), str(path))
    return idx


def agent_post(event):
    """Record a Claude subagent run; a foreground one has already finished."""
    import datetime as dt
    sd = worker_state(event.get('cwd'))
    tool_input = event.get('tool_input') or {}
    resp = event.get('tool_response') if isinstance(event.get('tool_response'), dict) else {}
    m = CLAUDE_PANEL.match(str(tool_input.get('description') or '').strip())
    if sd is None or not m or not resp.get('agentId'):
        return
    kind, number, what = m.group(1), m.group(2), m.group(3).strip()
    task_id = '%s-%s-claude-%s' % (kind, number, _slug(re.sub(r'\((retry|resume) \d+\)', '', what)))
    attempt = 1 + len(list((sd / 'runs').glob(task_id + '.*')))
    run_id = '%s.%d' % (task_id, attempt)
    started = dt.datetime.now() - dt.timedelta(milliseconds=int(resp.get('totalDurationMs') or 0))
    run = dict(schema='ds-run/1', provider='claude', run_id=run_id, task_id=task_id, attempt=attempt, kind=kind,
               title=what, project=Path(event.get('cwd')).name, dir=str(event.get('cwd')), parent_run_id=None,
               root_run_id=run_id, lineage='claude/' + run_id, depth=1, state='working',
               model=resp.get('resolvedModel') or tool_input.get('model'), agent_id=resp['agentId'],
               agent_type=resp.get('agentType') or tool_input.get('subagent_type'),
               background=resp.get('status') == 'async_launched', session_id=event.get('session_id'),
               started=started.strftime('%Y-%m-%dT%H:%M:%S'), ended=None, seconds=None, turns=None,
               tokens_in=None, tokens_out=None)
    _write_run(sd, run, 'start')
    _index(sd, resp['agentId'], run_id)
    if resp.get('status') == 'completed':
        text = '\n'.join(b.get('text', '') for b in resp.get('content') or [] if isinstance(b, dict))
        base = Path(str(event.get('transcript_path') or ''))
        _finish(sd, run, text, str(base.with_suffix('') / 'subagents' / ('agent-%s.jsonl' % resp['agentId'])))


def agent_stop(event):
    """A background subagent recorded by agent_post has stopped: finish its run."""
    # A subagent started with isolation "worktree" stops in <project>/.claude/worktrees/agent-<id>, which has no
    # state dir: every Project A coding subagent on 2026-09-25 afternoon stayed "working" for this reason.
    sd = worker_state(re.sub(r'[\\/]\.claude[\\/]worktrees[\\/].*$', '', str(event.get('cwd') or '')))
    if sd is None or not event.get('agent_id'):
        return
    run_id = _index(sd).get(event['agent_id'])
    path = sd / 'runs' / str(run_id) / 'manifest.json'
    if not run_id or not path.is_file():
        return          # not ours, or a foreground one: agent_post finishes those
    try:
        run = json.loads(path.read_text(encoding='utf-8'))
    except ValueError:
        return
    _finish(sd, run, event.get('last_assistant_message') or '', event.get('agent_transcript_path'))


def _launcher_module(name):
    """A launcher module (ds_claude, ds_envelope, ds_providers): beside this file once installed, in launcher/ in the
    harness; None when missing."""
    here = Path(__file__).resolve().parent
    for folder in (here, here.parents[1] / 'launcher'):
        if (folder / (name + '.py')).is_file():
            if str(folder) not in sys.path:
                sys.path.insert(0, str(folder))
            return __import__(name)
    return None


def _last_turn(transcript):
    """(context tokens, model) of the main thread's last turn, from the transcript's tail ((0, '') when unreadable)."""
    try:
        with open(transcript, 'rb') as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 400000))
            tail = fh.read().decode('utf-8', errors='replace').splitlines()
    except (OSError, TypeError):
        return 0, ''
    for line in reversed(tail):
        if '"usage"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = (e.get('message') or {}) if isinstance(e, dict) else {}
        us = msg.get('usage')
        if isinstance(us, dict) and not e.get('isSidechain'):
            return (sum(us.get(k) or 0 for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')),
                    str(msg.get('model') or ''))
    return 0, ''


def context_tokens(transcript):
    """The main thread's context at its last turn, from the transcript's tail (0 when unreadable)."""
    return _last_turn(transcript)[0]


def context_window(model):
    """The session's context window in tokens: DS_CONTEXT_WINDOW if set, 200k for Haiku, else 1M (the Opus and
    Sonnet sessions here run with 1M; one reached 737k without compacting, 2026-09-25)."""
    try:
        return int(os.environ['DS_CONTEXT_WINDOW'])
    except (KeyError, ValueError):
        return 200000 if 'haiku' in model.lower() else 1000000


def claude_note(event, now=None):
    """At most every NOTE_EVERY seconds a session: the Claude plan's pace when it is off pace or the
    reading is missing or old, and a /compact suggestion past COMPACT_AT of the context window. '' when there is nothing to say.
    Afterwards the session's due marker holds when to look next, so quiet() can skip the calls in between."""
    ds_claude = _launcher_module('ds_claude')
    if ds_claude is None:
        return ''
    d = ds_claude.spend_dir()
    notes = d / 'claude-notes.json'
    try:
        seen = json.loads(notes.read_text(encoding='utf-8'))
        seen = seen if isinstance(seen, dict) else {}
    except (OSError, ValueError):
        seen = {}
    sid = str(event.get('session_id') or '')
    now = now or time.time()
    parts = []
    if now - float(seen.get(sid) or 0) >= NOTE_EVERY:
        s = ds_claude.status(d)
        if s['level'] is None or s['level'] or s['stale'] or s.get('spend_down'):
            parts += ds_claude.lines(d)
        size, model = _last_turn(event.get('transcript_path'))
        if size >= COMPACT_AT * context_window(model):
            # The user compacts rather than starting new sessions (2026-09-25): a fresh session would also miss
            # the finish notices of workers this one launched.
            parts.append('This session re-reads about %dk tokens on every turn, %d%% of its context window.'
                         ' At the next quiet moment (nothing '
                         'mid-edit, no worker still running whose finish notice this session must get), suggest the '
                         'user runs /compact. Before that, make sure the state lives in files that survive it (the '
                         'handoff, the team log, or local/handover-<date>.md). Until then, hand big reading to '
                         'workers.' % (size // 1000, 100 * size // context_window(model)))
        if parts:
            seen[sid] = now
    # Worker memory, on its own clock (the check runs git, about 0.6 s): the morning report says it only at
    # session start, and Project A's sessions ran for days while the map fell 226 commits behind (2026-09-26).
    mem_key = 'memory:' + sid
    checked = False
    if now - float(seen.get(mem_key) or 0) >= NOTE_EVERY:
        seen[mem_key] = now
        checked = True
        line = memory_note(event.get('cwd'))
        if line:
            parts.append(line)
    pace_next = float(seen.get(sid) or 0) + NOTE_EVERY
    if pace_next <= now:    # looked at just now with nothing to say: pace and context move, so look again soon
        pace_next = now + RECHECK
    _set_due(sid, min(pace_next, float(seen[mem_key]) + NOTE_EVERY), now if checked else None)
    if not parts and not checked:
        return ''
    seen = {k: v for k, v in seen.items() if now - float(v or 0) < 86400}
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = notes.with_name(notes.name + '.tmp')
        tmp.write_text(json.dumps(seen), encoding='utf-8')
        os.replace(str(tmp), str(notes))
    except OSError:
        pass
    return '\n'.join(parts)


def _set_due(sid, when, prune_at=None):
    """Set the session's due marker to `when`; with prune_at, also drop markers a day older than that."""
    path = _due_marker(sid)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'a').close()
        os.utime(path, (when, when))
        if prune_at is not None:
            for entry in os.scandir(os.path.dirname(path)):
                if entry.stat().st_mtime < prune_at - 86400:
                    os.remove(entry.path)
    except OSError:
        pass


def memory_note(cwd):
    """'Project memory: map due ...' for a project that runs workers and has a digest due, else ''."""
    if not cwd or worker_state(cwd) is None:
        return ''
    try:
        import ds_memory             # beside ds_state.py in tools/
        # ds_memory asks the launcher's rule, which starts PowerShell (0.35 to 1.7 s) inside the hook's 10 s limit:
        # give it the rule in Python, as worker_state() uses.
        if hasattr(ds_memory, '_state_dir'):
            ds_memory._state_dir = _ds_state().fallback_dir
        todo = ds_memory.due(Path(cwd))
    except Exception:
        return ''
    if not todo:
        return ''
    return ('Project memory: %s due, so worker briefs are missing recent changes. At a quiet moment, run '
            '`python "%s" due` and start the command(s) it prints in the background.'
            % (' and '.join(todo), Path(ds_memory.__file__).resolve().as_posix()))


def post_note(event):
    if event.get('agent_id'):      # a subagent's tool call: these notes are for the session that leads
        return
    note = claude_note(event)
    if note:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': note}}))


KINDS = ('research', 'websearch', 'impl', 'review', 'analysis', 'critic', 'digest', 'advisor', 'lead',
         'selftest', 'probe')
# "<Provider> <kind> #<nnn>: <what>", the panel format in the skill. The provider is the one the launch runs on
# (-Provider, launcher/providers.json): a MiMo worker read "DeepSeek ..." in the panel (the user, 2026-09-27).
# The names are the registry's (ds_providers.short_name); these stand in when it can't be read.
FALLBACK_NAMES = {'deepseek': 'DeepSeek', 'xiaomi': 'MiMo', 'meta': 'Muse'}
_names = {}


def provider_names():
    """Provider key -> its short name in panels ("MiMo"), from launcher/providers.json."""
    if not _names:
        try:
            prov = _launcher_module('ds_providers')
            _names.update({k: prov.short_name(k) for k in (prov.registry().get('providers') or {})})
        except Exception:
            _names.clear()
        if not _names:
            _names.update(FALLBACK_NAMES)
    return _names


def _alternation():
    names = sorted(set(provider_names().values()), key=len, reverse=True)
    return '|'.join(re.escape(n) for n in names)


def _panel_any():
    """ "<Provider> <kind> #<nnn>: <what>" for any provider in the registry."""
    return re.compile(r'^(%s) (%s) #(\d{3,}(?:\.\d+)?): (\S.*)$' % (_alternation(), '|'.join(KINDS)))


def _numbered():
    """A numbered panel entry, a worker's or a Claude subagent's, as it appears in a session transcript."""
    return re.compile(r'"description":\s*"(?:%s|Claude) (?:%s) #(\d{3,})' % (_alternation(), '|'.join(KINDS)))


# The same house format for Claude subagents, a DeepSeek task id inside a description, and any numbered entry.
CLAUDE_PANEL = re.compile(r'^Claude (%s) #(\d{3,})(?:\.\d+)?: (\S.*)$' % '|'.join(KINDS))
TASK_REF = re.compile(r'\b(%s)-(\d{3})\b' % '|'.join(KINDS))
RECENT = 20


def without_heredocs(cmd):
    """The command with any here-document body removed. A script written into a file
    (python - <<PY ... PY) often contains a launch line as text: that is data, not a command."""
    out, terminator = [], None
    for line in cmd.split('\n'):
        if terminator is not None:
            if line.strip() == terminator:
                terminator = None
            continue
        out.append(line)
        m = re.search(r'<<-?\s*["\']?([A-Za-z_][A-Za-z_0-9]*)["\']?', line)
        if m:
            terminator = m.group(1)
    return '\n'.join(out)


SHELLS = ('powershell', 'powershell.exe', 'pwsh', 'pwsh.exe')
WRAPPERS = ('timeout', 'timeout.exe', 'nohup', 'env', 'nice', 'time', 'exec', 'command', 'stdbuf')
MANAGEMENT = re.compile(r'^-(DryRun|List|Integrate|Discard|Post)$', re.I)


def _words(segment):
    """A segment split like a shell would, quotes kept together, so a path with a space is one word.
    Windows backslashes are kept; the quotes are removed afterwards. A quote inside a word counts too:
    shlex split T="C:/.../DeepSeek Workers/tools/ds_impl.ps1" at the space, and the second half then read
    as a direct call to ds_impl, so `-Integrate` through "$T" was refused as a launch (2026-09-26)."""
    words = re.findall(r'''(?:[^\s"']+|"[^"]*"?|'[^']*'?)+''', segment)
    return [re.sub(r'''["']''', '', w) for w in words]


DURATION = re.compile(r'^(\d+(?:\.\d+)?)([smhd]?)$')
# Options of timeout (and nice, env, stdbuf) given their value as the next word: `timeout -k 5 3h`, `-s KILL`.
OPTION_WITH_VALUE = re.compile(r'^(-[ksnuC]|--(signal|kill-after|adjustment|unset|chdir))$')


def _strip_wrappers(words):
    """The words after any VAR=value prefixes and wrappers that run the rest of the line, and the seconds a
    `timeout` wrapper allows (None when there is none). `timeout 10800 powershell -File ...` went unchecked (a Project B
    MiMo launch, 2026-09-29), since `timeout` is not a shell."""
    limit = None
    while words:
        if re.match(r'^[A-Za-z_][A-Za-z_0-9]*=', words[0]):
            words.pop(0)
        elif Path(words[0]).name.lower() in WRAPPERS:
            is_timeout = Path(words.pop(0)).name.lower() in ('timeout', 'timeout.exe')
            got_duration = False
            while words and (words[0].startswith('-') or DURATION.match(words[0])):
                w = words.pop(0)       # the wrapper's options and duration or priority
                if OPTION_WITH_VALUE.match(w) and words:
                    words.pop(0)
                    continue
                m = DURATION.match(w)
                if is_timeout and m and not got_duration:
                    got_duration = True
                    limit = float(m.group(1)) * {'': 1, 's': 1, 'm': 60, 'h': 3600, 'd': 86400}[m.group(2)]
        else:
            break
    return words, limit


def _launch_segments(cmd):
    """(words, timeout seconds or None) for each segment of `cmd` that really starts a worker."""
    for segment in re.split(r'&&|\|\||;|\n', without_heredocs(cmd)):
        words, limit = _strip_wrappers(_words(segment))
        if words and words[0] == '&':
            words.pop(0)
        if not words:
            continue
        first = Path(words[0]).name.lower()
        if first in SHELLS:
            script = next((words[i + 1] for i, w in enumerate(words[:-1]) if w.lower() == '-file'), '')
        else:
            script = words[0]
        if Path(script).name.lower() not in SCRIPTS:
            continue
        if any(MANAGEMENT.match(w) for w in words):
            continue
        yield words, limit


def launches_worker(cmd):
    """True when this command really starts a worker, rather than mentioning one.

    A segment counts when it runs the launcher or ds_impl itself: a PowerShell given the script as its
    -File (the path may contain spaces, and the shell may be a quoted full path), or the script invoked
    directly or with the call operator &, behind any VAR=value prefixes and wrappers (timeout, nohup, env,
    nice). Text that merely contains the names (a grep pattern, a script being written in a here-document, a
    quoted message) does not count, nor do dry runs and ds_impl's management commands. Found by review-024:
    the first version split paths at their spaces, so any launch from a folder such as "DeepSeek Workers" went
    unchecked."""
    return any(True for _ in _launch_segments(cmd))


def timeout_wrapper(cmd):
    """The seconds a `timeout` wrapper around a worker launch allows, or None when no launch is wrapped in one."""
    return next((limit for _, limit in _launch_segments(cmd) if limit is not None), None)


def timeout_refusal(seconds):
    """Why a launch wrapped in `timeout` is refused, with the -TimeoutMinutes that stands in for it. A Project B MiMo
    worker started as `timeout 5400 powershell -File ds-agent.ps1 ...` hit the 90 minutes after writing its files
    but before reporting (2026-09-29): the killed launcher recorded it as canceled, with no report and no warning."""
    minutes = max(1, int(-(-seconds // 60)))
    return ('Don\'t wrap a worker launch in `timeout`: when it fires it kills the launcher from outside, so the worker '
            'gets no warning, the run is recorded as canceled and its report is lost. Drop the `timeout` wrapper and '
            'pass -TimeoutMinutes %d instead (the same limit): the launcher then tells the worker to wrap up at 75%% '
            'of that time, stops it at the limit, keeps a partial report and records the run as timed out (exit 3). '
            'Other wrappers (nohup, env, nice) are fine.' % minutes)


def held(cmd, provider, kind, cwd):
    """Why a worker launch would be held by its provider's pace, limits or plan allowance, or None. The user,
    2026-09-29: held work should still happen, as a Claude subagent on Sonnet 5.5. A resume stays with its
    provider (a Claude subagent can't continue that session); anything unsure lets the launcher decide."""
    if re.search(r'-Resume\b', cmd, re.I):
        return None
    try:
        ds_spend = _launcher_module('ds_spend')
        if ds_spend is None:
            return None
        plan = ds_spend.plan(ds_spend.spend_dir(), 'impl' if kind == 'impl' else kind, provider=provider)
        if not plan.get('ok'):
            return ' '.join(plan.get('lines') or []) or '%s is held by its pace' % provider
        providers = _launcher_module('ds_providers')
        allowance = _launcher_module('ds_allowance')
        if providers and allowance:
            tier = str(arg(cmd, 'Tier') or '').strip('"\'') or None
            r = providers.resolve(provider, tier, cwd)
            if r.get('allowance'):
                s = allowance.status(r['provider'], r['tier'])
                if s.get('configured') and s.get('level') == 'hold':
                    return '%s %s: %.1f%% of its plan allowance is used, well ahead of an even pace.' % (
                        r.get('name', provider), r['tier'], 100 * s['share'])
    except Exception:
        return None
    return None


def claude_instead(cmd, who, why, named):
    """The refusal for a held launch: the same work as a Claude subagent, ready to start."""
    brief = arg(cmd, 'TaskFile') or arg(cmd, 'Brief')
    task = arg(cmd, 'Task')
    source = ('the brief in %s (read it and pass its text as the prompt)' % str(brief).strip('"\'') if brief
              else 'the -Task text' if task else 'the same brief')
    edits = named.group(2) == 'impl' or re.search(r'-Mode\s+edit\b', cmd, re.I) or 'ds_impl' in cmd.lower()
    text = ('%s is held right now: %s Do the same work as a Claude subagent instead: the Agent tool with '
            'description "Claude %s #%s: %s", model "sonnet" (Sonnet 5.5; "opus" only if Sonnet has already failed at this task, or the user asks), '
            'the prompt from %s%s, and run_in_background true.' % (
                who, why.strip(), named.group(2), named.group(3), named.group(4), source,
                ', isolation "worktree", since it edits files' if edits else ''))
    try:
        ds_claude = _launcher_module('ds_claude')
        if ds_claude is not None and ds_claude.level(ds_claude.spend_dir()) >= 3:
            text += (" But Claude's plan is at hold too: do only the small parts yourself and queue the rest for when "
                     'either budget frees up.')
    except Exception:
        pass
    return text


def arg(cmd, name):
    """The value of -Name in a PowerShell command line, quoted or not."""
    m = re.search(r'-%s\s+(?:"([^"]+)"|\'([^\']+)\'|(\S+))' % name, cmd, re.I)
    return next((g for g in m.groups() if g), None) if m else None


def state_dir(cmd, cwd):
    """Where the launcher will keep the manifest, by its own rule (tools/ds_state.py, and behind it
    launcher/ds-state.ps1): DS_STATE_DIR, the project's stateDir, local/agents when local/ exists, else
    ~/.claude-deepseek/agents. DS_STATE_DIR written into the command itself is not the rule's - it is the
    value the launched process really gets - so the command is read for it first."""
    # Only an assignment that starts a command segment (bash VAR=value prefixes, export, or PowerShell's
    # $env:), never the same text inside an argument (audit finding SDR-20260924-03).
    m = re.search(r'(?:^|&&|\|\||;|\n)\s*(?:(?:export\s+)?(?:[A-Za-z_][A-Za-z_0-9]*=\S*\s+)*|\$env:)DS_STATE_DIR\s*=\s*'
                  r'(?:"([^"]+)"|\'([^\']+)\'|([^\s;]+))', cmd)
    if m:
        return next(g for g in m.groups() if g)
    ds_state = _ds_state()
    if ds_state is None:
        raise RuntimeError('ds_state.py not found beside this hook or two folders up')
    # The rule in Python, as worker_state() uses: ds_state.state_dir() starts PowerShell (0.35 to 1.7 s) inside the
    # hook's 10 s limit. A relative -Dir is the launch's, which runs in the session's folder.
    return str(ds_state.fallback_dir(Path(cwd) / (arg(cmd, 'Dir') or '')))


def session_start(event):
    """The short morning report, as context for a session that opens in a project where workers ran
    since the last report. Silent everywhere else."""
    import subprocess
    cwd = event.get('cwd') or os.getcwd()
    here = Path(__file__).resolve().parent
    # tools/ beside this file once installed; the harness keeps it two folders up.
    morning = next((p for p in (here / 'tools' / 'ds_morning.py', here.parents[1] / 'tools' / 'ds_morning.py') if p.is_file()), None)
    text = ''
    if morning and ((Path(cwd) / 'local').is_dir() or os.environ.get('DS_STATE_DIR')):
        try:
            done = subprocess.run([sys.executable, str(morning), '--project', cwd, '--short'], capture_output=True,
                                  text=True, encoding='utf-8', errors='replace', timeout=12)
            text = done.stdout.strip() if done.returncode == 0 else ''
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        ds_claude = _launcher_module('ds_claude')
        if ds_claude is not None:
            text = (text + '\n\n' if text else '') + '\n'.join(ds_claude.lines(ds_claude.spend_dir()))
            import ds_spend             # beside ds_claude.py
            low = ds_spend.balance_warning(ds_claude.spend_dir())
            if low:
                text += '\n' + low
    except Exception:
        pass
    if text:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': text}}))


# A budget instruction to one session has to reach the launcher, which every session shares: on 2026-09-26 a
# "make it last a month" told to one session was saved in its project memory with no ds_spend.py command, so the
# old daily limit stayed in force for hours until another session applied `stretch 1m`.
# DeepSeek or credit named outright: "add the end-of-week spend-down" (about Claude's own weekly allowance) matched
# the old 'spend' and 'week' and got the DeepSeek reminder (2026-09-27).
BUDGET_WORDS = re.compile(r'\b(deepseek|credit|top(ped)?[- ]?up)\b', re.I)
BUDGET_ASK = re.compile(r'\b(last|month|months|week|weeks|days?|limit|cap|stretch|per day|per week|topped|top[- ]?up|raise|lower)\b', re.I)


def budget_prompt(event):
    """A user prompt about the DeepSeek budget: remind the session to apply it with ds_spend.py."""
    prompt = str(event.get('prompt') or '')
    if not (BUDGET_WORDS.search(prompt) and BUDGET_ASK.search(prompt)):
        return
    tool = Path(__file__).resolve().parent / 'ds_spend.py'
    if not tool.is_file():
        tool = Path(__file__).resolve().parents[2] / 'launcher' / 'ds_spend.py'
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': (
        'If this message sets or changes the DeepSeek budget (make the credit last a while, a limit, a top-up), apply it '
        'with `python "%s"` (`stretch <1m|2w|10d|date>`, `set <dollars> --per day|week|run`, `off`, `status`): the '
        'launcher enforces that for every session and project. A memory note or a change in your own habits does not '
        'reach the launcher or the other sessions. Then show the user the `status` line.' % tool.as_posix())}}))


def main(raw=None):
    """One hook event: the raw JSON Claude Code sent (read from stdin when not given)."""
    event = json.loads(sys.stdin.read() if raw is None else raw)
    if event.get('hook_event_name') == 'SessionStart':
        session_start(event)
        return
    hook = event.get('hook_event_name')
    if hook == 'UserPromptSubmit':
        budget_prompt(event)
        return
    if hook == 'SubagentStop':
        agent_stop(event)
        return
    if event.get('tool_name') not in ('Bash', 'PowerShell'):
        if event.get('tool_name') in ('Agent', 'Task'):
            if hook == 'PreToolUse':
                agent_pre(event)
            elif hook == 'PostToolUse':
                try:
                    agent_post(event)
                finally:
                    post_note(event)
        return
    tool_input = event.get('tool_input') or {}
    cmd = str(tool_input.get('command') or tool_input.get('script') or '')
    if not launches_worker(cmd):
        if hook == 'PostToolUse':
            post_note(event)
        return
    limit = timeout_wrapper(cmd) if hook == 'PreToolUse' else None
    if limit is not None:
        print(json.dumps({'hookSpecificOutput': {
            'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
            'permissionDecisionReason': timeout_refusal(limit)}}))
        return
    label =(arg(cmd, 'Label') or arg(cmd, 'Name')
             or (Path(arg(cmd, 'TaskFile') or arg(cmd, 'Brief')).stem if (arg(cmd, 'TaskFile') or arg(cmd, 'Brief')) else None)
             or 'task')

    # The panel is how a human sees what is running, so every worker entry names its provider, kind and number.
    provider = str(arg(cmd, 'Provider') or 'deepseek').strip('"\'').lower()
    who = provider_names().get(provider) or provider.capitalize()
    desc = str(tool_input.get('description') or '')
    named = _panel_any().match(desc)
    if hook == 'PreToolUse' and not (named and named.group(1) == who):
        kind, number = 'research', '001'
        parts = label.split('-')
        if parts and parts[0] in KINDS:
            kind = parts[0]
            if len(parts) > 1 and parts[1].isdigit():
                number = parts[1].zfill(3)
        what = ' '.join(parts[2:]) if len(parts) > 2 else (label if kind == 'research' else 'what it does')
        given = str(arg(cmd, 'Kind') or '').strip('"\'').lower()
        if given in KINDS and given != kind:   # a label like t88d-console-schema says nothing of the kind
            kind, what = given, 'what it does'
        if named:     # the right shape under another provider's name: keep its kind, number and wording
            kind, number, what = named.group(2), named.group(3), named.group(4)
        tail = ' (lead, spawns workers)' if re.search(r'-CanSpawn\b', cmd, re.I) else ''
        print(json.dumps({'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny',
            'permissionDecisionReason': (
                'A worker launch needs a Background tasks description in the house format, so the panel '
                'says what the entry is and which provider it runs on: "%s %s #%s: %s"%s. Run the same command '
                'again with that description (and run_in_background true).' % (who, kind, number, what, tail)),
        }}))
        return

    if hook == 'PreToolUse':
        why = held(cmd, provider, named.group(2), event.get('cwd') or os.getcwd())
        if why:
            print(json.dumps({'hookSpecificOutput': {
                'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                'permissionDecisionReason': claude_instead(cmd, who, why, named)}}))
            return

    if hook == 'PreToolUse' and not tool_input.get('run_in_background'):
        print(json.dumps({'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny',
            'permissionDecisionReason': (
                'A %s worker must run in the background, so it appears in the Background tasks panel '
                'and you can keep working while it runs. Run the same command again with run_in_background '
                'true. For a lead, this hook then gives you the watcher command to start straight after.' % who),
        }}))
        return

    if hook == 'PostToolUse' and re.search(r'-CanSpawn\b', cmd, re.I):
        folder = state_dir(cmd, event.get('cwd') or os.getcwd())
        shell_var = re.search(r'[$%]', folder)
        watch = 'powershell -NoProfile -ExecutionPolicy Bypass -File "%s" -Children %s -StateDir "%s"' % (
            WATCH.as_posix(), label, folder if shell_var else Path(folder).as_posix())
        # The launch set the state folder through a shell variable, which this hook can't expand.
        unexpanded = ('\nThe -StateDir above contains a shell variable (%s); replace it with the real folder '
                      'before running the watcher.' % folder) if shell_var else ''
        print(json.dumps({'hookSpecificOutput': {
            'hookEventName': 'PostToolUse',
            'additionalContext': (
                '%s lead %s started. Start its watcher now, in the background (run_in_background true), '
                'before anything else, with the description "Watch lead #<nnn> for new workers":\n%s\n'
                'When it exits, start each ds-watch.ps1 -Run command it prints, in the background with the '
                'description it prints. If the lead is still running, also restart the printed -Children ... '
                '-Known ... command. Don\'t block in the foreground while the lead runs.%s' % (who, label, watch, unexpanded))
                + ('\n\n' + note if (note := claude_note(event)) else ''),
        }}))
    elif hook == 'PostToolUse':
        post_note(event)


def install(settings_path=None):
    """Register this hook in ~/.claude/settings.json, replacing an earlier registration of it and keeping
    every other setting. The file is backed up to ~/.claude-deepseek/backups first."""
    import shutil, time
    path = Path(settings_path) if settings_path else Path.home() / '.claude' / 'settings.json'
    settings = {}
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding='utf-8') or '{}')
        except ValueError:
            print('hook not registered: %s is not valid JSON' % path)
            return 1
        backups = Path.home() / '.claude-deepseek' / 'backups'
        backups.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backups / ('settings.json.bak-%s' % time.strftime('%Y%m%d-%H%M%S')))
    # Hook commands run in Git Bash or PowerShell; a bare first word works in both, a quoted path only in bash.
    runner = 'python' if shutil.which('python') else ('py' if shutil.which('py') else Path(sys.executable).as_posix())
    command = '%s "%s"' % (runner, Path(__file__).resolve().with_name('ds_hook.py').as_posix())
    if not isinstance(settings, dict) or not isinstance(settings.get('hooks', {}), dict):
        print('hook not registered: %s does not hold a settings object (audit finding WLH-20260924-01)' % path)
        return 1
    hooks = settings.setdefault('hooks', {})

    def without_ours(groups):
        # Drop only this hook's own entries: a group the user also put other hooks in keeps them
        # (audit finding WORKERLA-20260924-01: whole groups used to be dropped).
        out = []
        for g in groups if isinstance(groups, list) else []:
            if not isinstance(g, dict) or not isinstance(g.get('hooks'), list):
                out.append(g)             # not ours to judge: keep it exactly as it was
                continue
            entries = [h for h in g['hooks'] if not (isinstance(h, dict) and 'ds_hook.py' in str(h.get('command', '')))]
            if entries:
                out.append(dict(g, hooks=entries))
            elif not g['hooks']:
                out.append(g)
        return out

    for event, matcher, timeout in (('PreToolUse', 'Bash|PowerShell|Agent|Task', 10),
                                    ('PostToolUse', 'Bash|PowerShell|Agent|Task', 10),
                                    ('SubagentStop', '', 30), ('SessionStart', 'startup|resume', 15),
                                    ('UserPromptSubmit', '', 10)):
        hooks[event] = without_ours(hooks.get(event, [])) + [
            {'matcher': matcher, 'hooks': [{'type': 'command', 'command': command, 'timeout': timeout}]}]
    path.parent.mkdir(parents=True, exist_ok=True)
    # Through a temp file, so an interrupted write can't leave the user's settings half-written (review-049).
    tmp = path.with_name(path.name + '.ds-hook.tmp')
    tmp.write_text(json.dumps(settings, indent=2) + '\n', encoding='utf-8')
    os.replace(str(tmp), str(path))
    print('registered the worker hook (ds_hook.py) in %s (new sessions pick it up)' % path)
    return 0
