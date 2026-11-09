"""Explicit, unearned cinematic Research rehearsal in a fresh isolated database."""
import argparse
import importlib.util
import json
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from dittobuffer.gameplay.game import Game, Database
from dittobuffer.gameplay.special_encounters import registry
from dittobuffer.gameplay import stories


def write(path, value):
    with path.open('x', encoding='utf8') as stream:
        json.dump(value, stream, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-mode', action='store_true', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--registry', required=True)
    parser.add_argument('--encounter', action='append', choices=list(registry()), required=True)
    parser.add_argument('--shiny-encounter', action='append', choices=list(registry()), default=[],
                        help='Use a supported shared-controller shiny variant for a selected encounter')
    parser.add_argument('--form-encounter', action='append', default=[], metavar='ENCOUNTER=FORM',
                        help='Explicit Game Master form for a selected isolated encounter (for example shaymin_research=92)')
    parser.add_argument('--port', type=int, default=8448)
    args = parser.parse_args()
    for key in args.shiny_encounter:
        if key not in args.encounter or registry()[key]['shiny_selector'] == 'normal':
            raise ValueError('Shiny variant must name a selected, shiny-compatible native strategy')
    forms = {}
    for value in args.form_encounter:
        key, separator, number = value.partition('=')
        if not separator or key not in args.encounter or key in forms or not number.isascii() or not number.isdecimal() or not 0 < int(number) < 65536:
            raise ValueError('A unique selected ENCOUNTER=FORM with an integer form ID is required')
        forms[key] = int(number)
    output = (ROOT / args.output).resolve()
    if output.exists() or not output.is_relative_to(ROOT / '.private') or not output.relative_to(ROOT / '.private').parts[0].startswith('m23-'):
        raise ValueError('Fresh isolated .private/m23-* output required')
    if not 1024 <= args.port <= 65535:
        raise ValueError('Unprivileged port required')
    output.mkdir(parents=True)
    cfg = json.loads((ROOT / 'config/family.json').read_text())
    cfg.update(profile='m23-native-research', host='127.0.0.1', port=args.port,
               database=(output / 'game.sqlite3').relative_to(ROOT).as_posix(),
               authentication={'mode': 'family'}, world={'enabled': False}, weather={'enabled': False},
               social={'enabled': False}, rocket={'enabled': False}, pvp={'enabled': False},
               routes={'enabled': False}, vivillon={'enabled': False},
               random_spawns={'enabled': False}, catch_probability=1,
               spawn_pools=[{'species': [{'id': 1, 'weight': 1}]}],
               spawn_points_per_tile=1, spawn_neighbor_tiles=0,
               initial_items={'1': 100, '2': 20, '3': 20, '101': 10, '201': 10, '701': 10})
    cfg['max_system']['enabled'] = False
    cfg['pokemon_assets'] = {'enabled': True, 'manifest': 'config/resources/pokemon-assets.json'}
    cfg['avatar_assets'] = {'enabled': True, 'manifest': 'config/resources/avatar-assets.json'}
    cfg['client_resources'] = {'enabled': True, 'registry': args.registry}
    cfg['walking']['allow_debug_fixture'] = False
    research = json.loads((ROOT / 'src/dittobuffer/gameplay/research.json').read_text())
    research.update(enabled=True, stories=[], field_pool=[])
    cfg['research'] = research
    cfg['shiny_eligible_forms'] = list(cfg.get('shiny_eligible_forms', []))
    for key in dict.fromkeys(args.encounter):
        definition = registry()[key]
        shiny = definition['shiny_selector'] == 'shiny' or key in args.shiny_encounter
        if shiny:
            cfg['shiny_eligible_forms'].append({'species': definition['species'], **({'form': forms[key]} if key in forms else {})})
        reward = dict(type='encounter', species=definition['species'], level=15,
                      special_encounter=key, shiny_rate=int(shiny))
        if key in forms:
            reward['form'] = forms[key]
        research['stories'].append(dict(id='m23-' + key, title='M23 TEST: ' + key.replace('_', ' '),
            ending='Isolated rehearsal saved. This reward is not live progression.',
            stages=[dict(description='Development fixture: native encounter compatibility test.',
                         objectives=[dict(event='throw', target=1,
                             description='Fixture objective, explicitly pre-completed for testing.',
                             rewards=[dict(type='xp', amount=1)])], rewards=[reward])]))
    tls = output / 'tls'
    spec = importlib.util.spec_from_file_location('m23_tls', ROOT / 'scripts/create-family-tls.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.create('127.0.0.1', tls)
    cfg['network'] = dict(mode='tls', certificate=(tls / 'server.pem').relative_to(ROOT).as_posix(),
                         private_key=(tls / 'server-key.pem').relative_to(ROOT).as_posix())
    game = Game(cfg, Database(output / 'game.sqlite3'))
    username = 'm23-' + secrets.token_hex(5)
    password = secrets.token_urlsafe(24)
    game.accounts.register(username, password, 'M23Cinematics', game.accounts.invite(), peer='m23-isolated-NOT-EARNED')
    with game.db.transaction() as db:
        player = db.execute('SELECT player_id FROM family_accounts WHERE username=?', (username,)).fetchone()[0]
        from dittobuffer.gameplay.progression import bounds
        level, floor, ceiling = bounds(cfg, 210000)
        fixture_xp = floor + min(1000, max(0, (ceiling-floor)//2))
        db.execute('UPDATE players SET xp=? WHERE id=?', (fixture_xp, player))
        for tutorial in (9, 15, 19, 20):db.execute('INSERT OR IGNORE INTO tutorials VALUES(?,?)', (player, tutorial))
        for level in range(2, bounds(cfg, fixture_xp)[0]+1):db.execute('INSERT OR IGNORE INTO level_reward_claims VALUES(?,?)', (player, level))
        stories.ensure(game, db, player)
        db.execute('UPDATE research_tasks SET progress=1,completed_ms=created_ms WHERE player_id=?', (player,))
    secret_spec = importlib.util.spec_from_file_location('m23_private', ROOT / 'tools/transfer-runtime.py')
    private = importlib.util.module_from_spec(secret_spec);secret_spec.loader.exec_module(private)
    (output / 'credentials.dpapi').write_bytes(private.protect(json.dumps({'alice': dict(username=username, password=password)}).encode()))
    cfg['rpc_trace_methods'] = [method for method, name in game.methods.items() if name in (
        'GET_NEW_QUESTS', 'GET_QUEST_DETAILS', 'GET_QUEST_UI', 'COMPLETE_QUEST',
        'QUEST_ENCOUNTER', 'ENCOUNTER', 'CATCH_POKEMON', 'GET_HOLOHOLO_INVENTORY')]
    write(output / 'server.json', cfg)
    write(output / 'profile.json', dict(origin='https://127.0.0.1:' + str(args.port),
        ca_file=(tls / 'ca.pem').relative_to(ROOT).as_posix(), fixture_profile=cfg['profile'],
        fixture_origin=dict(lat=52.079632, lng=5.122653), max_server=True,
        rocket_server_combat=False, pvp_server_combat=False, network_diagnostics=True,
        native_research_diagnostics=True,
        runtime_registry=args.registry,
        avatar_body_presets=['BodyPresetDefault', 'BodyPresetEight', 'BodyPresetFour'],
        avatar_face_presets=['FacePresetSeven'],
        world_style=json.loads((ROOT / 'src/dittobuffer/world/world-style.json').read_text())))
    write(output / 'declaration.json', dict(development_mode=True, profile=cfg['profile'], isolated_player=player,
        encounters=args.encounter, shiny_variants=args.shiny_encounter, form_variants=forms,
        objectives='NOT_EARNED: pre-completed fixture objectives',
        resets='Create a fresh fixture output; never reset a normal trainer',
        native_path='CompleteQuest -> type 4 inventory entitlement -> QuestEncounter -> native strategy',
        physical_walking='NOT_TESTED', android='NOT_TESTED', normal_players='Not accessed'))
    print('Created isolated cinematic Research fixture; credentials encrypted, not printed.')


if __name__ == '__main__':
    main()
