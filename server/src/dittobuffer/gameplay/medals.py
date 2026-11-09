"""Source-backed medals projected from durable, committed gameplay facts."""
import json
from functools import lru_cache
from pathlib import Path
from dittobuffer.gameplay.game import p,now_ms


@lru_cache(maxsize=1)
def definitions():
    from dittobuffer.gameplay.mega import DATA
    from dittobuffer.gameplay.level_research import reference
    return json.loads(Path(__file__).with_name('trainer-reference.json').read_text())['medals']+DATA['medals']+reference()['additional_medals']


def available(config):
    return [r for r in definitions() if (r['counter'] not in ('trades','best_friends') or config.get('social',{}).get('enabled')) and (r['counter']!='routes' or config.get('routes',{}).get('enabled')) and (r['counter'] not in ('rocket_grunts','rocket_bosses','purified') or config.get('rocket',{}).get('enabled'))]


def templates(config):
    values=[]
    for rule in available(config):
        value=p.GameMasterClientTemplateProto(template_id=rule['id'])
        value.badge.CopyFrom(p.BadgeSettingsProto(badge_type=rule['badge'],badge_ranks=rule['ranks'],targets=rule['targets']))
        values.append(value)
    return values


def counters(game,db,player):
    from dittobuffer.gameplay.pokedex import ensure
    ensure(game,db,player)
    row=game.player(db,player)
    result=dict(captures=row['captures'],stops=row['stops'],walking=row['km_walked'],hatches=row['eggs_hatched'])
    scalar=lambda sql:db.execute(sql,(player,)).fetchone()[0]
    result['dex']=scalar('SELECT COUNT(DISTINCT species) FROM pokedex_observations WHERE player_id=? AND caught=1 AND species BETWEEN 1 AND 151')
    for gen,(low,high) in enumerate(((1,151),(152,251),(252,386),(387,493),(494,649),(650,721),(722,809),(810,898),(906,1025)),1):
        result['dex_gen:'+str(gen)]=db.execute('SELECT COUNT(DISTINCT species) FROM pokedex_observations WHERE player_id=? AND caught=1 AND species BETWEEN ? AND ?',(player,low,high)).fetchone()[0]
    result['dex_hisui']=scalar('SELECT COUNT(DISTINCT species) FROM pokedex_observations WHERE player_id=? AND caught=1 AND species BETWEEN 899 AND 905')
    result['pikachu']=scalar('SELECT COUNT(*) FROM pokedex_observations WHERE player_id=? AND caught=1 AND species=25')
    result['unown']=scalar('SELECT COUNT(DISTINCT form) FROM pokedex_observations WHERE player_id=? AND caught=1 AND species=201')
    result['dex_all']=scalar('SELECT COUNT(DISTINCT species) FROM pokedex_observations WHERE player_id=? AND caught=1')
    # Relationships persist across switches; count Pokemon, not level-up polls.
    result['best_buddies']=sum(json.loads(r[0]).get('points',0)>=300 for r in db.execute('SELECT data FROM buddy_state WHERE player_id=?',(player,)))
    result['evolutions']=scalar("SELECT COUNT(*) FROM pokedex_observations WHERE player_id=? AND source_key LIKE 'evolve:%'")
    result['rocket_grunts']=scalar("SELECT COUNT(*) FROM trainer_events WHERE player_id=? AND kind='rocket_grunt'")
    result['rocket_bosses']=scalar("SELECT COUNT(*) FROM trainer_events WHERE player_id=? AND kind='rocket_boss'")
    result['purified']=scalar("SELECT COUNT(*) FROM trainer_events WHERE player_id=? AND kind='purify'")
    result['collections']=scalar("SELECT COUNT(*) FROM research_stories WHERE player_id=? AND completed=1 AND json_extract(definition,'$.research_type')='collection'")
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result['trades']=db.execute('SELECT COUNT(*) FROM social_trade_history WHERE a=? OR b=?',(player,player)).fetchone()[0] if 'social_trade_history' in tables else 0
    result['best_friends']=db.execute('SELECT COUNT(*) FROM social_pairs WHERE active=1 AND points>=90 AND (a=? OR b=?)',(player,player)).fetchone()[0] if 'social_pairs' in tables else 0
    result['unique_stops']=scalar("SELECT COUNT(DISTINCT fort_id) FROM fort_visits WHERE player_id=?")
    result['training_wins']=0
    for name in ('great','ultra','master'):result['league_'+name]=0
    if {'pvp_results','pvp_battles'}<=tables:
        for battle in db.execute('SELECT b.kind,b.state FROM pvp_results r JOIN pvp_battles b ON b.id=r.battle_id WHERE r.player_id=? AND r.finish=0',(player,)):
            state=json.loads(battle['state'])
            if battle['kind']=='npc':result['training_wins']+=1
            else:
                league=state.get('league','').lower()
                for name in ('great','ultra','master'):
                    if name in league:result['league_'+name]+=1
    result['routes']=db.execute('SELECT COALESCE(SUM(completions),0) FROM route_badges WHERE player=?',(player,)).fetchone()[0] if 'route_badges' in tables else 0
    result['research']=scalar('SELECT COUNT(*) FROM research_tasks WHERE player_id=? AND parent_id IS NULL AND claimed=1 AND abandoned=0')
    result['mega_total']=scalar("SELECT COUNT(*) FROM mega_ledger WHERE player_id=? AND kind='evolve'")
    result['mega_unique']=scalar("SELECT COUNT(DISTINCT species) FROM mega_ledger WHERE player_id=? AND kind='evolve'")
    totals=db.execute('SELECT COALESCE(SUM(battles_won),0),COALESCE(SUM(berries),0) FROM gym_badges WHERE player_id=?',(player,)).fetchone()
    result['gym_wins'],result['berries']=totals
    result['raid_wins']=result['legendary_wins']=0
    for raid in db.execute('SELECT r.definition FROM raid_members m JOIN raids r ON r.seed=m.seed WHERE m.player_id=? AND m.reward IS NOT NULL',(player,)):
        result['legendary_wins' if json.loads(raid['definition'])['tier']==5 else 'raid_wins']+=1
    for capture in db.execute("SELECT a.data FROM encounters e JOIN spawn_attributes a ON a.spawn_id=e.spawn_id WHERE e.player_id=? AND e.state='caught'",(player,)):
        mon=p.PokemonProto.FromString(capture['data']);rule=game.forms.get((mon.pokemon_id,mon.pokemon_display.form),game.species[mon.pokemon_id])
        for kind in set(rule['types']):
            key='type:'+p.HoloPokemonType.Name(kind).split('PokemonType')[-1].upper()
            result[key]=result.get(key,0)+1
    return result


def refresh(game,db,player):
    values=counters(game,db,player)
    db.execute('INSERT OR IGNORE INTO trainer_counter_imports VALUES(?,?,?)',(player,
        'Preserve player totals and walking; derive Dex/evolutions from retained observations, types from caught encounters, gyms/raids/research from durable receipts. Unrecorded history is not invented.',now_ms()))
    from dittobuffer.gameplay.trainer import event
    for rule in available(game.config):
        value=values.get(rule['counter'],0);rank=sum(value>=target for target in rule['targets'])
        previous=db.execute('SELECT rank FROM trainer_medals WHERE player_id=? AND badge=?',(player,rule['badge'])).fetchone()
        db.execute('INSERT INTO trainer_medals(player_id,badge,rank) VALUES(?,?,?) ON CONFLICT(player_id,badge) DO UPDATE SET rank=MAX(rank,excluded.rank)',(player,rule['badge'],rank))
        if rank>(previous[0] if previous else 0):
            event(game,db,player,'medal:'+str(rule['badge'])+':'+str(rank),'medal',dict(badge=rule['badge'],rank=rank,value=value))
    return values


def profile(game,db,player):
    values=refresh(game,db,player);result=[]
    for rule in available(game.config):
        value=values.get(rule['counter'],0);rank=sum(value>=target for target in rule['targets'])
        earned=db.execute('SELECT rank FROM trainer_medals WHERE player_id=? AND badge=?',(player,rule['badge'])).fetchone()
        rank=max(rank,earned[0] if earned else 0)
        result.append(p.PlayerBadgeProto(badge_type=rule['badge'],rank=rank,current_value=value,
            start_value=rule['targets'][rank-1] if rank else 0,end_value=rule['targets'][min(rank,len(rule['targets'])-1)]))
    return result


def awarded(game,db,player,body):
    refresh(game,db,player)
    out=p.CheckAwardedBadgesOutProto(success=True)
    for row in db.execute('SELECT * FROM trainer_medals WHERE player_id=? AND rank>notified_rank ORDER BY badge',(player,)).fetchall():
        out.awarded_badges.append(row['badge']);out.awarded_badge_levels.append(row['rank'])
        db.execute('UPDATE trainer_medals SET notified_rank=rank WHERE player_id=? AND badge=?',(player,row['badge']))
    return out


def cosmetic_rewards(game,db,player,body):
    p.NeutralAvatarBadgeRewardProto.FromString(body)
    refresh(game,db,player)
    from dittobuffer.gameplay.wardrobe import registry,grant
    out=p.NeutralAvatarBadgeRewardOutProto(result=1)
    for item in registry()['articles']:
        if item['ownership']!='medal':continue
        row=db.execute('SELECT rank FROM trainer_medals WHERE player_id=? AND badge=?',(player,item['medal'])).fetchone()
        if not row or row['rank']<item['medal_rank']:continue
        if not grant(game,db,player,item['id'],'medal:'+str(item['medal'])+':'+str(item['medal_rank'])):continue
        display=out.avatar_badge_display.add(badge_type=item['medal'],badge_level=item['medal_rank'])
        display.display.icon_address=item['display'].get('iconAddress','')
        display.display.display_string_id=item['display'].get('displayStringId','')
        db.execute('UPDATE trainer_medals SET claimed_rank=MAX(claimed_rank,?) WHERE player_id=? AND badge=?',(item['medal_rank'],player,item['medal']))
    return out


def apply_stats(game,db,player,stats,values=None):
    values=refresh(game,db,player) if values is None else values
    stats.num_unique_pokedex_entries=int(values['dex_all'])
    stats.num_evolutions=int(values['evolutions'])
    stats.num_battle_attack_won=int(values['gym_wins'])
    stats.num_raid_battle_won=int(values['raid_wins'])
    stats.num_legendary_battle_won=int(values['legendary_wins'])
    stats.num_berries_fed=int(values['berries'])
    stats.num_challenge_quests_completed=int(values['research'])
    from dittobuffer.gameplay.friends import enabled
    if enabled(game):
        stats.num_trades=db.execute('SELECT COUNT(*) FROM social_trade_history WHERE a=? OR b=?',(player,player)).fetchone()[0]
        stats.num_max_level_friends=db.execute('SELECT COUNT(*) FROM social_pairs WHERE points>=90 AND active=1 AND (a=? OR b=?)',(player,player)).fetchone()[0]
        stats.num_forever_friends_earned=db.execute('SELECT COUNT(*) FROM social_pairs WHERE points>=180 AND (a=? OR b=?)',(player,player)).fetchone()[0]


def inventory(game,db,player):
    """Collector history is native badge data, separate from aggregate ranks."""
    rows=db.execute("SELECT s.id,c.claimed_ms FROM research_stories s JOIN research_stage_claims c ON c.player_id=s.player_id AND c.story_id=s.id AND c.stage_index=0 WHERE s.player_id=? AND s.completed=1 AND json_extract(s.definition,'$.research_type')='collection' ORDER BY c.claimed_ms,s.id",(player,)).fetchall()
    if not rows:return []
    item=p.HoloInventoryItemProto();item.badge_data.badge=1002
    for row in rows:item.badge_data.mini_collection.event.add(event_id=row['id'],completed_timestamp=row['claimed_ms'])
    return [item]
