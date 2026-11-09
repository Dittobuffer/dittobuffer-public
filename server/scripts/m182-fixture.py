"""Create a fresh, explicitly unearned private Power Spot rehearsal fixture.

No normal trainer database, published world or Android installation is changed.
Requires independently restored hash-pinned local resources; never downloads.
"""
from pathlib import Path
import argparse,hashlib,importlib.util,json,secrets,sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'src'))
from dittobuffer.gameplay.game import Game,p,now_ms
from dittobuffer.core.database import Database
from dittobuffer.gameplay import max_system as mx
from dittobuffer.gameplay.species import make_pokemon

REFERENCE='assets/pokemon/local/max/max-reference.json'
REFERENCE_SHA='e43457935016e732a64dbd2a07e20d785025387697ec609cbda00a3b4b366d45'

def write(path,value):
    with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,indent=2)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,help='Fresh directory under .private/')
    parser.add_argument('--port',type=int,default=8447)
    parser.add_argument('--extended-test-hours',action='store_true',
                        help='Explicit custom rehearsal-only 00:00–24:00 availability; device clock unchanged')
    args=parser.parse_args()
    output=(ROOT/args.output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'.private'):
        raise ValueError('Fresh project-private output required')
    if not 1024<=args.port<=65535:raise ValueError('Explicit unprivileged TLS port required')
    original=(ROOT/REFERENCE).read_bytes()
    if hashlib.sha256(original).hexdigest()!=REFERENCE_SHA:raise ValueError('Restore the pinned complete Max reference first')
    gmax_form=p.PokemonDisplayProto.Form.Value('Form_CHARIZARD_NORMAL')
    wooloo_form=p.PokemonDisplayProto.Form.Value('Form_WOOLOO_NORMAL')
    output.mkdir(parents=True)
    cfg=json.loads((ROOT/'config/family.json').read_text(encoding='utf-8'))
    cfg.update(profile='m182-power-spots',host='127.0.0.1',port=args.port,
               database=(output/'game.sqlite3').relative_to(ROOT).as_posix(),
               authentication={'mode':'family'},weather={'enabled':False},
               rocket={'enabled':False},pvp={'enabled':False},
               routes={'enabled':False},vivillon={'enabled':False},
               random_spawns={'enabled':False},catch_probability=1,
               spawn_pools=[{'species':[{'id':1,'weight':1}]}],
               spawn_points_per_tile=1,spawn_neighbor_tiles=0,
               initial_items={'1':100,'2':20,'3':20,'101':30,'201':20,'701':10})
    cfg['social']={**cfg.get('social',{}),'enabled':True,'allow_fixture_location':True}
    cfg['geographic_fixture_locations']=[{'lat':52.079632,'lng':5.122653},
        {'lat':52.07970,'lng':5.12290},{'lat':52.07945,'lng':5.12300}]
    cfg['walking']['allow_debug_fixture']=False
    cfg['world']={'enabled':False}
    for key,manifest in [('pokemon_assets','pokemon-assets.json'),('avatar_assets','avatar-assets.json')]:
        cfg[key]={'enabled':True,'manifest':'config/resources/'+manifest}
    cfg['pokemon_assets']['manifest']='config/resources/pokemon-max-assets.json'
    cfg['client_resources']={'enabled':True,'registry':'config/resources/client-runtime.json'}
    cfg['max_system'].update(enabled=True,battle_enabled=True,reference=REFERENCE,
        reference_sha256=REFERENCE_SHA,require_complete_reference=True,
        active_probability=0,minimum_level=20,
        pools=[{'species':831,'form':wooloo_form,'mode':1,'tier':1,'level':10},
               {'species':6,'form':gmax_form,'mode':2,'tier':7,'level':10}])
    cfg['max_system']['schedule']={'enabled':False}
    cfg['max_system']['development_balance'].update(
        provenance='Explicit M18.2 isolated development rehearsal; source move identities and costs retained; combat factors are custom, not official tuning.',
        lobby_seconds=30,battle_seconds=900,boss_hp=1000,boss_interval_ms=6000,
        boss_damage_multiplier=.25,meter_goal=100,meter_per_attack=10)
    cfg['max_system']['stationing']['enabled']=True
    if args.extended_test_hours:
        reference=json.loads(original)
        shared=p.GameMasterClientTemplateProto.FromString(bytes.fromhex(reference['templates']['BREAD_SHARED_SETTINGS']))
        availability=shared.bread_settings.bread_battle_availability
        source_minutes=[availability.bread_battle_availability_start_minute,
                        availability.bread_battle_availability_end_minute]
        availability.bread_battle_availability_start_minute=0
        availability.bread_battle_availability_end_minute=1440
        reference['templates']['BREAD_SHARED_SETTINGS']=shared.SerializeToString().hex()
        derived=output/'max-reference-rehearsal-hours.json';write(derived,reference)
        cfg['max_system']['reference']=derived.relative_to(ROOT).as_posix()
        cfg['max_system']['reference_sha256']=hashlib.sha256(derived.read_bytes()).hexdigest()
    else:source_minutes=[360,1260]
    tls=output/'tls'
    spec=importlib.util.spec_from_file_location('m182_tls',ROOT/'scripts/create-family-tls.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.create('127.0.0.1',tls)
    cfg['network']={'mode':'tls','certificate':(tls/'server.pem').relative_to(ROOT).as_posix(),
                    'private_key':(tls/'server-key.pem').relative_to(ROOT).as_posix()}
    cfg['rpc_trace_methods']=[102,103,106,2450,2451,2452,2453,2454,2455,2460,2461,2462,2463,2464,2465,2466,2467,2468,2470,2471,2472,2473,2474,2475,3051,3062]
    write(output/'server.json',cfg)
    game=Game(cfg,Database(output/'game.sqlite3'))
    credentials={};players={}
    for index,name in enumerate(('alice','bob')):
        username='m182-'+name+'-'+secrets.token_hex(3);password=secrets.token_urlsafe(24)
        game.accounts.register(username,password,'M182'+name.title(),game.accounts.invite(),peer='m182-declared-isolated-fixture')
        credentials[name]={'username':username,'password':password}
        with game.db.transaction() as db:
            player=db.execute('SELECT player_id FROM family_accounts WHERE username=?',(username,)).fetchone()[0]
            players[name]=player;db.execute('UPDATE players SET xp=210000,stardust=100000 WHERE id=?',(player,))
            db.execute('UPDATE trainer_teams SET team=1 WHERE player_id=?',(player,))
            for tutorial in (9,15,19,20):db.execute('INSERT OR IGNORE INTO tutorials VALUES(?,?)',(player,tutorial))
            for level in range(2,21):db.execute('INSERT OR IGNORE INTO level_reward_claims VALUES(?,?)',(player,level))
            mx.mutate(game,db,player,1000,'m182-declared-NOT-EARNED-starting-particles')
            for number in range(3):
                spawn=182000000000000+index*10+number
                db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',(spawn,'m182-NOT-EARNED-party:'+str(spawn),831,52.079632,5.122653,0))
                mon=make_pokemon(p,game.forms[831,wooloo_form],831,30,[15]*3);mx.attach(game,mon)
                for slot in mon.bread_moves:slot.move_level=1
                mon.nickname='M182 '+str(number+1)
                mon.id=db.execute('INSERT INTO pokemon(player_id,spawn_id,species,data) VALUES(?,?,?,?)',(player,spawn,831,b'')).lastrowid
                db.execute('UPDATE pokemon SET data=? WHERE id=?',(mon.SerializeToString(),mon.id))
                db.execute('INSERT INTO pokemon_levels VALUES(?,30)',(mon.id,))
    with game.db.transaction() as db:
        sites=[mx.place(game,db,52.07970,5.12290,'M18.2 Wooloo Test',{'species':831,'form':wooloo_form,'mode':1,'tier':1,'level':10},21600),
               mx.place(game,db,52.07945,5.12300,'M18.2 Charizard Test',{'species':6,'form':gmax_form,'mode':2,'tier':7,'level':10},21600)]
    secret_spec=importlib.util.spec_from_file_location('m182_private',ROOT/'tools/transfer-runtime.py')
    private=importlib.util.module_from_spec(secret_spec);secret_spec.loader.exec_module(private)
    (output/'credentials.dpapi').write_bytes(private.protect(json.dumps(credentials).encode()))
    origin={'lat':52.079632,'lng':5.122653}
    profile={'origin':'https://127.0.0.1:'+str(args.port),'ca_file':(tls/'ca.pem').relative_to(ROOT).as_posix(),
             'fixture_profile':cfg['profile'],'fixture_origin':origin,
             'fixture_locations':[{'name':'M18.2 isolated origin',**origin}]+[
                 {'name':site['name'],'lat':site['lat'],'lng':site['lng']} for site in sites],
             'max_server':True,'pvp_server_combat':False,'rocket_server_combat':False,
             'runtime_registry':'config/resources/client-runtime.json','network_diagnostics':True,
             'avatar_body_presets':['BodyPresetDefault','BodyPresetEight','BodyPresetFour'],
             'avatar_face_presets':['FacePresetSeven'],
             'world_style':json.loads((ROOT/'src/dittobuffer/world/world-style.json').read_text())}
    write(output/'profile.json',profile)
    write(output/'declaration.json',{'profile':cfg['profile'],'isolated':True,'accounts':players,
        'initial_parties_and_move_unlocks':'NOT_EARNED: three level-30 Wooloo per isolated account, three Max moves unlocked',
        'initial_particles_per_account':1000,'particle_budget':'NOT_EARNED',
        'source_reference_sha256':REFERENCE_SHA,'source_availability_minutes':source_minutes,
        'extended_test_hours':args.extended_test_hours,'test_availability_minutes':[0,1440] if args.extended_test_hours else source_minutes,
        'balance':'Custom development rehearsal, not official combat tuning',
        'physical_walking':'NOT_TESTED: declared simulated fixture locations',
        'multiplayer_android':'NOT_TESTED: only one physical client','stations':sites,
        'normal_player_data':'Not read or written','source_resources':'Restored independent local resources; no downloads'})
    print(json.dumps({'output':str(output),'profile':cfg['profile'],'stations':len(sites),'credentials':'DPAPI encrypted, not printed'}))

if __name__=='__main__':main()
