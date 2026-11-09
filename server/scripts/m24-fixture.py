"""Create a fresh isolated M24 native fixture; never touches normal players.

Only parties and access requirements are provisioned. Battles, raid rewards and
captures must be earned through the actual client. Credentials stay private.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import secrets
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from dittobuffer.core.database import Database
from dittobuffer.gameplay.game import Game, p
from dittobuffer.gameplay.species import make_pokemon


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--port', type=int, default=8448)
    parser.add_argument('--scenario', choices=['raid', 'balloon', 'rewards', 'expiry', 'onboarding'], default='raid')
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if output.exists() or not output.is_relative_to(ROOT / '.private') or not output.name.startswith('m24-'):
        raise ValueError('Fresh project-private m24-* output required')
    if not 1024 <= args.port <= 65535:
        raise ValueError('Unprivileged fixture port required')
    output.mkdir(parents=True)
    lat, lng = 52.079632, 5.122653
    world = output / 'world.json'
    write(world, dict(epoch=2401, pois=[
        dict(id='m24-gym', name='M24 isolated raid gym', kind=0, lat=lat+.00015, lng=lng),
        dict(id='m24-stop', name='M24 isolated research stop', kind=1, lat=lat-.00015, lng=lng)]))
    cfg = json.loads((ROOT / 'config/family.json').read_text(encoding='utf8'))
    cfg.update(profile='m24-raid-victory', host='127.0.0.1', port=args.port,
        database=(output / 'game.sqlite3').relative_to(ROOT).as_posix(),
        authentication={'mode': 'family'},
        world={'enabled': True, 'json': world.relative_to(ROOT).as_posix(), 'osm_download': False,
               'map_tiles_base': 'http://127.0.0.1:8080/map/tiles'},
        weather={'enabled': False}, social={'enabled': False}, rocket={'enabled': False},
        pvp={'enabled': False}, routes={'enabled': False}, vivillon={'enabled': False},
        random_spawns={'enabled': False}, catch_probability=1,
        spawn_pools=[{'species': [{'id': 1, 'weight': 1}]}], spawn_points_per_tile=1,
        spawn_neighbor_tiles=0, initial_items={'1': 100, '101': 30, '201': 20, '701': 20, '1402': 5})
    cfg['max_system']['enabled'] = False
    cfg['pokemon_assets'] = {'enabled': True, 'manifest': 'config/resources/pokemon-assets.json'}
    cfg['avatar_assets'] = {'enabled': True, 'manifest': 'config/resources/avatar-assets.json'}
    cfg['client_resources'] = {'enabled': True, 'registry': 'config/resources/client-runtime-m23.json'}
    cfg['walking']['allow_debug_fixture'] = False
    cfg['research'] = {'enabled': False}
    cfg['raids'] = dict(enabled=True, development_only=True, multiplayer=False, participation_limit=1,
        minimum_level=5, epoch_ms=int(time.time()*1000)-2000, egg_seconds=1, active_seconds=3600,
        rotation_seconds=7200, lobby_seconds=5, relobby_seconds=5, battle_seconds=120,
        premier_balls=6, encounter_seconds=86400,
        ball_bonuses={'provenance': 'Explicit isolated development tuning, not an official numeric raid reward table.',
            'gym_ownership': 2, 'damage': [{'minimum_fraction': .5, 'balls': 3}],
            'speed': [{'maximum_fraction': .5, 'balls': 4}, {'maximum_fraction': 1, 'balls': 2}]},
        pools=[dict(species=16, tier=1, weight=1, hp=180, cp=1000, xp=3000, dust=500, items={'101': 2, '1301': 1})])
    if args.scenario == 'balloon':
        cfg['raids']['enabled'] = False
        cfg['rocket'] = dict(enabled=True, server_combat=True, balloons_enabled=True,
            probability=0, npc_level=8, balloon_themes=['grass'],
            balloon_period_seconds=21600, balloon_duration_seconds=1200)
    if args.scenario == 'rewards':
        cfg['raids']['enabled'] = False
        cfg['reward_bonuses'] = json.loads((ROOT/'config/reward-bonuses.example.json').read_text())['reward_bonuses']
        cfg['spawn_pools'] = [dict(species=[dict(id=16,weight=1)])]
        cfg['spawn_level_range'] = [5,5]
    if args.scenario == 'expiry':
        cfg['raids'].update(epoch_ms=int(time.time()*1000)+25000,egg_seconds=60,active_seconds=120)
        cfg['item_capacity']=5000
    if args.scenario == 'onboarding':
        cfg['raids']['enabled']=False
        cfg['onboarding']={'enabled':True,'starter_species':[1,4,7,25]}
        cfg['initial_items']={'1':50,'101':5,'201':2,'701':5}
        cfg['research']={'enabled':True}
        cfg['rocket']={'enabled':True,'server_combat':True,'balloons_enabled':False,'probability':0}
    tls = output / 'tls'
    spec = importlib.util.spec_from_file_location('m24_tls', ROOT / 'scripts/create-family-tls.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.create('127.0.0.1', tls)
    cfg['network'] = dict(mode='tls', certificate=(tls/'server.pem').relative_to(ROOT).as_posix(),
        private_key=(tls/'server-key.pem').relative_to(ROOT).as_posix())
    game = Game(cfg, Database(output/'game.sqlite3'))
    username, password = 'm24-'+secrets.token_hex(4), secrets.token_urlsafe(24)
    player=None;invitation=game.accounts.invite()
    if args.scenario != 'onboarding':
        game.accounts.register(username, password, 'M24RaidTest', game.accounts.invite(), peer='m24-isolated')
        with game.db.transaction() as db:
            player = db.execute('SELECT player_id FROM family_accounts WHERE username=?', (username,)).fetchone()[0]
            db.execute('UPDATE players SET xp=210000 WHERE id=?', (player,))
            db.execute('UPDATE trainer_teams SET team=1 WHERE player_id=?', (player,))
            game.world.materialize(db, lat, lng)
            from dittobuffer.gameplay.gyms import gym
            gym(db, 'm24-gym');db.execute("UPDATE gyms SET team=1 WHERE fort_id='m24-gym'")
            for index, species in enumerate((25, 68, 149, 3, 6, 9), 1):
                spawn = 240000 + index
                db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)', (spawn, 'm24-owned-'+str(index), species, lat, lng, 0))
                mon = make_pokemon(p, game.species[species], species, 35, [15]*3)
                mon.id = db.execute('INSERT INTO pokemon(player_id,spawn_id,species,data) VALUES(?,?,?,?)',
                    (player, spawn, species, b'')).lastrowid
                db.execute('UPDATE pokemon SET data=? WHERE id=?', (mon.SerializeToString(), mon.id))
    if args.scenario == 'expiry':
        from dittobuffer.gameplay.gyms import deploy
        from dittobuffer.gameplay.eggs import storage_id
        owner_user,owner_pass='m24-owner-'+secrets.token_hex(4),secrets.token_urlsafe(24)
        game.accounts.register(owner_user,owner_pass,'M24GymOwner',game.accounts.invite(),peer='m24-isolated')
        with game.db.transaction() as db:
            owner=db.execute('SELECT player_id FROM family_accounts WHERE username=?',(owner_user,)).fetchone()[0]
            db.execute('UPDATE players SET xp=210000 WHERE id=?',(owner,))
            db.execute('UPDATE trainer_teams SET team=2 WHERE player_id=?',(owner,))
            db.execute("UPDATE gyms SET team=0 WHERE fort_id='m24-gym'")
            db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,0)',(240400,'m24-owned-defender',7,lat+.00015,lng))
            mon=make_pokemon(p,game.species[7],7,10,[10]*3);mon.id=storage_id(db)
            db.execute('INSERT INTO pokemon VALUES(?,?,?,?,?)',(mon.id,owner,240400,7,mon.SerializeToString()))
            result=deploy(game,db,owner,p.GymDeployProto(fort_id='m24-gym',pokemon_id=mon.id,player_lat_degrees=lat,player_lng_degrees=lng).SerializeToString())
            if result.result!=1:raise ValueError('Fixture defender deployment failed')
        write(output/'owner-private.json',dict(username=owner_user,password=owner_pass))
    write(output/'config.json', cfg)
    write(output/'account-private.json', dict(username=username, password=password,trainer='M24Newcomer',invitation=invitation))
    write(output/'profile.json', dict(origin=f'https://127.0.0.1:{args.port}',
        ca_file=(tls/'ca.pem').relative_to(ROOT).as_posix(), fixture_profile=cfg['profile'],
        fixture_origin=dict(lat=lat, lng=lng), max_server=True,
        rocket_server_combat=True, pvp_server_combat=False, network_diagnostics=True,
        runtime_registry='config/resources/client-runtime-m23.json',
        avatar_body_presets=['BodyPresetDefault','BodyPresetEight','BodyPresetFour'],
        avatar_face_presets=['FacePresetSeven']))
    write(output/'declaration.json', dict(development_mode=True, milestone='M24.1' if args.scenario == 'raid' else 'M24.2' if args.scenario == 'balloon' else 'M24.3' if args.scenario == 'rewards' else 'M24.4' if args.scenario=='expiry' else 'M24.5',
        isolated_player=player, battle_results='NOT_EARNED', owned_fixture_party=[] if args.scenario=='onboarding' else [25,68,149,3,6,9],
        initial_xp=0 if args.scenario=='onboarding' else 'Fixture access only',players_before_native_registration=0 if args.scenario=='onboarding' else None, normal_database='Not accessed', android='NOT_TESTED'))
    print('Created isolated M24 '+args.scenario+' fixture. No victory or catch awarded.')


if __name__ == '__main__':
    main()
