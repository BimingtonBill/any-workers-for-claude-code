"""Move old handoff entries out of AGENTS.md into the archive, leaving an index.

AGENTS.md is imported by CLAUDE.md, so every assistant loads the whole handoff log at
startup. The log is the project's memory and must stay complete and findable, so entries
are moved intact into docs/handoff-archive.md, newest first, and AGENTS.md keeps the
working agreement, the newest entries and a dated index of what was archived.

  python tools/archive_handoffs.py --dry-run     # what would move
  python tools/archive_handoffs.py --keep 2      # keep the newest two entries inline

Run it again whenever the log has grown; it only moves what is newly old.
"""
import os
import argparse
import re
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
AGENTS = ROOT / 'AGENTS.md'
ARCHIVE = ROOT / 'docs' / 'handoff-archive.md'
LOG_HEADING = '## Handoff log'
INDEX_HEADING = '## Archived handoffs'
ARCHIVE_HEADER = ("# Handoff archive\n\nOlder entries from the handoff log in `AGENTS.md`, "
                  "newest first, moved verbatim.\nThe newest entries stay in `AGENTS.md`; "
                  "everything else lives here.\n")


def read(path):
    """Read a file preserving its line endings."""
    with path.open('r', encoding='utf-8', newline='') as handle:
        return handle.read()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as handle:
        handle.write(text)


def split_entries(log_body):
    """Split a handoff log body into entries, each starting at its '### ' heading."""
    parts = re.split(r'(?m)^(?=### )', log_body)
    lead = parts[0] if parts and not parts[0].startswith('### ') else ''
    entries = [part for part in parts if part.startswith('### ')]
    return lead, entries


def entry_title(entry):
    """The heading line of an entry, without the '### '."""
    return entry.splitlines()[0][4:].strip()


def build_index(entries, archive_name):
    lines = [INDEX_HEADING, '',
             'Moved out of this file to keep startup context small. Full text, newest first, in '
             '[`%s`](%s) - search it like any other document.' % (archive_name, archive_name), '']
    for entry in entries:
        lines.append('- %s' % entry_title(entry))
    return '\n'.join(lines) + '\n'


def archive(keep=2, dry_run=False, agents=AGENTS, archive_path=ARCHIVE):
    text = read(agents)
    if LOG_HEADING not in text:
        return 'AGENTS.md has no "%s" section' % LOG_HEADING, 0
    head, log_body = text.split(LOG_HEADING, 1)
    newline = '\r\n' if '\r\n' in text else '\n'

    # An index written by an earlier run sits after the entries; take it off first.
    existing_index = []
    if INDEX_HEADING in log_body:
        log_body, index_part = log_body.split(INDEX_HEADING, 1)
        existing_index = [line.strip()[2:] for line in index_part.splitlines()
                          if line.strip().startswith('- ')]

    lead, entries = split_entries(log_body)
    if len(entries) <= keep:
        return 'nothing to archive: %d entries, keeping %d' % (len(entries), keep), 0

    kept, moved = entries[:keep], entries[keep:]
    if dry_run:
        return 'would archive %d entries, keep %d:\n  %s' % (
            len(moved), len(kept), '\n  '.join(entry_title(entry) for entry in moved)), len(moved)

    old_archive = read(archive_path) if archive_path.exists() else ARCHIVE_HEADER
    if not old_archive.endswith('\n'):
        old_archive += '\n'
    _, archived_before = split_entries(old_archive.split('\n', 1)[-1])
    body_start = old_archive.index('\n\n') + 2 if '\n\n' in old_archive else len(old_archive)
    write(archive_path, old_archive[:body_start].rstrip('\n') + '\n\n'
          + ''.join(moved).rstrip('\n') + '\n\n' + old_archive[body_start:].strip('\n') + '\n')

    index_titles = [entry_title(entry) for entry in moved] + existing_index
    rebuilt = (head + LOG_HEADING + lead + ''.join(kept).rstrip('\n') + '\n\n'
               + build_index([('### ' + title) for title in index_titles],
                             archive_path.relative_to(ROOT).as_posix()))
    write(agents, rebuilt.replace('\r\n', '\n').replace('\n', newline))
    return 'archived %d entries, kept %d inline' % (len(moved), len(kept)), len(moved)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--keep', type=int, default=2, help='entries to leave in AGENTS.md')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    message, _ = archive(args.keep, args.dry_run)
    print(message)
    return 0


if __name__ == '__main__':
    sys.exit(main())
