"""Local-only administrator console for the family development server.

Runs on ``http://127.0.0.1:8080`` and edits the same SQLite database the game
server uses. It never opens a public socket and is not part of the game
protocol. The published world snapshot is never rewritten: gyms and stops are
stored in ``data/world/admin-pois.json`` and merged by ``world_admin``.
"""
import argparse
import contextlib
import json
import logging
import math
import random
import re
import secrets
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

from dittobuffer.paths import ROOT
from dittobuffer.gameplay.routes import DEFAULTS as DEFAULTS_ROUTES
import game_pb2 as p
from s2sphere import CellId, LatLng

UI = ROOT / 'src/dittobuffer/admin/ui/admin_ui.html'
NAMES_CACHE = ROOT / 'data/admin/pokeapi-species.json'
FALLBACK_NAMES = {i: f'Pokémon #{i}' for i in range(1, 1026)}

_forms = None
_species = None
_reference = None
_names = None
_names_lock = threading.Lock()
_device_lock = threading.Lock()
_device_cache = {'at': 0.0, 'value': None}


def now_ms():
    return int(time.time() * 1000)


def load_catalog():
    global _forms, _species, _reference
    if _forms is None:
        from dittobuffer.gameplay.species import catalog, form_catalog, REFERENCE
        forms = form_catalog(ROOT / 'data/static/gmts.binpb.gz')
        _forms = {key: dict(rules) for key, rules in forms.items()}
        _species = {sid: _forms[(sid, rules['form'])] for sid, rules in catalog(ROOT / 'data/static/gmts.binpb.gz').items()}
        _reference = REFERENCE
    return _forms, _species, _reference


COSTUME_MANIFEST = ROOT / 'builds' / 'm113-reviewed-assets.json'


def form_options(species):
    """Every stat-bearing form of a species, ordinary form first."""
    forms, catalog, _ = load_catalog()
    options = []
    for (sid, form), rules in forms.items():
        if sid != species:
            continue
        label = str(rules.get('name', 'FORM')).replace('_', ' ').title()
        options.append({'id': form, 'name': label, 'suffix': rules.get('asset_suffix', '')})
    options.sort(key=lambda item: (item['id'] != catalog[species]['form'], item['id']))
    return options


def max_form_options(species, config=None):
    """Forms that the installed Max reference can actually serve."""
    from dittobuffer.gameplay import max_system
    cfg = max_system.DEFAULTS | ((config or {}).get('max_system') or {})
    try:
        groups = max_system.reference(cfg['reference'])['species_groups']
    except (OSError, ValueError, KeyError):
        return form_options(species)
    return [entry for entry in form_options(species) if f'{int(species)}:{entry["id"]}' in groups]


def costume_options(species):
    """Costumes that have dependency-complete local asset mappings."""
    try:
        costumes = json.loads(COSTUME_MANIFEST.read_text(encoding='utf8')).get('costumes', {})
    except (OSError, ValueError):
        return []
    result = []
    for key, value in costumes.items():
        try:
            sid, cid = (int(part) for part in str(key).split(':', 1))
        except ValueError:
            continue
        if sid != species:
            continue
        suffix = str(value.get('suffix', '')).replace('_', ' ').title()
        result.append({'id': cid, 'name': suffix or f'Costume {cid}'})
    result.sort(key=lambda item: item['id'])
    return result



MEGA_NAMES = {1: 'Mega', 2: 'Mega X', 3: 'Mega Y'}
MAX_NAMES = {0: 'Normal', 1: 'Dynamax', 2: 'Gigantamax'}


def asset_manifest_path(config=None):
    """Prefer the configured reviewed manifest, then the shipped local copy."""
    configured = ((config or {}).get('pokemon_assets') or {}).get('manifest')
    candidates = []
    if configured:
        candidates.append(ROOT / configured)
    candidates.extend((COSTUME_MANIFEST, ROOT / 'assets/pokemon/m113-reviewed-assets.json'))
    return next((path for path in candidates if Path(path).is_file()), candidates[0])


_gigantamax = None


def gigantamax_species(config=None):
    """Species with a dependency-complete local Gigantamax (fLOAF) model."""
    global _gigantamax
    if _gigantamax is not None:
        return _gigantamax
    path = asset_manifest_path(config)
    result = set()
    try:
        variants = json.loads(Path(path).read_text(encoding='utf8')).get('variants') or []
    except (OSError, ValueError):
        return None
    for variant in variants:
        if variant.get('missing_dependencies'):
            continue
        address = str(variant.get('address') or '')
        species = variant.get('species')
        if species and ('.fLOAF' in address or '.fETERNAMAX' in address):
            result.add(int(species))
    _gigantamax = result
    return result


def max_options(species, config=None):
    """Admin max modes for a species; Gigantamax requires a local model."""
    options = [{'id': 0, 'name': MAX_NAMES[0]}, {'id': 1, 'name': MAX_NAMES[1]}]
    available = gigantamax_species(config)
    if available is None or int(species) in available:
        options.append({'id': 2, 'name': MAX_NAMES[2]})
    return options


def validate_max_mode(species, mode, config=None):
    mode = int(mode or 0)
    if mode not in MAX_NAMES:
        raise ValueError('Max mode must be 0, 1 or 2')
    if mode == 2:
        available = gigantamax_species(config)
        if available is not None and int(species) not in available:
            raise ValueError('This species has no local Gigantamax asset')
    return mode


_item_catalog = None


def item_catalog():
    """Every item the client protocol knows, for arbitrary admin grants."""
    global _item_catalog
    if _item_catalog is None:
        seen = {}
        for name, value in p.Item.items():
            if value <= 0 or value in seen:
                continue
            label = name[5:] if name.startswith('Item_') else name
            label = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', label).replace('_', ' ').strip()
            seen[value] = {'id': value, 'name': label}
        _item_catalog = sorted(seen.values(), key=lambda entry: entry['id'])
    return _item_catalog


def mega_species():
    from dittobuffer.gameplay.mega import SPECIES
    names = species_names()
    return [{'id': species, 'name': names.get(species, f'Pokémon #{species}'),
             'forms': [{'id': branch['temp_evo'], 'name': MEGA_NAMES.get(branch['temp_evo'],
                                                                        f'Mega {branch["temp_evo"]}')}
                       for branch in entry.get('branches', [])]}
            for species, entry in sorted(SPECIES.items())]


def mega_options(species):
    """Source-backed temporary evolutions available for this species."""
    from dittobuffer.gameplay.mega import SPECIES
    entry = SPECIES.get(int(species)) or {}
    return [{'id': branch['temp_evo'], 'name': MEGA_NAMES.get(branch['temp_evo'], f'Mega {branch["temp_evo"]}')}
            for branch in entry.get('branches', [])]


def species_names():
    global _names
    with _names_lock:
        if _names is not None:
            return _names
        if NAMES_CACHE.is_file():
            try:
                _names = {int(k): v for k, v in json.loads(NAMES_CACHE.read_text(encoding='utf8')).items()}
                return _names
            except (OSError, ValueError):
                pass
        names = dict(FALLBACK_NAMES)
        try:
            context = ssl._create_unverified_context()
            request = urllib.request.Request('https://pokeapi.co/api/v2/pokemon?limit=2000',
                                             headers={'User-Agent': 'Dittobuffer-Admin/1.0'})
            with urllib.request.urlopen(request, timeout=12, context=context) as response:
                document = json.loads(response.read().decode('utf8'))
            fetched = {}
            for entry in document.get('results', []):
                match = re.search(r'/pokemon/(\d+)/?$', str(entry.get('url', '')))
                if match:
                    fetched[int(match.group(1))] = str(entry.get('name', '')).replace('-', ' ').title()
            if fetched:
                names.update(fetched)
        except Exception:
            pass
        NAMES_CACHE.parent.mkdir(parents=True, exist_ok=True)
        NAMES_CACHE.write_text(json.dumps({str(k): v for k, v in sorted(names.items())}, indent=0), encoding='utf8')
        _names = names
        return _names


class Admin:
    def __init__(self, config_path, port, host):
        self.config_path = Path(config_path).resolve()
        self.config = json.loads(self.config_path.read_text(encoding='utf8'))
        self.db_path = (ROOT / self.config['database']).resolve()
        self.port = port
        self.host = host
        self.lock = threading.Lock()
        self.ensure_schema()
        threading.Thread(target=species_names, daemon=True).start()
        from dittobuffer.world import poi_import
        poi_import.start(lambda: self.player_location())

    # ---------------------------------------------------------------- storage
    def connect(self):
        connection = sqlite3.connect(str(self.db_path), timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA busy_timeout=10000')
        return connection

    def ensure_schema(self):
        with contextlib.closing(self.connect()) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS admin_locations(player_id TEXT PRIMARY KEY, lat REAL NOT NULL,
                    lng REAL NOT NULL, accuracy REAL NOT NULL DEFAULT 0, seen_ms INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS admin_spawns(spawn_id INTEGER PRIMARY KEY, created_ms INTEGER NOT NULL,
                    label TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS admin_raids(seed INTEGER PRIMARY KEY, created_ms INTEGER NOT NULL,
                    label TEXT NOT NULL DEFAULT '');
            ''')

    def player_location(self, db=None, player=None):
        own = db is None
        db = db or self.connect()
        try:
            if player:
                row = db.execute('SELECT lat,lng,seen_ms FROM admin_locations WHERE player_id=?', (player,)).fetchone()
                if row:
                    return dict(row)
                row = db.execute('SELECT lat,lng,fix_ms AS seen_ms FROM walking WHERE player_id=? AND lat IS NOT NULL', (player,)).fetchone()
                return dict(row) if row else None
            row = db.execute('SELECT lat,lng,seen_ms FROM admin_locations ORDER BY seen_ms DESC LIMIT 1').fetchone()
            if row:
                return dict(row)
            row = db.execute('SELECT lat,lng,seen_ms FROM walking WHERE lat IS NOT NULL ORDER BY COALESCE(seen_ms,fix_ms,0) DESC LIMIT 1').fetchone()
            return dict(row) if row else None
        finally:
            if own:
                db.close()

    # ------------------------------------------------------------------ API
    def status(self):
        with self.connect() as db:
            counts = {
                'players': db.execute('SELECT COUNT(*) FROM players').fetchone()[0],
                'accounts': db.execute('SELECT COUNT(*) FROM family_accounts').fetchone()[0],
                'active_spawns': db.execute('SELECT COUNT(*) FROM spawns WHERE expires_ms>?', (now_ms(),)).fetchone()[0],
                'admin_spawns': db.execute('SELECT COUNT(*) FROM admin_spawns s JOIN spawns p ON p.id=s.spawn_id WHERE p.expires_ms>?', (now_ms(),)).fetchone()[0],
                'active_raids': db.execute('SELECT COUNT(*) FROM raids WHERE end_ms>?', (now_ms(),)).fetchone()[0],
                'forts': db.execute('SELECT COUNT(*) FROM forts').fetchone()[0],
            }
        from dittobuffer.gameplay.random_spawns import settings as random_settings
        return {'counts': counts, 'location': self.player_location(), 'readonly': self.readonly(), 'config': {
            'database': str(self.db_path), 'world': self.config.get('world', {}),
            'spawn_generation': self.config.get('spawn_generation', 1),
            'random_spawns': random_settings(self.config),
            'target': '0.429.2', 'host': self.host, 'port': self.port}}

    def spawn_settings(self):
        from dittobuffer.gameplay.random_spawns import settings as random_settings
        return {'random_spawns': random_settings(self.config),
                'spawn_duration_seconds': self.config.get('spawn_duration_seconds', 1800)}

    def raid_settings(self):
        raids = self.config.get('raids') or {}
        return {'lobby_seconds': max(20, min(600, int(raids.get('lobby_seconds', 120)))),
                'relobby_seconds': max(20, min(600, int(raids.get('relobby_seconds', 120)))),
                'battle_seconds': max(20, min(3600, int(raids.get('battle_seconds', 120))))}

    def save_raid_settings(self, body):
        raw = body.get('raids', body) if isinstance(body, dict) else {}
        if not isinstance(raw, dict):
            raise ValueError('Raid settings must be an object')
        self.config.setdefault('raids', {})
        for key in ('lobby_seconds', 'relobby_seconds', 'battle_seconds'):
            if key in raw:
                self.config['raids'][key] = max(20, min(3600, int(raw[key])))
        self.config_path.write_text(json.dumps(self.config, indent=2) + '\n', encoding='utf8')
        return self.raid_settings()

    def save_spawn_settings(self, body):
        from dittobuffer.gameplay.random_spawns import settings as random_settings
        raw = body.get('random_spawns', body) if isinstance(body, dict) else {}
        if not isinstance(raw, dict):
            raise ValueError('random_spawns must be an object')
        self.config['random_spawns'] = random_settings({'random_spawns': raw})
        self.config_path.write_text(json.dumps(self.config, indent=2) + '\n', encoding='utf8')
        return self.spawn_settings()


    def reward_catalog(self):
        return {'players': self.players(), 'items': item_catalog(), 'mega': mega_species()}

    def loot(self):
        from dittobuffer.world.world_rewards import default, load
        return {'table': load(self.config), 'defaults': default(self.config), 'items': item_catalog()}

    def save_loot(self, body):
        from dittobuffer.world.world_rewards import save
        table = body.get('table') if isinstance(body, dict) and 'table' in body else body
        return {'table': save(self.config, table)}

    def grant_rewards(self, body):
        player_id = str(body.get('player_id') or '').strip()
        if not player_id:
            raise ValueError('Choose a player')
        xp = max(0, min(50_000_000, int(body.get('xp', 0) or 0)))
        stardust = max(0, min(50_000_000, int(body.get('stardust', 0) or 0)))
        items = {int(key): max(0, min(9999, int(value))) for key, value in (body.get('items') or {}).items()}
        candy = {int(key): max(0, min(99999, int(value))) for key, value in (body.get('candy') or {}).items()}
        xl_candy = {int(key): max(0, min(99999, int(value))) for key, value in (body.get('xl_candy') or {}).items()}
        pokemon_storage = max(0, min(5000, int(body.get('pokemon_storage', 0) or 0)))
        item_storage = max(0, min(5000, int(body.get('item_storage', 0) or 0)))
        mega_energy = body.get('mega_energy') or []
        if not isinstance(mega_energy, list) or len(mega_energy) > 20:
            raise ValueError('Mega Energy must be a list of at most 20 grants')
        with self.connect() as db:
            row = db.execute('SELECT xp,stardust FROM players WHERE id=?', (player_id,)).fetchone()
            if not row:
                raise ValueError('Unknown player')
            db.execute('UPDATE players SET xp=xp+?,stardust=stardust+?,revision=revision+1 WHERE id=?',
                       (xp, stardust, player_id))
            for item, count in items.items():
                if count:
                    db.execute('INSERT INTO items VALUES(?,?,?) ON CONFLICT(player_id,item) DO UPDATE SET count=count+excluded.count',
                               (player_id, item, count))
            for family, count in candy.items():
                if count:
                    db.execute('INSERT INTO candy VALUES(?,?,?) ON CONFLICT(player_id,family) DO UPDATE SET count=count+excluded.count',
                               (player_id, family, count))
            for family, count in xl_candy.items():
                if count:
                    db.execute('INSERT INTO mega_catch_xl VALUES(?,?,?) ON CONFLICT(player_id,family) DO UPDATE SET count=count+excluded.count',
                               (player_id, family, count))
            if pokemon_storage or item_storage:
                db.execute('UPDATE family_accounts SET pokemon_capacity=pokemon_capacity+?,item_capacity=item_capacity+? WHERE player_id=?',
                           (pokemon_storage, item_storage, player_id))
            granted_energy = []
            if mega_energy:
                from dittobuffer.gameplay.mega import SPECIES, grant
                fake = SimpleNamespace(config=self.config, touch=lambda _db, _player: None)
                for entry in mega_energy:
                    if not isinstance(entry, dict):
                        raise ValueError('Invalid Mega Energy grant')
                    species = int(entry.get('species', 0))
                    evo = int(entry.get('evo', 0))
                    amount = max(0, min(50000, int(entry.get('amount', 0) or 0)))
                    if species not in SPECIES or not any(branch['temp_evo'] == evo for branch in SPECIES[species]['branches']):
                        raise ValueError('Unknown Mega species/form')
                    if amount:
                        awarded = grant(fake, db, player_id, species, evo, amount, 'admin:' + secrets.token_hex(8))
                        granted_energy.append({'species': species, 'evo': evo, 'amount': awarded})
            db.commit()
            updated = db.execute('SELECT xp,stardust FROM players WHERE id=?', (player_id,)).fetchone()
            inventory = {str(row[0]): row[1] for row in
                         db.execute('SELECT item,count FROM items WHERE player_id=?', (player_id,))}
            capacity = db.execute('SELECT pokemon_capacity,item_capacity FROM family_accounts WHERE player_id=?',
                                  (player_id,)).fetchone()
        return {'player_id': player_id, 'xp': updated['xp'], 'stardust': updated['stardust'],
                'pokemon_capacity': capacity['pokemon_capacity'] if capacity else self.config.get('pokemon_capacity'),
                'item_capacity': capacity['item_capacity'] if capacity else self.config.get('item_capacity'),
                'granted': {'xp': xp, 'stardust': stardust, 'items': items, 'candy': candy,
                            'xl_candy': xl_candy, 'pokemon_storage': pokemon_storage, 'item_storage': item_storage,
                            'mega_energy': granted_energy},
                'items': inventory}

    def players(self):
        from dittobuffer.gameplay.progression import bounds
        with self.connect() as db:
            rows = db.execute('''
                SELECT p.id,p.name,p.xp,p.created_ms,fa.username,fa.pokemon_capacity,fa.item_capacity,
                       COALESCE(al.lat,w.lat) lat, COALESCE(al.lng,w.lng) lng,
                       COALESCE(al.seen_ms,w.fix_ms,w.seen_ms) seen_ms,
                       (SELECT COUNT(*) FROM sessions s WHERE s.player_id=p.id) sessions
                FROM players p
                LEFT JOIN family_accounts fa ON fa.player_id=p.id
                LEFT JOIN walking w ON w.player_id=p.id
                LEFT JOIN admin_locations al ON al.player_id=p.id
                ORDER BY COALESCE(al.seen_ms,w.seen_ms,p.created_ms) DESC
            ''').fetchall()
        result = []
        for row in rows:
            value = dict(row)
            try:
                value['level'] = bounds(self.config, value['xp'])[0]
            except Exception:
                value['level'] = 1
            result.append(value)
        return result

    def species(self):
        names = species_names()
        try:
            _, catalog, _ = load_catalog()
            ids = sorted(catalog)
        except Exception:
            ids = sorted(names)
        return [{'id': i, 'name': names.get(i, f'Pokémon #{i}')} for i in ids if i <= 1025]

    def spawns(self, query):
        limit = min(500, max(1, int(query.get('limit', ['300'])[0])))
        lat = query.get('lat', [None])[0]
        lng = query.get('lng', [None])[0]
        radius = float(query.get('radius', ['1500'])[0])
        sql = ('SELECT s.*, a.data, a.level, (ad.spawn_id IS NOT NULL) admin FROM spawns s '
               'LEFT JOIN spawn_attributes a ON a.spawn_id=s.id '
               'LEFT JOIN admin_spawns ad ON ad.spawn_id=s.id WHERE s.expires_ms>?')
        args = [now_ms()]
        if lat is not None and lng is not None:
            lat, lng = float(lat), float(lng)
            dy = radius / 111320
            dx = dy / max(.1, math.cos(math.radians(lat)))
            sql += ' AND s.lat BETWEEN ? AND ? AND s.lng BETWEEN ? AND ?'
            args += [lat - dy, lat + dy, lng - dx, lng + dx]
            sql += ' ORDER BY s.expires_ms DESC LIMIT ?'
            args += [limit]
        else:
            sql += ' ORDER BY s.expires_ms DESC LIMIT ?'
            args += [limit]
        result = []
        with self.connect() as db:
            for row in db.execute(sql, args):
                value = dict(row)
                pokemon = None
                if value.get('data'):
                    try:
                        pokemon = p.PokemonProto.FromString(value.pop('data'))
                    except Exception:
                        pokemon = None
                result.append({
                    'id': str(value['id']), 'species': value['species'], 'lat': value['lat'], 'lng': value['lng'],
                    'expires_ms': value['expires_ms'], 'spawnpoint': value['spawnpoint'],
                    'level': value.get('level'), 'admin': bool(value.get('admin')),
                    'shiny': bool(pokemon and pokemon.pokemon_display.shiny),
                    'shadow': bool(pokemon and pokemon.pokemon_display.alignment == 1),
                    'ivs': [pokemon.individual_attack, pokemon.individual_defense, pokemon.individual_stamina] if pokemon else None,
                    'cp': pokemon.cp if pokemon else None, 'form': pokemon.pokemon_display.form if pokemon else 0,
                    'lucky': bool(pokemon and pokemon.is_lucky),
                    'mega': pokemon.pokemon_display.current_temp_evolution if pokemon else 0,
                    'max': pokemon.pokemon_display.bread_mode_enum if pokemon else 0,
                })
        return result

    def _spawn_one(self, db, lat, lng, species, level, ivs, shiny, shadow, label, duration, form=None, costume=0, lucky=False, mega=0, max_mode=0):
        forms, catalog, reference = load_catalog()
        from dittobuffer.gameplay.species import choose_gender, make_pokemon
        dummy = SimpleNamespace(pokemon_genders=reference['genders'], forms=forms, config=self.config)
        rules = forms.get((species, catalog[species]['form'] if form is None else int(form)))
        if rules is None:
            raise ValueError('Unknown form for this species')
        gender = choose_gender(dummy, species, random.Random())
        pokemon = make_pokemon(p, rules, species, level, ivs, shiny, gender, costume=int(costume or 0))
        if shadow:
            pokemon.pokemon_display.alignment = 1
        if max_mode:
            pokemon.pokemon_display.bread_mode_enum = int(max_mode)
        stamp = now_ms()
        pokemon.creation_time_ms = stamp
        if lucky:
            pokemon.is_lucky = True
        if mega:
            pokemon.pokemon_display.current_temp_evolution = int(mega)
            pokemon.pokemon_display.temporary_evolution_finish_ms = stamp + duration * 1000
            pokemon.pokemon_display.temp_evolution_is_locked = False
        spawn_id = secrets.randbits(63)
        base = CellId.from_lat_lng(LatLng.from_degrees(lat, lng))
        token = None
        for depth in (20, 19, 18):
            candidate = base.parent(depth).to_token()
            if not db.execute('SELECT 1 FROM spawns WHERE spawnpoint=? AND expires_ms>?', (candidate, stamp)).fetchone():
                token = candidate
                break
        token = token or base.parent(17).to_token()
        expires = stamp + duration * 1000
        db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)', (spawn_id, token, species, lat, lng, expires))
        db.execute('INSERT INTO spawn_attributes VALUES(?,?,?)', (spawn_id, pokemon.SerializeToString(), level))
        db.execute('INSERT INTO spawn_generations VALUES(?,?)', (spawn_id, self.config.get('spawn_generation', 1)))
        db.execute('INSERT OR REPLACE INTO admin_spawns VALUES(?,?,?)', (spawn_id, stamp, label))
        return {'id': str(spawn_id), 'species': species, 'lat': lat, 'lng': lng, 'expires_ms': expires,
                'shiny': shiny, 'shadow': shadow, 'level': level, 'form': pokemon.pokemon_display.form,
                'costume': pokemon.pokemon_display.costume, 'form_name': rules.get('name', ''),
                'lucky': bool(lucky), 'mega': int(mega or 0), 'max': int(max_mode or 0)}

    def create_spawns(self, body):
        species = int(body.get('species'))
        forms, catalog, _ = load_catalog()
        if species not in catalog:
            raise ValueError('Unsupported species')
        level = max(1, min(50, int(body.get('level', 25))))
        ivs = [max(0, min(15, int(body.get(key, 15)))) for key in ('iv_attack', 'iv_defense', 'iv_stamina')]
        shiny = bool(body.get('shiny'))
        shadow = bool(body.get('shadow'))
        lucky = bool(body.get('lucky'))
        mega = int(body.get('mega', 0) or 0)
        max_mode = validate_max_mode(species, body.get('max_mode', body.get('max', 0)), self.config)
        form = None if body.get('form') in (None, '') else int(body['form'])
        costume = int(body.get('costume', 0) or 0)
        if form is not None and (species, form) not in forms:
            raise ValueError('Unknown form for this species')
        if mega and mega not in {entry['id'] for entry in mega_options(species)}:
            raise ValueError('This species has no supported Mega form')
        if costume and not any(entry['id'] == costume for entry in costume_options(species)):
            raise ValueError('Costume has no local asset mapping for this species')
        count = max(1, min(25, int(body.get('count', 1))))
        radius = max(0.0, min(500.0, float(body.get('radius_m', 0))))
        duration = max(60, min(7 * 86400, int(body.get('duration_s', 86400))))
        location = self.player_location() if body.get('lat') is None or body.get('lng') is None else None
        if location:
            lat, lng = location['lat'], location['lng']
        else:
            lat, lng = float(body['lat']), float(body['lng'])
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError('Invalid coordinates')
        label = str(body.get('label', ''))[:80]
        created = []
        with self.connect() as db:
            for index in range(count):
                angle = 2 * math.pi * index / max(1, count)
                distance = radius if count == 1 else radius * ((index + 1) / count) ** .5
                slat = lat + distance * math.cos(angle) / 111320
                slng = lng + distance * math.sin(angle) / (111320 * max(.01, math.cos(math.radians(lat))))
                created.append(self._spawn_one(db, slat, slng, species, level, ivs, shiny, shadow, label, duration, form, costume, lucky, mega, max_mode))
            db.commit()
        return created

    def delete_spawn(self, spawn_id):
        with self.connect() as db:
            for table in ('spawns', 'spawn_attributes', 'spawn_generations', 'admin_spawns'):
                db.execute(f'DELETE FROM {table} WHERE {"spawn_id" if table != "spawns" else "id"}=?', (int(spawn_id),))
            db.commit()
        return {'deleted': spawn_id}

    def clear_admin_spawns(self):
        """Expire every admin-created spawn and clear its admin marker.

        Caught Pokemon keep a foreign-key reference to their original spawn,
        so the spawn row is expired rather than deleted.  Active encounters are
        closed at the same time so they cannot keep a removed spawn on screen.
        """
        stamp = now_ms()
        with contextlib.closing(self.connect()) as db:
            ids = [row[0] for row in db.execute('SELECT spawn_id FROM admin_spawns')]
            if ids:
                marks = ','.join('?' for _ in ids)
                db.execute('DELETE FROM encounter_deadlines WHERE spawn_id IN (' + marks + ')', ids)
                db.execute('DELETE FROM encounter_items WHERE spawn_id IN (' + marks + ')', ids)
                db.execute('DELETE FROM encounters WHERE spawn_id IN (' + marks + ')', ids)
                db.execute('UPDATE spawns SET expires_ms=? WHERE id IN (' + marks + ')', [stamp, *ids])
                db.execute('DELETE FROM admin_spawns')
            db.commit()
        return {'deleted': len(ids)}

    def pois(self):
        from dittobuffer.world import world_admin
        return world_admin.load()

    def create_poi(self, body):
        from dittobuffer.world import world_admin
        kind = int(body.get('kind', 0))
        if kind not in (0, 1):
            raise ValueError('kind must be 0 (gym) or 1 (stop)')
        name = str(body.get('name', '')).strip()
        if not name:
            raise ValueError('A name is required')
        location = self.player_location() if body.get('lat') is None or body.get('lng') is None else None
        lat = float(location['lat'] if location else body['lat'])
        lng = float(location['lng'] if location else body['lng'])
        identity = f'admin:{"gym" if kind == 0 else "stop"}-{secrets.token_hex(5)}'
        row = {'id': identity, 'name': name[:60], 'lat': lat, 'lng': lng, 'kind': kind,
               'status': 'APPROVED', 'category': 'gym' if kind == 0 else 'stop'}
        rows = world_admin.load()
        rows.append(row)
        world_admin.save(rows)
        stamp = now_ms()
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO forts VALUES(?,?,?,?,?)', (identity, kind, row['name'], lat, lng))
            if kind == 0:
                db.execute('INSERT OR IGNORE INTO gyms(fort_id,modified_ms) VALUES(?,?)', (identity, stamp))
            db.commit()
        return row

    def delete_poi(self, identity):
        from dittobuffer.world import world_admin
        rows = [row for row in world_admin.load() if row['id'] != identity]
        world_admin.save(rows)
        kept = False
        with self.connect() as db:
            attached = db.execute('SELECT 1 FROM gym_defenders WHERE fort_id=? LIMIT 1', (identity,)).fetchone()
            battle = db.execute('SELECT 1 FROM gym_battles WHERE fort_id=? AND settled=0 LIMIT 1', (identity,)).fetchone()
            raid = db.execute('SELECT 1 FROM raids WHERE fort_id=? AND end_ms>? LIMIT 1', (identity, now_ms())).fetchone()
            if attached or battle or raid:
                kept = True
            else:
                db.execute('DELETE FROM gyms WHERE fort_id=?', (identity,))
                db.execute('DELETE FROM forts WHERE id=?', (identity,))
            db.commit()
        return {'deleted': identity, 'kept_history': kept}

    def gyms(self):
        with self.connect() as db:
            rows = db.execute('SELECT id,name,lat,lng FROM forts WHERE kind=0 ORDER BY name LIMIT 1000').fetchall()
        return [dict(row) for row in rows]

    def raids(self):
        result = []
        with self.connect() as db:
            rows = db.execute('''SELECT r.*, f.name fort_name, f.lat, f.lng,
                                        (ar.seed IS NOT NULL) admin
                                 FROM raids r JOIN forts f ON f.id=r.fort_id
                                 LEFT JOIN admin_raids ar ON ar.seed=r.seed
                                 WHERE r.end_ms>? ORDER BY r.battle_ms''', (now_ms(),)).fetchall()
        for row in rows:
            value = dict(row)
            value.pop('pokemon', None)
            try:
                definition = json.loads(value['definition'])
            except Exception:
                definition = {}
            result.append({**value, 'species': definition.get('species'), 'tier': definition.get('tier'),
                           'shiny_rate': definition.get('shiny_rate', 0), 'shadow': bool(definition.get('shadow')),
                           'level': definition.get('level', 20), 'hp': definition.get('hp'),
                           'cp': definition.get('cp'), 'mega': definition.get('mega_evolution', 0),
                           'max': definition.get('bread_mode', 0),
                           'iv_floor': definition.get('iv_floor', 10),
                           'battle_seconds': definition.get('_rules', {}).get('battle_seconds'),
                           'lobby_seconds': definition.get('_rules', {}).get('lobby_seconds'),
                           'egg': value['battle_ms'] > now_ms(),
                           'admin': bool(value.get('admin'))})
        return result

    def create_raid(self, body):
        forms, catalog, _ = load_catalog()
        species = int(body.get('species'))
        if species not in catalog:
            raise ValueError('Unsupported species')
        mega = int(body.get('mega', 0) or 0)
        max_mode = validate_max_mode(species, body.get('max_mode', body.get('max', 0)), self.config)
        if mega and mega not in {entry['id'] for entry in mega_options(species)}:
            raise ValueError('This species has no supported Mega form')
        tier = 6 if mega else int(body.get('tier', 5))
        if tier not in (1, 3, 5, 6):
            raise ValueError('tier must be 1, 3, 5 or 6')
        if tier == 6 and not mega:
            raise ValueError('Tier 6 raids need a Mega form')
        fort_id = str(body.get('fort_id', ''))
        with self.connect() as db:
            fort = db.execute('SELECT * FROM forts WHERE id=? AND kind=0', (fort_id,)).fetchone()
            if not fort:
                raise ValueError('Choose an existing gym')
            defaults = {1: (600, 5000), 3: (1200, 9000), 5: (2000, 22000), 6: (3000, 30000)}[tier]
            hp = max(10, int(body.get('hp', defaults[0])))
            cp = max(10, int(body.get('cp', defaults[1])))
            level = max(1, min(50, int(body.get('level', 20))))
            shiny_rate = max(0.0, min(1.0, float(body.get('shiny_rate', 0.05))))
            shadow = bool(body.get('shadow'))
            egg_seconds = max(0, min(3600, int(body.get('egg_seconds', 0))))
            active_seconds = max(60, min(86400, int(body.get('active_seconds', 3600))))
            battle_seconds = max(20, min(3600, int(body.get('battle_seconds', 120))))
            lobby_seconds = max(20, min(600, int(body.get('lobby_seconds', self.raid_settings()['lobby_seconds']))))
            iv_floor = max(0, min(15, int(body.get('iv_floor', 10))))
            from dittobuffer.gameplay.species import make_pokemon
            form = None if body.get('form') in (None, '') else int(body['form'])
            costume = int(body.get('costume', 0) or 0)
            key = (species, catalog[species]['form'] if form is None else form)
            if key not in forms:
                raise ValueError('Unknown form for this species')
            if costume and not any(entry['id'] == costume for entry in costume_options(species)):
                raise ValueError('Costume has no local asset mapping for this species')
            variant = forms[key]
            seed = secrets.randbits(63)
            pokemon = make_pokemon(p, variant, species, level, [15, 15, 15], costume=costume)
            if shadow:
                pokemon.pokemon_display.alignment = 1
            pokemon.id = seed
            pokemon.cp = cp
            pokemon.stamina = pokemon.max_stamina = hp
            if max_mode:
                pokemon.pokemon_display.bread_mode_enum = int(max_mode)
            if mega:
                definition_mega = {'mega_evolution': mega, 'reward_policy': 'admin local',
                                   'mega_energy_rewards': [{'max_seconds': 300, 'amount': 200},
                                                           {'max_seconds': 600, 'amount': 150},
                                                           {'max_seconds': 3600, 'amount': 100}]}
            rules = self.config.get('raids', {})
            definition = {
                'species': species, 'tier': tier, 'hp': hp, 'cp': cp, 'weight': 100,
                'xp': int(body.get('xp', 5000)), 'dust': int(body.get('dust', 1000)), 'level': level,
                'shiny_rate': shiny_rate, 'shadow': shadow, 'iv_floor': iv_floor,
                'bread_mode': max_mode,
                '_rules': {key: rules.get(key) for key in
                           ('lobby_seconds', 'relobby_seconds', 'battle_seconds', 'premier_balls',
                            'encounter_seconds', 'participation_limit') if rules.get(key) is not None},
            }
            if mega:
                definition.update(definition_mega)
            definition['_rules']['battle_seconds'] = battle_seconds
            definition['_rules']['lobby_seconds'] = lobby_seconds
            stamp = now_ms()
            battle = stamp + egg_seconds * 1000
            end = battle + active_seconds * 1000
            if mega:
                pokemon.pokemon_display.current_temp_evolution = mega
                pokemon.pokemon_display.temporary_evolution_finish_ms = end
                pokemon.pokemon_display.temp_evolution_is_locked = True
                pokemon.pokemon_display.locked_temp_evolution = mega
                # The raid card reads temp_evo_cp for the Mega form; leaving it
                # at zero makes the client show "0CP".
                pokemon.temp_evo_cp = cp
                pokemon.temp_evo_cp_multiplier = pokemon.cp_multiplier
                pokemon.temp_evo_stamina_modifier = 1
            db.execute('INSERT INTO raids VALUES(?,?,?,?,?,?,?)',
                       (seed, fort_id, json.dumps(definition), pokemon.SerializeToString(), stamp - 1000, battle, end))
            db.execute('INSERT OR IGNORE INTO gyms(fort_id,modified_ms) VALUES(?,?)', (fort_id, stamp))
            db.execute('INSERT OR REPLACE INTO admin_raids VALUES(?,?,?)', (seed, stamp, str(body.get('label', ''))[:80]))
            db.commit()
        return {'seed': str(seed), 'fort_id': fort_id, 'species': species, 'tier': tier, 'mega': mega,
                'max': max_mode, 'battle_ms': battle, 'end_ms': end, 'shiny_rate': shiny_rate, 'shadow': shadow,
                'battle_seconds': battle_seconds, 'lobby_seconds': lobby_seconds, 'iv_floor': iv_floor}

    def delete_raid(self, seed):
        with self.connect() as db:
            ids = [row[0] for row in db.execute('SELECT id FROM raid_battles WHERE seed=?', (int(seed),))]
            db.execute('DELETE FROM raids WHERE seed=?', (int(seed),))
            db.execute('DELETE FROM admin_raids WHERE seed=?', (int(seed),))
            db.execute('DELETE FROM raid_members WHERE seed=?', (int(seed),))
            db.execute('DELETE FROM raid_tokens WHERE seed=?', (int(seed),))
            for battle in ids:
                db.execute('DELETE FROM raid_battles WHERE id=?', (battle,))
            db.commit()
        return {'deleted': seed}

    def rocket(self):
        from dittobuffer.gameplay import rocket
        with self.connect() as db:
            balloons = rocket.active(db)
        return {'config': rocket.config(), 'balloons': balloons, 'species': self.species()}

    def save_rocket(self, body):
        from dittobuffer.gameplay import rocket
        value = body.get('config', body) if isinstance(body, dict) else body
        return {'config': rocket.save_config(value)}

    def spawn_rocket_balloon(self, body):
        from dittobuffer.gameplay import rocket
        kind = str(body.get('kind', 'grunt'))
        character_id = str(body.get('character_id', ''))
        forms, catalog, reference = load_catalog()
        location = self.player_location() if body.get('lat') is None or body.get('lng') is None else None
        lat = float(location['lat'] if location else body['lat'])
        lng = float(location['lng'] if location else body['lng'])
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError('Invalid coordinates')
        game = SimpleNamespace(forms=forms, species=catalog, config=self.config,
                               pokemon_genders=reference['genders'])
        duration = max(60, min(86400, int(body.get('duration_s', 1200))))
        with self.connect() as db:
            result = rocket.spawn(game, db, kind, character_id, lat, lng, duration)
            db.commit()
        return result

    def rescue_rocket_balloon(self, incident_id, body):
        from dittobuffer.gameplay import rocket
        player_id = str(body.get('player_id') or '').strip()
        if not player_id:
            raise ValueError('Choose a player')
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM players WHERE id=?', (player_id,)).fetchone():
                raise ValueError('Unknown player')
            result = rocket.rescue(db, player_id, incident_id)
            db.commit()
        return result

    def delete_rocket_balloon(self, incident_id):
        from dittobuffer.gameplay import rocket
        with self.connect() as db:
            result = rocket.clear(db, incident_id)
            db.commit()
        return result

    # ---------------------------------------------------------------- street map
    def map_proxy(self, path):
        """Plain-HTTP bridge to the game server's own map tile origin.

        The game process serves map settings and vector tiles on the plain
        loopback map port (``world.map_tiles_port``, 8081 by default) so map
        tiles work with the admin console closed.  The panel only proxies.
        """
        host = str(self.config.get('map_host') or '127.0.0.1')
        if host in ('0.0.0.0', '::', ''):
            host = '127.0.0.1'
        world = self.config.get('world') or {}
        port = int(world.get('map_tiles_port', 8081))
        request = urllib.request.Request(
            f'http://{host}:{port}{path}',
            headers={'User-Agent': 'Dittobuffer-Admin-Map/1.0', 'Accept': 'application/x-protobuf'})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()

    # ------------------------------------------------------------------ routes
    def routes(self):
        from dittobuffer.gameplay import routes as route_mod
        with contextlib.closing(self.connect()) as db:
            route_mod.storage(db)
            rows = route_mod.list_rows(db)
        return {'routes': rows, 'settings': dict(route_mod.DEFAULTS | (self.config.get('routes') or {}))}

    def create_route(self, body):
        from dittobuffer.gameplay import routes as route_mod
        with contextlib.closing(self.connect()) as db:
            route_mod.storage(db)
            return route_mod.create_row(db, body)

    def delete_route(self, route_id):
        from dittobuffer.gameplay import routes as route_mod
        with contextlib.closing(self.connect()) as db:
            route_mod.storage(db)
            return route_mod.delete_row(db, unquote(route_id))

    # ------------------------------------------------------------ feature flags
    def features(self):
        """Every gameplay switch the panel can flip, with its live value."""
        from dittobuffer.gameplay import rocket as rocket_mod
        world = self.config.get('world') or {}
        routes_cfg = dict(DEFAULTS_ROUTES | (self.config.get('routes') or {}))
        return {
            'rocket': {
                'enabled': bool(rocket_mod.config().get('enabled')),
                'combat_enabled': bool(rocket_mod.config().get('combat_enabled', True)),
                'hideouts_enabled': bool(rocket_mod.config().get('hideouts_enabled', True)),
                'balloon_interval_minutes': int(rocket_mod.config().get('balloon_interval_minutes', 120)),
            },
            'routes': routes_cfg,
            'world': {'map_tiles_port': int(world.get('map_tiles_port', 8081)),
                      'map_tiles_base': str(world.get('map_tiles_base') or '')},
            'max_system': dict(self.config.get('max_system') or {}),
            'pvp': {'enabled': bool((self.config.get('pvp') or {}).get('enabled'))},
        }

    def save_features(self, body):
        """Persist gameplay switches into the files the server loads."""
        if not isinstance(body, dict):
            raise ValueError('Feature settings must be an object')
        from dittobuffer.gameplay import rocket as rocket_mod
        current = rocket_mod.config()
        rocket_body = body.get('rocket') or {}
        for key in ('enabled', 'combat_enabled', 'hideouts_enabled'):
            if key in rocket_body:
                current[key] = bool(rocket_body[key])
        if 'balloon_interval_minutes' in rocket_body:
            current['balloon_interval_minutes'] = max(1, min(1440, int(rocket_body['balloon_interval_minutes'])))
        rocket_mod.save_config(current)
        if isinstance(body.get('routes'), dict):
            routes_cfg = dict(self.config.get('routes') or {})
            if 'enabled' in body['routes']:
                routes_cfg['enabled'] = bool(body['routes']['enabled'])
            for key in ('minimum_level', 'play_seconds', 'cooldown_seconds', 'max_favorites',
                        'stamp_spacing_m', 'reward_stardust', 'reward_xp'):
                if key in body['routes']:
                    routes_cfg[key] = int(body['routes'][key])
            self.config['routes'] = routes_cfg
        if isinstance(body.get('world'), dict) and 'map_tiles_port' in body['world']:
            port = max(1024, min(65535, int(body['world']['map_tiles_port'])))
            world = dict(self.config.get('world') or {})
            world['map_tiles_port'] = port
            if body['world'].get('rewrite_base', True):
                world['map_tiles_base'] = f'http://127.0.0.1:{port}/map/tiles'
            self.config['world'] = world
        if isinstance(body.get('max_system'), dict):
            max_cfg = dict(self.config.get('max_system') or {})
            for key in ('enabled', 'battle_enabled'):
                if key in body['max_system']:
                    max_cfg[key] = bool(body['max_system'][key])
            self.config['max_system'] = max_cfg
        if isinstance(body.get('pvp'), dict) and 'enabled' in body['pvp']:
            pvp_cfg = dict(self.config.get('pvp') or {})
            pvp_cfg['enabled'] = bool(body['pvp']['enabled'])
            self.config['pvp'] = pvp_cfg
        self.config_path.write_text(json.dumps(self.config, indent=2) + '\n', encoding='utf8')
        return self.features()

    def osm(self):
        from dittobuffer.world import osm_cache, world_map
        value = osm_cache.status(self.config.get('world') or {})
        value['tile_cache'] = world_map.tile_cache_status()
        return value

    def save_osm(self, body):
        from dittobuffer.world import osm_cache
        raw = body.get('world', body) if isinstance(body, dict) else {}
        if not isinstance(raw, dict):
            raise ValueError('Map settings must be an object')
        world = dict(self.config.get('world') or {})
        if 'osm_download' in raw:
            world['osm_download'] = bool(raw['osm_download'])
        if 'osm_download_radius_m' in raw:
            world['osm_download_radius_m'] = max(osm_cache.RADIUS_MIN_M,
                                                 min(osm_cache.RADIUS_MAX_M, int(raw['osm_download_radius_m'])))
        if 'osm_expand' in raw:
            world['osm_expand'] = bool(raw['osm_expand'])
        if 'osm_expand_ring_max' in raw:
            world['osm_expand_ring_max'] = max(0, int(raw['osm_expand_ring_max']))
        if 'osm_request_interval_s' in raw:
            world['osm_request_interval_s'] = max(osm_cache.MIN_INTERVAL_S,
                                                  min(osm_cache.MAX_INTERVAL_S, float(raw['osm_request_interval_s'])))
        if 'osm_expand_focus' in raw:
            focus = raw['osm_expand_focus']
            if isinstance(focus, dict) and focus.get('lat') is not None and focus.get('lng') is not None:
                world['osm_expand_focus'] = {'lat': float(focus['lat']), 'lng': float(focus['lng'])}
            else:
                # An empty focus field means "follow whoever is playing".
                world.pop('osm_expand_focus', None)
        self.config['world'] = world
        self.config_path.write_text(json.dumps(self.config, indent=2) + '\n', encoding='utf8')
        focus = world.get('osm_expand_focus')
        if isinstance(focus, dict):
            osm_cache.set_focus(focus['lat'], focus['lng'])
        return self.osm()

    def clear_map_cache(self):
        """Drop every persisted tile bundle; the OSM cell cache is untouched."""
        from dittobuffer.world import world_map
        world_map.tile.cache_clear()
        removed = world_map.clear_tile_cache()
        return {'cleared': removed['removed'], **self.osm()}

    # ----------------------------------------------------- OSM POI importing
    def poi_import(self):
        from dittobuffer.world import poi_import as importer
        return importer.status()

    def save_poi_import(self, body):
        from dittobuffer.world import poi_import as importer
        before = set(importer.generated_ids())
        importer.save(body)
        if not importer.settings()['enabled'] and before:
            with contextlib.closing(self.connect()) as db:
                for identity in before:
                    try:
                        db.execute('DELETE FROM forts WHERE id=?', (identity,))
                    except sqlite3.Error:
                        # A fresh install has no forts table yet; the overlay is
                        # already clean and materialize() never recreates them.
                        break
                db.commit()
        return importer.status()

    # ------------------------------------------------------- Max / Power Spots
    def _table_exists(self, db, name):
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())

    def max_admin(self):
        from dittobuffer.gameplay import max_system
        cfg = max_system.DEFAULTS | (self.config.get('max_system') or {})
        stations, wallets, daily = [], [], []
        with self.connect() as db:
            if self._table_exists(db, 'max_stations'):
                stations = []
                for row in db.execute(
                        'SELECT id,source_id,slot,name,lat,lng,start_ms,end_ms,definition FROM max_stations '
                        'ORDER BY end_ms DESC LIMIT 200'):
                    item = dict(row)
                    try:
                        definition = json.loads(item.pop('definition') or '{}')
                    except (TypeError, ValueError):
                        definition = {}
                    if not isinstance(definition, dict):
                        definition = {}
                    species = definition.get('species')
                    item.update(definition=definition,
                                manual=str(item['source_id']).startswith('manual:'),
                                species=int(species) if species else None,
                                form=definition.get('form'),
                                mode=int(definition.get('mode', 1) or 1),
                                tier=int(definition.get('tier', 1) or 1),
                                level=int(definition.get('level', 10) or 10),
                                active=item['start_ms'] <= now_ms() < item['end_ms'])
                    stations.append(item)
            if self._table_exists(db, 'max_wallet'):
                wallets = [dict(row) for row in db.execute(
                    'SELECT w.player_id,w.balance,p.name FROM max_wallet w '
                    'LEFT JOIN players p ON p.id=w.player_id ORDER BY w.balance DESC LIMIT 50')]
            if self._table_exists(db, 'max_daily'):
                from datetime import date
                daily = [dict(row) for row in db.execute(
                    'SELECT player_id,day,collected FROM max_daily ORDER BY day DESC LIMIT 50')]
        candidates = None
        try:
            path = (ROOT / str(cfg.get('candidates', ''))).resolve()
            if path.is_file():
                with contextlib.closing(sqlite3.connect('file:' + path.as_posix() + '?mode=ro', uri=True)) as source:
                    candidates = source.execute('SELECT COUNT(*) FROM candidates').fetchone()[0]
        except (sqlite3.Error, OSError, ValueError):
            candidates = None
        names = species_names()
        for item in stations:
            item['species_name'] = names.get(item['species'], item['species'])
        pools = []
        for entry in cfg.get('pools') or []:
            try:
                species = int(entry.get('species', 0))
            except (TypeError, ValueError):
                species = 0
            pools.append({**entry, 'name': names.get(species, species)})
        gmax = {'power_spot_supported': False, 'source_allowlisted': 0,
                'local_asset_species': None, 'allowlisted': []}
        try:
            data = max_system.reference(cfg['reference'])
            allowed = [value for value in
                       max_system.gm(data, 'BREAD_SHARED_SETTINGS').bread_settings.allowed_sourdough_pokemon
                       if value.bread_mode == 2]
            assets = gigantamax_species(self.config)
            gmax = {'power_spot_supported': bool(allowed), 'source_allowlisted': len(allowed),
                    'local_asset_species': len(assets) if assets is not None else None,
                    'allowlisted': [{'species': value.pokemon_id,
                                     'name': names.get(value.pokemon_id, value.pokemon_id),
                                     'forms': list(value.form)} for value in allowed]}
        except (OSError, ValueError, KeyError, AttributeError) as error:
            gmax['error'] = str(error)
        return {'config': cfg, 'pools': pools, 'stations': stations, 'wallets': wallets,
                'daily': daily, 'candidates': candidates, 'now_ms': now_ms(),
                'gmax': gmax, 'maximum_seconds': max_system.MANUAL_MAX_SECONDS,
                'species': self.species()}

    def save_max(self, body):
        from dittobuffer.gameplay import max_system
        raw = body.get('config', body) if isinstance(body, dict) else None
        if not isinstance(raw, dict):
            raise ValueError('Max settings must be an object')
        cfg = max_system.DEFAULTS | raw
        for key in ('enabled', 'battle_enabled'):
            cfg[key] = bool(cfg.get(key))
        for key, low, high in (('reset_hour', 0, 23), ('daily_limit', 1, 100000),
                               ('capacity', 1, 100000), ('station_particles', 1, 10000),
                               ('first_visit_bonus', 0, 10000), ('rotation_seconds', 60, 604800),
                               ('active_seconds', 30, 604800), ('minimum_level', 1, 100)):
            cfg[key] = max(low, min(high, int(cfg.get(key, max_system.DEFAULTS[key]))))
        if cfg['active_seconds'] > cfg['rotation_seconds']:
            raise ValueError('active_seconds cannot exceed rotation_seconds')
        if cfg.get('cap_policy') not in ('threshold', 'clamp'):
            raise ValueError('cap_policy must be threshold or clamp')
        pools = cfg.get('pools') or []
        if not isinstance(pools, list) or not 1 <= len(pools) <= 200:
            raise ValueError('pools must contain 1..200 species entries')
        cleaned, known = [], {entry['id']: entry for entry in self.species()}
        forms, catalog, _ = load_catalog()
        reference = max_system.reference(cfg['reference'])
        game = SimpleNamespace(forms=forms, species=catalog, max_reference=reference)
        for entry in pools:
            if not isinstance(entry, dict):
                raise ValueError('Each Max pool entry must be an object')
            try:
                species = int(entry.get('species', 0))
            except (TypeError, ValueError):
                raise ValueError('Invalid Max species') from None
            if species not in known:
                raise ValueError('Unknown Max species ' + str(species))
            form = entry.get('form')
            if form is None:
                form = max_system._default_form(game, species)
            try:
                boss = max_system.boss_definition(game, {**entry, 'species': species, 'form': form})
            except ValueError as error:
                raise ValueError(f'Max pool {species}:{form}: {error}') from None
            boss['level'] = max(1, min(40, int(boss.get('level', 20))))
            boss['weight'] = max(1, int(boss.get('weight', 1)))
            cleaned.append(boss)
        cfg['pools'] = cleaned
        stationing = cfg.get('stationing') or {}
        stationing['enabled'] = bool(stationing.get('enabled'))
        for key in ('candy_per_assist', 'xl_candy_per_assist', 'max_candy', 'max_xl_candy'):
            stationing[key] = max(0, min(10000, int(stationing.get(key, 0))))
        if not str(stationing.get('reward_provenance', '')).strip():
            stationing['reward_provenance'] = 'Explicit custom development assist rewards.'
        cfg['stationing'] = stationing
        self.config['max_system'] = cfg
        self.config_path.write_text(json.dumps(self.config, indent=2) + '\n', encoding='utf8')
        return self.max_admin()

    def max_action(self, body):
        from dittobuffer.gameplay import max_system
        action = str(body.get('action', '')).strip()
        player = str(body.get('player_id') or '').strip()
        with self.connect() as db:
            if action == 'clear_stations':
                if not self._table_exists(db, 'max_stations'):
                    return {'cleared': 0}
                cleared = db.execute('DELETE FROM max_stations').rowcount
                db.commit()
                return {'cleared': cleared}
            if action == 'expire_stations':
                if not self._table_exists(db, 'max_stations'):
                    return {'expired': 0}
                expired = db.execute('DELETE FROM max_stations WHERE end_ms<=?', (now_ms(),)).rowcount
                db.commit()
                return {'expired': expired}
            if action == 'set_particles':
                if not player:
                    raise ValueError('Choose a player')
                if not db.execute('SELECT 1 FROM players WHERE id=?', (player,)).fetchone():
                    raise ValueError('Unknown player')
                amount = max(0, min(1000000, int(body.get('amount', 0))))
                db.execute('INSERT INTO max_wallet(player_id,balance) VALUES(?,?) '
                           'ON CONFLICT(player_id) DO UPDATE SET balance=excluded.balance', (player, amount))
                db.commit()
                return {'player_id': player, 'balance': amount}
            if action == 'grant_particles':
                if not player:
                    raise ValueError('Choose a player')
                if not db.execute('SELECT 1 FROM players WHERE id=?', (player,)).fetchone():
                    raise ValueError('Unknown player')
                amount = max(0, min(1000000, int(body.get('amount', 0))))
                db.execute('INSERT INTO max_wallet(player_id,balance) VALUES(?,?) '
                           'ON CONFLICT(player_id) DO UPDATE SET balance=balance+excluded.balance', (player, amount))
                balance = db.execute('SELECT balance FROM max_wallet WHERE player_id=?', (player,)).fetchone()[0]
                db.commit()
                return {'player_id': player, 'balance': balance}
            if action == 'reset_daily':
                if not player:
                    raise ValueError('Choose a player')
                db.execute('DELETE FROM max_daily WHERE player_id=?', (player,))
                db.commit()
                return {'player_id': player, 'daily': 0}
            if action == 'place_station':
                cfg = max_system.DEFAULTS | (self.config.get('max_system') or {})
                if not cfg['enabled']:
                    raise ValueError('Enable Dynamax / Power Spots before placing one')
                if not self._table_exists(db, 'max_stations'):
                    raise ValueError('Power Spot tables are not installed yet; start the game server once')
                if body.get('lat') is None or body.get('lng') is None:
                    location = self.player_location()
                    if not location:
                        raise ValueError('Pick a point on the map first')
                    lat, lng = float(location['lat']), float(location['lng'])
                else:
                    lat, lng = float(body['lat']), float(body['lng'])
                entry = body.get('boss') if isinstance(body.get('boss'), dict) else {
                    'species': body.get('species'), 'form': body.get('form'),
                    'mode': body.get('mode', 1), 'tier': body.get('tier', 1),
                    'level': body.get('level', 10)}
                forms, catalog, _ = load_catalog()
                game = SimpleNamespace(forms=forms, species=catalog, max_config=cfg,
                                       max_reference=max_system.reference(cfg['reference']))
                result = max_system.place(game, db, lat, lng, body.get('name') or 'Power Spot',
                                          entry, body.get('duration_s', 86400))
                db.commit()
                return result
            if action in ('delete_station', 'end_station', 'extend_station'):
                ident = str(body.get('id') or '').strip()
                if not ident:
                    raise ValueError('Choose a Power Spot')
                if not self._table_exists(db, 'max_stations'):
                    raise ValueError('Power Spot tables are not installed')
                if action == 'delete_station':
                    result = max_system.remove(db, ident)
                elif action == 'end_station':
                    result = max_system.end_now(db, ident)
                else:
                    result = max_system.extend(db, ident, body.get('seconds', 3600))
                db.commit()
                return result
            if action == 'clear_manual':
                if not self._table_exists(db, 'max_stations'):
                    return {'deleted': 0, 'kept': []}
                deleted, kept = 0, []
                for row in db.execute("SELECT id FROM max_stations WHERE source_id LIKE 'manual:%'").fetchall():
                    try:
                        max_system.remove(db, row['id'])
                        deleted += 1
                    except ValueError:
                        kept.append(row['id'])
                db.commit()
                return {'deleted': deleted, 'kept': kept}
        raise ValueError('Unknown Max action ' + action)

    # ------------------------------------------------------------------ Trainer battles
    def pvp_admin(self):
        from dittobuffer.gameplay import pvp
        cfg = pvp.DEFAULTS | (self.config.get('pvp') or {})
        queue, battles, profiles = [], [], []
        with self.connect() as db:
            if self._table_exists(db, 'pvp_queue'):
                queue = [dict(row) for row in db.execute(
                    'SELECT q.id,q.player_id,p.name,q.league,q.created_ms,q.expires_ms,q.rating,q.state '
                    'FROM pvp_queue q LEFT JOIN players p ON p.id=q.player_id ORDER BY q.created_ms DESC LIMIT 100')]
            if self._table_exists(db, 'pvp_battles'):
                battles = [dict(row) for row in db.execute(
                    'SELECT id,kind,league,state,end_ms,finished FROM pvp_battles ORDER BY rowid DESC LIMIT 100')]
            if self._table_exists(db, 'pvp_profiles'):
                profiles = [dict(row) for row in db.execute(
                    'SELECT f.player_id,p.name,f.season,f.rank,f.rating,f.battles,f.wins,f.streak,f.dust '
                    'FROM pvp_profiles f LEFT JOIN players p ON p.id=f.player_id '
                    'ORDER BY f.rating DESC LIMIT 100')]
        names = species_names()
        ai_species = [{'species': species, 'name': names.get(species, species)}
                      for species in cfg.get('ai_species', [])]
        return {'config': cfg, 'queue': queue, 'battles': battles, 'profiles': profiles,
                'ai_species': ai_species, 'now_ms': now_ms(), 'species': self.species()}

    def save_pvp(self, body):
        from dittobuffer.gameplay import pvp
        raw = body.get('config', body) if isinstance(body, dict) else None
        if not isinstance(raw, dict):
            raise ValueError('PvP settings must be an object')
        cfg = pvp.DEFAULTS | raw
        cfg['enabled'] = bool(cfg.get('enabled'))
        for key, low, high in (('season', 1, 100000), ('minimum_level', 1, 50), ('set_size', 1, 5),
                               ('daily_sets', 1, 100), ('queue_seconds', 10, 600),
                               ('ai_backfill_seconds', 0, 600), ('ai_max_level', 1, 50),
                               ('friend_rewards_per_day', 0, 100)):
            cfg[key] = max(low, min(high, int(cfg.get(key, pvp.DEFAULTS[key]))))
        if cfg.get('ai_difficulty') not in cfg.get('ai', {}):
            raise ValueError('Unknown AI difficulty')
        leagues = cfg.get('leagues') or {}
        if not isinstance(leagues, dict) or not leagues:
            raise ValueError('At least one league is required')
        for name, entry in leagues.items():
            if not isinstance(entry, dict):
                raise ValueError('League ' + name + ' must be an object')
            entry['cap'] = max(0, int(entry.get('cap', 0)))
            entry['unlock_cp'] = max(0, int(entry.get('unlock_cp', 0)))
        ai_species = cfg.get('ai_species') or []
        known = {entry['id'] for entry in self.species()}
        if not isinstance(ai_species, list) or not all(int(species) in known for species in ai_species):
            raise ValueError('AI species must reference known Pokémon')
        cfg['ai_species'] = [int(species) for species in ai_species]
        self.config['pvp'] = cfg
        self.config_path.write_text(json.dumps(self.config, indent=2) + '\n', encoding='utf8')
        return self.pvp_admin()

    def pvp_action(self, body):
        action = str(body.get('action', '')).strip()
        player = str(body.get('player_id') or '').strip()
        with self.connect() as db:
            if action == 'clear_queue':
                if not self._table_exists(db, 'pvp_queue'):
                    return {'cleared': 0}
                cleared = db.execute("DELETE FROM pvp_queue WHERE state='queued'").rowcount
                db.commit()
                return {'cleared': cleared}
            if action == 'end_battles':
                if not self._table_exists(db, 'pvp_battles'):
                    return {'ended': 0}
                ended = db.execute('UPDATE pvp_battles SET finished=1 WHERE finished=0').rowcount
                db.commit()
                return {'ended': ended}
            if action == 'reset_profile':
                if not player:
                    raise ValueError('Choose a player')
                season = int(body.get('season') or 1)
                for table in ('pvp_profiles', 'pvp_rank_starts', 'pvp_set_starts', 'pvp_set_tracks',
                              'pvp_set_battles', 'pvp_claims', 'pvp_season_claims'):
                    if self._table_exists(db, table):
                        db.execute('DELETE FROM ' + table + ' WHERE player_id=? AND season=?', (player, season))
                db.commit()
                return {'player_id': player, 'season': season}
        raise ValueError('Unknown PvP action ' + action)

    # --------------------------------------------------------------- liveOps
    def readonly(self):
        """A platform room owns the world; local edits would silently diverge."""
        platform = self.config.get('platform') or {}
        return bool(self.config.get('admin_readonly')) or str(platform.get('mode') or 'local') == 'player'

    def liveops_admin(self):
        from dittobuffer.gameplay import liveops
        value = liveops.load(self.config)
        stamp = now_ms()
        active = lambda entry: entry.get('enabled', True) and entry['starts_ms'] <= stamp < entry['ends_ms']
        return {'settings': value,
                'active_events': [entry for entry in value['events'] if active(entry)],
                'active_community_days': [entry for entry in value['community_days'] if active(entry)],
                'now_ms': stamp, 'readonly': self.readonly(),
                'species': self.species()}

    def save_liveops(self, body):
        from dittobuffer.gameplay import liveops
        value = body.get('settings', body) if isinstance(body, dict) else None
        liveops.save(self.config, value)
        return self.liveops_admin()

    def research_admin(self):
        bundled = json.loads((ROOT / 'src/dittobuffer/gameplay/research.json').read_text(encoding='utf8'))
        custom = ROOT / 'data/research.json'
        saved = {}
        if custom.is_file():
            try:
                saved = json.loads(custom.read_text(encoding='utf8'))
            except (OSError, ValueError):
                saved = {}
        value = {**bundled, **saved, **(self.config.get('research') or {})}
        return {'research': value, 'defaults': bundled, 'saved': bool(saved),
                'readonly': self.readonly(), 'species': self.species()}

    def save_research(self, body):
        from dittobuffer.gameplay import research
        value = body.get('research', body) if isinstance(body, dict) else None
        if not isinstance(value, dict):
            raise ValueError('Research settings must be an object')
        if type(value.get('enabled')) is not bool:
            raise ValueError('enabled must be true or false')
        capacity = int(value.get('capacity', 3))
        if not 1 <= capacity <= 3:
            raise ValueError('Research capacity is 1..3')
        tasks = value.get('field_tasks') or {}
        if not isinstance(tasks, dict):
            raise ValueError('field_tasks must be an object')
        for key, task in tasks.items():
            if not isinstance(task, dict) or task.get('event') not in research.TYPES:
                raise ValueError('Unknown research event for ' + str(key))
            if not isinstance(task.get('target'), int) or not 1 <= task['target'] <= 10000:
                raise ValueError('Research target must be 1..10000')
            if not str(task.get('description') or '').strip():
                raise ValueError('Research tasks need a description')
            rewards = task.get('rewards')
            if not isinstance(rewards, list) or not rewards or len(rewards) > 20:
                raise ValueError('Research tasks need 1..20 rewards')
            for reward in rewards:
                if not isinstance(reward, dict) or reward.get('type') not in ('item', 'xp', 'stardust', 'encounter'):
                    raise ValueError('Unsupported research reward')
        for entry in value.get('field_pool') or []:
            if entry.get('task') not in tasks or int(entry.get('weight', 0)) < 1:
                raise ValueError('field_pool entries must reference a task and a positive weight')
        for story in value.get('stories') or []:
            if not isinstance(story, dict) or not story.get('id'):
                raise ValueError('Every story needs an id')
            for stage in story.get('stages') or []:
                if not isinstance(stage, dict) or not stage.get('objectives'):
                    raise ValueError('Story stages need objectives')
        target = ROOT / 'data/research.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf8')
        temporary.replace(target)
        return self.research_admin()

    def shadow_admin(self):
        from dittobuffer.gameplay import liveops
        cfg = self.config.get('shadow') or {}
        return {'config': {
            'enabled': bool(cfg.get('enabled', True)),
            'purify_stardust_cost': int(cfg.get('purify_stardust_cost', self.config.get('purify_stardust_cost', 1000))),
            'purify_candy_cost': int(cfg.get('purify_candy_cost', self.config.get('purify_candy_cost', 25))),
            'shadow_third_move_stardust_multiplier': float(cfg.get('shadow_third_move_stardust_multiplier', self.config.get('shadow_third_move_stardust_multiplier', 1.2))),
            'shadow_third_move_candy_multiplier': float(cfg.get('shadow_third_move_candy_multiplier', self.config.get('shadow_third_move_candy_multiplier', 1.2))),
            'purified_third_move_stardust_multiplier': float(cfg.get('purified_third_move_stardust_multiplier', self.config.get('purified_third_move_stardust_multiplier', 0.8))),
            'purified_third_move_candy_multiplier': float(cfg.get('purified_third_move_candy_multiplier', self.config.get('purified_third_move_candy_multiplier', 0.8))),
        }, 'overrides': liveops.load(self.config).get('shadow_overrides', {}),
            'readonly': self.readonly(), 'species': self.species()}

    def save_shadow(self, body):
        from dittobuffer.gameplay import liveops
        cfg = body.get('config', body) if isinstance(body, dict) else None
        if not isinstance(cfg, dict):
            raise ValueError('Shadow settings must be an object')
        self.config['shadow'] = {
            'enabled': bool(cfg.get('enabled', True)),
            'purify_stardust_cost': max(0, min(1_000_000, int(cfg.get('purify_stardust_cost', 1000)))),
            'purify_candy_cost': max(0, min(10_000, int(cfg.get('purify_candy_cost', 25)))),
            'shadow_third_move_stardust_multiplier': max(0.0, min(100.0, float(cfg.get('shadow_third_move_stardust_multiplier', 1.2)))),
            'shadow_third_move_candy_multiplier': max(0.0, min(100.0, float(cfg.get('shadow_third_move_candy_multiplier', 1.2)))),
            'purified_third_move_stardust_multiplier': max(0.0, min(100.0, float(cfg.get('purified_third_move_stardust_multiplier', 0.8)))),
            'purified_third_move_candy_multiplier': max(0.0, min(100.0, float(cfg.get('purified_third_move_candy_multiplier', 0.8)))),
        }
        value = liveops.load(self.config)
        overrides = body.get('overrides', value.get('shadow_overrides', {}))
        if not isinstance(overrides, dict):
            raise ValueError('Shadow overrides must be an object')
        value['shadow_overrides'] = overrides
        liveops.save(self.config, value)
        self.config_path.write_text(json.dumps(self.config, indent=2) + '\n', encoding='utf8')
        return self.shadow_admin()

    def platform_admin(self):
        from types import SimpleNamespace
        from dittobuffer.platform import client as platform_client
        value = platform_client.status(SimpleNamespace(config=self.config, db=self.db_path))
        value['readonly'] = self.readonly()
        # Keep the JSON payload serialisable; the UI never needs a Path object.
        value['bundle_url'] = str(ROOT / 'data/platform-room.json')
        return value

    def save_platform(self, body):
        from dittobuffer.platform import client as platform_client
        raw = body.get('settings', body) if isinstance(body, dict) else None
        if not isinstance(raw, dict):
            raise ValueError('Platform settings must be an object')
        # Pass the live config dict, not a wrapper: save_settings rewrites
        # config['platform'], and a namespace made the room name vanish.
        platform_client.save_settings(self.config, raw, self.config_path)
        return self.platform_admin()

    def publish_platform(self):
        from dittobuffer.platform import client as platform_client
        return platform_client.publish_now(self.config, self.db_path)

    def device_location(self):
        with _device_lock:
            if time.time() - _device_cache['at'] < 15:
                return _device_cache['value']
        try:
            result = subprocess.run(['adb', 'shell', 'dumpsys', 'location'], capture_output=True,
                                    text=True, timeout=20, encoding='utf-8', errors='replace')
            match = re.search(r'last location=Location\[fused (-?\d+\.\d+),(-?\d+\.\d+)', result.stdout or '')
            value = {'lat': float(match.group(1)), 'lng': float(match.group(2))} if match else None
        except Exception:
            value = None
        with _device_lock:
            _device_cache.update(at=time.time(), value=value)
        return value

    def status_full(self):
        return self.status()


def handler(admin):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'DittobufferAdmin/1.0'

        def log_message(self, *args):
            return

        def send_json(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode('utf8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def send_bytes(self, status, body, content_type='application/x-protobuf'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if body:
                self.wfile.write(body)

        def send_ui(self):
            body = UI.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy',
                             "default-src 'self'; img-src 'self' data: https://raw.githubusercontent.com "
                             "https://tile.openstreetmap.org; style-src 'self' 'unsafe-inline'; "
                             "script-src 'self' 'unsafe-inline'; connect-src 'self';")
            self.end_headers()
            self.wfile.write(body)

        def body(self):
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 2500000:
                raise ValueError('Request too large')
            from dittobuffer.gameplay.liveops.bundle import loads
            return loads(self.rfile.read(length) or b'{}')

        def route(self, method):
            from dittobuffer.admin.access import access,AccessDenied
            path = urlparse(self.path)
            parts = [unquote(part) for part in path.path.split('/') if part]
            query = parse_qs(path.query)
            try:
                if method != 'GET' and admin.readonly():
                    return self.send_json(403, {'error': 'This server is managed by a Dittobuffer Platform room; local edits are disabled.'})
                boundary=access(admin)
                if parts==['api','admin-session']:
                    if method=='POST':
                        origin=self.headers.get('Origin')
                        if origin and urlparse(origin).netloc!=self.headers.get('Host'):raise AccessDenied(403,'Cross-origin administrator request blocked')
                        token,row=boundary.exchange(self.body().get('access_key'))
                        raw=json.dumps({'csrf':row['csrf'],'role':row['role']}).encode()
                        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store')
                        self.send_header('Set-Cookie','dittobuffer_admin='+token+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800')
                        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
                    return self.send_json(200,{k:v for k,v in boundary.session(self.headers).items() if k in ('csrf','user','role')})
                if parts[:1]==['api'] and parts[:2]!=['api','studio']:
                    boundary.session(self.headers,method!='GET')
                if parts[:2] == ['api', 'studio']:
                    from dittobuffer.admin.studio import Studio
                    with admin.lock:
                        if not hasattr(admin,'studio'):admin.studio=Studio(admin,security=boundary if not admin.config.get('liveops',{}).get('admin_auth',False) else None)
                    return admin.studio.handle(self,method,parts,query)
                if method == 'GET' and parts in (['studio.js'],['studio.css'],['admin-access.js']):
                    target=ROOT/'src/dittobuffer/admin/ui'/parts[0]
                    return self.send_bytes(200,target.read_bytes(),'text/javascript; charset=utf-8' if parts[0].endswith('.js') else 'text/css; charset=utf-8')
                if method == 'GET' and not parts:
                    return self.send_ui()
                if method == 'GET' and parts == ['favicon.ico']:
                    self.send_response(204)
                    return self.end_headers()
                if method == 'GET' and (parts == ['map', 'settings']
                                        or len(parts) == 5 and parts[:2] == ['map', 'tiles']):
                    # This origin is plain HTTP and already reversed to the
                    # phone. Proxying keeps the published world snapshot and
                    # its tile cache authoritative in the game server.
                    try:
                        payload = admin.map_proxy(path.path + (('?' + path.query) if path.query else ''))
                    except (OSError, ValueError, urllib.error.URLError) as error:
                        logging.warning('Admin map proxy failed for %s: %s', path.path, error)
                        return self.send_bytes(502, b'')
                    return self.send_bytes(200, payload)
                if method == 'GET' and parts == ['assets', 'world-postcard.png']:
                    # Native image loading uses the plain HTTP map/admin origin.
                    # The image is original project art, shared with game HTTP.
                    body = (ROOT / 'src/dittobuffer/resources/world-postcard.png').read_bytes()
                    return self.send_bytes(200, body, 'image/png')
                if method == 'GET' and parts == ['assets', 'dittobuffer-stop.png']:
                    # Older builds baked this origin into fort image URLs. Keep
                    # it working so gym and raid lobby photos never go black.
                    body = (ROOT / 'src/dittobuffer/resources/dittobuffer-stop.png').read_bytes()
                    return self.send_bytes(200, body, 'image/png')
                if method == 'GET' and parts == ['api', 'status']:
                    return self.send_json(200, admin.status_full())
                if method == 'GET' and parts == ['api', 'device-location']:
                    return self.send_json(200, admin.device_location() or {})
                if method == 'GET' and parts == ['api', 'players']:
                    return self.send_json(200, admin.players())
                if method == 'GET' and parts == ['api', 'forms']:
                    species = int(query.get('species', ['0'])[0])
                    forms = max_form_options(species, admin.config) if query.get('max') else form_options(species)
                    return self.send_json(200, {'forms': forms, 'costumes': costume_options(species),
                                                'mega': mega_options(species), 'max': max_options(species, admin.config)})
                if method == 'GET' and parts == ['api', 'rewards']:
                    return self.send_json(200, admin.reward_catalog())
                if method == 'POST' and parts == ['api', 'rewards']:
                    return self.send_json(201, admin.grant_rewards(self.body()))
                if method == 'GET' and parts == ['api', 'items']:
                    return self.send_json(200, item_catalog())
                if method == 'GET' and parts == ['api', 'mega-species']:
                    return self.send_json(200, mega_species())
                if method == 'GET' and parts == ['api', 'loot']:
                    return self.send_json(200, admin.loot())
                if method == 'POST' and parts == ['api', 'loot']:
                    return self.send_json(200, admin.save_loot(self.body()))
                if method == 'GET' and parts == ['api', 'species']:
                    return self.send_json(200, admin.species())
                if method == 'GET' and parts == ['api', 'spawns']:
                    return self.send_json(200, admin.spawns(query))
                if method == 'GET' and parts == ['api', 'pois']:
                    return self.send_json(200, admin.pois())
                if method == 'GET' and parts == ['api', 'gyms']:
                    return self.send_json(200, admin.gyms())
                if method == 'GET' and parts == ['api', 'raids']:
                    return self.send_json(200, admin.raids())
                if method == 'GET' and parts == ['api', 'spawn-settings']:
                    return self.send_json(200, admin.spawn_settings())
                if method == 'POST' and parts == ['api', 'spawn-settings']:
                    return self.send_json(200, admin.save_spawn_settings(self.body()))
                if method == 'GET' and parts == ['api', 'raid-settings']:
                    return self.send_json(200, admin.raid_settings())
                if method == 'POST' and parts == ['api', 'raid-settings']:
                    return self.send_json(200, admin.save_raid_settings(self.body()))
                if method == 'GET' and parts == ['api', 'max']:
                    return self.send_json(200, admin.max_admin())
                if method == 'POST' and parts == ['api', 'max']:
                    return self.send_json(200, admin.save_max(self.body()))
                if method == 'POST' and parts == ['api', 'max', 'action']:
                    return self.send_json(200, admin.max_action(self.body()))
                if method == 'GET' and parts == ['api', 'osm']:
                    return self.send_json(200, admin.osm())
                if method == 'POST' and parts == ['api', 'osm']:
                    return self.send_json(200, admin.save_osm(self.body()))
                if method == 'POST' and parts == ['api', 'osm', 'clear']:
                    return self.send_json(200, admin.clear_map_cache())
                if method == 'GET' and parts == ['api', 'poi-import']:
                    return self.send_json(200, admin.poi_import())
                if method == 'POST' and parts == ['api', 'poi-import']:
                    return self.send_json(200, admin.save_poi_import(self.body()))
                if method == 'GET' and parts == ['api', 'pvp']:
                    return self.send_json(200, admin.pvp_admin())
                if method == 'POST' and parts == ['api', 'pvp']:
                    return self.send_json(200, admin.save_pvp(self.body()))
                if method == 'POST' and parts == ['api', 'pvp', 'action']:
                    return self.send_json(200, admin.pvp_action(self.body()))
                if method == 'GET' and parts == ['api', 'rocket']:
                    return self.send_json(200, admin.rocket())
                if method == 'POST' and parts == ['api', 'rocket']:
                    return self.send_json(200, admin.save_rocket(self.body()))
                if method == 'POST' and parts == ['api', 'rocket', 'balloons']:
                    return self.send_json(201, admin.spawn_rocket_balloon(self.body()))
                if method == 'POST' and len(parts) == 5 and parts[:3] == ['api', 'rocket', 'balloons'] and parts[4] == 'rescue':
                    return self.send_json(200, admin.rescue_rocket_balloon(parts[3], self.body()))
                if method == 'DELETE' and len(parts) == 4 and parts[:3] == ['api', 'rocket', 'balloons']:
                    return self.send_json(200, admin.delete_rocket_balloon(parts[3]))
                if method == 'GET' and parts == ['api', 'routes']:
                    return self.send_json(200, admin.routes())
                if method == 'POST' and parts == ['api', 'routes']:
                    return self.send_json(201, admin.create_route(self.body()))
                if method == 'DELETE' and len(parts) == 3 and parts[:2] == ['api', 'routes']:
                    return self.send_json(200, admin.delete_route(parts[2]))
                if method == 'GET' and parts == ['api', 'liveops']:
                    return self.send_json(200, admin.liveops_admin())
                if method == 'POST' and parts == ['api', 'liveops']:
                    return self.send_json(200, admin.save_liveops(self.body()))
                if method == 'GET' and parts == ['api', 'research']:
                    return self.send_json(200, admin.research_admin())
                if method == 'POST' and parts == ['api', 'research']:
                    return self.send_json(200, admin.save_research(self.body()))
                if method == 'GET' and parts == ['api', 'shadow']:
                    return self.send_json(200, admin.shadow_admin())
                if method == 'POST' and parts == ['api', 'shadow']:
                    return self.send_json(200, admin.save_shadow(self.body()))
                if method == 'GET' and parts == ['api', 'platform']:
                    return self.send_json(200, admin.platform_admin())
                if method == 'POST' and parts == ['api', 'platform']:
                    return self.send_json(200, admin.save_platform(self.body()))
                if method == 'POST' and parts == ['api', 'platform', 'publish']:
                    return self.send_json(200, admin.publish_platform())
                if method == 'GET' and parts == ['api', 'features']:
                    return self.send_json(200, admin.features())
                if method == 'POST' and parts == ['api', 'features']:
                    return self.send_json(200, admin.save_features(self.body()))
                if method == 'POST' and parts == ['api', 'spawns', 'clear-admin']:
                    return self.send_json(200, admin.clear_admin_spawns())
                if method == 'POST' and parts == ['api', 'spawns']:
                    return self.send_json(201, admin.create_spawns(self.body()))
                if method == 'POST' and parts == ['api', 'pois']:
                    return self.send_json(201, admin.create_poi(self.body()))
                if method == 'POST' and parts == ['api', 'raids']:
                    return self.send_json(201, admin.create_raid(self.body()))
                if method == 'DELETE' and len(parts) == 3 and parts[:2] == ['api', 'spawns']:
                    return self.send_json(200, admin.delete_spawn(parts[2]))
                if method == 'DELETE' and len(parts) == 3 and parts[:2] == ['api', 'pois']:
                    return self.send_json(200, admin.delete_poi(parts[2]))
                if method == 'DELETE' and len(parts) == 3 and parts[:2] == ['api', 'raids']:
                    return self.send_json(200, admin.delete_raid(parts[2]))
                return self.send_json(404, {'error': 'Unknown endpoint'})
            except AccessDenied as error:
                return self.send_json(error.status, {'error': str(error)})
            except (ValueError, KeyError, TypeError) as error:
                return self.send_json(400, {'error': str(error)})
            except Exception as error:
                return self.send_json(500, {'error': type(error).__name__ + ': ' + str(error)})

        def do_GET(self):
            self.route('GET')

        def do_POST(self):
            self.route('POST')

        def do_DELETE(self):
            self.route('DELETE')

    return Handler


def main():
    parser = argparse.ArgumentParser(description='Dittobuffer localhost admin console')
    parser.add_argument('--config', default='config/family.json')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    admin = Admin(args.config, args.port, args.host)
    if not UI.is_file():
        raise SystemExit(f'UI file missing: {UI}')
    server = ThreadingHTTPServer((args.host, args.port), handler(admin))
    print(f'Dittobuffer admin console: http://{args.host}:{args.port}  (db {admin.db_path})', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
