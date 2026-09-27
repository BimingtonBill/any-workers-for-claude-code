"""Claude Code hook: every DeepSeek lead runs in the background with a watcher for its workers.

Registered by tools/install-skill.ps1 in ~/.claude/settings.json for the Bash and PowerShell tools:

    PreToolUse   a worker launch (ds-agent.ps1, or ds_impl.ps1 starting a coder) is refused unless it
                 runs in the background under a panel description in the house format,
                 "DeepSeek <kind> #<nnn>: <what>", so the panel says what each entry is. OpenSkyrim's
                 panel read "Launch the shadows worker" (2026-09-23), which hides the kind, the number
                 and the project.
    PostToolUse  after a lead starts, Claude is told the exact ds-watch.ps1 -Children command to start
                 next, so the lead's workers get their own Background tasks entries.
                 Also on Agent calls, at most every half hour a session: the Claude plan's pace
                 (ds_claude.py) when it is off pace or the reading is old, and a nudge to hand over when
                 the session's own context has grown large.
    SessionStart the short morning report in a project where workers ran, and the Claude plan's pace.
    UserPromptSubmit  a prompt about the DeepSeek budget: a reminder to apply it with ds_spend.py, which every
                 session shares, not only in this session's memory.

In a project that runs workers, Claude subagents (the Agent tool) are named and recorded like workers:
    PreToolUse   an Agent call is refused unless its description reads "Claude <kind> #<nnn>: <what>",
                 numbered in the same sequence as the session's DeepSeek workers (a task Claude takes
                 over from DeepSeek keeps its number), so the panel reads as one team. A well-named call
                 gets the standard report opening added to its prompt (launcher/ds_envelope.py).
    PostToolUse  the run is recorded in the manifest (runs/<run id>/manifest.json and manifest.jsonl,
                 provider "claude"), finished at once for a foreground subagent;
    SubagentStop a background subagent's run is finished: its report, tokens, turns and time.

Remembering this was left to Claude and it was forgotten (lead-001-duck-photos.1 ran in the foreground,
with no panel entries for its three workers), so the hook makes it structural. Anything else passes
untouched, and any error here lets the tool call through: a broken hook must never block work.
"""
import json
import os
import re
import sys
from pathlib import Path


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
    try:                                # a broken adapter must not stop the hook: the tool call goes through
        import ds_state
    except ImportError:
        ds_state = None
else:
    ds_state = None

WATCH = Path(__file__).resolve().parent / 'ds-watch.ps1'
CODING_KINDS = ('impl',)  # Claude subagents of these kinds run on Opus unless the plan is tight
NOTE_EVERY = 1800       # seconds between Claude-pace notes in one session
COMPACT_AT = 0.7         # suggest /compact once a session's context is this full (the user's call, 2026-09-27)




def worker_state(cwd):
    """The project's state dir when the project runs workers (the dir has runs/ and belongs to this
    project), else None: elsewhere Agent calls are left alone."""
    if ds_state is None or not cwd:
        return None
    try:
        # The rule in Python: state_dir() starts PowerShell (1.7 s idle), and under a cargo build that passed
        # the hook's 10 s limit, so OpenSkyrim's impl #185 was never recorded as finished (2026-09-25).
        sd = Path(ds_state.fallback_dir(Path(cwd))).resolve()
        root = Path(cwd).resolve()
    except Exception:
        return None
    ours = root in sd.parents or (root / '.deepseek-agents.json').is_file()
    return sd if ours and (sd / 'runs').is_dir() else None


def next_number(transcript, sd):
    """The next task number: after the highest #nnn among the last RECENT entries this session gave a
    DeepSeek worker or Claude subagent (each session keeps its own sequence; only recent ones, so one odd
    number long ago, like a hook test's #999, doesn't set it), else after the highest in the project's runs."""
    try:
        text = Path(transcript).read_text(encoding='utf-8', errors='replace')
        seen = [int(n) for n in NUMBERED.findall(text)][-RECENT:]
    except (OSError, TypeError):
        seen = []
    if not seen:
        seen = [int(m.group(1)) for m in (re.match(r'^[a-z]+-(\d{3})-', p.name) for p in (sd / 'runs').iterdir()) if m]
    return '%03d' % ((max(seen) if seen else 0) + 1)


def coding_model(kind, asked):
    """Opus for a Claude subagent that writes code, unless the Claude plan is tight (level 2 or more), when
    the caller's choice stands. The user's call, 2026-09-25: coding subagents had been sent on Sonnet
    (impl #185 spent 48 minutes and 172 turns on a renderer bug). None means leave the model alone."""
    if kind not in CODING_KINDS or asked == 'opus':
        return None
    ds_claude = _ds_claude()
    try:
        tight = ds_claude is not None and ds_claude.level(ds_claude.spend_dir()) >= 2
    except Exception:
        tight = False
    return None if tight else 'opus'


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
            'In this project Claude subagents are named like DeepSeek workers, in one numbered sequence, so the '
            'panel reads as one team: "Claude %s #%s: %s" (kind: %s). A task taken over from a DeepSeek worker '
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
    with open(sd / 'manifest.jsonl', 'a', encoding='utf-8') as fh:
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
    # state dir: every OpenSkyrim coding subagent on 2026-09-25 afternoon stayed "working" for this reason.
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
    """A launcher module (ds_claude, ds_envelope): beside this file once installed, in launcher/ in the
    harness; None when missing."""
    here = Path(__file__).resolve().parent
    for folder in (here, here.parents[1] / 'launcher'):
        if (folder / (name + '.py')).is_file():
            if str(folder) not in sys.path:
                sys.path.insert(0, str(folder))
            return __import__(name)
    return None


def _ds_claude():
    """ds_claude.py: beside this file once installed, in launcher/ in the harness; None when missing."""
    here = Path(__file__).resolve().parent
    for folder in (here, here.parents[1] / 'launcher'):
        if (folder / 'ds_claude.py').is_file():
            if str(folder) not in sys.path:
                sys.path.insert(0, str(folder))
            import ds_claude
            return ds_claude
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
    reading is missing or old, and a /compact suggestion past COMPACT_AT of the context window. '' when there is nothing to say."""
    import time
    ds_claude = _ds_claude()
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
                         'DeepSeek workers.' % (size // 1000, 100 * size // context_window(model)))
        if parts:
            seen[sid] = now
    # Worker memory, on its own clock (the check runs git, about 0.6 s): the morning report says it only at
    # session start, and OpenSkyrim's sessions ran for days while the map fell 226 commits behind (2026-09-26).
    mem_key = 'memory:' + sid
    checked = False
    if now - float(seen.get(mem_key) or 0) >= NOTE_EVERY:
        seen[mem_key] = now
        checked = True
        line = memory_note(event.get('cwd'))
        if line:
            parts.append(line)
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


def memory_note(cwd):
    """'Project memory: map due ...' for a project that runs workers and has a digest due, else ''."""
    if not cwd or worker_state(cwd) is None:
        return ''
    try:
        import ds_memory             # beside ds_state.py in tools/
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
# "DeepSeek <kind> #<nnn>: <what>", the panel format in the skill.
PANEL = re.compile(r'^DeepSeek (%s) #\d{3,}(\.\d+)?: \S' % '|'.join(KINDS))
# The same house format for Claude subagents, a DeepSeek task id inside a description, and any numbered entry.
CLAUDE_PANEL = re.compile(r'^Claude (%s) #(\d{3,})(?:\.\d+)?: (\S.*)$' % '|'.join(KINDS))
TASK_REF = re.compile(r'\b(%s)-(\d{3})\b' % '|'.join(KINDS))
NUMBERED = re.compile(r'"description":\s*"(?:DeepSeek|Claude) (?:%s) #(\d{3,})' % '|'.join(KINDS))
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
SCRIPTS = ('ds-agent.ps1', 'ds_impl.ps1')
MANAGEMENT = re.compile(r'^-(DryRun|List|Integrate|Discard|Post)$', re.I)


def _words(segment):
    """A segment split like a shell would, quotes kept together, so a path with a space is one word.
    Windows backslashes are kept; the quotes are removed afterwards. A quote inside a word counts too:
    shlex split T="C:/.../DeepSeek Workers/tools/ds_impl.ps1" at the space, and the second half then read
    as a direct call to ds_impl, so `-Integrate` through "$T" was refused as a launch (2026-09-26)."""
    words = re.findall(r'''(?:[^\s"']+|"[^"]*"?|'[^']*'?)+''', segment)
    return [re.sub(r'''["']''', '', w) for w in words]


def launches_worker(cmd):
    """True when this command really starts a worker, rather than mentioning one.

    A segment counts when it runs the launcher or ds_impl itself: a PowerShell given the script as its
    -File (the path may contain spaces, and the shell may be a quoted full path), or the script invoked
    directly or with the call operator &. Text that merely contains the names (a grep pattern, a script
    being written in a here-document, a quoted message) does not count, nor do dry runs and ds_impl's
    management commands. Found by review-024: the first version split paths at their spaces, so any
    launch from a folder such as "DeepSeek Workers" went unchecked."""
    for segment in re.split(r'&&|\|\||;|\n', without_heredocs(cmd)):
        words = _words(segment)
        while words and re.match(r'^[A-Za-z_][A-Za-z_0-9]*=', words[0]):   # VAR=value prefixes
            words.pop(0)
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
        return True
    return False


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
    if ds_state is None:
        raise RuntimeError('ds_state.py not found beside this hook or two folders up')
    return str(ds_state.state_dir(Path(arg(cmd, 'Dir') or cwd)))


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
        ds_claude = _ds_claude()
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


def main():
    event = json.load(sys.stdin)
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
    label = (arg(cmd, 'Label') or arg(cmd, 'Name')
             or (Path(arg(cmd, 'TaskFile') or arg(cmd, 'Brief')).stem if (arg(cmd, 'TaskFile') or arg(cmd, 'Brief')) else None)
             or 'task')

    # The panel is how a human sees what is running, so every worker entry names its kind and number.
    if hook == 'PreToolUse' and not PANEL.match(str(tool_input.get('description') or '')):
        kind, number = 'research', '001'
        parts = label.split('-')
        if parts and parts[0] in KINDS:
            kind = parts[0]
            if len(parts) > 1 and parts[1].isdigit():
                number = parts[1].zfill(3)
        what = ' '.join(parts[2:]) if len(parts) > 2 else (label if kind == 'research' else 'what it does')
        tail = ' (lead, spawns workers)' if re.search(r'-CanSpawn\b', cmd, re.I) else ''
        print(json.dumps({'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny',
            'permissionDecisionReason': (
                'A worker launch needs a Background tasks description in the house format, so the panel '
                'says what the entry is: "DeepSeek %s #%s: %s"%s. Run the same command again with that '
                'description (and run_in_background true).' % (kind, number, what, tail)),
        }}))
        return

    if hook == 'PreToolUse' and not tool_input.get('run_in_background'):
        print(json.dumps({'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny',
            'permissionDecisionReason': (
                'A DeepSeek worker must run in the background, so it appears in the Background tasks panel '
                'and you can keep working while it runs. Run the same command again with run_in_background '
                'true. For a lead, this hook then gives you the watcher command to start straight after.'),
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
                'DeepSeek lead %s started. Start its watcher now, in the background (run_in_background true), '
                'before anything else, with the description "Watch lead #<nnn> for new workers":\n%s\n'
                'When it exits, start each ds-watch.ps1 -Run command it prints, in the background with the '
                'description it prints. If the lead is still running, also restart the printed -Children ... '
                '-Known ... command. Don\'t block in the foreground while the lead runs.%s' % (label, watch, unexpanded))
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
    command = '%s "%s"' % (runner, Path(__file__).resolve().as_posix())
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
    print('registered the DeepSeek lead hook in %s (new sessions pick it up)' % path)
    return 0


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--install':
        sys.exit(install(sys.argv[2] if len(sys.argv) > 2 else None))
    try:
        main()
    except Exception:  # never block a tool call because the hook itself failed
        pass
    sys.exit(0)
