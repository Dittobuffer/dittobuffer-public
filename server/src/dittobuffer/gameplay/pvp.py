"""Opt-in authenticated Trainer battles, native leagues and private AI policy."""
import hashlib
import json
import math
from contextlib import closing
from types import MethodType
from dittobuffer.gameplay.game import ROOT, p, now_ms, RpcError
from dittobuffer.gameplay import pvp_engine as engine, pvp_rules as rules

SCHEMA = '''
CREATE TABLE IF NOT EXISTS pvp_battles(id TEXT PRIMARY KEY,kind TEXT NOT NULL,league TEXT NOT NULL,state TEXT NOT NULL,end_ms INTEGER NOT NULL,finished INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS pvp_members(battle_id TEXT NOT NULL REFERENCES pvp_battles(id),player_id TEXT NOT NULL REFERENCES players(id),side INTEGER NOT NULL,epoch INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(battle_id,player_id),UNIQUE(battle_id,side));
CREATE TABLE IF NOT EXISTS pvp_actions(battle_id TEXT NOT NULL,player_id TEXT NOT NULL,epoch INTEGER NOT NULL,counter INTEGER NOT NULL,digest TEXT NOT NULL,response BLOB NOT NULL,PRIMARY KEY(battle_id,player_id,epoch,counter));
CREATE TABLE IF NOT EXISTS pvp_results(battle_id TEXT NOT NULL,player_id TEXT NOT NULL,finish INTEGER NOT NULL,claimed_ms INTEGER NOT NULL,loot BLOB NOT NULL,PRIMARY KEY(battle_id,player_id));
CREATE TABLE IF NOT EXISTS pvp_profiles(player_id TEXT NOT NULL REFERENCES players(id),season INTEGER NOT NULL,rank INTEGER NOT NULL DEFAULT 1,rating REAL NOT NULL DEFAULT 1000,battles INTEGER NOT NULL DEFAULT 0,wins INTEGER NOT NULL DEFAULT 0,streak INTEGER NOT NULL DEFAULT 0,longest INTEGER NOT NULL DEFAULT 0,dust INTEGER NOT NULL DEFAULT 0,set_id INTEGER NOT NULL DEFAULT 1,active INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(player_id,season));
CREATE TABLE IF NOT EXISTS pvp_queue(id TEXT PRIMARY KEY,player_id TEXT NOT NULL REFERENCES players(id),league TEXT NOT NULL,party TEXT NOT NULL,created_ms INTEGER NOT NULL,expires_ms INTEGER NOT NULL,rating REAL NOT NULL,state TEXT NOT NULL,battle_id TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS pvp_one_queue ON pvp_queue(player_id) WHERE state='queued';
CREATE TABLE IF NOT EXISTS pvp_challenges(id TEXT PRIMARY KEY,challenger TEXT NOT NULL REFERENCES players(id),opponent TEXT REFERENCES players(id),league TEXT NOT NULL DEFAULT '',state INTEGER NOT NULL DEFAULT 1,created_ms INTEGER NOT NULL,expires_ms INTEGER NOT NULL,party0 TEXT NOT NULL DEFAULT '[]',party1 TEXT NOT NULL DEFAULT '[]',battle_id TEXT);
CREATE TABLE IF NOT EXISTS pvp_set_battles(player_id TEXT NOT NULL,season INTEGER NOT NULL,set_id INTEGER NOT NULL,battle_id TEXT NOT NULL,finish INTEGER NOT NULL,PRIMARY KEY(player_id,battle_id));
CREATE TABLE IF NOT EXISTS pvp_claims(player_id TEXT NOT NULL,season INTEGER NOT NULL,set_id INTEGER NOT NULL,slot INTEGER NOT NULL,loot BLOB NOT NULL,claimed_ms INTEGER NOT NULL,PRIMARY KEY(player_id,season,set_id,slot));
CREATE TABLE IF NOT EXISTS pvp_set_starts(player_id TEXT NOT NULL,season INTEGER NOT NULL,set_id INTEGER NOT NULL,rank INTEGER NOT NULL,battles INTEGER NOT NULL,wins INTEGER NOT NULL,rating REAL NOT NULL,PRIMARY KEY(player_id,season,set_id));
CREATE TABLE IF NOT EXISTS pvp_rematch_sessions(previous_id TEXT PRIMARY KEY,combat_id TEXT NOT NULL UNIQUE,created_ms INTEGER NOT NULL,expires_ms INTEGER NOT NULL,party0 TEXT NOT NULL DEFAULT '[]',party1 TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS pvp_connections(battle_id TEXT NOT NULL,player_id TEXT NOT NULL,join_ms INTEGER NOT NULL,PRIMARY KEY(battle_id,player_id));
CREATE TABLE IF NOT EXISTS pvp_set_tracks(player_id TEXT NOT NULL,season INTEGER NOT NULL,set_id INTEGER NOT NULL,track INTEGER NOT NULL,PRIMARY KEY(player_id,season,set_id));
CREATE TABLE IF NOT EXISTS pvp_reward_encounters(player_id TEXT NOT NULL,season INTEGER NOT NULL,set_id INTEGER NOT NULL,slot INTEGER NOT NULL,spawn_id INTEGER NOT NULL UNIQUE,PRIMARY KEY(player_id,season,set_id,slot));
CREATE TABLE IF NOT EXISTS pvp_rank_starts(player_id TEXT NOT NULL,season INTEGER NOT NULL,rank INTEGER NOT NULL,battles INTEGER NOT NULL,wins INTEGER NOT NULL,rating REAL NOT NULL,PRIMARY KEY(player_id,season,rank));
CREATE TABLE IF NOT EXISTS pvp_queue_seasons(queue_id TEXT PRIMARY KEY,season INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS pvp_season_claims(player_id TEXT NOT NULL,season INTEGER NOT NULL,response BLOB NOT NULL,PRIMARY KEY(player_id,season));
CREATE TABLE IF NOT EXISTS pvp_invitation_logins(player_id TEXT NOT NULL,session_digest TEXT NOT NULL,created_ms INTEGER NOT NULL,PRIMARY KEY(player_id,session_digest));
'''

DEFAULTS = dict(enabled=False, reference='', season=1, season_start_ms=1767225600000,
    season_end_ms=1893456000000, minimum_level=10, set_size=5, daily_sets=5,
    friend_rewards_per_day=3, friend_stardust=100, npc_stardust=300, set_stardust=500,
    queue_seconds=120, ai_backfill_enabled=True, ai_backfill_seconds=15, ai_difficulty='normal', ai_max_level=40, rating_window=100,
    npc_reward_claim_seconds=600,
    rematch_seconds=300,
    premium_enabled=False, premium_pass_item=1402, premium_reward_items=None,
    rank_requirements='cumulative',
    template_revision=0, season_stardust=0,
    rating_expansion_per_second=5, elo_k=24,
    ranks=[dict(battles=0,wins=0),dict(battles=5,wins=0),dict(battles=10,wins=3),dict(battles=15,wins=6)],
    leagues={'COMBAT_LEAGUE_DEFAULT_GREAT':dict(cap=1500,unlock_cp=0),
             'COMBAT_LEAGUE_DEFAULT_ULTRA':dict(cap=2500,unlock_cp=1500),
             'COMBAT_LEAGUE_DEFAULT_MASTER':dict(cap=0,unlock_cp=2500)},
    ai={'beginner':dict(score=.35,shield_probability=.1,charge_probability=.3,switch=False),
        'normal':dict(score=.7,shield_probability=.65,charge_probability=.65,switch=False),
        'advanced':dict(score=.9,shield_probability=.85,charge_probability=.85,switch=True),
        'expert':dict(score=1,shield_probability=1,charge_probability=1,switch=True)},
    ai_species=[3,6,9], reward_items={'1':dict(item=1,count=5),'2':dict(item=701,count=1),
                                   '3':dict(stardust=1000),'4':dict(item=1,count=10),'5':dict(stardust=2000)})


def config(value):
    cfg = DEFAULTS | value.get('pvp', {})
    if not cfg['enabled']:
        return cfg
    for key, low, high in [('season',1,100000),('minimum_level',1,50),('set_size',1,5),
        ('daily_sets',1,100),('queue_seconds',10,600),('ai_backfill_seconds',0,600),('ai_max_level',1,50),
        ('friend_rewards_per_day',0,100),('friend_stardust',0,100000),('npc_stardust',0,100000),('set_stardust',0,100000),('npc_reward_claim_seconds',30,3600),('rematch_seconds',10,600),('template_revision',0,9999),('season_stardust',0,1000000)]:
        if type(cfg[key]) is not int or not low <= cfg[key] <= high:
            raise ValueError('Invalid private PvP rule: '+key)
    if cfg['season_end_ms'] <= cfg['season_start_ms'] or cfg['ai_difficulty'] not in cfg['ai']:
        raise ValueError('Invalid private PvP season/AI')
    if type(cfg['premium_enabled']) is not bool or cfg['premium_pass_item']!=1402:
        raise ValueError('Premium battles use only locally owned Premium Battle Passes')
    if cfg['rank_requirements'] not in ('additional','cumulative') or not cfg['ranks'] or cfg['ranks'][0]!={'battles':0,'wins':0}:
        raise ValueError('Invalid private rank requirement mode')
    if any(type(v[k]) is not int or v[k]<0 for v in cfg['ranks'] for k in ('battles','wins')):
        raise ValueError('Invalid private rank requirement')
    for table in (cfg['reward_items'],cfg['premium_reward_items'] or cfg['reward_items']):
        for slot in range(1,cfg['set_size']+1):
            definition=table[str(slot)]
            if sum(bool(definition.get(k)) for k in ('item','stardust','species'))!=1:
                raise ValueError('Each PvP reward requires one supported type')
            if definition.get('item') and (definition['item'] not in p.Item.values() or type(definition.get('count')) is not int or definition['count']<=0):
                raise ValueError('Invalid PvP item reward')
            if definition.get('species') and (definition['species'] not in p.HoloPokemonId.values() or not 1<=definition.get('level',20)<=50):
                raise ValueError('Invalid PvP encounter reward')
    for league, definition in cfg['leagues'].items():
        if not league or type(definition['cap']) is not int or definition['cap'] < 0:
            raise ValueError('Invalid PvP league')
    return cfg


def reference(cfg):
    path = (ROOT/cfg['reference']).resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError('Existing authorized project-local combat reference required')
    data = json.loads(path.read_text())['templates']
    values={key:p.GameMasterClientTemplateProto.FromString(bytes.fromhex(data[key]))
            for key in ('COMBAT_SETTINGS','COMBAT_STAT_STAGE_SETTINGS','BATTLE_PARTY_SETTINGS')}
    # The snapshot was captured with a three-slot gym/raid party. PvP leagues
    # are three, but the raid lobby is six; never let this copy leak to gyms.
    values['BATTLE_PARTY_SETTINGS'].battle_party_settings.gym_and_raid_battle_party_size=6
    return values


def practice_templates(cfg):
    data=json.loads((ROOT/cfg['reference']).read_text())['templates']
    return [p.GameMasterClientTemplateProto.FromString(bytes.fromhex(raw)) for key,raw in data.items()
            if key.startswith(('TRAINER_BLANCHE_','TRAINER_CANDELA_','TRAINER_SPARK_','TRAINER_PERSONALITY_'))]


def templates(value):
    cfg = config(value)
    if not cfg['enabled']:
        return []
    # Identical shared settings are already provided when Rocket is enabled.
    values = [] if value.get('rocket',{}).get('enabled') else list(reference(cfg).values())
    hub=p.GameMasterClientTemplateProto(template_id='BATTLE_HUB_ORDER_SETTINGS')
    hub.battle_hub_order_settings.section.add(main_section=1,subsection=[2,3] if cfg['premium_enabled'] else [2])
    hub.battle_hub_order_settings.section_group.add(section=[1])
    practice=practice_templates(cfg)
    source_leagues=json.loads((ROOT/cfg['reference']).read_text())['templates']
    if practice:hub.battle_hub_order_settings.section_group.add(section=[5])
    values.append(hub)
    for ident, definition in cfg['leagues'].items():
        for suffix in ('', '_NPC'):
            template = p.GameMasterClientTemplateProto(template_id=ident+suffix)
            league = template.combat_league
            league.CopyFrom(p.CombatLeagueProto(title={'COMBAT_LEAGUE_DEFAULT_GREAT':'combat_great_league',
                'COMBAT_LEAGUE_DEFAULT_ULTRA':'combat_ultra_league','COMBAT_LEAGUE_DEFAULT_MASTER':'combat_master_league'}.get(ident,ident),
                enabled=True,pokemon_count=3,minigame_defense_chance_limit=2,
                battle_party_combat_league_template_id=ident+suffix,league_type=1))
            # Keep the original league-specific art and medal identity. Leaving
            # badge_type unset makes native widgets choose their default badge.
            raw=source_leagues.get(ident+suffix,source_leagues.get(ident))
            if raw:
                original=p.GameMasterClientTemplateProto.FromString(bytes.fromhex(raw)).combat_league
                league.icon_url=original.icon_url
                league.badge_type=original.badge_type
            if definition['cap']:
                condition=league.pokemon_condition.add(type=1)
                condition.with_pokemon_cp_limit.max_cp=definition['cap']
            if definition.get('unlock_cp'):
                condition=league.unlock_condition.add(type=1,min_pokemon_count=3)
                condition.with_pokemon_cp_limit.min_cp=definition['unlock_cp']+1
                condition.with_pokemon_cp_limit.max_cp=50000
            values.append(template)
    for difficulty, policy in cfg['ai'].items():
        ident='LIBRE_AI_'+difficulty.upper()
        values.append(p.GameMasterClientTemplateProto(template_id=ident,
            combat_npc_personality=p.CombatNpcPersonalityProto(personality_name='Libre AI ('+difficulty+')',
                super_effective_chance=1,special_chance=policy['charge_probability'],
                defensive_minimum_score=0,defensive_maximum_score=1,
                offensive_minimum_score=policy['score'],offensive_maximum_score=policy['score'])))
    values.extend(practice)
    item=p.GameMasterClientTemplateProto(template_id='VS_SEEKER_CLIENT_SETTINGS')
    item.vs_seeker_client_settings.allowed_vs_seeker_league_template_id.extend(cfg['leagues'])
    values.append(item)
    values.append(p.GameMasterClientTemplateProto(template_id='COMBAT_LEAGUE_SETTINGS',
        combat_league_settings=p.CombatLeagueSettingsProto(combat_league_template_id=list(cfg['leagues']))))
    values.append(p.GameMasterClientTemplateProto(template_id='COMBAT_COMPETITIVE_SEASON_SETTINGS',
        combat_competitive_season_settings=p.CombatCompetitiveSeasonSettingsProto(
            season_end_time_timestamp=[cfg['season_end_ms']],player_facing_season_number=cfg['season'])))
    item=p.GameMasterClientTemplateProto(template_id='VS_SEEKER_SCHEDULE_SETTINGS')
    item.vs_seeker_schedule_settings.enabled_combat_hub_main=True
    item.vs_seeker_schedule_settings.enabled_combat_league_view=True
    season=item.vs_seeker_schedule_settings.season_schedules.add(season_title='Libre League',description_key='Private server season')
    season.vs_seeker_schedules.add(start_time_ms=cfg['season_start_ms'],end_time_ms=cfg['season_end_ms'],vs_seeker_league_template_id=list(cfg['leagues']))
    values.append(item)
    item=p.GameMasterClientTemplateProto(template_id='COMBAT_RANKING_SETTINGS')
    item.combat_ranking_proto_settings.season_number=cfg['season']
    item.combat_ranking_proto_settings.min_rank_to_display_rating=1
    item.combat_ranking_proto_settings.required_for_rewards.CopyFrom(p.RankLevelProto(
        rank_level=1,additional_total_battles_required=cfg['set_size']))
    for i, rank in enumerate(cfg['ranks'],1):
        item.combat_ranking_proto_settings.rank_level.add(rank_level=i,additional_total_battles_required=rank['battles'],additional_wins_required=rank['wins'])
        for track in range(2 if cfg['premium_enabled'] else 1):
            table=(cfg['premium_reward_items'] or cfg['reward_items']) if track else cfg['reward_items']
            rewards=p.GameMasterClientTemplateProto(template_id='VS_SEEKER_LOOT_'+('PREMIUM_' if track else 'FREE_')+str(i))
            rewards.vs_seeker_loot_proto.rank_level=i;rewards.vs_seeker_loot_proto.reward_track=track
            for slot in range(1,cfg['set_size']+1):
                definition=table[str(slot)];reward=rewards.vs_seeker_loot_proto.reward.add()
                if definition.get('species'):reward.pokemon_reward=True;continue
                loot=reward.item;loot.count=definition.get('count',definition.get('stardust',0))
                if definition.get('item'):loot.item=definition['item']
                elif definition.get('stardust'):loot.stardust=True
            values.append(rewards)
    for track in range(2 if cfg['premium_enabled'] else 1):
        pool=p.GameMasterClientTemplateProto(template_id='VS_SEEKER_POKEMON_REWARDS_'+str(track))
        pool.vs_seeker_pokemon_rewards.reward_track=track
        table=(cfg['premium_reward_items'] or cfg['reward_items']) if track else cfg['reward_items']
        for species in sorted({v['species'] for v in table.values() if v.get('species')}):
            entry=pool.vs_seeker_pokemon_rewards.available_pokemon.add(unlocked_at_rank=1,weight=1)
            entry.pokemon.pokemon_id=species
        if pool.vs_seeker_pokemon_rewards.available_pokemon:values.append(pool)
    values.append(item)
    return values


def install(game):
    game.pvp = config(game.config)
    if not game.pvp['enabled']:
        return
    game.pvp_settings = reference(game.pvp)['COMBAT_SETTINGS'].combat_settings
    if game.pvp_settings.turn_duration_seconds != .5:
        raise ValueError('This native protocol adapter requires reviewed 500 ms turns')
    with closing(game.db.connect()) as db:
        db.executescript(SCHEMA)
    for name in ('open_combat_session','update_combat','quit_combat','get_combat_player_profile','get_vs_seeker_status'):
        original = getattr(game,'rpc_'+name)
        implementation = globals()[name]
        def handler(self,db,player,body,original=original,implementation=implementation,name=name):
            if name in ('open_combat_session','update_combat','quit_combat'):
                cls={'open_combat_session':p.OpenCombatSessionProto,'update_combat':p.UpdateCombatProto,'quit_combat':p.QuitCombatProto}[name]
                if not cls.FromString(body).combat_id.startswith('pvp-'):
                    return original(db,player,body)
            return implementation(self,db,player,body)
        setattr(game,'rpc_'+name,MethodType(handler,game))
    for name in ('open_npc_combat_session','get_combat_results','get_npc_combat_rewards','get_eligible_combat_leagues','activate_vs_seeker','combat_sync_server_offset','upload_combat_client_log'):
        setattr(game,'rpc_'+name,MethodType(globals()[name],game))
    from dittobuffer.gameplay.pvp_matchmaking import install as install_matchmaking
    install_matchmaking(game)
    from dittobuffer.gameplay.pvp_rewards import install as install_rewards
    install_rewards(game)
    from dittobuffer.gameplay.pvp_challenges import install as install_challenges
    install_challenges(game)
    from dittobuffer.gameplay.pvp_encounters import install as install_encounters
    install_encounters(game)


def profile(game, db, player, season=None):
    season=game.pvp['season'] if season is None else season
    db.execute('INSERT OR IGNORE INTO pvp_profiles(player_id,season) VALUES(?,?)',(player,season))
    value=db.execute('SELECT * FROM pvp_profiles WHERE player_id=? AND season=?',(player,season)).fetchone()
    # Legacy profiles begin the new per-rank ledger at their observed totals;
    # no historic rank-attainment timestamp or rating is invented.
    db.execute('INSERT OR IGNORE INTO pvp_rank_starts VALUES(?,?,?,?,?,?)',
        (player,season,value['rank'],value['battles'],value['wins'],value['rating']))
    return value


def wire_player(player):
    # Match GetPlayer.obfuscated_player_id. Canonical authenticated DB identities
    # stay full length; native challenge role selection compares this wire ID.
    return player[:20]


def bot_identity(bot):
    # Native player/scene registries require the same hex identifier format as
    # authenticated profiles. The visible name and authoritative bot metadata
    # identify the AI; this never creates a human account or participant.
    return hashlib.sha256(('libre-ai:'+bot['id']).encode()).hexdigest()


def canonical_player(db, ident):
    """Resolve the native public ID only when it names exactly one account."""
    rows=db.execute('SELECT id FROM players WHERE id=? OR substr(id,1,20)=?',(ident,ident)).fetchall()
    return rows[0][0] if len(rows)==1 else None


def combat_sync_server_offset(game,db,player,body):
    p.CombatSyncServerOffsetProto.FromString(body)
    return p.CombatSyncServerOffsetOutProto(server_time_ms=now_ms(),result=1)


def upload_combat_client_log(game,db,player,body):
    p.UploadCombatClientLogProto.FromString(body)
    return p.UploadCombatClientLogOutProto(result=1)


def eligible(game, db, player):
    from dittobuffer.gameplay.progression import bounds
    if bounds(game.config,game.player(db,player)['xp'])[0] < game.pvp['minimum_level']:
        return []
    from dittobuffer.core.management import owned
    mons=[owned(db,player,row[0]) for row in db.execute('SELECT id FROM pokemon WHERE player_id=?',(player,))]
    return [key for key,definition in game.pvp['leagues'].items()
            if not definition.get('unlock_cp') or sum(not v.is_egg and v.cp > definition['unlock_cp'] for v in mons)>=3]


def locked(game, db, player, pokemon):
    """True while a Pokémon sits in the party of an unfinished Trainer battle."""
    if not game.config.get('pvp',{}).get('enabled'):
        return False
    for row in db.execute('SELECT b.state FROM pvp_battles b JOIN pvp_members m ON m.battle_id=b.id '
                          'WHERE m.player_id=? AND b.finished=0',(player,)):
        state=json.loads(row['state'])
        for member in state['members']:
            for value in member['party']:
                if value.get('pokemon') and p.PokemonProto.FromString(
                        bytes.fromhex(value['pokemon'])).id==pokemon:
                    return True
    return False


def party(game, db, player, ids, league):
    from dittobuffer.core.management import owned
    from dittobuffer.gameplay.gyms import battle_busy
    from dittobuffer.gameplay.trading import locked
    if league.endswith('_NPC'):
        league=league[:-4]
    if league not in eligible(game,db,player) or len(ids)!=3 or len(set(ids))!=3:
        return None
    values=[owned(db,player,ident) for ident in ids]
    cfg=game.pvp['leagues'][league]
    if any(not v or v.is_egg or not v.stamina or v.deployed_fort_id or v.deployed_station_id or
           v.is_fusion or v.is_component or v.pokemon_display.current_temp_evolution or
           (cfg['cap'] and v.cp>cfg['cap']) or v.pokemon_id in cfg.get('banned_species',[]) or
           v.pokemon_display.form in cfg.get('banned_forms',[]) or
           v.move1 not in rules.MOVES or v.move2 not in rules.MOVES or
           rules.MOVES[v.move1]['energy_delta'] <= 0 or rules.MOVES[v.move2]['energy_delta'] >= 0
           for v in values):
        return None
    if len({v.pokemon_id for v in values})!=3 or battle_busy(db,player,ids) or any(locked(game,db,i) for i in ids):
        return None
    # Expired sessions are advanced before checking the shared PvP party lock.
    for battle in db.execute('SELECT b.* FROM pvp_battles b JOIN pvp_members m ON m.battle_id=b.id WHERE m.player_id=? AND b.finished=0',(player,)).fetchall():
        state=json.loads(battle['state']);engine.advance(game,state);engine.save(db,state)
        from dittobuffer.gameplay.pvp_rewards import settle
        settle(game,db,state)
        if state['phase'] not in (6,7,8):
            return None
    return values


def bot_party(game, league, species=None):
    from dittobuffer.gameplay.species import make_pokemon,rules_for
    cap=game.pvp['leagues'][league.removesuffix('_NPC')]['cap']
    result=[]
    for index, value in enumerate(species or game.pvp['ai_species']):
        ident=value.pokemon_type if isinstance(value,p.NpcPokemonProto) else value
        appearance=value.pokemon_display if isinstance(value,p.NpcPokemonProto) else None
        probe=p.PokemonProto(pokemon_id=ident)
        if appearance:probe.pokemon_display.CopyFrom(appearance)
        definition=rules_for(game,probe)
        level=game.pvp['ai_max_level']
        mon=make_pokemon(p,definition,ident,level,[12]*3)
        while cap and mon.cp>cap and level>1:
            level-=1;mon=make_pokemon(p,definition,ident,level,[12]*3)
        if appearance:mon.pokemon_display.CopyFrom(appearance)
        mon.id=2**60+index
        if mon.move1 not in rules.MOVES or mon.move2 not in rules.MOVES:
            raise ValueError('AI party contains a move absent from reviewed PvP catalog')
        result.append(mon)
    return result


def get_combat_player_profile(game,db,player,body):
    req=p.GetCombatPlayerProfileProto.FromString(body)
    target=canonical_player(db,req.player_id) if req.player_id else player
    if not target or not game.player(db,target):
        return p.GetCombatPlayerProfileOutProto(result=2)
    if target!=player:
        from dittobuffer.gameplay.friends import pair
        if not pair(db,player,target):return p.GetCombatPlayerProfileOutProto(result=3)
    from dittobuffer.gameplay.gyms import public
    leagues=eligible(game,db,target)
    out=p.GetCombatPlayerProfileOutProto(result=1,calling_player_eligible_leagues=eligible(game,db,player))
    public_profile=public(game,db,target);public_profile.player_id=wire_player(target)
    out.profile.CopyFrom(p.CombatPlayerProfileProto(player_id=wire_player(target),player_nia_id=target,
        public_profile=public_profile,combat_league_template_id=leagues))
    from dittobuffer.core.preferences import load_group
    out.profile.combat_player_preferences.CopyFrom(load_group(db,target,'combat'))
    return out


def get_eligible_combat_leagues(game,db,player,body):
    req=p.GetEligibleCombatLeaguesProto.FromString(body)
    if len(req.player_ids)>200:return p.GetEligibleCombatLeaguesOutProto(result=3)
    out=p.GetEligibleCombatLeaguesOutProto(result=1)
    out.player_eligible_leagues.CopyFrom(p.PlayerEligibleCombatLeaguesProto(player_id=wire_player(player),eligible_combat_leagues=eligible(game,db,player)))
    from dittobuffer.core.preferences import load_group
    out.player_eligible_leagues.combat_player_preferences.CopyFrom(load_group(db,player,'combat'))
    from dittobuffer.gameplay.friends import pair
    for target in req.player_ids:
        other=canonical_player(db,target)
        if other==player:continue
        if other and pair(db,player,other):
            entry=out.other_players_eligible_leagues.add(player_id=wire_player(other),eligible_combat_leagues=eligible(game,db,other))
            entry.combat_player_preferences.CopyFrom(load_group(db,other,'combat'))
        else:out.skipped_player_ids.append(target)
    return out


def open_npc_combat_session(game,db,player,body):
    req=p.OpenNpcCombatSessionProto.FromString(body)
    template=next((v.combat_npc_trainer for v in templates(game.config) if v.template_id==req.combat_npc_template_id),None)
    if template is None:
        return p.OpenNpcCombatSessionOutProto(result=5)
    values=party(game,db,player,list(req.attacking_pokemon_id),template.combat_league_template_id)
    if values is None:
        return p.OpenNpcCombatSessionOutProto(result=3)
    opponents=bot_party(game,template.combat_league_template_id,list(template.available_pokemon))
    bot=dict(id=req.combat_npc_template_id,name=template.trainer_name,difficulty='normal',personality=template.combat_personality_id)
    state=engine.create(game,db,[player,None],[values,opponents],template.combat_league_template_id,'npc',bot)
    return p.OpenNpcCombatSessionOutProto(result=1,combat=engine.projection(game,db,state,0))


def open_combat_session(game,db,player,body):
    req=p.OpenCombatSessionProto.FromString(body)
    battle=engine.row(db,req.combat_id,player)
    if not battle:
        from dittobuffer.gameplay.pvp_challenges import open_rematch
        pending=open_rematch(game,db,player,req)
        if pending is not None:return pending
        return p.OpenCombatSessionOutProto(result=11)
    state=json.loads(battle['state'])
    if list(req.attacking_pokemon_id)!=[rules.source(v).id for v in state['members'][battle['side']]['party']]:
        return p.OpenCombatSessionOutProto(result=4)
    connection=db.execute('SELECT join_ms FROM pvp_connections WHERE battle_id=? AND player_id=?',(battle['id'],player)).fetchone()
    if req.combat_league_template_id and req.combat_league_template_id!=state['league']:
        return p.OpenCombatSessionOutProto(result=9)
    if connection and req.lobby_join_time_ms and req.lobby_join_time_ms<connection['join_ms']:
        return p.OpenCombatSessionOutProto(result=11)
    # Native readiness polls repeat the same lobby-join timestamp. They must
    # not reset replay protection. A new client join can start a new counter
    # epoch; the authoritative HP, energy and deadline never reset.
    if not connection or req.lobby_join_time_ms>connection['join_ms']:
        if req.lobby_join_time_ms>now_ms()+30000:return p.OpenCombatSessionOutProto(result=11)
        db.execute('INSERT INTO pvp_connections VALUES(?,?,?) ON CONFLICT(battle_id,player_id) DO UPDATE SET join_ms=excluded.join_ms',
            (battle['id'],player,req.lobby_join_time_ms))
        db.execute('UPDATE pvp_members SET epoch=epoch+1 WHERE battle_id=? AND player_id=?',(battle['id'],player))
    already_open=state['opened'][battle['side']]
    engine.join(game,state,battle['side'])
    # The native arena polls OpenCombatSession until the ready countdown
    # finishes; its first UpdateCombat is later. A repeated open is therefore
    # also a loading acknowledgement, avoiding a readiness deadlock.
    if already_open:engine.ready(game,state,battle['side'])
    engine.advance(game,state);engine.save(db,state)
    from dittobuffer.gameplay.pvp_rewards import settle
    settle(game,db,state)
    if state['phase'] in (6,7,8):return p.OpenCombatSessionOutProto(result=2,combat=engine.projection(game,db,state,battle['side']))
    return p.OpenCombatSessionOutProto(result=1,combat=engine.projection(game,db,state,battle['side']))


def update_combat(game,db,player,body):
    req=p.UpdateCombatProto.FromString(body)
    battle=engine.row(db,req.combat_id,player)
    if not battle:
        return p.UpdateCombatOutProto(result=3)
    digest=hashlib.sha256(body).hexdigest()
    if req.action.type:
        old=db.execute('SELECT * FROM pvp_actions WHERE battle_id=? AND player_id=? AND epoch=? AND counter=?',
            (battle['id'],player,battle['epoch'],req.combat_request_counter)).fetchone()
        if old:
            return p.UpdateCombatOutProto.FromString(old['response']) if old['digest']==digest else p.UpdateCombatOutProto(result=5)
        highest=db.execute('SELECT MAX(counter) FROM pvp_actions WHERE battle_id=? AND player_id=? AND epoch=?',
            (battle['id'],player,battle['epoch'])).fetchone()[0]
        replay=db.execute('SELECT 1 FROM pvp_actions WHERE battle_id=? AND player_id=? AND digest=?',
            (battle['id'],player,digest)).fetchone()
        if replay or (highest is not None and req.combat_request_counter<=highest):return p.UpdateCombatOutProto(result=5)
    state=json.loads(battle['state']);engine.ready(game,state,battle['side']);engine.advance(game,state)
    result=engine.submit(game,state,battle['side'],req.action) if req.action.type else 1
    engine.advance(game,state);engine.save(db,state)
    from dittobuffer.gameplay.pvp_rewards import settle
    settle(game,db,state)
    out=p.UpdateCombatOutProto(result=result,combat=engine.projection(game,db,state,battle['side'],req.combat_request_counter))
    if req.action.type:
        db.execute('INSERT INTO pvp_actions VALUES(?,?,?,?,?,?)',(battle['id'],player,battle['epoch'],req.combat_request_counter,digest,out.SerializeToString()))
    return out


def quit_combat(game,db,player,body):
    req=p.QuitCombatProto.FromString(body);battle=engine.row(db,req.combat_id,player)
    if not battle:
        from dittobuffer.gameplay.pvp_challenges import cancel_rematch
        if cancel_rematch(game,db,player,req.combat_id):return p.QuitCombatOutProto(result=1)
        return p.QuitCombatOutProto(result=2)
    state=json.loads(battle['state']);engine.advance(game,state)
    if state['phase'] not in (6,7,8):
        engine.finish(state,1-battle['side'],'quit');engine.save(db,state)
    from dittobuffer.gameplay.pvp_rewards import settle
    settle(game,db,state)
    return p.QuitCombatOutProto(result=1,combat=engine.projection(game,db,state,battle['side']))


def result(game,db,player,ident):
    battle=engine.row(db,ident,player)
    if not battle:
        return None,None
    state=json.loads(battle['state']);engine.advance(game,state);engine.save(db,state)
    from dittobuffer.gameplay.pvp_rewards import settle
    settle(game,db,state)
    return battle,state


def get_combat_results(game,db,player,body):
    req=p.GetCombatResultsProto.FromString(body);battle,state=result(game,db,player,req.combat_id)
    if not state:
        return p.GetCombatResultsOutProto(result=3)
    if state['phase'] not in (6,7,8):
        return p.GetCombatResultsOutProto(result=2)
    finish=2 if state['winner'] is None else int(state['winner']!=battle['side'])
    receipt=db.execute('SELECT loot FROM pvp_results WHERE battle_id=? AND player_id=?',(battle['id'],player)).fetchone()
    loot=p.LootProto.FromString(receipt[0]) if receipt else p.LootProto()
    out=p.GetCombatResultsOutProto(result=1,reward_status=1 if loot.loot_item else 4,rewards=loot,combat_player_finish_state=finish)
    # CombatResolveState.boht unconditionally dereferences CombatRematch at
    # 0x850BF30. A missing message freezes the native results transition.
    out.combat_rematch.SetInParent()
    out.friend_level_up.SetInParent()
    if state['kind']=='friend' and state.get('started'):
        from dittobuffer.gameplay.pvp_challenges import rematch
        out.combat_rematch.CopyFrom(rematch(game,db,state,player))
    return out


def get_npc_combat_rewards(game,db,player,body):
    req=p.GetNpcCombatRewardsProto.FromString(body)
    ident=req.combat_id
    if not ident:
        # The classic native NPC resolver omits field 4 even after server
        # combat. Bind it to the latest owned matching practice session, never
        # to the client's outcome or quest-update snapshot.
        for candidate in db.execute("SELECT b.id,b.state FROM pvp_battles b JOIN pvp_members m ON m.battle_id=b.id WHERE m.player_id=? AND b.kind='npc' ORDER BY b.rowid DESC LIMIT 1",(player,)):
            saved=json.loads(candidate['state'])
            if saved['bot']['id']==req.combat_npc_trainer_template_id and [rules.source(v).id for v in saved['members'][0]['party']]==list(req.attacking_pokemon_id):
                ident=candidate['id'];break
    battle,state=result(game,db,player,ident)
    if not state or state['kind']!='npc' or state['bot']['id']!=req.combat_npc_trainer_template_id or state['phase'] not in (6,7,8) or not state.get('started',True):
        return p.GetNpcCombatRewardsOutProto(result=3)
    expected=[rules.source(v).id for v in state['members'][battle['side']]['party']]
    if list(req.attacking_pokemon_id)!=expected:
        return p.GetNpcCombatRewardsOutProto(result=2)
    old=db.execute('SELECT loot FROM pvp_results WHERE battle_id=? AND player_id=?',(battle['id'],player)).fetchone()
    if old:
        loot=p.LootProto.FromString(old[0])
        return p.GetNpcCombatRewardsOutProto(result=1,reward_status=1 if loot.loot_item else 2,rewards=loot)
    if state.get('reason') in ('quit','lobby_timeout') or now_ms()>state.get('finished_ms',0)+game.pvp['npc_reward_claim_seconds']*1000:
        return p.GetNpcCombatRewardsOutProto(result=3)
    day=now_ms()//86400000*86400000
    count=db.execute("SELECT COUNT(*) FROM pvp_results r JOIN pvp_battles b ON b.id=r.battle_id WHERE r.player_id=? AND b.kind='npc' AND r.claimed_ms>=?",(player,day)).fetchone()[0]
    loot=p.LootProto()
    dust=game.pvp['npc_stardust'] if count==0 else 0
    if dust:
        db.execute('UPDATE players SET stardust=stardust+? WHERE id=?',(dust,player));game.touch(db,player)
        loot.loot_item.add(stardust=True,count=dust)
    finish=2 if state['winner'] is None else int(state['winner']!=battle['side'])
    db.execute('INSERT INTO pvp_results VALUES(?,?,?,?,?)',(battle['id'],player,finish,now_ms(),loot.SerializeToString()))
    return p.GetNpcCombatRewardsOutProto(result=1,reward_status=1 if dust else 2,rewards=loot,number_rewarded_npc_battles_today=count+1)


def attributes(game,db,player):
    value=profile(game,db,player)
    count=db.execute('SELECT COUNT(*) FROM pvp_set_battles WHERE player_id=? AND season=? AND set_id=?',(player,game.pvp['season'],value['set_id'])).fetchone()[0]
    return p.VsSeekerAttributesProto(vs_seeker_status=3 if value['active'] else 2,
        battle_granted_remaining=max(0,game.pvp['set_size']-count),max_battles_in_set=game.pvp['set_size'],reward_track=reward_track(game,db,player))


def reward_track(game,db,player):
    value=profile(game,db,player)
    row=db.execute('SELECT track FROM pvp_set_tracks WHERE player_id=? AND season=? AND set_id=?',(player,game.pvp['season'],value['set_id'])).fetchone()
    return row['track'] if row else 0


def reward_table(game,db,player):
    return (game.pvp['premium_reward_items'] or game.pvp['reward_items']) if reward_track(game,db,player) else game.pvp['reward_items']


def get_vs_seeker_status(game,db,player,body):
    p.GetVsSeekerStatusProto.FromString(body)
    value=profile(game,db,player)
    out=p.GetVsSeekerStatusOutProto(result=1,vs_seeker=attributes(game,db,player))
    out.season_ended=not game.pvp['season_start_ms']<=now_ms()<game.pvp['season_end_ms']
    season=p.CombatSeasonResult(season=game.pvp['season'],rank=value['rank'],total_battles=value['battles'],total_wins=value['wins'],
        rating=value['rating'],longest_win_streak=value['longest'],current_streak=value['streak'],stardust_earned=value['dust'])
    out.combat_log.current_season_results.CopyFrom(season)
    out.combat_log.lifetime_results.CopyFrom(season)
    totals=db.execute('SELECT COALESCE(SUM(battles),0),COALESCE(SUM(wins),0),COALESCE(MAX(longest),0),COALESCE(SUM(dust),0) FROM pvp_profiles WHERE player_id=?',(player,)).fetchone()
    lifetime=out.combat_log.lifetime_results
    lifetime.total_battles,lifetime.total_wins,lifetime.longest_win_streak,lifetime.stardust_earned=totals
    previous=db.execute('SELECT * FROM pvp_profiles WHERE player_id=? AND season<? ORDER BY season DESC LIMIT 1',(player,game.pvp['season'])).fetchone()
    if previous:
        out.combat_log.previous_season_results.CopyFrom(p.CombatSeasonResult(season=previous['season'],rank=previous['rank'],rating=previous['rating'],total_battles=previous['battles'],total_wins=previous['wins'],longest_win_streak=previous['longest'],stardust_earned=previous['dust']))
    for battle in db.execute('SELECT * FROM pvp_set_battles WHERE player_id=? AND season=? AND set_id=? ORDER BY rowid',(player,game.pvp['season'],value['set_id'])):
        out.combat_log.current_vs_seeker_set_results.add(battle_result=battle['finish'])
    wins=sum(v.battle_result==0 for v in out.combat_log.current_vs_seeker_set_results)
    claims={v[0] for v in db.execute('SELECT slot FROM pvp_claims WHERE player_id=? AND season=? AND set_id=?',(player,game.pvp['season'],value['set_id']))}
    # Claim indices count WINS, not the chronological battle position. A loss
    # before the first win must never make the first reward appear claimed.
    win_index=0
    for v in out.combat_log.current_vs_seeker_set_results:
        v.rewards_claimed=v.battle_result!=0 or win_index in claims
        if v.battle_result==0:win_index+=1
    return out


def activate_vs_seeker(game,db,player,body):
    req=p.ActivateVsSeekerProto.FromString(body)
    if req.reward_track not in (0,1) or req.reward_track==1 and not game.pvp['premium_enabled']:
        return p.ActivateVsSeekerOutProto(result=2)
    value=profile(game,db,player)
    if value['active']:
        return p.ActivateVsSeekerOutProto(result=1 if req.reward_track==reward_track(game,db,player) else 4,vs_seeker=attributes(game,db,player))
    if not game.pvp['season_start_ms']<=now_ms()<game.pvp['season_end_ms']:return p.ActivateVsSeekerOutProto(result=6)
    if req.reward_track==1:
        if not db.execute('UPDATE items SET count=count-1 WHERE player_id=? AND item=? AND count>0',(player,game.pvp['premium_pass_item'])).rowcount:
            return p.ActivateVsSeekerOutProto(result=2)
        game.touch(db,player)
    db.execute('INSERT INTO pvp_set_tracks VALUES(?,?,?,?)',(player,game.pvp['season'],value['set_id'],req.reward_track))
    db.execute('INSERT OR IGNORE INTO pvp_set_starts VALUES(?,?,?,?,?,?,?)',(player,game.pvp['season'],value['set_id'],value['rank'],value['battles'],value['wins'],value['rating']))
    db.execute('UPDATE pvp_profiles SET active=1 WHERE player_id=? AND season=?',(player,game.pvp['season']))
    return p.ActivateVsSeekerOutProto(result=1,vs_seeker=attributes(game,db,player))
