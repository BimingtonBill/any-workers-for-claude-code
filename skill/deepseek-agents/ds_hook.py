"""Claude Code hook: every DeepSeek lead runs in the background with a watcher for its workers.

Registered by tools/install-skill.ps1 in ~/.claude/settings.json for the Bash and PowerShell tools:

    PreToolUse   a worker launch (ds-agent.ps1, or ds_impl.ps1 starting a coder) is refused unless it
                 runs in the background under a panel description in the house format,
                 "DeepSeek <kind> #<nnn>: <what>", so the panel says what each entry is. Project A's
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

The hook runs on every Bash and PowerShell call in every session, and almost all of them need nothing from it.
Those are answered from the raw event text by quiet(), before anything else is imported: 75 ms an event fell to
40 ms, against 31 ms for bare Python, most of the rest being Python compiling this file (2026-09-29). So this file
holds only that fast path; the rest of the hook is ds_hook_main.py beside it, imported rather than run, so Python
keeps its compiled form in __pycache__. Importing ds_hook (the tests do) gives ds_hook_main, the whole hook.
"""
import os
import sys
import time

NOTE_EVERY = 1800       # seconds between Claude-pace notes in one session
RECHECK = 300           # seconds before a session with nothing to say is looked at again
SCRIPTS = ('ds-agent.ps1', 'ds_impl.ps1')
# Substrings every budget prompt contains (BUDGET_WORDS and BUDGET_ASK below match only prompts that have them).
_BUDGET_HINTS = ('deepseek', 'credit', 'top')
_ASK_HINTS = ('last', 'month', 'week', 'day', 'limit', 'cap', 'stretch', 'top', 'raise', 'lower')


def _field(raw, key):
    """The plain string value of "key" in the raw event JSON, without parsing it; None when absent or escaped.
    A quote inside a JSON string is always escaped, so an unescaped "key" followed by a colon is a real key."""
    token = '"%s"' % key
    i = raw.find(token)
    while i >= 0:
        rest = raw[i + len(token):].lstrip()
        if rest.startswith(':'):
            rest = rest[1:].lstrip()
            if not rest.startswith('"'):
                return None
            end = rest.find('"', 1)
            value = rest[1:end] if end > 0 else None
            return None if value is None or '\\' in value else value
        i = raw.find(token, i + 1)
    return None


def _spend_dir():
    """ds_claude.spend_dir() without importing it: DS_SPEND_DIR, else ~/.claude-deepseek/spend."""
    return os.environ.get('DS_SPEND_DIR') or os.path.join(os.path.expanduser('~'), '.claude-deepseek', 'spend')


def _due_marker(session_id):
    """The session's marker file, whose modified time is when its Claude-pace and memory notes are next due."""
    safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in str(session_id))[:100] or '_'
    return os.path.join(_spend_dir(), 'claude-due', safe)


def quiet(raw):
    """True when this event certainly needs nothing from the hook, decided from the raw event text alone: a
    shell call that can't be a worker launch (the command never names ds-agent.ps1 or ds_impl.ps1), after
    which no note is due; or a prompt without the budget words. Anything unsure goes the full way."""
    event = _field(raw, 'hook_event_name')
    low = raw.lower()
    if event == 'UserPromptSubmit':
        if not raw.isascii() or '\\u' in raw:   # the regexes ignore case the Unicode way; let them decide
            return False
        return not (any(w in low for w in _BUDGET_HINTS) and any(w in low for w in _ASK_HINTS))
    if event not in ('PreToolUse', 'PostToolUse') or _field(raw, 'tool_name') not in ('Bash', 'PowerShell'):
        return False
    # launches_worker() reads a script name with its quotes removed, so a quote inside it must not hide it here.
    plain = low.replace('\\"', '').replace('"', '').replace("'", '')
    if any(s in plain for s in SCRIPTS):
        return False
    if event == 'PreToolUse':
        return True
    if _field(raw, 'agent_id'):     # a subagent's shell call: the notes are for the session that leads
        return True
    sid = _field(raw, 'session_id')
    if sid is None:
        return False
    try:
        return os.stat(_due_marker(sid)).st_mtime > time.time()
    except OSError:
        return False


def _whole_hook():
    """ds_hook_main.py beside this file: the rest of the hook. It takes the fast-path helpers above from this module,
    so a run as a script is registered as ds_hook first rather than read a second time."""
    if __name__ == '__main__':
        sys.modules.setdefault('ds_hook', sys.modules[__name__])
    import ds_hook_main
    return ds_hook_main


if __name__ == '__main__':
    if sys.argv[1:2] == ['--install']:
        sys.exit(_whole_hook().install(sys.argv[2] if len(sys.argv) > 2 else None))
    _RAW = sys.stdin.read()
    try:
        if quiet(_RAW):
            sys.exit(0)
    except Exception:  # never block a tool call: the full path decides, and fails open itself
        pass
    try:
        _whole_hook().main(_RAW)
    except Exception:  # never block a tool call because the hook itself failed
        pass
    sys.exit(0)
elif 'ds_hook_main' not in sys.modules:     # (when it is, ds_hook_main is importing this: stay the fast path)
    # Imported: stand for the whole hook, so ds_hook.<anything> (and mock.patch.object(ds_hook, ...)) reaches the code
    # that uses it.
    sys.modules[__name__] = _whole_hook()
