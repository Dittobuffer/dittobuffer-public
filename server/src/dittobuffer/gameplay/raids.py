"""Local, persistent solo raids. No production services or synthetic participants.

Boss state is keyed by raid, with a single admitted participant until co-op is
implemented. Balance is explicit local configuration, not inferred official data.
"""
import hashlib
import json
import logging
import math
import random
import secrets
from datetime import datetime,timezone
from dittobuffer.gameplay.game import p,now_ms,ROOT
from dittobuffer.gameplay import gyms
from dittobuffer.gameplay.items import quantity,consume
from dittobuffer.gameplay.species import apply_stats,make_pokemon,pool_rules
from dittobuffer.gameplay.progression import bounds

DEFAULTS={'enabled':False,'minimum_level':5,'egg_seconds':60,'active_seconds':3600,
          'rotation_seconds':7200,'lobby_seconds':120,'battle_seconds':120,'premier_balls':12,
          'relobby_seconds':120,'participation_limit':1,'daily_free_passes':1,
          'encounter_seconds':86400,'pools':[]}

def validate(game):
    overrides=game.config.get('raids',{});loaded={}
    if overrides.get('rules_file'):
        path=(ROOT/overrides['rules_file']).resolve()
        if not path.is_relative_to(ROOT):raise ValueError('Raid rules must be inside the project')
        loaded=json.loads(path.read_text(encoding='utf8'));loaded=loaded.get('raids',loaded)
    cfg=DEFAULTS|loaded|overrides
    if cfg.get('development_only') and game.config.get('environment','development')!='development':raise ValueError('Development raid balance cannot be used in a live environment')
    if type(cfg['participation_limit']) is not int or not 1<=cfg['participation_limit']<=10:raise ValueError('Raid participation limit must be 1..10')
    if cfg['participation_limit']>1 and (not cfg.get('multiplayer') or game.config.get('authentication',{}).get('mode')!='family'):raise ValueError('Cooperative raids require explicit multiplayer and family authentication')
    if type(cfg['daily_free_passes']) is not int or cfg['daily_free_passes'] not in (0,1):raise ValueError('Supported daily free pass limit is 0 or 1')
    if type(cfg['enabled']) is not bool:raise ValueError('Invalid raid enabled flag')
    for key in ('minimum_level','egg_seconds','active_seconds','rotation_seconds','lobby_seconds','relobby_seconds','battle_seconds','premier_balls','encounter_seconds'):
        if type(cfg[key]) is not int or cfg[key]<1:raise ValueError('Invalid raid rule: '+key)
    if cfg['battle_seconds']<20:raise ValueError('Raid battle time must be at least 20 seconds')
    if cfg['rotation_seconds']<cfg['egg_seconds']+cfg['active_seconds']:raise ValueError('Raid rotation overlaps')
    if cfg['minimum_level']>40 or cfg['premier_balls']>100 or cfg['battle_seconds']>3600:raise ValueError('Raid rule exceeds supported bounds')
    if cfg.get('gym_ids') is not None and (not isinstance(cfg['gym_ids'],list) or any(not isinstance(v,str) or not v for v in cfg['gym_ids'])):raise ValueError('Invalid eligible gym IDs')
    if 'epoch_ms' in cfg and (type(cfg['epoch_ms']) is not int or not 0<=cfg['epoch_ms']<2**53):raise ValueError('Invalid fixture raid epoch')
    dev_raid=cfg.get('dev_raid')
    if dev_raid:
        if not isinstance(dev_raid.get('gym_prefix'),str) or not dev_raid['gym_prefix']:raise ValueError('Invalid development raid gym prefix')
        if not isinstance(dev_raid.get('instance',''),str) or len(dev_raid.get('instance',''))>64:raise ValueError('Invalid development raid instance')
        if type(dev_raid.get('active_days',7)) is not int or not 1<=dev_raid['active_days']<=30:raise ValueError('Invalid development raid window')
        if not any(boss['species']==dev_raid.get('species') for boss in cfg['pools']):raise ValueError('Development raid species must be in the pool')
    for boss in cfg['pools']:
        if boss['species'] not in game.species or boss['tier'] not in (1,3,5,6):raise ValueError('Unsupported raid species/tier')
        variant=pool_rules(game,boss)
        if boss['tier']==6:
            from dittobuffer.gameplay.mega import branch,enabled,SPECIES
            evo=boss.get('mega_evolution')
            if not enabled(game) or type(evo) is not int or not branch(boss['species'],evo) or variant['form']!=SPECIES[boss['species']]['form'] or boss.get('costume',0):
                raise ValueError('Mega raids require an eligible ordinary species and supported temporary branch')
            energy=boss.get('mega_energy_rewards')
            if not isinstance(energy,list) or not energy or not boss.get('reward_policy'):
                raise ValueError('Mega Energy requires explicit documented reward policy; no guessed official loot')
            previous_time=0;previous_amount=50001
            for reward in energy:
                if set(reward)!={'max_seconds','amount'} or any(type(reward[k]) is not int or reward[k]<1 for k in reward):raise ValueError('Invalid Mega speed rewards')
                if reward['max_seconds']<=previous_time or reward['amount']>min(50000,previous_amount):raise ValueError('Mega speed thresholds must increase and rewards cannot increase with slower completion')
                previous_time=reward['max_seconds'];previous_amount=reward['amount']
            if previous_time<cfg['battle_seconds']:raise ValueError('Mega reward thresholds must cover the entire battle')
        elif any(k in boss for k in ('mega_evolution','mega_energy_rewards')):
            raise ValueError('Temporary raid boss requires Mega tier 6')
        if any(type(boss[k]) is not int or not 1<=boss[k]<=1000000 for k in ('hp','cp','weight')):raise ValueError('Explicit bounded raid stats/weight required')
        if not 1<=boss.get('level',20)<=50 or not 0<=boss.get('shiny_rate',0)<=1:raise ValueError('Invalid raid level/shiny rate')
        if type(boss.get('premier_balls',cfg['premier_balls'])) is not int or not 1<=boss.get('premier_balls',cfg['premier_balls'])<=100:raise ValueError('Invalid boss Premier Ball allocation')
        for key in ('xp','dust'):
            if type(boss.get(key,0)) is not int or boss.get(key,0)<0:raise ValueError('Invalid raid reward')
        from dittobuffer.gameplay.raid_items import REWARD_ITEMS
        if any(int(k) not in REWARD_ITEMS or type(v) is not int or v<1 for k,v in boss.get('items',{}).items()):raise ValueError('Unsupported raid reward item')
        from dittobuffer.gameplay.battle_master import MOVES
        if any(m not in MOVES for m in variant['moves']):raise ValueError('Unsupported boss moves')
    if cfg['enabled'] and not cfg['pools']:raise ValueError('Enabled raids require a pool')
    if type(cfg.get('staggered',False)) is not bool or not 0<=cfg.get('slot_probability',.5)<=1:raise ValueError('Invalid geographic raid schedule')
    if not 0<=cfg.get('start_hour',6)<cfg.get('end_hour',22)<=24:raise ValueError('Invalid local raid hours')
    if type(cfg.get('phase_offset_seconds',0)) is not int:raise ValueError('Invalid schedule phase')
    from dittobuffer.gameplay.raid_rewards import validate as validate_ball_bonuses
    validate_ball_bonuses(cfg)
    game.raids=cfg

def client_settings(config):
    return p.RaidClientSettingsProto(max_players_per_lobby=config.get('raids',{}).get('participation_limit',1),lobby_refresh_interval_ms=2000)

def templates(config):
    from dittobuffer.gameplay.raid_items import templates as reward_item_templates
    values=reward_item_templates()
    for item,name in [(1401,'FREE_RAID_TICKET'),(1402,'PAID_RAID_TICKET'),(1408,'REMOTE_RAID_TICKET')]:
        t=p.GameMasterClientTemplateProto(template_id='ITEM_'+name)
        t.item.unique_id=item;t.item.item_type=16;t.item.category=15;t.item.drop_trainer_level=5
        t.item.ignore_inventory_space=item==1401;values.append(t)
    t=p.GameMasterClientTemplateProto(template_id='ITEM_PREMIER_BALL')
    t.item.unique_id=5;t.item.item_type=1;t.item.category=1;t.item.pokeball.capture_multi=1;t.item.ignore_inventory_space=True
    values.append(t)
    return values

def _boss_pokemon(game,boss,seed,end):
    boss=dict(boss)
    if hasattr(game,'liveops_effective') and 'raids' in game.liveops_effective:boss['_liveops']=game.liveops_effective.get('generations',{}).get('raids',game.liveops_effective['generation'])
    boss['_rules']={k:game.raids[k] for k in ('lobby_seconds','relobby_seconds','battle_seconds','premier_balls','encounter_seconds','participation_limit')}
    boss['_rules']['ball_bonuses']=game.raids.get('ball_bonuses',{})
    from dittobuffer.gameplay.species import choose_gender
    pokemon=make_pokemon(p,pool_rules(game,boss),boss['species'],boss.get('level',20),[15]*3,
        gender=boss.get('gender') or choose_gender(game,boss['species'],random.Random(f'boss:{seed}')),costume=boss.get('costume',0))
    if boss.get('shadow'):pokemon.pokemon_display.alignment=1
    if boss.get('bread_mode'):pokemon.pokemon_display.bread_mode_enum=int(boss['bread_mode'])
    pokemon.id=seed;pokemon.cp=boss['cp'];pokemon.stamina=pokemon.max_stamina=boss['hp']
    if boss['tier']==6:
        pokemon.pokemon_display.current_temp_evolution=boss['mega_evolution']
        pokemon.pokemon_display.temporary_evolution_finish_ms=end
        pokemon.pokemon_display.temp_evolution_is_locked=True
        pokemon.pokemon_display.locked_temp_evolution=boss['mega_evolution']
        pokemon.temp_evo_cp=boss['cp'];pokemon.temp_evo_cp_multiplier=pokemon.cp_multiplier;pokemon.temp_evo_stamina_modifier=1
    return boss,pokemon

def _fixture_raid(game,db,fort,boss,start,battle,end,seed):
    boss,pokemon=_boss_pokemon(game,boss,seed,end)
    db.execute('INSERT OR IGNORE INTO raids VALUES(?,?,?,?,?,?,?)',(seed,fort['id'],json.dumps(boss),pokemon.SerializeToString(),start,battle,end))
    return db.execute('SELECT * FROM raids WHERE seed=?',(seed,)).fetchone()

def schedule(game,db,fort):
    if not game.raids['enabled']:return None
    if game.raids.get('gym_ids') is not None and fort['id'] not in game.raids['gym_ids']:return None
    stamp=now_ms()
    dev_raid=game.raids.get('dev_raid')
    if dev_raid and fort['id'].startswith(dev_raid['gym_prefix']):
        # One persisted development window. Expiry must not resurrect the same
        # immutable seed, even when the process or client reconnects.
        row=db.execute('SELECT * FROM raids WHERE fort_id=? AND end_ms>? ORDER BY spawn_ms DESC LIMIT 1',(fort['id'],stamp)).fetchone()
        if row:return row
        boss=next(b for b in game.raids['pools'] if b['species']==dev_raid['species'])
        key=f'dev:{fort["id"]}'+(':'+dev_raid['instance'] if dev_raid.get('instance') else '')
        seed=int.from_bytes(hashlib.sha256(key.encode()).digest()[:7],'big')
        end=stamp+dev_raid.get('active_days',7)*86400000
        row=_fixture_raid(game,db,fort,boss,stamp-1000,stamp-1000,end,seed)
        return row if row['spawn_ms']<=stamp<row['end_ms'] else None
    period=game.raids['rotation_seconds']*1000
    start=(stamp//period)*period
    if game.raids.get('staggered',False):
        revision=game.liveops_effective.get('generations',{}).get('raids',game.liveops_effective['generation']) if 'raids' in getattr(game,'liveops_effective',{}) else None
        current=db.execute('SELECT * FROM raids WHERE fort_id=? AND spawn_ms<=? AND end_ms>? AND json_extract(definition,?) IS ? ORDER BY spawn_ms DESC LIMIT 1',(fort['id'],stamp,stamp,'$._liveops',revision)).fetchone()
        if current:return current
        offset=int.from_bytes(hashlib.sha256(('schedule:'+fort['id']).encode()).digest()[:4],'big')%period
        offset+=game.raids.get('phase_offset_seconds',0)*1000
        start=((stamp-offset)//period)*period+offset
        chance=int.from_bytes(hashlib.sha256(f'eligible:{fort["id"]}:{start}'.encode()).digest()[:4],'big')/2**32
        if chance>=game.raids.get('slot_probability',.5):return None
        from zoneinfo import ZoneInfo
        hour=datetime.fromtimestamp(start/1000,ZoneInfo('Europe/Amsterdam')).hour
        if not game.raids.get('start_hour',6)<=hour<game.raids.get('end_hour',22):return None
    # Fixture epoch is explicit and immutable; ordinary schedules rotate by UTC slot.
    start=game.raids.get('epoch_ms',start)
    end=start+(game.raids['egg_seconds']+game.raids['active_seconds'])*1000
    if not start<=stamp<end:return None
    suffix=':studio:'+str(game.liveops_effective.get('generations',{}).get('raids',game.liveops_effective['generation'])) if 'raids' in getattr(game,'liveops_effective',{}) else ''
    seed=int.from_bytes(hashlib.sha256((f'{fort["id"]}:{start}'+suffix).encode()).digest()[:7],'big')
    row=db.execute('SELECT * FROM raids WHERE seed=?',(seed,)).fetchone()
    if not row:
        from dittobuffer.gameplay import liveops
        pools=liveops.raid_pool(game) or game.raids['pools']
        pick=seed%sum(b['weight'] for b in pools)
        for boss in pools:
            pick-=boss['weight']
            if pick<0:break
        row=_fixture_raid(game,db,fort,boss,start,start+game.raids['egg_seconds']*1000,end,seed)
    # Persisted deadlines remain authoritative after an admin/config edit.
    return row if row['spawn_ms']<=stamp<row['end_ms'] else None

def find(game,db,request):
    if not game.raids['enabled']:return None
    return db.execute('SELECT * FROM raids WHERE seed=? AND fort_id=? AND end_ms>?',(request.raid_seed,request.gym_id,now_ms())).fetchone()

def member(db,seed,player):return db.execute('SELECT * FROM raid_members WHERE seed=? AND player_id=?',(seed,player)).fetchone()

def rules(game,row):return game.raids|json.loads(row['definition']).get('_rules',{})

def current_battle(db,m):
    return db.execute('SELECT * FROM raid_battles WHERE id=? AND player_id=?',(m['battle_id'],m['player_id'])).fetchone() if m else None

def rotate_token(db,row,m):
    db.execute('UPDATE raid_combat_sessions SET revoked_ms=? WHERE battle_id=? AND revoked_ms=0',(now_ms(),m['battle_id']))
    db.execute('INSERT INTO raid_tokens VALUES(?,?,?) ON CONFLICT(seed,player_id) DO UPDATE SET token=excluded.token',(row['seed'],m['player_id'],secrets.token_hex(32)))

def open_lobby(game,db,row,m):
    if game.raids['participation_limit']>1:
        from dittobuffer.gameplay.cooperative import open_lobby as shared
        return shared(game,db,row,m)
    return _open_lobby(game,db,row,m)

def _open_lobby(game,db,row,m):
    """One current attempt per participant; pass/reward ownership stays on the raid."""
    b=current_battle(db,m);state=json.loads(b['state']) if b else None
    if state and state['status']==1:
        # Reopening an active raid creates a new native event producer. Keep
        # the battle/party/HP/energy/pending damage/deadline, but fence the old
        # producer and its sequence numbers. Repeated joins before Start share
        # this prepared connection instead of continually rotating its token.
        if not state.get('reconnect_prepared'):
            rotate_token(db,row,m)
            state.setdefault('previous_connections',[]).append({'generation':state.get('generation',0),'reconnected_ms':now_ms()})
            state['generation']=state.get('generation',0)+1
            state['reconnect_prepared']=True;persist(db,b['id'],state)
        return m
    if state and state['status']==2:return m
    if state and state['status']==3 and not state.get('surrendered') and now_ms()<state['end']:
        if not state.get('relobby_prepared'):
            rotate_token(db,row,m);state['relobby_prepared']=True;persist(db,b['id'],state)
            db.execute('UPDATE raid_members SET left_lobby=0,lobby_start_ms=? WHERE seed=? AND player_id=?',(now_ms(),row['seed'],m['player_id']))
        return member(db,row['seed'],m['player_id'])
    if not b and m['battle_id'] and not m['left_lobby']:return m
    rotate_token(db,row,m)
    if b:db.execute('UPDATE raid_battles SET settled=1,acknowledged=1 WHERE id=?',(b['id'],))
    db.execute('UPDATE raid_members SET battle_id=?,left_lobby=0,lobby_start_ms=? WHERE seed=? AND player_id=?',
        ('raid-'+str(row['seed'])+'-'+secrets.token_hex(8),now_ms(),row['seed'],m['player_id']))
    return member(db,row['seed'],m['player_id'])

def lobby_code(m,db=None):
    from dittobuffer.gameplay.cooperative import native_id
    digest=hashlib.sha256((native_id(db,m['battle_id']) if db else m['battle_id']).encode()).digest()
    # Native GroupCodePokemon has nine entries, indexed 0..8. A value of 9
    # throws during Initialize before the party panel is constructed.
    return [v%9 for v in digest[:3]]

def info(row,complete=False):
    pokemon=p.PokemonProto.FromString(row['pokemon'])
    # Mega raid cards read temp_evo_cp; legacy/admin rows stored it as zero.
    if pokemon.pokemon_display.current_temp_evolution and not pokemon.temp_evo_cp and pokemon.cp:
        pokemon.temp_evo_cp=pokemon.cp
        pokemon.temp_evo_cp_multiplier=pokemon.cp_multiplier
        pokemon.temp_evo_stamina_modifier=1
    return p.RaidInfoProto(raid_seed=row['seed'],raid_spawn_ms=row['spawn_ms'],raid_battle_ms=row['battle_ms'],raid_end_ms=row['end_ms'],
        raid_pokemon=pokemon,raid_level=json.loads(row['definition'])['tier'],complete=complete,raid_ball=5,default_raid_ball=5)

def entry_cost(item=1401):
    return p.RaidEntryCostProto(raid_type=1,item_requirement=[p.ItemRequirementProto(item=item,count=1)])

def pass_for(db,player,row):
    # Local standard-Mega entry reuses premium passes, without pretending to
    # implement 2026 Link Charges or remote entry.
    return 1402 if json.loads(row['definition'])['tier']==6 else 1401 if quantity(db,player,1401) else 1402

def free_available(db,player):
    day=datetime.fromtimestamp(now_ms()/1000,timezone.utc).date().isoformat()
    return not quantity(db,player,1401) and not db.execute('SELECT 1 FROM raid_daily_passes WHERE player_id=? AND day=?',(player,day)).fetchone()

def award_pass(game,db,player,body):
    req=p.AwardFreeRaidTicketProto.FromString(body);fort=gyms.gym(db,req.gym_id)
    result=1
    if not game.raids['enabled']:result=4
    elif bounds(game.config,game.player(db,player)['xp'])[0]<game.raids['minimum_level']:result=2
    elif not fort or not gyms.near(game,fort,req.player_lat_degrees,req.player_lng_degrees):result=4
    elif not game.raids['daily_free_passes'] or not free_available(db,player):result=3
    else:
        day=datetime.fromtimestamp(now_ms()/1000,timezone.utc).date().isoformat()
        db.execute('INSERT INTO raid_daily_passes VALUES(?,?,?)',(player,day,now_ms()))
        db.execute('INSERT INTO items VALUES(?,1401,1) ON CONFLICT(player_id,item) DO UPDATE SET count=count+1',(player,))
        game.touch(db,player)
    return p.AwardFreeRaidTicketOutProto(result=result)

def lobby(game,db,row,m):
    if game.raids['participation_limit']>1:
        from dittobuffer.gameplay.cooperative import lobby as shared
        return shared(game,db,row,m)
    cfg=rules(game,row);b=current_battle(db,m);state=json.loads(b['state']) if b else None
    relobby=bool(state and state.get('relobby_prepared'))
    start=m['lobby_start_ms'];battle_start=start+cfg['relobby_seconds' if relobby else 'lobby_seconds']*1000
    db.execute('INSERT OR IGNORE INTO raid_tokens VALUES(?,?,?)',(row['seed'],m['player_id'],secrets.token_hex(32)))
    token=db.execute('SELECT token FROM raid_tokens WHERE seed=? AND player_id=?',(row['seed'],m['player_id'])).fetchone()[0]
    out=p.LobbyProto(lobby_id=lobby_code(m),creation_ms=start,player_join_end_ms=battle_start-1000,pokemon_selection_end_ms=battle_start,
        raid_battle_start_ms=state['start'] if state and state['status']==1 else battle_start,
        raid_battle_end_ms=state['end'] if state and (state['status']==1 or relobby) else min(row['end_ms'],battle_start+cfg['battle_seconds']*1000),
        raid_battle_id=m['battle_id'],owner_nickname=game.player(db,m['player_id'])['name'],battle_plfe_instance=1,total_player_count_in_lobby=0 if m['left_lobby'] else 1,
        rvn_connection=p.RvnConnectionProto(base_uri='/raid/',token=token),rvn_version=1)
    if not m['left_lobby']:
        from dittobuffer.gameplay.mega import enabled
        has_mega=enabled(game) and db.execute('SELECT 1 FROM mega_active WHERE player_id=? AND end_ms>?',(m['player_id'],now_ms())).fetchone() is not None
        out.players.add(player_id=m['player_id'][:20],trainer_public_profile=gyms.public(game,db,m['player_id']),has_active_mega_evolved_pokemon=has_mega)
        out.total_mega_evolved_count_in_lobby=int(has_mega)
    return out

def details(game,db,player,body):
    req=p.GetRaidDetailsProto.FromString(body);row=find(game,db,req)
    if not row:
        # A globally expired raid cannot admit anyone. Its genuine winner may
        # still recover the durable summary/owned encounter after a restart.
        row=db.execute("SELECT r.* FROM raids r JOIN raid_members m ON m.seed=r.seed JOIN raid_battles b ON b.id=m.battle_id WHERE r.seed=? AND r.fort_id=? AND m.player_id=? AND (m.reward IS NOT NULL OR json_extract(b.state,'$.status')=2)",(req.raid_seed,req.gym_id,player)).fetchone()
    if not row:
        logging.warning('RAID details rejected result=4 gym=%s seed=%d: no active or owned completed raid',req.gym_id,req.raid_seed)
        return p.GetRaidDetailsOutProto(result=4)
    fort=gyms.gym(db,row['fort_id'])
    if not fort or not gyms.near(game,fort,req.player_lat_degrees,req.player_lng_degrees):
        logging.warning('RAID details rejected result=2 gym=%s seed=%d: gym unavailable or out of range',req.gym_id,req.raid_seed)
        return p.GetRaidDetailsOutProto(result=2)
    m=member(db,row['seed'],player)
    out=p.GetRaidDetailsOutProto(result=1,raid_info=info(row,bool(m and state_of(db,m)==9)),player_can_join_lobby=not(m and m['reward']),
        free_ticket_available=bool(json.loads(row['definition'])['tier']!=6 and game.raids['daily_free_passes'] and free_available(db,player)),server_ms=now_ms(),server_instance=1,is_within_plfe_range=True,raid_ball=5,
        raid_entry_cost=entry_cost(pass_for(db,player,row)))
    if m:
        if row['end_ms']<=now_ms() or state_of(db,m) in (7,8,9):out.player_can_join_lobby=False
        out.ticket_used=bool(db.execute('SELECT 1 FROM raid_pass_receipts WHERE seed=? AND player_id=?',(row['seed'],player)).fetchone());out.received_rewards=m['reward'] is not None;out.throws_remaining=m['balls']
        b=current_battle(db,m);state=json.loads(b['state']) if b else None
        retry=bool(b and b['acknowledged'] and (state['status']==4 or state.get('surrendered')))
        relobby=bool(b and b['acknowledged'] and state['status']==3 and not state.get('surrendered'))
        if relobby:m=open_lobby(game,db,row,m)
        if not retry:
            out.lobby.CopyFrom(lobby(game,db,row,m));out.num_players_in_lobby=out.lobby.total_player_count_in_lobby
        if b and not retry and not relobby:
            state=json.loads(b['state']);out.raid_battle.CopyFrom(battle_proto(game,db,b,state))
            out.rvn_battle_completed=state['status']!=1;out.rvn_battle_flushed=bool(m['reward'])
            out.rvn_battle_is_victory=state['status']==2
        if m['reward']:
            result=saved_reward(m)
            # Return the saved encounter with its live ball count, never mint a
            # second reward when Android reopens the gym after an interruption.
            for action in out.raid_battle.battle_log.battle_actions:
                if action.type==8:action.battle_results.CopyFrom(result.battle_results)
            out.capture_probabilities.CopyFrom(probabilities(game));out.appraisal_star=3
    return out

def simple_details(game,db,player,body):
    req=p.GetSimpleRaidDetailsProto.FromString(body);row=find(game,db,req)
    if not row:return p.GetSimpleRaidDetailsOutProto(result=4)
    fort=gyms.gym(db,row['fort_id'])
    # 0.429.2 sends gym coordinates, not player coordinates, in this preview
    # request. Joining/starting still validates the actual player location.
    if not gyms.accessible(game,fort):return p.GetSimpleRaidDetailsOutProto(result=6)
    m=member(db,row['seed'],player);boss=json.loads(row['definition'])
    out=p.GetSimpleRaidDetailsOutProto(result=1,server_ms=now_ms(),server_instance=1,trainer_battle_state=state_of(db,m))
    a=out.approach_screen_details;a.raid_seed=row['seed'];a.free_ticket_available=bool(json.loads(row['definition'])['tier']!=6 and game.raids['daily_free_passes'] and free_available(db,player))
    a.raid_spawn_ms=row['spawn_ms'];a.raid_battle_ms=row['battle_ms'];a.raid_end_ms=row['end_ms'];a.raid_pokemon.ParseFromString(row['pokemon'])
    a.raid_level_entry_cost.raid_level=boss['tier']
    a.raid_level_entry_cost.raid_entry_cost.add().CopyFrom(entry_cost(pass_for(db,player,row)))
    if m:
        a.payments_made.battle_payments.add(payment_state=2,item_requirement=p.ItemRequirementProto(item=m['pass_item'],count=1))
        a.num_players_in_lobby=int(not m['left_lobby']);a.lobby_creation_ms=m['lobby_start_ms'];a.lobby_join_end_ms=m['lobby_start_ms']+rules(game,row)['lobby_seconds']*1000-1000
    return out

def state_of(db,m):
    if not m:return 0
    if m['spawn_id']:
        caught=db.execute('SELECT state FROM encounters WHERE player_id=? AND spawn_id=?',(m['player_id'],m['spawn_id'])).fetchone()
        return 9 if caught and caught['state']!='active' else 8
    b=current_battle(db,m)
    if b:
        state=json.loads(b['state'])
        if b['acknowledged'] and (state['status']==4 or state.get('surrendered')):return 0
        return {1:2,2:5,3:3,4:6}[state['status']]
    return 4 if m['left_lobby'] else 1

def history(db,player,fort_id):
    out=p.PlayerRaidInfoProto()
    for m in db.execute('SELECT m.*,r.fort_id,r.definition FROM raid_members m JOIN raids r ON r.seed=m.seed WHERE m.player_id=? AND m.reward IS NOT NULL',(player,)):
        boss=json.loads(m['definition']);out.total_completed_raids+=1
        out.total_completed_legendary_raids+=int(boss['tier']==5)
        if m['fort_id']!=fort_id:continue
        caught=db.execute('SELECT state FROM encounters WHERE player_id=? AND spawn_id=?',(player,m['spawn_id'])).fetchone()
        raid=out.raids.add(raid_seed=m['seed'],started_ms=m['joined_ms'],completed_battle=True,received_rewards=True,
            finished_encounter=bool(caught and caught['state']!='active'),encounter_pokemon_id=boss['species'])
        owned=db.execute('SELECT data FROM pokemon WHERE player_id=? AND spawn_id=?',(player,m['spawn_id'])).fetchone()
        if owned:raid.reward_pokemon.ParseFromString(owned['data'])
    return out

def join(game,db,player,body):
    req=p.JoinLobbyProto.FromString(body);row=find(game,db,req)
    def fail(code):return p.JoinLobbyOutProto(result=code)
    if not row or row['battle_ms']>now_ms():return fail(3)
    fort=gyms.gym(db,row['fort_id'])
    if not gyms.near(game,fort,req.player_lat_degrees,req.player_lng_degrees):return fail(2)
    if bounds(game.config,game.player(db,player)['xp'])[0]<game.raids['minimum_level']:return fail(6)
    if req.use_remote_pass:return fail(10)
    m=member(db,row['seed'],player)
    if m and m['reward']:return fail(4)
    if game.raids['participation_limit']==1 and db.execute('SELECT 1 FROM raid_members WHERE seed=? AND player_id!=? AND left_lobby=0',(row['seed'],player)).fetchone():return fail(13)
    if game.raids['participation_limit']>1:
        from dittobuffer.gameplay.cooperative import admission
        if not admission(game,db,row,player):return fail(13)
    if not m:
        item=pass_for(db,player,row)
        if not quantity(db,player,item):return fail(9)
        stamp=now_ms()
        db.execute('INSERT INTO raid_members(seed,player_id,joined_ms,lobby_start_ms,pass_item) VALUES(?,?,?,?,?)',(row['seed'],player,stamp,stamp,item))
        game.touch(db,player)
    m=open_lobby(game,db,row,member(db,row['seed'],player))
    return p.JoinLobbyOutProto(result=1,lobby=lobby(game,db,row,m))

def leave(game,db,player,body):
    req=p.LeaveLobbyProto.FromString(body);row=find(game,db,req);m=member(db,req.raid_seed,player)
    if not row or not m:return p.LeaveLobbyOutProto(result=3)
    b=current_battle(db,m)
    if b:
        state=json.loads(b['state'])
        if state['status']==1:
            state.update(status=3,surrendered=True,results='');finish_battle(game,db,b,state)
        if state['status']!=2:
            db.execute('UPDATE raid_battles SET acknowledged=1,settled=1 WHERE id=?',(b['id'],))
            db.execute('UPDATE raid_combat_sessions SET revoked_ms=? WHERE battle_id=? AND revoked_ms=0',(now_ms(),b['id']))
    db.execute('UPDATE raid_members SET left_lobby=1 WHERE seed=? AND player_id=?',(row['seed'],player))
    return p.LeaveLobbyOutProto(result=1,lobby=lobby(game,db,row,member(db,row['seed'],player)))

def lobby_details(game,db,player,body):
    req=p.GetRaidLobbyDetailsProto.FromString(body);row=find(game,db,req);m=member(db,req.raid_seed,player)
    if not row or not m:return p.GetRaidLobbyDetailsOutProto(result=4)
    return p.GetRaidLobbyDetailsOutProto(result=1,server_ms=now_ms(),server_instance=1,trainer_battle_state=state_of(db,m),lobby=lobby(game,db,row,m))

def participant(game,db,player,state,defender=False):
    from dittobuffer.gameplay import gym_battle as engine
    if not defender:return engine.participant(game,db,player,state)
    return p.BattleParticipantProto(active_pokemon=engine.info(state['defender']),player_id='raid-boss')

def battle_proto(game,db,b,state):
    from dittobuffer.gameplay import gym_battle as engine
    from dittobuffer.gameplay.cooperative import native_id
    return p.BattleProto(battle_id=native_id(db,b['id']),battle_start_ms=state['start'],battle_end_ms=state['end'],
        attacker=participant(game,db,b['player_id'],state),defender=participant(game,db,b['player_id'],state,True),battle_log=engine.log(state,now_ms()))

def start(game,db,player,body):
    import logging
    from dittobuffer.gameplay import gym_battle as engine
    from dittobuffer.core.management import owned
    from dittobuffer.gameplay.battle_master import MOVES
    req=p.StartRaidBattleProto.FromString(body);row=find(game,db,req);m=member(db,req.raid_seed,player)
    logging.info('RAID start gym=%s seed=%d lobby=%s party=%s',req.gym_id,req.raid_seed,list(req.lobby_id),list(req.attacking_pokemon_id))
    def fail(code):
        logging.info('RAID start rejected result=%d',code)
        return p.StartRaidBattleOutProto(result=code)
    if not row or row['battle_ms']>now_ms():return fail(3)
    if not m or m['left_lobby']:return fail(12)
    if m['reward']:return fail(4)
    if bounds(game.config,game.player(db,player)['xp'])[0]<game.raids['minimum_level']:return fail(6)
    existing=current_battle(db,m);previous=json.loads(existing['state']) if existing else None
    wait=rules(game,row)['relobby_seconds' if previous and previous.get('relobby_prepared') else 'lobby_seconds']
    if now_ms()<m['lobby_start_ms']+wait*1000-1000:return fail(9)
    # The actual client omits the public lobby code in StartRaidBattle. Resolve
    # that through its durable membership; still reject a different explicit code.
    if req.lobby_id and list(req.lobby_id)!=lobby_code(m,db):return fail(9)
    fort=gyms.gym(db,row['fort_id'])
    if not gyms.near(game,fort,req.player_lat_degrees,req.player_lng_degrees):return fail(7)
    ids=list(req.attacking_pokemon_id)
    if not 1<=len(ids)<=6 or len(ids)!=len(set(ids)):return fail(5)
    if previous and previous['status']==1:
        # Android rebuilds its recommendation after a cold start, including
        # reordering the same survivors. Resume the authoritative saved party;
        # only a genuine party defeat may replace it through the relobby path.
        previous.pop('reconnect_prepared',None);persist(db,existing['id'],previous)
        return start_response(game,db,existing,previous,player)
    for other in db.execute('SELECT id,state FROM gym_battles WHERE player_id=? AND settled=0 AND end_ms>? UNION ALL SELECT id,state FROM raid_battles WHERE player_id=? AND settled=0 AND end_ms>?',(player,now_ms(),player,now_ms())):
        if other['id']!=m['battle_id'] and json.loads(other['state'])['status']==1:return fail(5)
    party=[owned(db,player,i) for i in ids]
    if any(not v or v.is_egg or not v.stamina or v.deployed_fort_id or v.deployed_station_id or v.move1 not in MOVES or v.move2 not in MOVES for v in party):return fail(5)
    from dittobuffer.gameplay.mega import project
    party=[project(game,db,v,combat=True) for v in party]
    b=existing
    if b:
        if b['player_id']!=player:return fail(14)
        state=json.loads(b['state'])
        if state['status']==3 and not state.get('surrendered') and now_ms()<state['end']:
            # Relobby retains the boss HP and original clock, never another pass.
            state.setdefault('previous_parties',[]).append({'party':state['party'],'events':state['events'],'results':state['results'],
                'ended_ms':state['clock'],'rejoined_ms':now_ms(),'boss_hp':state['defender']['hp']})
            if not state.get('relobby_prepared'):rotate_token(db,row,m)
            state.update(party=[engine.entry(v) for v in party],active=0,status=1,pending=[],ai_due=now_ms()+1500,results='',
                events=[],seen=[],generation=state.get('generation',0)+1)
            state.pop('relobby_prepared',None)
            state.pop('pending_swap',None);engine.event(state,6,now_ms(),joined_player=participant(game,db,player,state))
            persist(db,b['id'],state)
        elif state['status']!=1:return fail(4)
        elif [engine.mon(v).id for v in state['party']]!=ids:return fail(5)
    else:
        paid=db.execute('SELECT 1 FROM raid_pass_receipts WHERE seed=? AND player_id=?',(row['seed'],player)).fetchone()
        if not paid:
            if not consume(db,player,m['pass_item']):return fail(10)
            db.execute('INSERT INTO raid_pass_receipts VALUES(?,?,?,?)',(row['seed'],player,m['pass_item'],now_ms()));game.touch(db,player)
        stamp=now_ms();end=min(row['end_ms'],stamp+rules(game,row)['battle_seconds']*1000)
        state={'start':stamp,'end':end,'clock':stamp,'status':1,'active':0,'owner':player,'party':[engine.entry(v) for v in party],
            'defender':engine.entry(p.PokemonProto.FromString(row['pokemon'])),'ai_due':stamp+1500,'pending':[],'events':[],'seen':[],'results':'','battle_type':3}
        engine.event(state,6,stamp,joined_player=participant(game,db,player,state))
        ident=m['battle_id']
        db.execute('INSERT INTO raid_battles(id,seed,player_id,state,end_ms) VALUES(?,?,?,?,?)',(ident,row['seed'],player,json.dumps(state),end))
        db.execute('UPDATE raid_members SET battle_id=? WHERE seed=? AND player_id=?',(ident,row['seed'],player))
        b=db.execute('SELECT * FROM raid_battles WHERE id=?',(ident,)).fetchone()
    from dittobuffer.gameplay.buddy import activity
    activity(game,db,player,'battle',b['id'],ids)
    return start_response(game,db,b,state,player)

def start_response(game,db,b,state,player):
    from dittobuffer.gameplay.cooperative import attach
    if game.raids['participation_limit']>1:attach(game,db,b,state)
    token=db.execute('SELECT token FROM raid_tokens WHERE seed=? AND player_id=?',(b['seed'],player)).fetchone()[0]
    db.execute('INSERT OR IGNORE INTO raid_combat_sessions(battle_id,generation,player_id,token,created_ms) VALUES(?,?,?,?,?)',(b['id'],state.get('generation',0),player,token,now_ms()))
    if game.accounts:
        from dittobuffer.core.accounts import principal_session
        session=principal_session.get()
        if session:db.execute('INSERT INTO family_battle_sessions VALUES(?,?,?) ON CONFLICT(battle_id,generation) DO UPDATE SET session_digest=excluded.session_digest',(b['id'],state.get('generation',0),session))
    db.execute('UPDATE raid_battles SET acknowledged=0,settled=0 WHERE id=?',(b['id'],))
    return p.StartRaidBattleOutProto(result=1,battle=battle_proto(game,db,b,state),session_player_id=player[:20],total_attacker_count=state.get('room_participants',1))

def persist(db,ident,state,settled=None):
    db.execute('UPDATE raid_battles SET state=? WHERE id=?',(json.dumps(state,separators=(',',':')),ident))
    if settled is not None:db.execute('UPDATE raid_battles SET settled=? WHERE id=?',(settled,ident))

def checkpoint(game,db,b,state):
    from dittobuffer.gameplay import gym_battle as engine
    engine.project_research_charges(game,db,b['player_id'],b['id'],state)
    from dittobuffer.core.management import owned,update_owned
    for value in state['party']:
        pokemon=owned(db,b['player_id'],engine.mon(value).id)
        previous=value.get('checkpoint_hp',engine.mon(value).stamina)
        if pokemon and value['hp']<previous:
            from dittobuffer.gameplay.mega import ordinary_hp
            pokemon.stamina=min(pokemon.stamina,ordinary_hp(engine.mon(value),value['hp']));update_owned(game,db,b['player_id'],pokemon)
        value['checkpoint_hp']=value['hp']

def finish_battle(game,db,b,state):
    from dittobuffer.gameplay import gym_battle as engine
    if not state['results']:
        result=p.BattleResultsProto(battle_duration_ms=max(0,min(now_ms(),state['end'])-state['start']))
        result.attackers.add().CopyFrom(participant(game,db,b['player_id'],state));result.player_xp_awarded.append(0)
        state['results']=result.SerializeToString().hex()
        engine.event(state,{2:8,3:9,4:10}[state['status']],min(now_ms(),state['end']),battle_results=result)
    checkpoint(game,db,b,state);persist(db,b['id'],state,1 if state['status'] in (2,4) or state.get('surrendered') else 0)

def attack(game,db,player,body):
    import logging
    from dittobuffer.gameplay import gym_battle as engine
    req=p.AttackRaidBattleProto.FromString(body)
    if game.raids['participation_limit']>1:return p.AttackRaidBattleOutProto(result=4) # Shared combat uses the authenticated PBS sequence protocol.
    b=db.execute('SELECT b.* FROM raid_battles b JOIN raids r ON r.seed=b.seed WHERE b.id=? AND b.player_id=? AND r.fort_id=?',(req.battle_id,player,req.gym_id)).fetchone()
    if not b or member(db,b['seed'],player)['battle_id']!=b['id']:return p.AttackRaidBattleOutProto(result=3)
    state=json.loads(b['state']);stamp=now_ms()
    # Legacy 166 carries no party-generation/session identifier. The real
    # client uses authenticated PBS; don't let stale legacy actions cross a relobby.
    if state.get('generation',0):return p.AttackRaidBattleOutProto(result=4)
    if len(req.attacker_actions)>100:return p.AttackRaidBattleOutProto(result=4)
    if state['status']==1:
        if not engine.process_actions(game,state,req.attacker_actions,stamp):return p.AttackRaidBattleOutProto(result=4)
        if any(a.type==7 for a in req.attacker_actions) and state['status']==3:state['surrendered']=True
        engine.advance(game,state,stamp);checkpoint(game,db,b,state)
        if state['status']!=1:finish_battle(game,db,b,state)
        persist(db,b['id'],state)
    logging.info('RAID %s actions=%s status=%d boss_hp=%d attacker_hp=%d',b['id'],[(a.type,a.action_start_ms) for a in req.attacker_actions],state['status'],state['defender']['hp'],state['party'][state['active']]['hp'])
    return p.AttackRaidBattleOutProto(result=1,battle_update=p.BattleUpdateProto(battle_id=b['id'],battle_log=engine.log(state,stamp),
        active_defender=engine.info(state['defender']),active_attacker=engine.info(state['party'][state['active']])))

def expire(game,db):
    from dittobuffer.gameplay import gym_battle as engine
    if game.raids['participation_limit']>1:
        from dittobuffer.gameplay.cooperative import expire as shared_expire
        shared_expire(game,db)
        return
    for b in db.execute('SELECT * FROM raid_battles WHERE settled=0 AND end_ms<=?',(now_ms(),)).fetchall():
        state=json.loads(b['state'])
        if state['status']==1:engine.advance(game,state,state['end'])
        if state['status']==3:state['status']=4;state['results']=''
        finish_battle(game,db,b,state)

def probabilities(game):
    return p.CaptureProbabilityProto(reticle_difficulty_scale=1,pokeball_type=[5],capture_probability=[game.config['catch_probability']])

def sync_inventory(game,db,player,body):
    """Native all-fainted handoff: health was committed during combat polls."""
    p.SyncBattleInventoryProto.FromString(body)
    for m in db.execute('SELECT * FROM raid_members WHERE player_id=? AND reward IS NULL AND left_lobby=0',(player,)).fetchall():
        b=current_battle(db,m)
        if not b:continue
        state=json.loads(b['state'])
        if state['status']==3 and not state.get('surrendered') and now_ms()<state['end']:
            row=db.execute('SELECT * FROM raids WHERE seed=?',(m['seed'],)).fetchone()
            db.execute('UPDATE raid_battles SET acknowledged=1 WHERE id=?',(b['id'],))
            open_lobby(game,db,row,m)
    return p.SyncBattleInventoryOutProto(result=1)

def apply_mega_reward(game,pokemon,variant,boss,finish_ms):
    """Register a Mega raid catch without turning the caught Pokemon into a Mega.

    The raid boss is temporarily Mega-evolved while it defends the gym, but the
    reward encounter is the ordinary species.  Keeping current_temp_evolution
    here made caught Megas remain evolved for the whole encounter window and
    left stale negative timers in storage.
    """
    if boss.get('tier')!=6:return pokemon
    evo=boss.get('mega_evolution')
    if not evo:raise ValueError('Mega raid is missing its temporary evolution')
    pokemon.has_mega_evolved=True
    if evo not in pokemon.mega_evolved_forms:pokemon.mega_evolved_forms.append(evo)
    return pokemon


def bonus_encounter(game,db,row,m):
    spawn=db.execute('SELECT s.*,a.data pokemon_data FROM spawns s JOIN spawn_attributes a ON a.spawn_id=s.id WHERE s.id=?',(m['spawn_id'],)).fetchone()
    return p.RaidEncounterProto(pokemon=game.pokemon(spawn),encounter_id=m['spawn_id'],spawnpoint_id=spawn['spawnpoint'],
        capture_probabilities=probabilities(game),throws_remaining=m['balls'],raid_level=json.loads(row['definition'])['tier'],fort_id=row['fort_id'],raid_ball=5,appraisal_star=3)

def complete(game,db,player,body):
    from dittobuffer.gameplay import gym_battle as engine
    req=p.CompleteRaidBattleProto.FromString(body)
    if game.raids['participation_limit']>1:
        from dittobuffer.gameplay.cooperative import individual_id,advance_for
        req.battle_id=individual_id(db,req.battle_id,player)
        advance_for(game,db,req.battle_id)
    b=db.execute('SELECT b.* FROM raid_battles b JOIN raids r ON r.seed=b.seed WHERE b.id=? AND b.player_id=? AND r.fort_id=?',(req.battle_id,player,req.gym_id)).fetchone()
    if not b:return p.CompleteRaidBattleOutProto(result=2)
    state=json.loads(b['state']);m=member(db,b['seed'],player)
    if m['reward'] and state['status']==2 and m['battle_id']==b['id']:return saved_reward(m)
    if state['status']==1:return p.CompleteRaidBattleOutProto(result=3)
    if state['status']!=2:
        db.execute('UPDATE raid_battles SET acknowledged=1 WHERE id=?',(b['id'],))
        return p.CompleteRaidBattleOutProto(result=1,is_victory=False,trainer_battle_state=3 if state['status']==3 and not state.get('surrendered') and now_ms()<state['end'] else 6,battle_results=p.BattleResultsProto.FromString(bytes.fromhex(state['results'])))
    if m['reward'] or m['battle_id']!=b['id']:return p.CompleteRaidBattleOutProto(result=2)
    row=db.execute('SELECT * FROM raids WHERE seed=?',(b['seed'],)).fetchone();boss=json.loads(row['definition']);fort=gyms.gym(db,row['fort_id']);stamp=now_ms()
    cfg=rules(game,row);balls=boss.get('premier_balls',cfg['premier_balls'])
    from dittobuffer.gameplay.cooperative import room_for
    room=room_for(db,b['id']);friend_tier=friend_balls=0
    if room:
        from dittobuffer.gameplay.friends import enabled as social_enabled,raid_bonus,interact
        peers=[r[0] for r in db.execute('SELECT m.player_id FROM raid_room_members m JOIN raid_battles b ON b.id=m.attempt_id WHERE m.room_id=? AND m.damage>0 AND json_extract(b.state,\'$.status\')=2',(room['id'],))]
        friend_tier,friend_balls=state.get('friendship_reward_bonus',raid_bonus(game,db,player,peers)[:2])
        balls+=friend_balls
        if social_enabled(game):
            for peer in peers:
                if peer!=player:interact(game,db,player,peer,'raid','raid:'+str(row['seed'])+':'+':'.join(sorted((player,peer))))
    from dittobuffer.gameplay.raid_rewards import participation as ball_participation,total_balls
    allocation=ball_participation(game,db,b,state,balls-friend_balls,friend_tier,friend_balls)
    balls=total_balls(allocation)
    spawn_id=800000000000000000+row['seed'] if game.raids['participation_limit']==1 else 900000000000000000+int.from_bytes(hashlib.sha256(f'bonus:{row["seed"]}:{player}'.encode()).digest()[:7],'big')
    point='raid:'+str(row['seed'])+':'+player[:12]
    from dittobuffer.gameplay.species import choose_gender
    display_rng=random.Random(f'display:{row["seed"]}:{player}')
    from dittobuffer.gameplay import liveops
    variant=pool_rules(game,boss)
    rate=liveops.shiny_rate(game,boss.get('shiny_rate',0),boss['species'],variant['form'],source='raids')
    shiny=int.from_bytes(hashlib.sha256(f'{row["seed"]}:{player}'.encode()).digest()[:4],'big')/2**32<rate
    shiny=shiny and (boss['species'],variant['form']) in game.shiny_eligible
    # The catch IV floor is an explicit local rule: every IV is rolled between
    # the configured floor and 15, deterministically per raid and player.
    iv_floor=max(0,min(15,int(boss.get('iv_floor',10))))
    iv_rng=random.Random(f'ivs:{row["seed"]}:{player}')
    reward_ivs=[iv_rng.randint(iv_floor,15) for _ in range(3)]
    pokemon=make_pokemon(p,variant,boss['species'],boss.get('level',20),reward_ivs,
        shiny,boss.get('gender') or choose_gender(game,boss['species'],display_rng),costume=boss.get('costume',0))
    if boss.get('shadow'):pokemon.pokemon_display.alignment=1
    from dittobuffer.gameplay.pokemon_sizes import assign as assign_size
    assign_size(game,pokemon,boss,display_rng)
    from dittobuffer.gameplay.backgrounds import assign as assign_background
    assign_background(game,pokemon,random.Random(f'background:{row["seed"]}:{player}'),'raid',boss.get('background',0))
    pokemon.creation_time_ms=stamp
    apply_mega_reward(game,pokemon,variant,boss,stamp+cfg['encounter_seconds']*1000)
    pokemon.origin_detail.raid_detail.raid_level=boss['tier'];pokemon.origin_detail.raid_detail.lobby_size=state.get('room_participants',1)
    db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',(spawn_id,point,boss['species'],fort['lat'],fort['lng'],stamp+cfg['encounter_seconds']*1000))
    db.execute('INSERT INTO spawn_attributes VALUES(?,?,?)',(spawn_id,pokemon.SerializeToString(),boss.get('level',20)))
    db.execute('INSERT INTO encounters(player_id,spawn_id) VALUES(?,?)',(player,spawn_id))
    db.execute('UPDATE raid_members SET spawn_id=?,balls=? WHERE seed=? AND player_id=?',(spawn_id,balls,row['seed'],player))
    result=p.BattleResultsProto(battle_duration_ms=max(0,state['clock']-state['start']))
    result.attackers.add().CopyFrom(participant(game,db,player,state));result.player_xp_awarded.append(boss.get('xp',0))
    result.player_results.add(attacker=participant(game,db,player,state),player_xp_awarded=boss.get('xp',0))
    from dittobuffer.gameplay.cooperative import team_contribution
    contribution=team_contribution(db,b['id'])
    if contribution is None:contribution={team:int(gyms.team(db,player)['team']==team) for team in (1,2,3)}
    allocation.blue_percentage=contribution[1];allocation.red_percentage=contribution[2];allocation.yellow_percentage=contribution[3]
    result.participation.add().CopyFrom(allocation)
    loot=result.raid_item_rewards.add()
    for item,count in boss.get('items',{}).items():
        db.execute('INSERT INTO items VALUES(?,?,?) ON CONFLICT(player_id,item) DO UPDATE SET count=count+excluded.count',(player,int(item),count))
        loot.loot_item.add(item=int(item),count=count)
    if boss.get('dust'):loot.loot_item.add(stardust=True,count=boss['dust'])
    if boss['tier']==6:
        from dittobuffer.gameplay.mega import grant
        elapsed=max(0,state['clock']-state['start'])
        expected=next(r['amount'] for r in boss['mega_energy_rewards'] if elapsed<=r['max_seconds']*1000)
        amount=grant(game,db,player,boss['species'],boss['mega_evolution'],expected,'raid:'+str(row['seed']))
        result.participation[-1].speed_completion_mega_resource=amount
        result.participation[-1].mega_resource_capped=amount<expected
        reward=loot.loot_item.add(mega_energy_pokemon_id=boss['species'],count=amount)
        reward.temp_evo_pokemon_branch.CopyFrom(p.TempEvoPokemonBranch(pokedex_id=boss['species'],temp_evo_id=boss['mega_evolution']))
    db.execute('UPDATE players SET xp=xp+?,stardust=stardust+? WHERE id=?',(boss.get('xp',0),boss.get('dust',0),player));game.touch(db,player)
    result.post_raid_encounter.add().CopyFrom(bonus_encounter(game,db,row,member(db,row['seed'],player)))
    result.gym_badge.add().CopyFrom(gyms.badge(game,db,player,fort,1000))
    from dittobuffer.gameplay.research import progress
    progress(game,db,player,'raid')
    from dittobuffer.gameplay.raid_rewards import project as project_summary
    project_summary(game,db,b,state,result)
    out=p.CompleteRaidBattleOutProto(result=1,is_victory=True,trainer_battle_state=8,battle_results=result)
    db.execute('UPDATE raid_members SET reward=? WHERE seed=? AND player_id=?',(out.SerializeToString(),row['seed'],player))
    from dittobuffer.gameplay.trainer import event
    event(game,db,player,'raid:'+str(row['seed']),'raid',dict(seed=row['seed'],species=pokemon.pokemon_id,tier=boss['tier'],xp=boss.get('xp',0),dust=boss.get('dust',0),items=boss.get('items',{})))
    return out

def saved_reward(m):
    out=p.CompleteRaidBattleOutProto.FromString(m['reward'])
    for encounter in out.battle_results.post_raid_encounter:encounter.throws_remaining=m['balls']
    for result in out.battle_results.player_results:
        if result.HasField('post_raid_encounter'):result.post_raid_encounter.throws_remaining=m['balls']
    return out

def encounter_details(game,db,player,body):
    req=p.GetRaidEncounterDetailsProto.FromString(body)
    m=db.execute('SELECT m.* FROM raid_members m JOIN raids r ON r.seed=m.seed WHERE m.seed=? AND m.player_id=? AND r.fort_id=?',(req.raid_seed,player,req.gym_id)).fetchone()
    if not m or not m['reward']:return p.GetRaidEncounterDetailsOutProto(result=4)
    current=state_of(db,m)
    if current==8:
        # Opening the native bonus encounter is a sighting even if every ball
        # later misses. Use the same source key as catching, so retries and the
        # eventual acquisition update one observation rather than duplicating it.
        from dittobuffer.gameplay.pokedex import record
        born=db.execute('SELECT data FROM spawn_attributes WHERE spawn_id=?',(m['spawn_id'],)).fetchone()
        if born:record(game,db,player,'spawn:'+str(m['spawn_id']),p.PokemonProto.FromString(born['data']))
    return p.GetRaidEncounterDetailsOutProto(result=3 if current==9 else 1,trainer_battle_state=current,throws_remaining=m['balls'],raid_ball=5,capture_probabilities=probabilities(game),appraisal_star=3)
