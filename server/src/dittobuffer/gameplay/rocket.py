"""Local Team GO Rocket balloons and shadow encounters.

Balloons are private to one trainer, arrive roughly every two hours and keep a
persistent schedule/log.  A balloon owns one map spawn (the balloon itself) and
one dedicated reward spawn created the first time the trainer opens the native
invasion encounter.  Catch ownership therefore uses the same durable encounter
record as a raid bonus encounter instead of trusting whatever id the client
happens to send.
"""
import json
import math
import random
import secrets

from s2sphere import CellId, LatLng

from dittobuffer.paths import ROOT
from dittobuffer.gameplay.game import p, now_ms

CONFIG = ROOT / 'config' / 'rocket.json'

DEFAULTS = {
    'enabled': False,
    'auto_spawn': True,
    'min_level': 8,
    'balloon_interval_minutes': 120,
    'balloon_lifetime_minutes': 20,
    'balloon_encounter_balls': 8,
    'grunts': [
        {'id': 'grunt-fire', 'name': 'Fire-type Grunt', 'species': 37, 'form': 0,
         'level': 25, 'iv_floor': 10, 'shadow': True},
        {'id': 'grunt-water', 'name': 'Water-type Grunt', 'species': 7, 'form': 0,
         'level': 25, 'iv_floor': 10, 'shadow': True},
        {'id': 'grunt-grass', 'name': 'Grass-type Grunt', 'species': 1, 'form': 0,
         'level': 25, 'iv_floor': 10, 'shadow': True},
    ],
    # Leaders award unevolved shadows.  Fully evolved species such as
    # Tyranitar/Scizor/Gengar were trivialising the local reward loop.
    'leaders': [
        {'id': 'arlo', 'name': 'Arlo', 'species': 123, 'form': 0,
         'level': 25, 'iv_floor': 10, 'shadow': True},
        {'id': 'cliff', 'name': 'Cliff', 'species': 246, 'form': 0,
         'level': 25, 'iv_floor': 10, 'shadow': True},
        {'id': 'sierra', 'name': 'Sierra', 'species': 92, 'form': 0,
         'level': 25, 'iv_floor': 10, 'shadow': True},
    ],
    'giovanni': {'id': 'giovanni', 'name': 'Giovanni', 'species': 150, 'form': 0,
                 'level': 40, 'iv_floor': 10, 'shadow': True},
}


def config():
    if CONFIG.is_file():
        try:
            supplied = json.loads(CONFIG.read_text(encoding='utf8'))
        except (OSError, ValueError):
            supplied = {}
        if isinstance(supplied, dict):
            return {**DEFAULTS, **supplied}
    return json.loads(json.dumps(DEFAULTS))


def save_config(value):
    if not isinstance(value, dict):
        raise ValueError('Rocket config must be an object')
    merged = {**DEFAULTS, **value}
    if not isinstance(merged.get('enabled'), bool):
        raise ValueError('enabled must be true or false')
    for key, low, high in (('min_level', 1, 50), ('balloon_interval_minutes', 1, 1440),
                           ('balloon_lifetime_minutes', 1, 1440),
                           ('balloon_encounter_balls', 1, 100)):
        merged[key] = max(low, min(high, int(merged.get(key, DEFAULTS[key]))))
    for key in ('grunts', 'leaders'):
        rows = merged.get(key)
        if not isinstance(rows, list) or not 1 <= len(rows) <= 50:
            raise ValueError(f'{key} must contain 1..50 characters')
        cleaned = []
        for row in rows:
            if not isinstance(row, dict) or not str(row.get('name', '')).strip():
                raise ValueError(f'Each {key} entry needs a name')
            entry = {'id': str(row.get('id') or secrets.token_hex(4))[:40],
                     'name': str(row['name']).strip()[:60],
                     'species': int(row.get('species', 1)),
                     'form': int(row.get('form', 0)),
                     'level': max(1, min(50, int(row.get('level', 25)))),
                     'iv_floor': max(0, min(15, int(row.get('iv_floor', 10)))),
                     'shadow': bool(row.get('shadow', True))}
            cleaned.append(entry)
        merged[key] = cleaned
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(merged, indent=2) + '\n', encoding='utf8')
    return merged


def ensure(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rocket_balloons(
        incident_id TEXT PRIMARY KEY, kind TEXT NOT NULL, character_id TEXT NOT NULL,
        character_name TEXT NOT NULL, species INTEGER NOT NULL, form INTEGER NOT NULL,
        spawn_id INTEGER NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL,
        spawned_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL)''')
    columns = {row['name'] for row in db.execute('PRAGMA table_info(rocket_balloons)')}
    for name, declaration in (
            ('player_id', "TEXT NOT NULL DEFAULT ''"),
            ('encounter_spawn_id', 'INTEGER NOT NULL DEFAULT 0'),
            ('caught_ms', 'INTEGER NOT NULL DEFAULT 0')):
        if name not in columns:
            db.execute(f'ALTER TABLE rocket_balloons ADD COLUMN {name} {declaration}')
    db.execute('''CREATE TABLE IF NOT EXISTS rocket_schedule(
        player_id TEXT PRIMARY KEY, last_spawn_ms INTEGER NOT NULL DEFAULT 0,
        next_spawn_ms INTEGER NOT NULL DEFAULT 0, last_incident_id TEXT NOT NULL DEFAULT '')''')
    db.execute('''CREATE TABLE IF NOT EXISTS rocket_balloon_log(
        incident_id TEXT PRIMARY KEY, player_id TEXT NOT NULL, kind TEXT NOT NULL,
        character_id TEXT NOT NULL, character_name TEXT NOT NULL,
        spawned_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL,
        caught_ms INTEGER NOT NULL DEFAULT 0)''')
    db.execute('''CREATE TABLE IF NOT EXISTS rocket_encounters(
        player_id TEXT NOT NULL, incident_id TEXT NOT NULL,
        spawn_id INTEGER UNIQUE NOT NULL REFERENCES spawns(id),
        balls INTEGER NOT NULL, created_ms INTEGER NOT NULL,
        caught_ms INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(player_id,incident_id))''')


def active(db, player=None):
    ensure(db)
    stamp = now_ms()
    db.execute('DELETE FROM rocket_balloons WHERE expires_ms<=?', (stamp,))
    if player is None:
        return [dict(row) for row in db.execute(
            'SELECT * FROM rocket_balloons ORDER BY spawned_ms DESC')]
    # Adopt a balloon created before per-player ownership existed so an
    # in-flight balloon is not lost on the first poll after an upgrade.
    legacy = db.execute("SELECT * FROM rocket_balloons WHERE player_id=''").fetchone()
    if legacy:
        db.execute('UPDATE rocket_balloons SET player_id=? WHERE incident_id=?',
                   (player, legacy['incident_id']))
    return [dict(row) for row in db.execute(
        'SELECT * FROM rocket_balloons WHERE player_id=? ORDER BY spawned_ms DESC',
        (player,))]


def log(db, player=None, limit=20):
    ensure(db)
    if player is None:
        rows = db.execute('SELECT * FROM rocket_balloon_log ORDER BY spawned_ms DESC LIMIT ?',
                          (max(1, min(200, int(limit))),))
    else:
        rows = db.execute('SELECT * FROM rocket_balloon_log WHERE player_id=? '
                          'ORDER BY spawned_ms DESC LIMIT ?',
                          (player, max(1, min(200, int(limit)))))
    return [dict(row) for row in rows]


def schedule(db, player):
    ensure(db)
    return db.execute('SELECT * FROM rocket_schedule WHERE player_id=?',
                      (player,)).fetchone()


def spawn(game, db, kind, character, lat, lng, duration_seconds=1200, player=None):
    """Spawn a private balloon and one shadow Pokémon at the same point."""
    ensure(db)
    if kind not in ('grunt', 'leader', 'giovanni'):
        raise ValueError('Balloon kind must be grunt, leader or giovanni')
    if kind == 'grunt':
        rows = config()['grunts']
    elif kind == 'leader':
        rows = config()['leaders']
    else:
        rows = [config()['giovanni']]
    entry = character if isinstance(character, dict) else next(
        (row for row in rows if row.get('id') == character), None)
    if not entry:
        raise ValueError('Unknown Rocket character')
    species = int(entry['species'])
    form = int(entry.get('form', 0))
    rules = game.forms.get((species, form))
    if rules is None:
        raise ValueError('Unknown Rocket Pokémon form')
    from dittobuffer.gameplay.species import choose_gender, make_pokemon
    floor = max(0, min(15, int(entry.get('iv_floor', 10))))
    rng = random.Random(secrets.token_bytes(16))
    ivs = [rng.randint(floor, 15) for _ in range(3)]
    pokemon = make_pokemon(p, rules, species, int(entry.get('level', 25)), ivs,
                           gender=choose_gender(game, species, rng))
    pokemon.pokemon_display.form = form
    if entry.get('shadow', True):
        pokemon.pokemon_display.alignment = 1
    stamp = now_ms()
    pokemon.creation_time_ms = stamp
    spawn_id = secrets.randbits(61) | (1 << 61)
    expires = stamp + max(60, min(86400, int(duration_seconds))) * 1000
    incident_id = 'rocket:' + secrets.token_hex(8)
    token = CellId.from_lat_lng(LatLng.from_degrees(lat, lng)).parent(20).to_token()
    db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',
               (spawn_id, token, species, lat, lng, expires))
    db.execute('INSERT INTO spawn_attributes VALUES(?,?,?)',
               (spawn_id, pokemon.SerializeToString(), int(entry.get('level', 25))))
    db.execute('''INSERT INTO rocket_balloons(
        incident_id,kind,character_id,character_name,species,form,spawn_id,lat,lng,
        spawned_ms,expires_ms,player_id,encounter_spawn_id,caught_ms)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
               (incident_id, kind, str(entry['id']), str(entry['name']), species, form,
                spawn_id, lat, lng, stamp, expires, player or '', 0, 0))
    db.execute('''INSERT OR REPLACE INTO rocket_balloon_log(
        incident_id,player_id,kind,character_id,character_name,spawned_ms,expires_ms,caught_ms)
        VALUES(?,?,?,?,?,?,?,?)''',
               (incident_id, player or '', kind, str(entry['id']), str(entry['name']),
                stamp, expires, 0))
    return {'incident_id': incident_id, 'kind': kind, 'character_id': entry['id'],
            'character_name': entry['name'], 'species': species, 'form': form,
            'spawn_id': str(spawn_id), 'lat': lat, 'lng': lng,
            'spawned_ms': stamp, 'expires_ms': expires, 'player_id': player or '',
            'ivs': ivs, 'shadow': bool(entry.get('shadow', True))}


def clear(db, incident_id):
    ensure(db)
    row = db.execute('SELECT spawn_id,encounter_spawn_id FROM rocket_balloons '
                     'WHERE incident_id=?', (incident_id,)).fetchone()
    if not row:
        return {'deleted': incident_id}
    # Keep spawn rows for encounter/foreign-key history, but expire them so
    # the balloon and its reward disappear from the map immediately.
    stamp = now_ms()
    for spawn_id in (row['spawn_id'], row['encounter_spawn_id']):
        if spawn_id:
            db.execute('UPDATE spawns SET expires_ms=? WHERE id=?', (stamp, spawn_id))
    db.execute('DELETE FROM rocket_balloons WHERE incident_id=?', (incident_id,))
    return {'deleted': incident_id}


def find(db, incident_id, player=None):
    ensure(db)
    if player is None:
        return db.execute('SELECT * FROM rocket_balloons WHERE incident_id=?',
                          (incident_id,)).fetchone()
    return db.execute('SELECT * FROM rocket_balloons WHERE incident_id=? '
                      'AND (player_id=? OR player_id=\'\')',
                      (incident_id, player)).fetchone()


def encounter(db, player, incident_id):
    ensure(db)
    return db.execute('SELECT * FROM rocket_encounters WHERE player_id=? '
                      'AND incident_id=?', (player, incident_id)).fetchone()


def encounter_for_spawn(db, player, spawn_id):
    ensure(db)
    return db.execute('SELECT * FROM rocket_encounters WHERE player_id=? '
                      'AND spawn_id=?', (player, spawn_id)).fetchone()


def resolve_catch(db, player, request):
    """Return the open Rocket encounter even when the client sends a stale id.

    Native 0.429.2 usually sends ``InvasionEncounterOutProto.encounter_id`` as
    ``CatchPokemonProto.encounter_id``.  A few deferred/restart paths instead
    send the spawn-point token, a zero id, or only the GUID.  The reward record
    is authoritative, so recover it from the GUID or from the trainer's single
    open Rocket encounter before declaring ownership missing.

    A normal wild catch must never be claimed just because one balloon is live:
    only fall back when the submitted id/GUID actually identifies that balloon.
    """
    ensure(db)
    ident = int(request.encounter_id or 0)
    direct = encounter_for_spawn(db, player, ident) if ident else None
    if direct:
        return direct
    guid = str(request.spawn_point_guid or '')
    if guid:
        by_guid = db.execute('SELECT r.* FROM rocket_encounters r JOIN spawns s '
                             'ON s.id=r.spawn_id WHERE r.player_id=? AND s.spawnpoint=? '
                             'AND r.caught_ms=0 ORDER BY r.created_ms DESC LIMIT 1',
                             (player, guid)).fetchone()
        if by_guid:
            return by_guid
        try:
            numeric = int(guid, 10)
        except ValueError:
            numeric = None
        if numeric:
            direct = encounter_for_spawn(db, player, numeric)
            if direct:
                return direct
    balloons = active(db, player)
    for balloon in balloons:
        ids = {int(balloon['spawn_id'])}
        if balloon['encounter_spawn_id']:
            ids.add(int(balloon['encounter_spawn_id']))
        match = ident in ids
        if not match and guid:
            match = bool(db.execute('SELECT 1 FROM spawns WHERE id IN (%s) AND spawnpoint=?' %
                                    ','.join('?' for _ in ids), (*ids, guid)).fetchone())
        if match:
            return {'player_id': player, 'incident_id': balloon['incident_id'],
                    'spawn_id': balloon['encounter_spawn_id'] or balloon['spawn_id'],
                    'balls': config().get('balloon_encounter_balls', 8),
                    'created_ms': balloon['spawned_ms'], 'caught_ms': 0}
    # Legacy/deferred clients omit both identities.  Only an unambiguous open
    # reward or balloon is safe to recover in that case.
    if not ident and not guid:
        rows = db.execute('SELECT * FROM rocket_encounters WHERE player_id=? AND caught_ms=0 '
                          'ORDER BY created_ms DESC LIMIT 2', (player,)).fetchall()
        if len(rows) == 1:
            return rows[0]
        if len(balloons) == 1 and not balloons[0]['caught_ms']:
            return {'player_id': player, 'incident_id': balloons[0]['incident_id'],
                    'spawn_id': balloons[0]['encounter_spawn_id'] or balloons[0]['spawn_id'],
                    'balls': config().get('balloon_encounter_balls', 8),
                    'created_ms': balloons[0]['spawned_ms'], 'caught_ms': 0}
    return None


# Native invasion characters.  Balloons use their dedicated balloon variants so
# the client shows the correct Rocket outfit and dialogue portrait.
CHARACTER_ENUMS = {
    'grunt-fire': 'InvasionCharacter_CHARACTER_FIRE_BALLOON_GRUNT_MALE',
    'grunt-water': 'InvasionCharacter_CHARACTER_WATER_BALLOON_GRUNT_MALE',
    'grunt-grass': 'InvasionCharacter_CHARACTER_GRASS_BALLOON_GRUNT_MALE',
    'arlo': 'InvasionCharacter_CHARACTER_EXECUTIVE_ARLO',
    'cliff': 'InvasionCharacter_CHARACTER_EXECUTIVE_CLIFF',
    'sierra': 'InvasionCharacter_CHARACTER_EXECUTIVE_SIERRA',
    'giovanni': 'InvasionCharacter_CHARACTER_GIOVANNI',
}
_KIND_ENUMS = {
    'grunt': 'InvasionCharacter_CHARACTER_BALLOON_GRUNT_MALE',
    'leader': 'InvasionCharacter_CHARACTER_EXECUTIVE_ARLO',
    'giovanni': 'InvasionCharacter_CHARACTER_GIOVANNI',
}


def invasion_character(balloon):
    """Return the native ``EnumWrapper.InvasionCharacter`` for a balloon."""
    name = CHARACTER_ENUMS.get(str(balloon['character_id'])) or _KIND_ENUMS.get(
        str(balloon['kind']), 'InvasionCharacter_CHARACTER_BALLOON_GRUNT_MALE')
    return p.EnumWrapper.InvasionCharacter.Value(name)


def rescue(db, player, incident_id):
    """Rescue the shadow Pokémon into storage (local admin/QA shortcut)."""
    balloon = find(db, incident_id, player)
    if not balloon:
        raise ValueError('Unknown Rocket balloon')
    spawn = db.execute('SELECT s.*,a.data pokemon_data FROM spawns s '
                       'LEFT JOIN spawn_attributes a ON a.spawn_id=s.id '
                       'WHERE s.id=?', (balloon['spawn_id'],)).fetchone()
    if not spawn or not spawn['pokemon_data']:
        raise ValueError('Balloon has no shadow encounter')
    pokemon = p.PokemonProto.FromString(spawn['pokemon_data'])
    ticket = secrets.randbits(63)
    pokemon.id = ticket
    pokemon.creation_time_ms = now_ms()
    db.execute('INSERT INTO pokemon VALUES(?,?,?,?,?)',
               (ticket, player, spawn['id'], spawn['species'], pokemon.SerializeToString()))
    stamp = now_ms()
    db.execute('UPDATE spawns SET expires_ms=? WHERE id IN (?,?)',
               (stamp, spawn['id'], balloon['encounter_spawn_id'] or spawn['id']))
    db.execute('DELETE FROM rocket_balloons WHERE incident_id=?', (incident_id,))
    db.execute('UPDATE rocket_balloon_log SET caught_ms=? WHERE incident_id=?',
               (stamp, incident_id))
    return {'pokemon_id': str(ticket), 'species': pokemon.pokemon_id,
            'form': pokemon.pokemon_display.form,
            'ivs': [pokemon.individual_attack, pokemon.individual_defense,
                    pokemon.individual_stamina], 'player_id': player}


def _next_delay(interval_ms):
    """About every two hours, with a small random jitter either side."""
    return max(60_000, int(interval_ms * random.uniform(0.85, 1.15)))


def maybe_spawn(game, db, player, lat, lng):
    """Create this trainer's balloon when the two-hour window has elapsed."""
    cfg = config()
    if not cfg.get('enabled') or not cfg.get('auto_spawn', True):
        return None
    if lat is None or lng is None or active(db, player):
        return None
    ensure(db)
    stamp = now_ms()
    row = schedule(db, player)
    interval = max(1, int(cfg.get('balloon_interval_minutes', 120))) * 60000
    if row:
        # First poll after a fresh schedule is immediate; subsequent balloons
        # wait for the persisted next-spawn deadline.
        if row['next_spawn_ms'] and stamp < row['next_spawn_ms']:
            return None
    roll = random.random()
    if roll < 0.03:
        kind, rows = 'giovanni', [cfg['giovanni']]
    elif roll < 0.18:
        kind, rows = 'leader', cfg['leaders']
    else:
        kind, rows = 'grunt', cfg['grunts']
    entry = random.choice(rows)
    balloon = spawn(game, db, kind, entry, lat, lng,
                    max(60, int(cfg.get('balloon_lifetime_minutes', 20))) * 60,
                    player=player)
    next_spawn = stamp + _next_delay(interval)
    db.execute('INSERT INTO rocket_schedule(player_id,last_spawn_ms,next_spawn_ms,last_incident_id) '
               'VALUES(?,?,?,?) ON CONFLICT(player_id) DO UPDATE SET '
               'last_spawn_ms=excluded.last_spawn_ms,next_spawn_ms=excluded.next_spawn_ms,'
               'last_incident_id=excluded.last_incident_id',
               (player, stamp, next_spawn, balloon['incident_id']))
    return balloon


def mark_caught(db, player, incident_id, spawn_id=None):
    ensure(db)
    stamp = now_ms()
    db.execute('UPDATE rocket_encounters SET caught_ms=? WHERE player_id=? AND (incident_id=? OR spawn_id=?)',
               (stamp, player, incident_id, spawn_id or 0))
    row = db.execute('SELECT spawn_id,encounter_spawn_id FROM rocket_balloons WHERE incident_id=?',
                     (incident_id,)).fetchone()
    if row:
        for value in (row['spawn_id'], row['encounter_spawn_id']):
            if value:
                db.execute('UPDATE spawns SET expires_ms=? WHERE id=?', (stamp, value))
        db.execute('DELETE FROM rocket_balloons WHERE incident_id=?', (incident_id,))
    db.execute('UPDATE rocket_balloon_log SET caught_ms=? WHERE incident_id=?',
               (stamp, incident_id))


def nearest(db, lat, lng, player=None):
    rows = active(db, player)
    if lat is None or lng is None:
        return rows[0] if rows else None

    def squared(row):
        return (row['lat'] - lat) ** 2 + ((row['lng'] - lng) *
                                          math.cos(math.radians(lat))) ** 2

    return min(rows, key=squared) if rows else None
