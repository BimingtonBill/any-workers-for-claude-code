"""Check the file:line and address citations in a markdown note against the repository.

Research notes and handoff notes are required to cite evidence, and about half of the
engine-internal claims in advisor reports have been wrong. This catches the mechanical
half of that: a cited file that does not exist, a line past the end of the file, and an
address that is not inside any function a disassembly export knows about (only when a
function table exists under local/ - OpenSkyrim has none yet, so addresses are not checked).

  python tools/check_citations.py docs/research/auto-switch.md
  python tools/check_citations.py --since HEAD~1        # every .md changed since a commit

Exit code 1 when a citation is broken. Two things are warnings rather than errors: an
address that is not inside a known function (it can be data, or a function the export
missed), and a bare file name that several files in the repository answer to - which
matters here, because every crate has its own `lib.rs`, `mod.rs` and `Cargo.toml`.
"""
import argparse
import os
import re
import subprocess
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

# "src/foo.cpp:120" or "docs/a.md:10-20". Paths are repo-relative, with / or \.
CITATION = re.compile(r'(?<![\w/\\.:])([\w][\w./\\-]*\.[A-Za-z][\w]{0,7}):(\d+)(?:-(\d+))?\b')
# A virtual address: at least six hex digits, so 0x2b and 0xE4 offsets are left alone.
ADDRESS = re.compile(r'0x([0-9a-fA-F]{6,8})\b')
# Where the function tables live, per platform.
FUNCTION_TABLES = ('local/pc/native-full/functions.tsv', 'local/xbox/native-full/functions.tsv')


def find_citations(text):
    """Yield (path, first_line, last_line) for every file:line citation in text."""
    for match in CITATION.finditer(text):
        path, first, last = match.group(1), int(match.group(2)), match.group(3)
        if path.startswith(('http:', 'https:')):
            continue
        yield path, first, int(last) if last else first


def find_addresses(text):
    """Yield every address in the text as an int, de-duplicated, in order."""
    seen = set()
    for match in ADDRESS.finditer(text):
        value = int(match.group(1), 16)
        if value not in seen:
            seen.add(value)
            yield value


def load_functions(root=ROOT, tables=FUNCTION_TABLES):
    """Read the exported function ranges as a list of (entry, end, name)."""
    ranges = []
    for relative in tables:
        table = Path(root) / relative
        if not table.exists():
            continue
        with table.open(encoding='utf-8', errors='replace') as handle:
            header = handle.readline().rstrip('\n').split('\t')
            try:
                entry_at, end_at, name_at = header.index('entry'), header.index('end'), header.index('name')
            except ValueError:
                continue
            for row in handle:
                columns = row.rstrip('\n').split('\t')
                if len(columns) <= max(entry_at, end_at, name_at):
                    continue
                try:
                    ranges.append((int(columns[entry_at], 16), int(columns[end_at], 16), columns[name_at]))
                except ValueError:
                    continue
    ranges.sort()
    return ranges


SKIP_DIRS = {'.git', '__pycache__', 'node_modules', 'target'}


def build_index(root=ROOT):
    """Map every file name in the repository to the repo-relative paths that carry it.

    A bare file name like `CharGen.as:89` is not a usable citation here, because the PC
    and the Xbox tree both hold one; the index is what turns that into a named warning.
    """
    index = {}
    root = Path(root)
    implementation_checkouts = (root / 'local' / 'impl').resolve()
    for folder, subfolders, names in os.walk(root):
        subfolders[:] = [s for s in subfolders if s not in SKIP_DIRS]
        # A worker's checkout is a copy of the repository; indexing it would make every
        # file in it look like a second file of the same name.
        if Path(folder).resolve() == implementation_checkouts:
            subfolders[:] = []
            continue
        for name in names:
            index.setdefault(name, []).append(str(Path(folder, name).relative_to(root)).replace('\\', '/'))
    return index


def count_lines(target):
    try:
        with target.open('rb') as handle:
            return sum(1 for _ in handle)
    except OSError:
        return None


def line_text(target, number):
    """The text of one line, for comparing candidates of the same file name."""
    try:
        with target.open('r', encoding='utf-8', errors='replace') as handle:
            for index, line in enumerate(handle, 1):
                if index == number:
                    return line.strip()
    except OSError:
        return None
    return None


def check_path(path, first, last, root=ROOT, index=None):
    """Return (problem, note): problem is None when the citation is sound.

    note carries a warning worth printing even when the citation itself resolves.
    """
    root = Path(root)
    if first < 1:
        return 'line %d is not a line number' % first, None
    clean = path.replace('\\', '/')
    if '/' in clean:
        target = root / clean
        # A worker-written note must not make this read files outside the project ("../../x", or an absolute
        # path, which pathlib's / would take as the whole path): OpenSkyrim audit finding HT-20260924-05.
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError:
            return 'outside the project', None
        if not target.exists():
            return 'no such file', None
        if target.is_dir():
            return 'is a directory, not a file', None
        lines = count_lines(target)
        if lines is None:
            return 'unreadable', None
        if last > lines:
            return 'line %d, but the file has %d lines' % (last, lines), None
        return None, None

    # A bare file name: resolve it, and say so when the answer is not unique.
    candidates = (index or {}).get(clean, [])
    if not candidates:
        return 'no such file', None
    fitting = [c for c in candidates if (count_lines(root / c) or 0) >= last]
    if not fitting:
        return 'line %d is past the end of every %s in the repository' % (last, clean), None
    # Several copies only matter when the cited line is not the same in all of them:
    # two exports of one function are harmless, the PC and Xbox copies of a script are not.
    if len(fitting) > 1 and len({line_text(root / c, first) for c in fitting}) > 1:
        return None, 'ambiguous: line %d differs between %d files named %s (%s) - cite a repo-relative path' % (
            first, len(fitting), clean, ', '.join(fitting[:3]))
    return None, None


def code_spans(ranges, gap=0x100000):
    """Merge the function ranges into the code regions they cover, one per platform.

    Anything outside them - the image base, a global, an object offset, a console
    address when that export is missing - is not a claim about code, so it is not
    something this tool can check and not something it should complain about.
    """
    spans = []
    for entry, end, _ in ranges:
        if spans and entry - spans[-1][1] <= gap:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([entry, end])
    return [tuple(span) for span in spans]


def check_address(address, ranges, spans=None):
    """Return (ok, note) for one address against the exported function ranges."""
    spans = code_spans(ranges) if spans is None else spans
    if not any(low <= address < high for low, high in spans):
        return True, 'outside the exported code'
    for entry, end, name in ranges:
        if entry <= address < end:
            return True, name
        if entry > address:
            break
    return False, 'inside the code but not in any exported function'


def check_file(note, root=ROOT, ranges=None, index=None):
    """Check one note. Returns (errors, warnings) as lists of strings."""
    ranges = load_functions(root) if ranges is None else ranges
    text = Path(note).read_text(encoding='utf-8', errors='replace')
    relative = Path(note).resolve()
    try:
        relative = relative.relative_to(Path(root).resolve())
    except ValueError:
        pass
    errors, warnings = [], []
    for path, first, last in find_citations(text):
        problem, warning = check_path(path, first, last, root, index)
        if problem:
            errors.append('%s: %s:%d - %s' % (relative, path, first, problem))
        elif warning:
            warnings.append('%s: %s:%d - %s' % (relative, path, first, warning))
    if ranges:
        spans = code_spans(ranges)
        for address in find_addresses(text):
            ok, note_text = check_address(address, ranges, spans)
            if not ok:
                warnings.append('%s: 0x%08x - %s' % (relative, address, note_text))
    return errors, warnings


def changed_markdown(since, root=ROOT):
    """The .md files changed since a git revision, as paths that exist now."""
    out = subprocess.run(['git', '-C', str(root), 'diff', '--name-only', since, 'HEAD'],
                         capture_output=True, text=True)
    files = []
    for name in out.stdout.splitlines():
        if name.lower().endswith('.md') and (Path(root) / name).exists():
            files.append(str(Path(root) / name))
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('notes', nargs='*', help='markdown files to check')
    parser.add_argument('--since', help='also check every .md changed since this git revision')
    parser.add_argument('--quiet', action='store_true', help='print only problems')
    args = parser.parse_args(argv)

    notes = list(args.notes)
    if args.since:
        notes += changed_markdown(args.since)
    if not notes:
        parser.error('name at least one file, or pass --since <rev>')

    ranges = load_functions()
    index = build_index()
    errors, warnings = [], []
    for note in notes:
        note_errors, note_warnings = check_file(note, ROOT, ranges, index)
        errors += note_errors
        warnings += note_warnings

    for line in errors:
        print('BROKEN  ' + line)
    for line in warnings:
        print('check   ' + line)
    if not args.quiet:
        print('%d file(s): %d broken citation(s), %d to check by hand.'
              % (len(notes), len(errors), len(warnings)))
        if not ranges:
            print('note: no function table found, so addresses were not checked.')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
