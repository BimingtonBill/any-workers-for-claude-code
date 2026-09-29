"""Model providers for workers: which API a worker talks to, with which key, model and prices.

    python ds_providers.py resolve --provider meta [--tier contributor] --dir <project>   JSON for the launcher
    python ds_providers.py list                                                            the providers

The registry is providers.json beside this file. ~/.claude-deepseek/providers.json (or DS_PROVIDERS_USER) holds
the user's own additions and opt-ins, for example:

    {"contributorProjects": {"meta": ["C:/Modding/OpenSkyrimProject"]}}

A training ("contributor") tier is allowed only for a project the user listed there: the opt-in lives in the
user's own settings, never in a project's .deepseek-agents.json, so no committed file (a pull request to a
public repository, say) can opt the user's machine in. The launcher then runs the worker in a clean checkout of
the project's tracked files. No key ever passes through this module: the launcher reads it from the
environment variable named here.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import ds_common  # noqa: E402  (beside this file, in the harness and once installed)


def registry():
    return json.loads((HERE / 'providers.json').read_text(encoding='utf-8'))


def short_name(provider):
    """The provider's short name for panels and messages ("MiMo", "Muse"): providers.json "short", else its key."""
    key = (provider or 'deepseek').lower()
    p = (registry().get('providers') or {}).get(key) or {}
    return p.get('short') or p.get('name') or provider


user_providers_path = ds_common.user_providers_path


def user_settings():
    data = ds_common.read_json(user_providers_path(), {})
    return data if isinstance(data, dict) else {}


def project_root(folder):
    """The project a folder belongs to: a coder's worktree (<project>/local/impl/<task>) or a contributor
    checkout counts as its project."""
    text = str(Path(folder).resolve())
    return Path(re.sub(r'[\\/]local[\\/](impl|contrib)[\\/][^\\/]+.*$', '', text, flags=re.I))


def opted_in(provider, folder, settings=None):
    settings = user_settings() if settings is None else settings
    listed = (settings.get('contributorProjects') or {}).get(provider) or []
    root = os.path.normcase(str(project_root(folder)))
    return any(os.path.normcase(str(Path(p).resolve())) == root for p in listed)


def resolve(provider=None, tier=None, folder=None, only_tiers=True):
    """What the launcher needs for one run, or {'error': ...}. The user can keep a provider to some of its tiers
    ("onlyTiers": {"meta": ["contributor"]}: 2026-09-29, Muse is worth it only on its contributor tier)."""
    reg = registry()
    name = (provider or reg.get('default') or 'deepseek').lower()
    p = (reg.get('providers') or {}).get(name)
    if not p:
        return dict(error='unknown provider %r; known: %s' % (name, ', '.join(sorted(reg.get('providers') or {}))))
    settings = user_settings()
    if not tier:   # the user's default tier for this provider (e.g. a subscription they bought), never a training tier
        default = (settings.get('defaultTiers') or {}).get(name)
        if default and not ((p.get('tiers') or {}).get(default) or {}).get('trainsOnPrompts'):
            tier = default
    out = dict(p, provider=name, tier=tier or 'standard', contributor=False)
    out.pop('tiers', None)
    if tier and tier != 'standard':
        t = (p.get('tiers') or {}).get(tier)
        if not t:
            return dict(error='%s has no %r tier' % (p.get('name', name), tier))
        if t.get('trainsOnPrompts'):
            if not folder or not opted_in(name, folder, settings):
                return dict(error=(
                    "the %s %s tier lets the provider train on what workers send it, so it is only for projects the user "
                    "has opted in, in their own settings (%s, \"contributorProjects\": {\"%s\": [\"<project folder>\"]}). "
                    "This project is not listed. Use the standard tier, or ask the user."
                    % (p.get('name', name), tier, user_providers_path(), name)))
            out['contributor'] = True
        out.update({k: v for k, v in t.items() if k not in ('trainsOnPrompts', 'baseUrls')})
        out['tier'] = tier
    allowed = (settings.get('onlyTiers') or {}).get(name)
    if only_tiers and allowed and out['tier'] not in allowed:
        return dict(error=("%s runs only on its %s tier here (the user's choice, in %s \"onlyTiers\"), so this %s-tier run "
                           "is refused: run it on DeepSeek (-Provider deepseek) or MiMo (-Provider xiaomi)%s."
                           % (p.get('name', name), ' or '.join(allowed), user_providers_path(),
                              out['tier'], ', or pass -Tier %s in a project opted in to it' % allowed[0] if len(allowed) == 1 else '')))
    # The address the user's account uses, found by the key window (a Token Plan has one per region).
    override = (settings.get('baseUrls') or {}).get('%s/%s' % (name, out['tier']))
    if override:
        out['baseUrl'] = override
    return out


def prices(provider=None, tier=None):
    """Prices for spend records: the provider's, or its tier's (a subscription tier costs nothing per token)."""
    r = resolve(provider, 'standard', only_tiers=False)
    if 'error' in r:
        return None
    p = dict(r.get('prices') or {})
    t = ((registry().get('providers') or {}).get(r['provider'], {}).get('tiers') or {}).get(tier or '') or {}
    p.update(t.get('prices') or {})
    return p


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('resolve')
    r.add_argument('--provider'); r.add_argument('--tier'); r.add_argument('--dir')
    sub.add_parser('list')
    a = ap.parse_args(argv)
    if a.cmd == 'list':
        reg = registry()
        for name, p in sorted((reg.get('providers') or {}).items()):
            tiers = ', '.join(sorted(p.get('tiers') or {}))
            print('%-9s %-18s %-22s key %s%s' % (name, p.get('name', ''), p.get('model', ''), p.get('keyEnv', ''),
                                                 ' (tiers: standard, %s)' % tiers if tiers else ''))
        return 0
    out = resolve(a.provider, a.tier, a.dir)
    print(json.dumps(out))
    return 1 if 'error' in out else 0


if __name__ == '__main__':
    sys.exit(main())
