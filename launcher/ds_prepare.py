"""Everything ds-agent.ps1 needs from Python before a worker starts, in one process.

    python ds_prepare.py --provider xiaomi [--tier token-plan] --dir <project> [--spend-dir <folder>]

The launcher used to start a Python process for each of these, about 70 ms apiece before any work:
  provider   ds_providers.py resolve       the provider, tier, key variable, model and prices
  allowance  ds_allowance.py status        the plan's pace, for a provider with an allowance (MiMo), else null
  notes      ds_envelope.py note --kind    the report-opening instruction, for every kind
  budgets    ds_steer.py budget --kind     the step budget, for every kind (one read of the spend record)
It prints them as one JSON object and exits 1 when the provider can't be used ("provider" then holds
{"error": ...}, as `resolve` does). Notes and budgets come for every kind because the kind is settled later
in the launcher (from the label, or a resumed run's manifest); a kind not listed falls back to the old call.

The spend check stays a call of its own (ds_spend.py check): it needs the run id and the launcher's pid, holds
the pace lock, and repeats while a worker waits for the pace. The old commands all still work.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# The kinds ds-agent.ps1 accepts (its -Kind list; the launcher settles an empty one as 'task'). No '' key:
# Windows PowerShell's ConvertFrom-Json refuses an empty property name.
KINDS = ('research', 'websearch', 'impl', 'review', 'analysis', 'critic', 'advisor', 'digest', 'lead',
         'selftest', 'probe', 'task')


def prepare(provider=None, tier=None, folder=None, spend_dir=None):
    import ds_providers
    out = dict(provider=ds_providers.resolve(provider, tier, folder), allowance=None, notes={}, budgets={})
    prov = out['provider']
    if 'error' in prov:
        return out
    if prov.get('allowance'):
        try:
            import ds_allowance
            out['allowance'] = ds_allowance.status(prov['provider'], prov['tier'])
        except Exception as exc:     # the launcher carries on without pacing, as it did when the call failed
            out['allowance_error'] = str(exc)
    try:
        import ds_envelope
        out['notes'] = {k: ds_envelope.note(k) for k in KINDS}
    except Exception as exc:
        out['notes_error'] = str(exc)
    try:
        import ds_steer
        out['budgets'] = ds_steer.budgets(spend_dir, KINDS)
    except Exception as exc:
        out['budgets_error'] = str(exc)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--provider'); ap.add_argument('--tier'); ap.add_argument('--dir')
    ap.add_argument('--spend-dir', help='default: DS_SPEND_DIR or ~/.claude-deepseek/spend')
    a = ap.parse_args(argv)
    out = prepare(a.provider, a.tier, a.dir, a.spend_dir)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(out))
    return 1 if 'error' in out['provider'] else 0


if __name__ == '__main__':
    sys.exit(main())
