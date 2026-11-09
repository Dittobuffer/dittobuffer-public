"""Realistic, rotating wild-spawn pools for the local family profile.

The legacy spawner picks from explicit ``spawn_pools``.  When the admin panel
enables random spawns, this module builds one weighted pool from the supplied
Game Master forms and the reviewed local asset manifest.  Ordinary species are
common; strong species are rare; fully-imported alternate forms (including
Eternamax, Zygarde and regional forms) are very rare.  The spawner's 30 minute
bucket rotation then replaces the whole neighbourhood naturally.
"""
import json
import math

from dittobuffer.paths import ROOT

DEFAULTS = {
    # The family profile wants a living world without extra setup.  Profiles
    # that deliberately keep the old fixed tables (development/testing) set
    # this false explicitly in their own config.
    'enabled': True,
    'refresh_seconds': 1800,
    'level_range': [5, 35],
    'iv_range': [0, 15],
    # 1 in 64, matching the usual community-day style wild rate.
    'shiny_rate': 1 / 64,
    'special_weight': 0.05,
    # Six points per S2 tile plus the optional neighbouring ring keeps a
    # dense but readable neighbourhood instead of carpeting the map.
    'points_per_tile': 6,
}

_EXCLUDED = ('MEGA', 'PRIMAL', 'GIGANTAMAX', 'TOTEM', 'SHADOW', 'PURIFIED', 'LUCKY')
# Class 1 is legendary, 2 mythical and 3 Ultra Beast.  Those only ever come
# from admin spawns and raids, never from the rotating wild pool.
_SPECIAL_CLASSES = (1, 2, 3)


def settings(config):
    raw = config.get('random_spawns') or {}
    value = {**DEFAULTS, **raw} if isinstance(raw, dict) else dict(DEFAULTS)
    value['enabled'] = bool(value.get('enabled', True))
    try:
        value['refresh_seconds'] = max(300, min(86400, int(value['refresh_seconds'])))
        value['points_per_tile'] = max(1, min(30, int(value['points_per_tile'])))
        low, high = (int(part) for part in value['level_range'])
        if not 1 <= low <= high <= 40:
            raise ValueError
        value['level_range'] = [low, high]
        low, high = (int(part) for part in value['iv_range'])
        if not 0 <= low <= high <= 15:
            raise ValueError
        value['iv_range'] = [low, high]
        value['shiny_rate'] = max(0.0, min(1.0, float(value['shiny_rate'])))
        value['special_weight'] = max(0.01, min(1.0, float(value['special_weight'])))
    except (TypeError, ValueError):
        raise ValueError('Invalid random_spawns settings') from None
    return value


def _complete_forms(game):
    """Map complete manifest addresses to (species, form) identities."""
    path = ROOT / (game.config.get('pokemon_assets') or {}).get(
        'manifest', 'builds/m113-reviewed-assets.json')
    if not path.is_file():
        return set()
    try:
        variants = json.loads(path.read_text(encoding='utf8')).get('variants') or []
    except (OSError, ValueError):
        return set()
    by_suffix = {}
    for (species, form), rules in game.forms.items():
        if rules.get('is_costume'):
            continue
        by_suffix.setdefault(species, []).append((form, rules))
    complete = set()
    for variant in variants:
        if variant.get('missing_dependencies'):
            continue
        species = variant.get('species')
        address = str(variant.get('address') or '')
        if not species or species not in game.species:
            continue
        suffix = None
        if '.f' in address:
            suffix = address.split('.f', 1)[1].split('.', 1)[0]
        candidates = by_suffix.get(species, [])
        if not suffix:
            complete.add((species, game.species[species]['form']))
            continue
        matches = [form for form, rules in candidates
                   if rules['name'].endswith('_' + suffix) or rules['name'] == suffix]
        complete.update((species, form) for form in matches)
    return complete


def _wild_weight(rules):
    """Native-ish encounter weight: common early routes, rare fully evolved."""
    total = rules['attack'] + rules['defense'] + rules['stamina']
    if total < 300:
        return 12
    if total < 400:
        return 5
    if total < 500:
        return 2
    return 0.4


def _apply_percent_mix(game, entries):
    """Reserve exact absolute shares; everything else splits the remainder.

    ``spawn_percent`` holds percentages, not multipliers: ``{'16': 20}`` means
    Pidgey is 20% of every rotating pool.  Species keys cover every form of
    that species; ``species:form`` keys target one form and take precedence.
    The unclaimed remainder is distributed over the other eligible entries by
    their natural playability weight, so 20% + 35% + 6% leaves 39% of
    everything else.
    """
    try:
        from dittobuffer.gameplay import liveops
        mix = liveops.spawn_percent(game)
    except ImportError:
        mix = {}
    if not mix:
        return entries
    form_mix = {key: float(value) for key, value in mix.items() if ':' in key}
    species_mix = {key: float(value) for key, value in mix.items() if ':' not in key}
    targeted = {int(key.split(':')[0]) for key in form_mix}
    natural = {index: max(1e-9, float(entry['weight'])) for index, entry in enumerate(entries)}
    members = {}
    for index, entry in enumerate(entries):
        form = entry.get('form', game.species[entry['id']]['form'])
        exact = f"{entry['id']}:{form}"
        if exact in form_mix:
            members.setdefault(exact, []).append(index)
        elif entry['id'] not in targeted and str(entry['id']) in species_mix:
            members.setdefault(str(entry['id']), []).append(index)
    if not members:
        return entries
    weights = {}
    claimed = 0.0
    for key, indexes in members.items():
        share = form_mix.get(key, species_mix.get(key, 0.0))
        claimed += share
        total = sum(natural[index] for index in indexes) or 1.0
        for index in indexes:
            weights[index] = share * natural[index] / total
    remaining = max(0.0, 100.0 - claimed)
    others = [index for index in natural if index not in weights]
    if remaining > 0 and others:
        total = sum(natural[index] for index in others) or 1.0
        for index in others:
            weights[index] = remaining * natural[index] / total
    result = [{**entry, 'weight': round(weights[index], 6)}
              for index, entry in enumerate(entries) if weights.get(index, 0.0) > 0]
    return result or entries


def build_pool(game, value=None):
    """Return a weighted, asset-complete wild pool plus level/shiny settings."""
    value = value or settings(game.config)
    complete = _complete_forms(game)
    entries = []
    for species, rules in game.species.items():
        if complete and (species, rules['form']) not in complete:
            continue
        if rules.get('class') in _SPECIAL_CLASSES:
            continue
        entries.append({'id': species, 'weight': _wild_weight(rules)})
    seen = {(entry['id'], game.species[entry['id']]['form']) for entry in entries}
    alternates = 0
    for (species, form), rules in game.forms.items():
        if (species, form) in seen or (species, form) not in complete:
            continue
        if rules.get('class') in _SPECIAL_CLASSES:
            continue
        name = rules.get('name', '')
        if any(word in name for word in _EXCLUDED):
            continue
        # Keep ordinary regional/seasonal forms rare but present.  Every
        # dependency-complete non-special alternate form is eligible, so the
        # wild pool always has a chance of a regional surprise.  Event-only
        # and special forms (Eternamax, Zygarde, Shadows) stay admin/raid only.
        if alternates >= 5000:
            break
        entries.append({'id': species, 'form': form,
                        'weight': round(max(0.002, float(value['special_weight'])), 4)})
        alternates += 1
    entries = _apply_percent_mix(game, entries)
    if not entries:
        raise ValueError('Random spawns need at least one supported form')
    pool = {'name': 'random-wild', 'species': entries,
            'shiny_rate': value['shiny_rate']}
    return [pool], value


def install(game):
    """Attach random spawn state to a Game instance (no-op when disabled)."""
    value = settings(game.config)
    game.random_spawns = value
    if not value['enabled']:
        return False
    pools, value = build_pool(game, value)
    game.spawn_pools = pools
    game.spawn_points_per_tile = value['points_per_tile']
    game.spawn_seconds = value['refresh_seconds']
    game.spawn_level_range = value['level_range']
    game.spawn_iv_range = value['iv_range']
    game.spawn_shiny_rate = value['shiny_rate']
    game.shiny_eligible = {(entry['id'], entry.get('form', game.species[entry['id']]['form']))
                           for entry in pools[0]['species']}
    return True
