"""Live-ops knobs shared by the game, the admin console and the platform.

Everything here is plain JSON so the Dittobuffer Platform can publish it as a
signed room document and the app can apply it without any extra assets:

* per-species (or per-form) shiny rates
* per-species wild spawn weight multipliers and an optional allow-list
* raid tier share and per-boss weight multipliers
* scheduled events (spotlight hours, shiny boosts, raid takeovers)
* Community Days: one species, one shiny rate, one window

The live document lives in ``data/liveops.json`` and is edited by the admin or
pushed by a platform room. Defaults come from ``config['liveops']``.
"""
from __future__ import annotations

import json
import math
import secrets
import time
from pathlib import Path

from dittobuffer.paths import ROOT

FILE = ROOT / 'data/liveops.json'

DEFAULTS = {
    'enabled': True,
    'shiny_rates': {},
    'spawn_weights': {},
    'spawn_percent': {},
    'spawn_allow': [],
    'raid_tier_weights': {'1': 50, '3': 30, '5': 20, '6': 0},
    'raid_boss_weights': {},
    'events': [],
    'community_days': [],
    'shadow_overrides': {},
    'source': '',
}

EVENT_KINDS = ('shiny', 'spawn', 'raid', 'spotlight')


def now_ms() -> int:
    return int(time.time() * 1000)


def _rate(value, field):
    try:
        rate = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{field} must be a number between 0 and 1') from None
    if not math.isfinite(rate) or not 0 <= rate <= 1:
        raise ValueError(f'{field} must be between 0 and 1')
    return rate


def _key(value) -> str:
    """Normalise ``25``/``25:0`` into a stable lookup key."""
    text = str(value).strip()
    if ':' in text:
        species, form = text.split(':', 1)
        return f'{int(species)}:{int(form)}'
    return str(int(text))


def _window(entry):
    starts = int(entry.get('starts_ms') or 0)
    ends = int(entry.get('ends_ms') or 0)
    if ends <= starts:
        raise ValueError('An event must end after it starts')
    return starts, ends


def _event(value):
    if not isinstance(value, dict):
        raise ValueError('Every event must be an object')
    kind = str(value.get('kind') or 'spotlight')
    if kind not in EVENT_KINDS and kind != 'community_day':
        raise ValueError('Unknown event kind')
    entry = {
        'id': str(value.get('id') or 'event-' + secrets.token_hex(4))[:40],
        'name': str(value.get('name') or 'Event')[:80],
        'kind': kind,
        'starts_ms': int(value.get('starts_ms') or 0),
        'ends_ms': int(value.get('ends_ms') or 0),
        'enabled': bool(value.get('enabled', True)),
        'species': [],
        'shiny_rate': None,
        'spawn_multiplier': 1.0,
        'raid_tier_weights': {},
        'shiny_rates': {},
        'spawn_weights': {},
    }
    _window(entry)
    species = value.get('species') or []
    if not isinstance(species, list):
        raise ValueError('Event species must be a list')
    entry['species'] = [int(item) for item in species]
    if value.get('shiny_rate') is not None:
        entry['shiny_rate'] = _rate(value['shiny_rate'], 'Event shiny rate')
    if value.get('spawn_multiplier') is not None:
        entry['spawn_multiplier'] = max(0.0, min(100.0, float(value['spawn_multiplier'])))
    for key, weight in (value.get('raid_tier_weights') or {}).items():
        entry['raid_tier_weights'][str(int(key))] = max(0.0, min(1e6, float(weight)))
    for key, rate in (value.get('shiny_rates') or {}).items():
        entry['shiny_rates'][_key(key)] = _rate(rate, 'Event shiny rate')
    for key, weight in (value.get('spawn_weights') or {}).items():
        entry['spawn_weights'][_key(key)] = max(0.0, min(100.0, float(weight)))
    return entry


def _community_day(value):
    if not isinstance(value, dict):
        raise ValueError('Every Community Day must be an object')
    entry = {
        'id': str(value.get('id') or 'community-day-' + secrets.token_hex(4))[:40],
        'name': str(value.get('name') or 'Community Day')[:80],
        'species': int(value.get('species') or 0),
        'form': int(value.get('form') or 0),
        'shiny_rate': _rate(value.get('shiny_rate', 0.04), 'Community Day shiny rate'),
        'starts_ms': int(value.get('starts_ms') or 0),
        'ends_ms': int(value.get('ends_ms') or 0),
        'enabled': bool(value.get('enabled', True)),
        'spawn_share': max(0.0, min(1.0, float(value.get('spawn_share', 1.0)))),
        'raid_tier_weights': {},
    }
    _window(entry)
    if entry['species'] <= 0:
        raise ValueError('Choose a Community Day species')
    for key, weight in (value.get('raid_tier_weights') or {}).items():
        entry['raid_tier_weights'][str(int(key))] = max(0.0, min(1e6, float(weight)))
    return entry


def normalise(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError('Live-ops settings must be an object')
    out = json.loads(json.dumps(DEFAULTS))
    out['enabled'] = bool(value.get('enabled', True))
    for key, rate in (value.get('shiny_rates') or {}).items():
        if int(key.split(':')[0]) <= 0:
            raise ValueError('Shiny rates need a species id')
        out['shiny_rates'][_key(key)] = _rate(rate, 'Shiny rate')
    for key, weight in (value.get('spawn_weights') or {}).items():
        out['spawn_weights'][_key(key)] = max(0.0, min(100.0, float(weight)))
    percent = value.get('spawn_percent') or {}
    if not isinstance(percent, dict):
        raise ValueError('spawn_percent must be an object of species=percent')
    total_percent = 0.0
    for key, share in percent.items():
        if int(str(key).split(':')[0]) <= 0:
            raise ValueError('Spawn percentages need a species id')
        try:
            share = float(share)
        except (TypeError, ValueError):
            raise ValueError('Spawn percentages must be numbers from 0 to 100') from None
        if not math.isfinite(share) or not 0 <= share <= 100:
            raise ValueError('Spawn percentages must be between 0 and 100')
        out['spawn_percent'][_key(key)] = round(share, 3)
        total_percent += share
    if total_percent > 100.001:
        raise ValueError('Spawn percentages add up to more than 100%')
    allow = value.get('spawn_allow') or []
    if not isinstance(allow, list):
        raise ValueError('spawn_allow must be a list of species')
    out['spawn_allow'] = [int(item) for item in allow]
    for key, weight in (value.get('raid_tier_weights') or {}).items():
        tier = int(key)
        if tier not in (1, 3, 5, 6):
            raise ValueError('Raid tiers are 1, 3, 5 and 6')
        out['raid_tier_weights'][str(tier)] = max(0.0, min(1e6, float(weight)))
    for key, weight in (value.get('raid_boss_weights') or {}).items():
        out['raid_boss_weights'][_key(key)] = max(0.0, min(100.0, float(weight)))
    events = value.get('events') or []
    if not isinstance(events, list) or len(events) > 100:
        raise ValueError('At most 100 events')
    out['events'] = [_event(item) for item in events]
    days = value.get('community_days') or []
    if not isinstance(days, list) or len(days) > 60:
        raise ValueError('At most 60 Community Days')
    out['community_days'] = [_community_day(item) for item in days]
    for key, override in (value.get('shadow_overrides') or {}).items():
        if not isinstance(override, dict):
            raise ValueError('Shadow overrides must be objects')
        out['shadow_overrides'][_key(key)] = {
            'enabled': bool(override.get('enabled', True)),
            'dust': max(0, min(100000, int(override.get('dust', 1000)))),
            'candy': max(0, min(1000, int(override.get('candy', 25)))),
        }
    return out


def load(config) -> dict:
    base = DEFAULTS | (config.get('liveops') if isinstance(config.get('liveops'), dict) else {})
    path = ROOT / (base.get('source') or 'data/liveops.json')
    if path.is_file():
        try:
            saved = json.loads(path.read_text(encoding='utf8'))
            merged = {**base, **saved}
            return normalise(merged)
        except (OSError, ValueError):
            pass
    try:
        return normalise(base)
    except ValueError:
        return json.loads(json.dumps(DEFAULTS))


def save(config, value) -> dict:
    clean = normalise(value)
    target = FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(clean, indent=2) + '\n', encoding='utf8')
    temporary.replace(target)
    return clean


def install(game):
    game.liveops = load(game.config)
    return game.liveops


def _active_events(game):
    value = getattr(game, 'liveops', None) or {}
    if not value.get('enabled', True):
        return []
    stamp = now_ms()
    return [entry for entry in value.get('events', [])
            if entry.get('enabled', True) and entry['starts_ms'] <= stamp < entry['ends_ms']]


def community_day(game):
    value = getattr(game, 'liveops', None) or {}
    if not value.get('enabled', True):
        return None
    stamp = now_ms()
    for entry in value.get('community_days', []):
        if entry.get('enabled', True) and entry['starts_ms'] <= stamp < entry['ends_ms']:
            return entry
    return None


def active_events(game):
    return _active_events(game)


def _rate_lookup(table, species, form):
    return table.get(f'{species}:{form}', table.get(str(species)))


def shiny_rate(game, base, species, form) -> float:
    """Most specific active override wins: Community Day, event, global."""
    value = getattr(game, 'liveops', None) or {}
    day = community_day(game)
    if day and int(day['species']) == int(species):
        return float(day['shiny_rate'])
    for event in _active_events(game):
        if int(species) not in event.get('species', []) and not event.get('shiny_rates'):
            continue
        rate = _rate_lookup(event.get('shiny_rates', {}), species, form)
        if rate is not None:
            return rate
        if event.get('shiny_rate') is not None and int(species) in event.get('species', []):
            return float(event['shiny_rate'])
    rate = _rate_lookup(value.get('shiny_rates', {}), species, form)
    return float(base if rate is None else rate)


def spawn_weight(game, species, form) -> float:
    value = getattr(game, 'liveops', None) or {}
    weight = _rate_lookup(value.get('spawn_weights', {}), species, form)
    result = 1.0 if weight is None else float(weight)
    if int(species) in (value.get('spawn_allow') or []) and value.get('spawn_allow'):
        result *= 1.0
    for event in _active_events(game):
        rate = _rate_lookup(event.get('spawn_weights', {}), species, form)
        if rate is not None:
            result *= rate
        elif event.get('kind') == 'spotlight' and int(species) in event.get('species', []):
            result *= float(event.get('spawn_multiplier') or 1.0)
    return max(0.0, result)


def spawn_percent(game) -> dict:
    """Absolute wild-pool shares chosen in an admin or a platform room."""
    value = getattr(game, 'liveops', None) or {}
    return dict(value.get('spawn_percent') or {})


def allowed(game, species) -> bool:
    value = getattr(game, 'liveops', None) or {}
    allow = value.get('spawn_allow') or []
    return not allow or int(species) in allow


def raid_pool(game):
    """Raid bosses with tier share and per-boss weights applied."""
    value = getattr(game, 'liveops', None) or {}
    tiers = dict(value.get('raid_tier_weights') or {})
    day = community_day(game)
    if day and day.get('raid_tier_weights'):
        tiers = {tier: min(tiers.get(str(tier), 0), weight) for tier, weight in day['raid_tier_weights'].items()}
    for event in _active_events(game):
        if event.get('kind') == 'raid' and event.get('raid_tier_weights'):
            for tier, weight in event['raid_tier_weights'].items():
                tiers[str(tier)] = weight
    bosses = []
    for boss in game.raids.get('pools', []):
        tier = str(boss.get('tier'))
        share = float(tiers.get(tier, 0) or 0)
        if share <= 0:
            continue
        multiplier = _rate_lookup(value.get('raid_boss_weights', {}), boss['species'], boss.get('form', 0))
        weight = float(boss.get('weight', 1)) * share * (1.0 if multiplier is None else float(multiplier))
        if weight <= 0:
            continue
        bosses.append({**boss, 'weight': weight})
    return bosses


def shadow_rule(game, species, form):
    value = (getattr(game, 'liveops', None) or {}).get('shadow_overrides', {})
    return _rate_lookup(value, species, form) or value.get(str(species))


def snapshot(game, db=None) -> dict:
    """Small JSON document published to a platform room."""
    value = getattr(game, 'liveops', None) or {}
    room = {'liveops': value, 'logical_ms': now_ms()}
    if db is not None:
        try:
            room['players'] = db.execute('SELECT COUNT(*) FROM players').fetchone()[0]
            room['active_spawns'] = db.execute('SELECT COUNT(*) FROM spawns WHERE expires_ms>?', (now_ms(),)).fetchone()[0]
        except Exception:
            room['players'] = 0
    return room
