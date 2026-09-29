"""The delegation level: how much Claude hands to DeepSeek workers, from 1 (only when asked) to 5 (everything).

    python tools/ds_delegation.py                     the effective level here, where it comes from, and what it means
    python tools/ds_delegation.py --set 4             set it for this project (.deepseek-agents.json "delegationLevel")
    python tools/ds_delegation.py --set 3 --global    set your default for every project (~/.claude-deepseek/config.json)
    python tools/ds_delegation.py --set 4 --agents-md also write the level's rules into this project's AGENTS.md
    python tools/ds_delegation.py --table             all five levels

Where the level comes from, first match wins: the DS_DELEGATION_LEVEL environment variable, the project's
.deepseek-agents.json, ~/.claude-deepseek/config.json, else 3.

The scale used to run from 0 to 10, stored as "delegation" (and DS_DELEGATION). Those still work: when
no 1-5 value is set at the same place, an old value is read and mapped (0-2 -> 1, 3-4 -> 2, 5-6 -> 3,
7-8 -> 4, 9-10 -> 5). --set writes the new key and drops the old one.

The level steers Claude, not the workers, so Claude has to see it. The deepseek-agents skill reads it,
but the skill only loads when workers come up. --agents-md puts the rules in the project's AGENTS.md,
which Claude reads at the start of every session; without it, levels above 3 can't make Claude reach
for workers unprompted.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

DEFAULT = 3
GLOBAL_CONFIG = Path.home() / '.claude-deepseek' / 'config.json'
# Where tools/install-skill.ps1 puts this script.
INSTALLED_TOOL = Path.home() / '.claude' / 'skills' / 'deepseek-agents' / 'tools' / 'ds_delegation.py'
BEGIN = '<!-- ds-delegation:begin (managed by DeepSeek Workers/tools/ds_delegation.py; edit with --set) -->'
END = '<!-- ds-delegation:end -->'

# What Claude keeps at every level: quality does not scale down with the dial.
ALWAYS = ('At every level Claude keeps: talking to the user, design and architecture decisions, '
          'integration into the main checkout, security-sensitive changes, and review of every worker '
          'result it acts on (spot-check the claims that matter; read the full diff of any edit). '
          'Whenever workers are running, Claude works in parallel on something that cannot collide with '
          'them (the next brief, integration plumbing, the previous review, its own tests and builds), '
          'and starts its own long builds and conversions in the background too; a worker run is not '
          'a wait.')
# From level 2 up, web lookups are a standard delegated step rather than Claude's own searching: a
# websearch worker has no file access, so a bad page can't reach the project (docs/design/websearch-injection.md).
WEB_LOOKUP_FROM = 2
WEB_LOOKUP = ('Web lookups: when a task needs information from outside the project (library or tool versions, '
              'changelogs, API documentation, an error message, how others solved something), send a DeepSeek '
              'websearch worker (-Kind websearch) instead of searching yourself; only a single quick search you '
              'need this minute is yours. Start it as soon as the question comes up and keep working while it '
              'runs. Give it everything it needs in the brief, nothing private, and check the claims you act on.')
# Shown in every generated AGENTS.md block, so every session sees it before it plans.
MISSING_TOOLS = ('Missing tools: before starting a job, check that the tools it needs are installed. If a compiler, '
                 'runtime or package the best approach needs is missing, stop and ask the user to install it (say '
                 'what it is for and give the exact install command) instead of working around it with a weaker '
                 'approach or a hand-written substitute. Continue without it only if the user chooses to.')

LEVELS = {
    1: ('Only when asked', 'Claude does the work itself and launches a worker only when the user asks for '
                           'one in this conversation.'),
    2: ('Research and review', 'Delegate research that takes more than a few searches, large read-only '
                               'surveys (many files, big dumps, long logs), independent reviews and '
                               'analyses of finished runs. Claude writes all code.'),
    3: ('Balanced (default)', 'As 2, plus bounded, self-contained implementation (a new parser, validator, '
                              'tool or test file with a fixed interface) through tools/ds_impl.ps1, and any '
                              'task that can be written as a standalone brief with a clear check. Run '
                              'independent tasks in parallel, with an independent reviewer on implementation. '
                              'Claude keeps anything touching shared interfaces or more than a few files.'),
    4: ('Claude manages', 'Claude plans, writes briefs, reviews and integrates; workers do the exploration '
                          'and most coding, including multi-file changes in their own worktrees with '
                          'owned-file lists. Claude avoids reading source beyond what review needs: ask a '
                          'worker to locate, summarize or trace code instead. Keep several workers busy, and '
                          'use a DeepSeek lead (-CanSpawn) for jobs that split into independent parts.'),
    5: ('Everything', 'Claude only orchestrates: every task, even small edits and lookups, goes to a worker. '
                      'Claude reads briefs, reports and diffs, integrates, and talks to the user. If no '
                      'worker can do a step (it needs this conversation, a GUI, or the user), Claude does it '
                      'and says why.'),
}
KEY, LEGACY_KEY = 'delegationLevel', 'delegation'
ENV, LEGACY_ENV = 'DS_DELEGATION_LEVEL', 'DS_DELEGATION'


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


try:        # launcher/ds_common.py: in the harness beside tools/'s parent, installed in the skill folder
    import ds_state  # noqa: E402
    ds_state.use_launcher()
    from ds_common import read_json  # noqa: E402
except (ImportError, AttributeError):     # this tool copied alone into a project's tools/
    def read_json(path, default=None):
        try:
            return json.loads(Path(path).read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            return default


def _read_json(path):
    """A JSON file's content, or None when it is missing or not JSON."""
    return read_json(path, None)


def _valid(value):
    try:
        level = int(value)
    except (TypeError, ValueError):
        return None
    return level if 1 <= level <= 5 else None


def from_legacy(value):
    """An old 0-10 level on the 1-5 scale, or None."""
    try:
        old = int(value)
    except (TypeError, ValueError):
        return None
    return (1, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5)[old] if 0 <= old <= 10 else None


def effective(project, env=None, global_config=None):
    """(level, source) for a project folder."""
    env = os.environ if env is None else env
    global_config = GLOBAL_CONFIG if global_config is None else Path(global_config)
    level = _valid(env.get(ENV))
    if level is None:
        level = from_legacy(env.get(LEGACY_ENV))
    if level is not None:
        return level, 'environment variable'
    for path in (Path(project) / '.deepseek-agents.json', global_config):
        config = _read_json(path) or {}
        level = _valid(config.get(KEY))
        if level is None:
            level = from_legacy(config.get(LEGACY_KEY))
        if level is not None:
            return level, str(path)
    return DEFAULT, 'default'


def describe(level):
    name, rule = LEVELS[level]
    web = (' ' + WEB_LOOKUP) if level >= WEB_LOOKUP_FROM else ''
    return 'Delegation level %d of 5 - %s. %s%s' % (level, name, rule, web)


def set_level(path, level):
    """Write "delegationLevel": level into a JSON config (dropping an old "delegation"), keeping every other key and their order."""
    path = Path(path)
    data = _read_json(path) if path.exists() else {}
    if data is None:
        raise SystemExit('%s is not valid JSON; fix it first' % path)
    data.pop(LEGACY_KEY, None)
    data[KEY] = level
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def agents_block(level):
    return '\n'.join([
        BEGIN,
        '## DeepSeek delegation level: %d of 5 (%s)' % (level, LEVELS[level][0]),
        '',
        LEVELS[level][1],
        '',
    ] + ([WEB_LOOKUP, ''] if level >= WEB_LOOKUP_FROM else []) + [
        ALWAYS,
        '',
        MISSING_TOOLS,
        '',
        'This block is generated. Change the level with `python "%s" --set <1-5> --agents-md` '
        'from this folder, or ask Claude to. The full scale is in the deepseek-agents skill.'
        % INSTALLED_TOOL.as_posix(),
        END,
    ])


def write_agents_md(project, level):
    """Insert or replace the managed block near the top of AGENTS.md (after its first heading)."""
    path = Path(project) / 'AGENTS.md'
    text = path.read_text(encoding='utf-8') if path.exists() else '# Working in this project\n'
    block = agents_block(level)
    pattern = re.compile(re.escape(BEGIN) + r'.*?' + re.escape(END), re.S)
    if pattern.search(text):
        text = pattern.sub(lambda _: block, text, count=1)
    else:
        lines = text.split('\n')
        at = next((i + 1 for i, l in enumerate(lines) if l.startswith('# ')), 0)
        lines[at:at] = ['', block, '']
        text = '\n'.join(lines)
    path.write_text(text, encoding='utf-8')
    return path


PACED_NOTE = (" (lowered from %d while DeepSeek spending runs ahead of the pace for the user's spend limit; "
              "ds_spend.py status shows it)")


def paced_level(level):
    """One level lower while spending runs ahead of pace (launcher/ds_spend.py), else the level itself.
    ds_spend.py sits in launcher/ here and in the skill folder once installed."""
    here = Path(__file__).resolve().parent
    for folder in (here.parent / 'launcher', here.parent):
        if (folder / 'ds_spend.py').is_file():
            sys.path.insert(0, str(folder))
            try:
                import ds_spend
                return ds_spend.lowered(level, ds_spend.spend_dir())
            except Exception:  # a broken spend record must never stop Claude reading its level
                return level
            finally:
                sys.path.remove(str(folder))
    return level


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--project', default=str(_project_root()))
    parser.add_argument('--set', type=int, choices=range(1, 6), metavar='1-5')
    parser.add_argument('--global', dest='is_global', action='store_true', help='with --set: your default for all projects')
    parser.add_argument('--agents-md', action='store_true', help="write the level's rules into the project's AGENTS.md")
    parser.add_argument('--table', action='store_true', help='print all levels')
    parser.add_argument('--brief', action='store_true', help='print only the one-line rule (for scripts)')
    args = parser.parse_args(argv)
    project = Path(args.project).resolve()

    if args.table:
        for n in sorted(LEVELS):
            print('%d  %-22s %s' % (n, LEVELS[n][0], LEVELS[n][1]))
        print('\n' + ALWAYS)
        return 0
    if args.set is not None:
        target = GLOBAL_CONFIG if args.is_global else project / '.deepseek-agents.json'
        set_level(target, args.set)
        print('set delegation %d in %s' % (args.set, target))
    level, source = effective(project)
    if args.agents_md:
        print('wrote the level-%d rules into %s' % (level, write_agents_md(project, level)))
    paced = paced_level(level)
    note = PACED_NOTE % level if paced != level else ''
    if args.brief:
        print(describe(paced) + note)
        return 0
    print('%s\n(from %s)%s\n\n%s' % (describe(paced), source, note, ALWAYS))
    if source == 'environment variable' and args.set is not None:
        print('\nNote: %s (or the older %s) is set and overrides the value just written.' % (ENV, LEGACY_ENV))
    return 0


if __name__ == '__main__':
    sys.exit(main())
