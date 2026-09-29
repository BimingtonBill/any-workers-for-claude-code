"""The standard opening of every worker report (and Claude subagent report), so Claude can read a few lines
instead of the whole thing.

On 2026-09-25 the OpenSkyrim sessions pulled about 2.1M characters of worker output into their own context
in 20 hours (reports ran 6-12k characters), 11 of 19 reviews had no verdict line, and fewer than half the
reports said what was left. Every report now opens with:

    Status: done | partial | blocked
    Verdict: accept | fix first | reject                (reviews; research: answered | partly | not found)
    Summary: up to five lines, the outcome first
    Left: what was not done, or "nothing"
    Next: follow-up tasks worth briefing, or "none"

then a blank line and the details. The launcher prints only this opening and the report's path, keeps the
full report on disk, and records the fields in the run's manifest.

    python ds_envelope.py note --kind review          the instruction the worker gets
    python ds_envelope.py head --report <file> [--json]   the opening, or exit 1 when there is none

Two more questions about how a worker ended, for the launcher:

    python ds_envelope.py partial --manifest <run>/manifest.json --why <text>
        keep what a worker had written when its launcher was stopped from outside (exit 1 when nothing)
    python ds_envelope.py retry --result <file> --transcript <file> --since <ISO time>
        whether a run that ended on an API error should be resumed once: {"retry": bool, "why": ..., "turns": n}
"""
import argparse
import json
import re
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from ds_common import parse_time, read_json  # noqa: E402  (beside this file, in the harness and once installed)

VERDICTS = {
    'review': ('accept', 'fix first', 'reject'),
    'critic': ('accept', 'fix first', 'reject'),
    'research': ('answered', 'partly', 'not found'),
    'websearch': ('answered', 'partly', 'not found'),
    'analysis': ('answered', 'partly', 'not found'),
}
FIELDS = ('status', 'verdict', 'summary', 'left', 'next')
FIELD = re.compile(r'^\s*(?:[-*>]\s*)?(?:\*\*|__)?(status|verdict|summary|left|next)(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(.*)$', re.I)


def note(kind):
    """The instruction, on one line (it goes into the worker's system prompt, which is one argument)."""
    verdict = VERDICTS.get(kind)
    fields = ['"Status: done | partial | blocked"'] + (['"Verdict: %s"' % ' | '.join(verdict)] if verdict else []) + [
        '"Summary: ..." (up to five lines, the outcome or answer first)', '"Left: ..." (what you did not do or '
        'could not finish, or nothing)', '"Next: ..." (follow-up tasks worth briefing, or none)']
    return ('Start your report with these lines, each on its own line, before anything else (no preamble such as '
            '"I have what I need"): %s. Then a blank line and the details the brief asks for. Claude reads only '
            'those opening lines unless it needs the details, so they must stand on their own.' % ', '.join(fields))


def status_word(text):
    """done, partial or blocked, by meaning rather than the first word: review-060 (2026-09-25) pointed out that
    "task incomplete - blocked on transport" came out as "task". Anything else is kept, lowercased."""
    words = set(re.findall(r'[a-z]+', text.lower()))
    if words & {'blocked', 'stuck'}:
        return 'blocked'
    if words & {'partial', 'partly', 'incomplete', 'unfinished'}:
        return 'partial'
    if words & {'done', 'complete', 'completed', 'finished'}:
        return 'done'
    return text.strip().lower()[:40]


def parse(text):
    """The opening block of a report: dict(fields..., head=<the block's text>), or None when it has none.
    The block may come after a short preamble or a heading, and fields may be bold or list items."""
    lines = re.sub(r'^\s*<!--.*?-->\s*', '', text or '', flags=re.S).splitlines()
    # The whole block on one line, "Status: done | Verdict: ... | Summary: ..." (seen 2026-09-26):
    # split it before each field name, only on a line that starts with Status.
    lines = [part for line in lines for part in (
        re.split(r'\s*\|\s*(?=(?:\*\*)?(?:Verdict|Summary|Left|Next)\b(?:\*\*)?\s*:)', line)
        if FIELD.match(line) and FIELD.match(line).group(1).lower() == 'status' else [line])]
    start = next((i for i, l in enumerate(lines[:40]) if FIELD.match(l) and FIELD.match(l).group(1).lower() == 'status'), None)
    if start is None:
        return None
    out, block, current = {}, [], None
    for line in lines[start:start + 40]:
        m = FIELD.match(line)
        if m:
            current = m.group(1).lower()
            value = m.group(2).strip().rstrip('*_').strip()
            if current == 'status' and current in out:     # a second Status: the block is over
                break
            if current in out:          # a field given twice ("Summary:" on two lines, MiMo 2026-09-28): one field
                out[current] = (out[current] + '\n' + value).strip()
            else:
                out[current] = value
            block.append(line.rstrip())
        elif line.lstrip().startswith('#'):
            break                       # the details begin
        elif not line.strip():
            if 'summary' in out:
                break                   # the blank line after the block
        elif current:                   # a continuation line of the current field
            out[current] = (out[current] + '\n' + line.strip()).strip()
            block.append(line.rstrip())
    if 'summary' not in out:
        return None
    out['status'] = status_word(out.get('status', ''))
    if out.get('verdict'):
        v = out['verdict'].lower()
        out['verdict'] = next((x for vs in VERDICTS.values() for x in vs if v.startswith(x)), v.split('.')[0][:40])
    out['head'] = '\n'.join(block).strip()
    return out


def _assistant_entries(transcript, since=None):
    """This launch's assistant messages in a transcript (entries at or after `since`), in order."""
    try:
        fh = open(transcript, encoding='utf-8', errors='replace')
    except (OSError, TypeError):
        return
    with fh:
        for line in fh:
            if '"assistant"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            msg = e.get('message') if isinstance(e, dict) else None
            if not isinstance(msg, dict) or msg.get('role') != 'assistant':
                continue
            if since and e.get('timestamp') and parse_time(e['timestamp']) < since:
                continue
            yield msg


def partial_text(transcript, since=None):
    """The text a worker had written in this launch, newest turn last: all that is left of a worker that was killed or
    crashed, since a -p run prints its result only at the end. The same as the launcher's own Save-PartialReport."""
    texts = []
    for msg in _assistant_entries(transcript, since):
        content = msg.get('content')
        text = ''.join(c.get('text') or '' for c in content if isinstance(c, dict) and c.get('type') == 'text') \
            if isinstance(content, list) else ''
        if text.strip():
            texts.append(text.strip())
    return '\n\n'.join(texts)


def save_partial(manifest, why):
    """Keep the partial output of the run in `manifest` (its manifest.json) in its report: a new report marked partial,
    or, after an earlier launch's report (a resume), a section added to it. Returns the report's path, or None when
    the worker had written nothing. A launcher killed from outside (`timeout 5400 powershell ...`, DOA 2026-09-29)
    can't do this itself; the next launch that finds the run does."""
    m = read_json(manifest, {})
    if not isinstance(m, dict) or not m.get('transcript') or not m.get('report'):
        return None
    try:
        since = parse_time(m['started']) if m.get('started') else None
    except ValueError:
        since = None
    text = partial_text(m['transcript'], since)
    if not text.strip():
        return None
    report = Path(m['report'])
    try:
        if report.is_file() and report.stat().st_size:
            with open(report, 'a', encoding='utf-8', newline='\n') as fh:
                fh.write('\n## Partial output of a later launch\n\n*(partial: %s)*\n\n%s\n' % (why, text))
        else:
            report.write_text('<!-- %s (%s): %s -->\n*(partial: %s)*\n\n%s\n' % (
                m.get('run_id'), m.get('kind'), m.get('title'), why, text), encoding='utf-8', newline='\n')
    except OSError:
        return None
    return str(report)


# A provider's content filter: a refusal, not a passing fault (the launcher's refused(provider-filter) check).
FILTERED = re.compile(r'(?i)considered high risk|content (policy|filter)|safety (policy|system)|request was rejected')
# Errors that a second try at the same point meets again: the key, the balance, the request's own shape or size.
LASTING = re.compile(r'(?i)\b40[1-4]\b|insufficient|balance|quota|invalid (api )?key|authenticat|unauthori[sz]ed|forbidden|'
                     r'output token maximum|tool_search|prompt is too long|context (length|window)')


def work_turns(transcript, since=None):
    """The model calls that did work in this launch: assistant messages with real usage, not the synthetic message
    Claude Code writes for an API error."""
    seen = set()
    for msg in _assistant_entries(transcript, since):
        us = msg.get('usage') if isinstance(msg.get('usage'), dict) else {}
        if msg.get('model') == '<synthetic>' or not msg.get('id') or msg['id'] in seen:
            continue
        if sum((us.get(k) or 0) for k in ('input_tokens', 'output_tokens', 'cache_read_input_tokens',
                                           'cache_creation_input_tokens')) > 0:
            seen.add(msg['id'])
    return len(seen)


def api_retry(result, turns):
    """(resume it?, why) for a worker's result and the turns that did work (work_turns): resume once when it ended on
    an API error after doing some work, since such an error mid-run is usually passing (a MiMo coder stopped on "API
    Error: 400 Request failed" after 30 turns and carried on fine when resumed by hand at the same context, DOA
    2026-09-29). Not when its provider's content filter refused it, not when it failed on its first request (a bad
    key, an empty balance, a setting the provider refuses: it would fail the same way again), and not for errors of
    that lasting sort later on. A timeout never gets here: the launcher records that itself."""
    if not isinstance(result, dict) or not result.get('is_error'):
        return False, 'it did not end on an error'
    subtype = result.get('subtype')
    reason = subtype if subtype and subtype != 'success' else result.get('terminal_reason')
    text = str(result.get('result') or '')
    if reason != 'api_error' and not text.startswith('API Error'):
        return False, 'it did not end on an API error'
    if FILTERED.search(text):
        return False, "its provider's content filter refused it"
    if turns < 1:
        return False, 'it failed on its first request, so a second try would fail the same way'
    if LASTING.search(text):
        return False, 'this error would come back on a second try'
    return True, 'it stopped on an API error after %d turn%s' % (turns, '' if turns == 1 else 's')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    n = sub.add_parser('note'); n.add_argument('--kind', default='')
    h = sub.add_parser('head'); h.add_argument('--report', required=True); h.add_argument('--json', action='store_true')
    p = sub.add_parser('partial'); p.add_argument('--manifest', required=True); p.add_argument('--why', required=True)
    r = sub.add_parser('retry'); r.add_argument('--result', required=True); r.add_argument('--transcript', required=True)
    r.add_argument('--since', required=True)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if a.cmd == 'note':
        print(note(a.kind))
        return 0
    if a.cmd == 'partial':
        kept = save_partial(a.manifest, a.why)
        if kept:
            print(kept)
        return 0 if kept else 1
    if a.cmd == 'retry':
        try:
            result = json.loads(Path(a.result).read_text(encoding='utf-8-sig', errors='replace'))
        except (OSError, ValueError):
            result = None
        turns = work_turns(a.transcript, parse_time(a.since))
        retry, why = api_retry(result, turns)
        print(json.dumps(dict(retry=retry, why=why, turns=turns)))
        return 0
    try:
        text = Path(a.report).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return 1
    env = parse(text)
    if not env:
        return 1
    print(json.dumps(dict(env, chars=len(text))) if a.json else env['head'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
