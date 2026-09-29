"""Check a worker's handoff note for the things the lead builds on.

Every worker leaves `docs/handoff/<task>.md` and the lead reads it instead of the worker's
transcript, so a note that reads finished but never says what was tested - or cites a file
that is not there - is worse than none: it looks done. This checks the mechanical half of
that. A sound note says what was verified and what was not, a claim of testing has a
command, a log line, a round number or a file behind it, no placeholder was left in, and
every `path:line` resolves. The last is `tools/check_citations.py`, imported rather than
repeated, and its findings are reported here as they come.

  python tools/check_handoff.py docs/handoff/t53-menu-open-cost.md
  python tools/check_handoff.py --all                # every note in docs/handoff
  python tools/check_handoff.py --all --quiet        # problems only

Exit code 1 when a note has a problem, 0 when none does. The two answers are kept apart: a
*problem* is a line the note has to answer for, and a *note* is a judgement the lead may
overrule and never changes the exit code. check_handoff and check_all return problems
alone, so `if check_handoff(path)` says the note is faulty; check_handoff_notes returns the
judgements, which the CLI prints prefixed "note: ".

The rules are deliberately lenient, because a rule that fires on a good note trains the
lead to ignore the tool. The notes in docs/handoff are the specification, they vary, and
substance decides rather than headings: "verified", "not tested", "open", "could not",
"inferred" count wherever they sit, in a heading or in half a sentence. Six shapes that
look like problems from the brief are not:

  * "..." is a placeholder only when it is the whole of a line, or of a table cell, of the
    note's own writing. `.../827c69d0.asm.txt:1465` is a citation with its folder
    abbreviated, `[A] OK [B] Back ...` is a list that stops - and a log line quoted in a
    fence prints `...` of its own accord (t07:73).
  * a bare file name that several files in the repository answer to is a citation to check
    by hand, not a broken one - tools/check_citations.py's own distinction, kept here.
  * a claim of testing is looked for in the prose only: a log line quoted in a fence is
    evidence, not a claim.
  * `local/` and `logs/` are Git-ignored, so a checkout made from a commit carries neither.
    There, a citation that cannot resolve - into those trees, or a bare file name, which
    the incomplete index cannot place - is reported as a note with a reason, not as a
    problem its author could act on. In the working copy, where both trees exist, every
    such citation is checked and broken ones are problems, as they should be.
  * a citation whose path writes a folder as "..." is a note in either checkout, because
    neither can resolve it: `local/pc/.../__Packages/OptionsMenu.as:276` (t02:136, t13:91,
    t44:17) abbreviates a folder the note does not name, and check_citations cannot see the
    leading form at all (`.../827c69d0.asm.txt:1465`). Reporting it as a problem would make
    the same citation a fault where local/ is on disk and a note where it is not.
  * a heading with nothing under it is a note, not a problem; some notes leave a heading
    where a section moved elsewhere.
"""
import os
import argparse
import re
import sys
from pathlib import Path

try:                                    # imported as tools.check_handoff
    from tools import check_citations
except ImportError:                     # run as a script from tools/
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import check_citations

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
DEFAULT_FOLDER = 'docs/handoff'
# Git-ignored trees of extracted game content and round output, read on disk but never
# committed, so a checkout made from a commit has neither (AGENTS.md). The first is the one
# the corpora live under, so it is also the one that decides whether a bare file name can be
# placed at all.
IGNORED_TREES = ('local', 'logs')
NAME_TREE = IGNORED_TREES[0]

# A judgement the lead may overrule is printed with this, and never counts as a problem.
# The findings themselves are already split by then: check_handoff returns problems only.
NOTE_PREFIX = 'note: '
# "an empty note, or one under ~10 lines of substance" (the brief). Headings, rules and
# table separators are not substance, so a note of ten headings is still a thin note.
MIN_LINES = 10

# A heading, as this repository's notes write them: `## What is verified`. Setext headings
# (`What is verified` over `===`) are allowed too, because markdown allows them; no note
# here uses one.
ATX = re.compile(r'^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$')
SETEXT = re.compile(r'^[ \t]*(=+|-+)[ \t]*$')
FENCE = re.compile(r'^[ \t]*(?:```|~~~)')
# The lines a setext underline cannot sit under: a list item, a table row, a quote.
STRUCTURAL = re.compile(r'^[ \t]*(?:[-*+]|\d+[.)]|\||>|#)')

# Words that say something was checked. Presence is the test, so this errs wide: a note
# that says "tested" and gives nothing to follow is caught by the claim rule below, not by
# this one.
VERIFIED = re.compile(
    r'\b(?:verified|verifies|confirmed|confirms|measured|reproduced|tested|exercised|'
    r'checked|validated|re-checked|proven|settled|observed|witnessed|evidence|pass(?:es|ed)|'
    r'works?|working|unit-tested|smoke-tested|in-game|no regressions)\b', re.I)
# Words that say something was not, or is not known to be. Both a heading and a sentence
# carry them: "## Still unknown", "**Not measured:**", "is open", "inferred, not proven".
UNTESTED = re.compile(
    r'\b(?:untested|unverified|unproven|unconfirmed|unknown|uncertain|unclear|unresolved|'
    r'unchecked|unmeasured|unexercised|unseen|inferred|tentative|'
    r'not\s+(?:yet\s+)?(?:tested|verified|checked|measured|proven|established|settled|'
    r'exercised|confirmed|done|seen|run|installed|possible)|'
    r'never\s+(?:tested|verified|checked|run|exercised|seen|measured)|'
    r'could\s+not\s+\w+|cannot\s+\w+|unable\s+to\s+\w+|'
    r"isn't\s+(?:tested|verified|known)|"
    r'no\s+(?:evidence|proof)|'
    r'is\s+(?:still\s+)?open|\(\s*open\s*\)|'
    r'remains?\s+(?:open|unknown|unverified|unproven|unclear|to\s+be)|'
    r'still\s+(?:open|unknown|unverified|unproven|to\s+\w+|need\w*|wait\w*)|'
    r'open\s+(?:questions?|items?|issues?|points?|problems?|threads?)|'
    r'left\s+(?:open|untested)|blocked|outstanding|unanswered|'
    r'to\s+be\s+(?:tested|checked|verified|proven|confirmed|determined|decided|seen))\b', re.I)
# A heading that names what is open or blocked in a word or two: t27-gui's "## Open", t54's
# "## Still open", "## Remaining". It answers both questions a heading can - is the untested
# part named, and is the next work named - so one pattern serves both rules; only a heading
# is asked, because "open" in a sentence is usually the verb ("the ring opens").
OPEN_HEADING = re.compile(r'^(?:what\s+(?:is|remains)\s+)?(?:still\s+|remains\s+)?'
                          r'(?:open|unknown|blocked|unresolved|remaining|left|'
                          r'to\s+(?:do|check|probe|try|prove))\b', re.I)
UNTESTED_HEADING = OPEN_HEADING
# The other things a handoff has, reported as notes rather than problems: what was done,
# and what to do next.
DONE = re.compile(r'\b(?:changed|added|wrote|written|built|implemented|delivered|deliverables?|'
                  r'committed|created|fixed|measured|ran|result|outcome|done|surveyed|produced)\b',
                  re.I)
# Notes here say what they produced by labelling the list: "Files: ...", "Research note:
# ...", "Deliverables: ...", "Helper kept on disk: ...". That is a statement of what was
# done, and a research note often has no other one (t35, t44).
DONE_LABEL = re.compile(r'(?m)^\s*(?:[-*+]\s+)?(?:\*\*)?(?:files?|deliverables?|research\s+note|'
                        r'helper|artifacts?|outputs?|what\s+i\s+did|changes?|commits?|state)\b'
                        r'[^\n:]{0,24}:', re.I)
NEXT = re.compile(r'\b(?:next|follow[- ]?up|to\s+do|suggested|recipe|for\s+the\s+(?:lead|reviewer|'
                  r'implementer|user)|after\s+that|step\s+after|test\s+steps?|restore|'
                  r'to\s+be\s+run)\b', re.I)
# A claim that something was tested. Narrower than VERIFIED: this one can cost the note a
# problem, so it only holds words that claim an act of checking.
CLAIM = re.compile(r'\b(?:verified|confirmed|tested|exercised|checked|validated|reproduced|'
                   r'measured|proven|works?|working|passes|passing)\b', re.I)
# "not verified", "never tested", "could not be checked": a negation in front of the match
# is what makes it a statement about the untested part instead of a claim.
NEGATED = re.compile(r'\b(?:not|never|no|none|nothing|cannot|can\'t|isn\'t|wasn\'t|weren\'t|'
                     r"aren't|don't|doesn't|didn't|unable|without|failed|nor|neither)\b"
                     r'[^.;!?\n]{0,32}$', re.I)

# A placeholder left in an unfinished note.
PLACEHOLDER = re.compile(r'\b(?:TODO|TBD|FIXME|XXX|N/?A)\b'
                         r'|<\s*(?:fill[- ]?in|insert|todo|tbd|placeholder|your\s+\w+)[^>]*>'
                         r'|\[\s*(?:fill[- ]?in|todo|tbd|insert)[^\]]*\]'
                         r'|lorem ipsum', re.I)

# What a reader can follow to check a claim of testing: a cited path or line, an address, a
# command, a round number ("r85"), or a line quoted out of a log. Deliberately wide - the
# claim rule exists for the note that has none of it.
CITED_LINE = re.compile(r'[\w./\\-]*\w\.[A-Za-z]\w{0,7}:\d+')
CITED_PATH = re.compile(r'(?<![\w/\\])(?:[\w.-]+[/\\])+[\w.-]+\.[A-Za-z]\w{0,7}\b')
ADDRESS = re.compile(r'\b0x[0-9a-fA-F]{4,}\b')
COMMAND = re.compile(r'(?<![\w-])(?:python3?|py|powershell|pwsh|git|nmake|msbuild|java|node|'
                     r'npm|gcc|clang|cmake|dumpbin|unzip|curl|7z|unittest)\s+\S', re.I)
ROUND = re.compile(r'\br\d{1,3}\b', re.I)
QUOTED = re.compile(r'`([^`\n]+)`')

# check_citations' function ranges and its file-name index, loaded once per root: building
# the index walks the whole repository, and --all would otherwise repeat that per note.
_TABLES = {}


def atx_heading(line):
    """(name, level) for an ATX heading line, or (None, None) when it is not one."""
    match = ATX.match(line)
    if not match:
        return None, None
    name = re.sub(r'[ \t]+#+$', '', (match.group(2) or '').strip()).strip()
    return name, len(match.group(1))


def is_paragraph(line):
    """True when a setext underline could sit under this line."""
    if line is None or not line.strip() or SETEXT.match(line):
        return False
    return not STRUCTURAL.match(line)


def add_line(bodies, open_names, preamble, line):
    """Give a line to every heading it is under, or to the preamble when it is under none."""
    if not open_names:
        preamble.append(line)
        return
    for _, name in open_names:
        bodies.setdefault(name, []).append(line)


def take_back(bodies, open_names, preamble):
    """Un-add the last line: a setext underline makes it the heading, not the body."""
    if not open_names:
        if preamble:
            preamble.pop()
        return
    for _, name in open_names:
        if bodies.get(name):
            bodies[name].pop()


def sections(text):
    """The note's sections as {heading: body}, the text before the first heading under ''.

    Headings are read by shape - ATX at any level, or a paragraph underlined with === or
    --- - rather than against a list of the ones these notes use. A heading's body runs to
    the next heading of the same or a higher level, so a `##` owns its `###` subsections:
    t03's "## Manual test steps" carries the three procedures under it rather than an empty
    body. A heading repeated in one file merges its bodies rather than losing one (t11
    carries a copy of t07 under a second title). A heading with nothing under it keeps an
    empty body, which is how a caller sees it.
    """
    bodies = {}                 # name -> the lines under it, in the order they appear
    open_names = []             # (level, name) of the headings the current line is under
    preamble = []               # the lines before the first heading
    fenced = False
    previous = None
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            add_line(bodies, open_names, preamble, line)
            previous = line
            continue
        name, level = (None, None) if fenced else atx_heading(line)
        if name is None and not fenced and SETEXT.match(line) and is_paragraph(previous):
            name = previous.strip()
            level = 1 if line.strip().startswith('=') else 2
            take_back(bodies, open_names, preamble)
        if name is None:
            add_line(bodies, open_names, preamble, line)
        else:
            while open_names and open_names[-1][0] >= level:
                open_names.pop()
            open_names.append((level, name))
            if name not in bodies:
                bodies[name] = []
            else:                               # a heading the note uses twice: merge the two
                while bodies[name] and not bodies[name][-1].strip():
                    bodies[name].pop()
        previous = line

    found = {}
    if any(line.strip() for line in preamble):
        found[''] = '\n'.join(preamble).strip()
    for name, body in bodies.items():
        found[name] = '\n'.join(body).strip()
    return found


def substantive_lines(text):
    """The note's content lines: the lines of its sections, headings and blanks left out.

    A line is counted once however many headings it is under, so the nesting above cannot
    inflate the count.
    """
    lines, seen = [], set()
    for body in sections(text).values():
        for line in body.splitlines():
            clean = line.strip()
            if re.search(r'[A-Za-z0-9]', clean) and clean not in seen:
                seen.add(clean)
                lines.append(clean)
    return lines


# A fenced block, both of markdown's markers: a log excerpt or a command's output. The
# closing run is the one that opened the block, so ``` inside a ~~~ block does not end it.
FENCED = re.compile(r'(```|~~~).*?(?:\1|\Z)', re.S)


def mask_code(text):
    """The prose of the note, with fenced blocks and code spans blanked out.

    Same length and the same lines as the original, so an offset found here can be turned
    back into the line a reader would open. Quoted code is evidence rather than a claim or a
    placeholder, so the claim rule and the word half of the placeholder rule read this; the
    other rules read the note as it stands. Both fences are read here, as they are by the
    heading rule and the line half of the placeholder rule.
    """
    def blank(match):
        return re.sub(r'[^\n]', ' ', match.group(0))

    text = FENCED.sub(blank, text)
    return re.sub(r'`[^`\n]*`', blank, text)


def line_at(text, index):
    """The whole line of text holding an offset into the masked copy of it."""
    start = text.rfind('\n', 0, index) + 1
    end = text.find('\n', index)
    return text[start:end if end != -1 else len(text)]


def short(text, limit=60):
    """A fragment quoted in a finding, collapsed and cut so one finding stays one line."""
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit - 3] + '...'


def states(text, pattern):
    """The first match of pattern whose own use is not negated, or None.

    "not verified", "never measured", "could not be checked" are statements about what is
    untested, so they must not satisfy a rule that asks whether something was verified.
    """
    for match in pattern.finditer(text):
        if not NEGATED.search(text[max(0, match.start() - 40):match.start()]):
            return match
    return None


def has_backing(text):
    """True when the note carries something a reader can follow to check a claim."""
    if CITED_LINE.search(text) or CITED_PATH.search(text) or ADDRESS.search(text):
        return True
    if COMMAND.search(text) or ROUND.search(text):
        return True
    for block in FENCED.finditer(text):
        if any(len(line.split()) >= 3 for line in block.group(0).splitlines()):
            return True
    return any(len(span.split()) >= 3 for span in QUOTED.findall(text))


def only_dots(text):
    """True when a run of dots is all this piece of the note says."""
    return bool(re.search(r'\.{3,}|…', text)) and not re.search(r'[A-Za-z0-9]',
                                                               re.sub(r'\.{3,}|…', '', text))


def first_placeholder(text):
    """The first placeholder left in the note's own writing, as text to quote back, or None.

    "..." counts only where it is the whole of a line - `.../827c69d0.asm.txt:1465` is a
    path with its folder abbreviated, `[A] OK [B] Back ...` is a list that stops, and notes
    here write both constantly - or the whole of one table cell, `| Store | ... |`. A fence
    is left alone either way, because a log excerpt prints `...` of its own accord (t07:73).
    """
    found = PLACEHOLDER.search(mask_code(text))
    if found:
        return found.group(0)
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        if only_dots(line):
            return line.strip()
        cells = line.split('|')[1:-1]           # a table row: every cell but the two ends
        if cells and any(only_dots(cell) for cell in cells):
            return line.strip()
    return None


def citation_tables(root):
    """check_citations' function ranges and file index, loaded once per root."""
    key = str(Path(root).resolve())
    if key not in _TABLES:
        _TABLES[key] = (check_citations.load_functions(root), check_citations.build_index(root))
    return _TABLES[key]


def ignored_trees_missing(root):
    """The Git-ignored trees this root does not have: a checkout made from a commit."""
    return [name for name in IGNORED_TREES if not (Path(root) / name).exists()]


def unfixable_citation(error, missing):
    """True when a "no such file" is one this checkout cannot settle either way.

    With `local/` absent, a citation into it cannot resolve, and a bare file name cannot be
    placed either, because the index that tree would fill is incomplete. In the working
    copy, where the trees are, this is False and every citation is checked as usual.
    """
    if not missing or 'no such file' not in error:
        return False
    for path, _, _ in check_citations.find_citations(error):
        parts = path.replace('\\', '/').split('/')
        if len(parts) == 1:                 # a bare file name: it may live under a missing tree
            return NAME_TREE in missing
        if parts[0] in missing:
            return True
    return False


def abbreviated_citation(error):
    """True when the citation it reports writes a folder as "...".

    `local/pc/.../__Packages/OptionsMenu.as:276` names a folder the note does not spell out,
    so no checkout can resolve it - it is a citation to open by hand, not a broken one, and
    it must not be a problem where local/ happens to be on disk and a note where it is not.
    """
    return any('...' in path for path, _, _ in check_citations.find_citations(error))


def citation_findings(path, root):
    """(problems, notes) from tools/check_citations.py for one note, prefixed 'citation'.

    A note that cites extracted game content a dozen times is one finding here, not a
    dozen: the reader of the note cannot do anything about a missing checkout, and one
    line per citation would bury the findings that are about the note.
    """
    ranges, index = citation_tables(root)
    errors, warnings = check_citations.check_file(path, root, ranges, index)
    missing = ignored_trees_missing(root)
    problems, unchecked, abbreviated = [], [], []
    for error in errors:
        if abbreviated_citation(error):
            abbreviated.append(error)
        elif unfixable_citation(error, missing):
            unchecked.append(error)
        else:
            problems.append('citation: %s' % error)
    notes = ['citation: %s' % warning for warning in warnings]
    if unchecked:
        notes.append('%d citation(s) not checked: this checkout has no %s (Git-ignored), so '
                     'citations into it, and bare file names, cannot resolve (e.g. %s)'
                     % (len(unchecked), ' or '.join('%s/' % name for name in missing),
                        short(unchecked[0], 80)))
    if abbreviated:
        notes.append('%d citation(s) not checked: the path abbreviates a folder as "..." '
                     '(e.g. %s)' % (len(abbreviated), short(abbreviated[0], 80)))
    return problems, notes


def findings(path, root=ROOT):
    """(problems, notes) for one handoff note; ([], []) when the note is sound.

    A problem is a line the note has to answer for; a note is a judgement the lead may
    overrule - a missing "what to do next", a citation only a human can settle, a heading
    with nothing under it. They are answered differently, which is why the two callers
    below hand them out separately rather than mixing them into one list.
    """
    path = Path(path)
    root = Path(root)
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError as error:
        return ['the note cannot be read: %s' % error], []

    if not text.strip():
        return ['the note is empty'], []

    problems, notes = [], []
    content = substantive_lines(text)
    if len(content) < MIN_LINES:
        problems.append('the note is %d line(s) of substance; a handoff needs about %d'
                        % (len(content), MIN_LINES))

    found = sections(text)
    if not states(text, VERIFIED):
        problems.append('no statement of what was verified')
    if not UNTESTED.search(text) and not any(UNTESTED_HEADING.match(name) for name in found):
        problems.append('no statement of what is untested or left open')

    prose = mask_code(text)
    claim = states(prose, CLAIM)
    if claim and not has_backing(text):
        problems.append('a claim of testing with no command, log line, round number or file '
                        'behind it: %r' % short(line_at(text, claim.start())))

    placeholder = first_placeholder(text)
    if placeholder:
        problems.append('placeholder text left in: %r' % short(placeholder))

    broken, to_check = citation_findings(path, root)
    problems += broken
    notes += to_check

    if not DONE.search(text) and not DONE_LABEL.search(text):
        notes.append('nothing says what was done')
    headings = [name for name in found if name]
    if not NEXT.search(text) and not (headings and OPEN_HEADING.match(headings[-1])):
        notes.append('nothing says what to do next')
    for name, body in found.items():
        if name and not body.strip():
            notes.append('the heading %r has nothing under it' % short(name))

    return problems, notes


def check_handoff(path, root=ROOT):
    """Every problem with one handoff note; [] when the note is sound.

    Problems alone, as the brief fixes it: a caller can read the list itself as the answer,
    and a note that is merely thin on "what to do next" does not come back as faulty.
    """
    return findings(path, root)[0]


def check_handoff_notes(path, root=ROOT):
    """The judgements about one note, as the CLI prints them after "note: "; [] when none.

    None of them is a fault: each is something the lead may want to look at by hand, and
    any of them may be overruled without the note being wrong.
    """
    return findings(path, root)[1]


def check_all(folder=None, root=ROOT):
    """Every `*.md` note in a folder (docs/handoff by default), as {path: [problems]}.

    A note with nothing wrong keeps an empty list here, so a caller can count what was read
    as well as what was wrong.
    """
    root = Path(root)
    folder = Path(folder) if folder is not None else root / DEFAULT_FOLDER
    return {str(note): check_handoff(note, root) for note in sorted(folder.glob('*.md'))}


def display(path, root):
    """A path as the lead reads it: relative to the repository when it is inside it."""
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve())).replace('\\', '/')
    except ValueError:
        return str(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('notes', nargs='*', help='handoff notes to check')
    parser.add_argument('--all', action='store_true',
                        help='check every note in %s' % DEFAULT_FOLDER)
    parser.add_argument('--quiet', action='store_true', help='print only the problems')
    args = parser.parse_args(argv)

    if args.all and args.notes:
        parser.error('name notes or pass --all, not both')
    if args.all:
        checked = check_all(None, ROOT)
    elif args.notes:
        checked = {str(note): check_handoff(note, ROOT) for note in args.notes}
    else:
        parser.error('name at least one note, or pass --all')

    problems = to_check = 0
    for path, broken in checked.items():
        notes = [] if args.quiet else check_handoff_notes(path, ROOT)
        if broken:
            print(display(path, ROOT))
            for line in broken:
                print('  PROBLEM ' + line)
        if notes:
            if not broken:
                print(display(path, ROOT))
            for line in notes:
                print('  ' + NOTE_PREFIX + line)
        problems += len(broken)
        to_check += len(notes)

    if not args.quiet:
        print('%d note(s): %d problem(s), %d to look at by hand.'
              % (len(checked), problems, to_check))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
