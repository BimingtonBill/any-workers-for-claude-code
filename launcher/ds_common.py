"""Small helpers the launcher's Python files share, one copy each.

    import ds_common        # beside this file: launcher/ in the harness, the skill folder once installed

A tool in tools/ finds this folder with ds_state.launcher_dir('ds_common.py'). The old names stay where they
were (ds_spend.spend_dir, ds_spend.write_json_atomic, ds_claude.parse_time, ...) as aliases of these.
"""
import datetime as dt
import json
import os
from pathlib import Path


def spend_dir():
    """The spend folder every project shares: DS_SPEND_DIR, else ~/.claude-deepseek/spend."""
    return Path(os.environ.get('DS_SPEND_DIR') or Path.home() / '.claude-deepseek' / 'spend')


def user_providers_path():
    """The user's own provider settings (opt-ins, plans, default tiers): DS_PROVIDERS_USER, else
    ~/.claude-deepseek/providers.json."""
    return Path(os.environ.get('DS_PROVIDERS_USER') or Path.home() / '.claude-deepseek' / 'providers.json')


def parse_time(text):
    """An ISO time as an aware UTC datetime. A trailing 'Z' is UTC (fromisoformat only takes it from Python 3.11
    on); a time without a zone is taken as local time."""
    t = dt.datetime.fromisoformat(str(text).strip().replace('Z', '+00:00').replace('z', '+00:00'))
    return (t if t.tzinfo else t.astimezone()).astimezone(dt.timezone.utc)


def read_json(path, default=None):
    """A JSON file's content, or `default` when it is missing or not JSON. A byte-order mark is allowed:
    PowerShell 5 writes one."""
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return default


def write_json_atomic(path, data, indent=2):
    """Write JSON to `path` whole or not at all: a temporary file in the same folder, then os.replace, so a reader
    never sees a half-written file (read_json turns one into {}, which silently turned pacing off). On Windows
    os.replace fails with PermissionError while another process has the file open for a moment: retry briefly."""
    import time
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=indent)       # before anything is written: bad data leaves the old file alone
    tmp = path.with_name('%s.%d.tmp' % (path.name, os.getpid()))
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text + '\n')
    for attempt in range(20):
        try:
            os.replace(str(tmp), str(path))
            return
        except PermissionError:
            if attempt == 19:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.05)
