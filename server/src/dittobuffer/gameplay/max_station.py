"""Canonical owned Max stationing, returns and once-only battle-assist rewards.

Station capacity and display limits come from the authorized Bread Game Master.
The supplied snapshot has no STATION_REWARD_GROUP tables: custom Candy amounts
therefore require an explicit configuration and provenance. They are never
presented as official balance. This module never heals, deletes or clones a mon.
"""
import json
import math
from dittobuffer.gameplay.game import p, now_ms, distance
from dittobuffer.gameplay import max_system as resources
from dittobuffer.core.management import owned, update_owned, add_candy

SCHEMA = '''
CREATE TABLE IF NOT EXISTS max_stationed(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 station_id TEXT NOT NULL REFERENCES max_stations(id),
 player_id TEXT NOT NULL REFERENCES players(id),
 pokemon_id INTEGER NOT NULL,
 origin_room_id INTEGER NOT NULL REFERENCES max_rooms(id),
 deploy_ms INTEGER NOT NULL,end_ms INTEGER NOT NULL,
 family INTEGER NOT NULL,reward_policy TEXT NOT NULL,
 returned_ms INTEGER NOT NULL DEFAULT 0,return_reason TEXT NOT NULL DEFAULT '',
 assists INTEGER NOT NULL DEFAULT 0,candy INTEGER NOT NULL DEFAULT 0,
 xl_candy INTEGER NOT NULL DEFAULT 0,rewarded_ms INTEGER NOT NULL DEFAULT 0,
 UNIQUE(station_id,player_id));
CREATE UNIQUE INDEX IF NOT EXISTS max_stationed_owned_active ON max_stationed(pokemon_id) WHERE returned_ms=0;
CREATE INDEX IF NOT EXISTS max_stationed_player ON max_stationed(player_id,returned_ms);
CREATE TABLE IF NOT EXISTS max_station_assists(
 deployment_id INTEGER NOT NULL REFERENCES max_stationed(id),
 room_id INTEGER NOT NULL REFERENCES max_rooms(id),created_ms INTEGER NOT NULL,
 PRIMARY KEY(deployment_id,room_id));
'''


def enabled(game):
    return resources.enabled(game) and bool(game.max_config.get('stationing', {}).get('enabled'))


def settings(game):
    return resources.gm(game.max_reference, 'BREAD_SHARED_SETTINGS').bread_settings


def policy(game):
    cfg = game.max_config.get('stationing', {})
    if not isinstance(cfg, dict):
        raise ValueError('Max stationing configuration must be an object')
    if 'enabled' in cfg and type(cfg['enabled']) is not bool:
        raise ValueError('Max stationing enabled flag must be boolean')
    if not cfg.get('enabled'):
        return None
    provenance = cfg.get('reward_provenance')
    if not isinstance(provenance, str) or len(provenance.strip()) < 20:
        raise ValueError('Explicit Max station assist reward provenance required')
    result = {'provenance': provenance}
    for key in ('candy_per_assist', 'xl_candy_per_assist', 'max_candy', 'max_xl_candy'):
        value = cfg.get(key)
        if type(value) is not int or not 0 <= value <= 10000:
            raise ValueError('Invalid custom Max station reward ' + key)
        result[key] = value
    shared = settings(game)
    if min(shared.max_stationed_pokemon, shared.max_stationed_pokemon_per_player,
           shared.max_stationed_pokemon_display_count) <= 0 or shared.min_ms_to_receive_release_station_rewards < 0:
        raise ValueError('Source Max station capacities/residency settings required')
    result['minimum_residency_ms'] = shared.min_ms_to_receive_release_station_rewards
    return result


def install(game, db):
    if not resources.enabled(game):
        return
    policy(game)
    if enabled(game):
        db.executescript(SCHEMA)


def _credit_assists(db, row, end_ms):
    # A co-op room is one assistance event, even when several real members win.
    # The placement's own victory room never assists its later deployment.
    wins = db.execute('''SELECT v.room_id,MIN(v.created_ms) stamp
        FROM max_victories v JOIN max_rooms r ON r.id=v.room_id
        WHERE v.station_id=? AND v.player_id<>? AND v.room_id<>?
        AND v.created_ms>=? AND v.created_ms<? AND v.created_ms<=?
        AND r.status=2 GROUP BY v.room_id''',
        (row['station_id'], row['player_id'], row['origin_room_id'],
         row['deploy_ms'], end_ms, now_ms())).fetchall()
    for win in wins:
        db.execute('INSERT OR IGNORE INTO max_station_assists VALUES(?,?,?)',
                   (row['id'], win['room_id'], win['stamp']))
    count = db.execute('SELECT COUNT(*) FROM max_station_assists WHERE deployment_id=?', (row['id'],)).fetchone()[0]
    cfg = json.loads(row['reward_policy'])
    candy = min(cfg['max_candy'], count * cfg['candy_per_assist'])
    xl = min(cfg['max_xl_candy'], count * cfg['xl_candy_per_assist'])
    db.execute('UPDATE max_stationed SET assists=?,candy=?,xl_candy=? WHERE id=?', (count, candy, xl, row['id']))


def _return(game, db, row, reason, at_ms=None):
    # Fetch the current canonical record rather than replaying a stored mon:
    # injuries, nicknames and other accepted fields must never be rolled back.
    pokemon = owned(db, row['player_id'], row['pokemon_id'])
    cfg = json.loads(row['reward_policy'])
    stamp = min(now_ms(), at_ms) if at_ms is not None else now_ms()
    eligible = stamp - row['deploy_ms'] >= cfg['minimum_residency_ms']
    candy, xl = (row['candy'], row['xl_candy']) if eligible else (0, 0)
    if not row['rewarded_ms']:
        if candy:
            add_candy(db, row['player_id'], row['family'], candy)
        if xl:
            db.execute('''INSERT INTO mega_catch_xl VALUES(?,?,?)
                ON CONFLICT(player_id,family) DO UPDATE SET count=count+excluded.count''',
                (row['player_id'], row['family'], xl))
    db.execute('UPDATE max_stationed SET returned_ms=?,return_reason=?,rewarded_ms=? WHERE id=? AND returned_ms=0',
               (stamp, reason, now_ms(), row['id']))
    if pokemon and pokemon.deployed_station_id == row['station_id']:
        pokemon.deployed_station_id = ''
        pokemon.deployed_station_expiration_time_ms = 0
        update_owned(game, db, row['player_id'], pokemon)
    elif candy or xl:
        game.touch(db, row['player_id'])


def settle(game, db, player=None, station_id=None):
    """Call inside the existing authenticated gameplay transaction."""
    if not enabled(game):
        return
    query = 'SELECT * FROM max_stationed WHERE returned_ms=0'
    args = []
    if player is not None:
        query += ' AND player_id=?'; args.append(player)
    if station_id is not None:
        query += ' AND station_id=?'; args.append(station_id)
    for original in db.execute(query, args).fetchall():
        station = db.execute('SELECT end_ms FROM max_stations WHERE id=?', (original['station_id'],)).fetchone()
        end_ms = min(original['end_ms'], station['end_ms']) if station else original['end_ms']
        _credit_assists(db, original, end_ms)
        row = db.execute('SELECT * FROM max_stationed WHERE id=?', (original['id'],)).fetchone()
        if not station or end_ms <= now_ms():
            _return(game, db, row, 'expired', end_ms)


def inventory(game, db, player):
    """Settle before reading inventory revision; canonical Pokemon is the UI record."""
    settle(game, db, player=player)
    return []


def entry(game, db, row):
    from dittobuffer.gameplay.progression import bounds
    pokemon = owned(db, row['player_id'], row['pokemon_id'])
    out = p.PlayerClientStationedPokemonProto(pokemon=pokemon,
        trainer_name=game.player(db, row['player_id'])['name'],
        deploy_timestamp_ms=row['deploy_ms'],
        trainer_level=bounds(game.config, game.player(db, row['player_id'])['xp'])[0])
    out.player_neutral_avatar.CopyFrom(game.appearance(db, row['player_id']))
    return out


def assign(game, db, player, body):
    req = p.StationPokemonProto.FromString(body)
    fail = lambda status: p.StationPokemonOutProto(result=status)
    if not enabled(game):
        return fail(5)
    settle(game, db, player=player)
    settle(game, db, station_id=req.station_id)
    station = resources.lookup(game, db, req.station_id)
    if not station:
        return fail(7)
    if (not all(math.isfinite(v) for v in (req.station_lat_degrees, req.station_lng_degrees))
        or not -90 <= req.station_lat_degrees <= 90 or not -180 <= req.station_lng_degrees <= 180
        or distance(req.station_lat_degrees, req.station_lng_degrees, station['lat'], station['lng']) > game.config['interaction_range_meters']
        or not resources.near(game, db, player, station)):
        return fail(4)
    if not 0 < req.pokemon_id < (1 << 63):
        return fail(6)
    pokemon = owned(db, player, req.pokemon_id)
    if pokemon is None:
        return fail(6)
    previous = db.execute('SELECT * FROM max_stationed WHERE station_id=? AND player_id=?', (station['id'], player)).fetchone()
    if previous:
        if previous['pokemon_id'] == pokemon.id and not previous['returned_ms']:
            return p.StationPokemonOutProto(result=1, player_client_stationed_pokemon=entry(game, db, previous))
        return fail(2)
    from dittobuffer.gameplay.max_battle import eligible
    from dittobuffer.gameplay.gyms import battle_busy
    from dittobuffer.gameplay.mega import active as active_mega
    from dittobuffer.gameplay.trading import locked
    if (not eligible(pokemon) or pokemon.is_component or pokemon.is_fusion
        or battle_busy(db, player, [pokemon.id]) or active_mega(db, pokemon.id)
        or locked(game, db, pokemon.id)
        or db.execute('SELECT 1 FROM buddy_active WHERE player_id=? AND pokemon_id=?', (player, pokemon.id)).fetchone()):
        return fail(2 if pokemon.deployed_station_id else 3)
    if (not req.bread_battle_id.isascii() or not req.bread_battle_id.isdigit()
        or len(req.bread_battle_id) > 19 or not 0 < int(req.bread_battle_id) < (1 << 63)):
        return fail(8)
    room = db.execute('''SELECT r.* FROM max_rooms r JOIN max_victories v ON v.room_id=r.id
        WHERE r.id=? AND r.station_id=? AND r.status=2 AND v.player_id=?''',
        (int(req.bread_battle_id), station['id'], player)).fetchone()
    if not room:
        return fail(8)
    shared = settings(game)
    if db.execute('SELECT COUNT(*) FROM max_stationed WHERE station_id=? AND returned_ms=0', (station['id'],)).fetchone()[0] >= shared.max_stationed_pokemon:
        return fail(9)
    if db.execute('SELECT COUNT(*) FROM max_stationed WHERE player_id=? AND returned_ms=0', (player,)).fetchone()[0] >= shared.max_stationed_pokemon_per_player:
        return fail(10)
    form = pokemon.pokemon_display.form or game.species[pokemon.pokemon_id]['form']
    family = game.forms[pokemon.pokemon_id, form]['family']
    cursor = db.execute('''INSERT INTO max_stationed(station_id,player_id,pokemon_id,origin_room_id,deploy_ms,end_ms,family,reward_policy)
        VALUES(?,?,?,?,?,?,?,?)''', (station['id'], player, pokemon.id, room['id'], now_ms(), station['end_ms'], family, json.dumps(policy(game))))
    pokemon.deployed_station_id = station['id']
    pokemon.deployed_station_expiration_time_ms = station['end_ms']
    update_owned(game, db, player, pokemon)
    return p.StationPokemonOutProto(result=1, player_client_stationed_pokemon=entry(game, db, db.execute('SELECT * FROM max_stationed WHERE id=?', (cursor.lastrowid,)).fetchone()))


def details(game, db, player, body):
    req = p.GetStationedPokemonDetailsProto.FromString(body)
    station = resources.lookup(game, db, req.station_id)
    if not station:
        return p.GetStationedPokemonDetailsOutProto(result=2)
    if not resources.near(game, db, player, station):
        return p.GetStationedPokemonDetailsOutProto(result=3)
    out = p.GetStationedPokemonDetailsOutProto(result=1)
    if enabled(game):
        settle(game, db, station_id=station['id'])
        rows = db.execute('SELECT * FROM max_stationed WHERE station_id=? AND returned_ms=0 ORDER BY deploy_ms,id', (station['id'],)).fetchall()
        out.total_num_stationed_pokemon = len(rows)
        limit = len(rows) if req.get_full_details else settings(game).max_stationed_pokemon_display_count
        out.stationed_pokemons.extend(entry(game, db, row) for row in rows[:limit])
    return out


def release(game, db, player, body):
    req = p.ReleaseStationedPokemonProto.FromString(body)
    if not enabled(game):
        return p.ReleaseStationedPokemonOutProto(result=3)
    if not db.execute('SELECT 1 FROM max_stations WHERE id=?', (req.station_id,)).fetchone():
        return p.ReleaseStationedPokemonOutProto(result=3)
    if not 0 < req.pokemon_id < (1 << 63):
        return p.ReleaseStationedPokemonOutProto(result=2)
    settle(game, db, player=player)
    row = db.execute('SELECT * FROM max_stationed WHERE station_id=? AND pokemon_id=? AND player_id=? ORDER BY id DESC LIMIT 1', (req.station_id, req.pokemon_id, player)).fetchone()
    if not row or owned(db, player, req.pokemon_id) is None:
        return p.ReleaseStationedPokemonOutProto(result=2)
    if not row['returned_ms']:
        _return(game, db, row, 'recalled')
    return p.ReleaseStationedPokemonOutProto(result=1)


def statistics(game, db, player, body):
    req = p.GetNumStationAssistsProto.FromString(body)
    out = p.GetNumStationAssistsOutProto()
    if not enabled(game) or not 0 < req.pokemon_id < (1 << 63) or owned(db, player, req.pokemon_id) is None:
        return out
    settle(game, db, player=player)
    row = db.execute('SELECT d.*,s.name FROM max_stationed d JOIN max_stations s ON s.id=d.station_id WHERE d.pokemon_id=? AND d.player_id=? ORDER BY d.id DESC LIMIT 1', (req.pokemon_id, player)).fetchone()
    if row:
        out.num_station_assists = row['assists']; out.candy_amount = row['candy']
        out.xl_candy_amount = row['xl_candy']; out.powerspot_title = row['name']
    return out


def boost(game, db, station_id):
    """Source UI boost thresholds; zero/missing source power invents no damage."""
    count = 0
    if enabled(game):
        settle(game, db, station_id=station_id)
        count = db.execute('SELECT COUNT(*) FROM max_stationed WHERE station_id=? AND returned_ms=0', (station_id,)).fetchone()[0]
    name = 'STATIONED_POKEMON_POWER_BOOST_TABLE_SETTINGS'
    selected = None
    if name in game.max_reference['templates']:
        values = resources.gm(game.max_reference, name).stationed_pokemon_table_settings.tier_boosts
        selected = max((v for v in values if v.num_stationed <= count), key=lambda v:v.num_stationed, default=None)
    return dict(num_stationed=count, boost_icons=selected.num_boost_icons if selected else 0,
                hundredths_of_percent=selected.hundredths_of_percent if selected else 0,
                source_template=name)
