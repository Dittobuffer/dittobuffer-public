"""Atomic private-server ranks, set progress and idempotent reward receipts."""
import math
from types import MethodType
from dittobuffer.gameplay.game import p,now_ms
from dittobuffer.gameplay import pvp


def settle(game,db,state):
    # Charge resolutions are engine facts, not client outcome flags. History
    # indexes remain stable; durable trainer source keys deduplicate each poll.
    from dittobuffer.gameplay.trainer import event
    for index,attack in enumerate(state.get('history',[])):
        if attack.get('type')!='charged' or not attack.get('super_effective'):continue
        owner=state['members'][attack['side']]['player']
        if owner:event(game,db,owner,'trainer-charge:'+state['id']+':'+str(index),'super_effective_charge',{'super_effective':True,'move':attack['move']})
    if state['phase'] not in (6,7,8) or state['kind']=='npc' or not state.get('started',True):return
    season=state.get('season',game.pvp['season'])
    profiles={m['player']:pvp.profile(game,db,m['player'],season) for m in state['members'] if m['player']}
    for side,member in enumerate(state['members']):
        player=member['player']
        if not player or db.execute('SELECT 1 FROM pvp_results WHERE battle_id=? AND player_id=?',(state['id'],player)).fetchone():continue
        finish=2 if state['winner'] is None else int(state['winner']!=side)
        loot=p.LootProto();value=profiles[player]
        if state['kind']=='league':
            wins=int(finish==0);streak=value['streak']+wins if wins else 0
            other=state['members'][1-side]['player']
            rating_other=profiles[other]['rating'] if other else value['rating']
            expected=1/(1+10**((rating_other-value['rating'])/400))
            score=.5 if finish==2 else float(wins)
            rating=max(0,value['rating']+state.get('elo_k',game.pvp['elo_k'])*(score-expected))
            definitions=state.get('rank_rules',game.pvp['ranks']);rank=value['rank']
            if state.get('rank_requirements',game.pvp['rank_requirements'])=='additional':
                start=db.execute('SELECT * FROM pvp_rank_starts WHERE player_id=? AND season=? AND rank=?',(player,season,rank)).fetchone()
                if rank<len(definitions):
                    needed=definitions[rank]
                    if value['battles']+1-start['battles']>=needed['battles'] and value['wins']+wins-start['wins']>=needed['wins']:rank+=1
            else:
                rank=max(rank,max(i for i,definition in enumerate(definitions,1)
                    if value['battles']+1>=definition['battles'] and value['wins']+wins>=definition['wins']))
            db.execute('UPDATE pvp_profiles SET battles=battles+1,wins=wins+?,streak=?,longest=MAX(longest,?),rating=?,rank=? WHERE player_id=? AND season=?',
                (wins,streak,streak,rating,rank,player,season))
            if rank!=value['rank']:
                db.execute('INSERT OR IGNORE INTO pvp_rank_starts VALUES(?,?,?,?,?,?)',(player,season,rank,value['battles']+1,value['wins']+wins,rating))
            db.execute('INSERT INTO pvp_set_battles VALUES(?,?,?,?,?)',(player,season,value['set_id'],state['id'],finish))
        else:
            day=now_ms()//86400000*86400000
            count=db.execute("SELECT COUNT(*) FROM pvp_results r JOIN pvp_battles b ON b.id=r.battle_id WHERE r.player_id=? AND b.kind='friend' AND r.claimed_ms>=?",(player,day)).fetchone()[0]
            if count<game.pvp['friend_rewards_per_day'] and state.get('reason')!='quit':
                dust=game.pvp['friend_stardust'];loot.loot_item.add(stardust=True,count=dust)
                db.execute('UPDATE players SET stardust=stardust+? WHERE id=?',(dust,player));game.touch(db,player)
        db.execute('INSERT INTO pvp_results VALUES(?,?,?,?,?)',(state['id'],player,finish,now_ms(),loot.SerializeToString()))
        from dittobuffer.gameplay.trainer import event
        event(game,db,player,'pvp:'+state['id'],'pvp_battle',{'won':finish==0,'source':state['kind']})


def grant(game,db,player,definition):
    loot=p.LootProto()
    if definition.get('item'):
        item,amount=definition['item'],definition['count']
        used=db.execute('SELECT COALESCE(SUM(count),0) FROM items WHERE player_id=?',(player,)).fetchone()[0]
        if used+amount>game.capacity(db,player,'item_capacity'):return None
        db.execute('INSERT INTO items VALUES(?,?,?) ON CONFLICT(player_id,item) DO UPDATE SET count=count+excluded.count',(player,item,amount))
        game.touch(db,player)
        loot.loot_item.add(item=item,count=amount)
    if definition.get('stardust'):
        dust=definition['stardust'];db.execute('UPDATE players SET stardust=stardust+? WHERE id=?',(dust,player));game.touch(db,player)
        loot.loot_item.add(stardust=True,count=dust)
        db.execute('UPDATE pvp_profiles SET dust=dust+? WHERE player_id=? AND season=?',(dust,player,game.pvp['season']))
    return loot


def claim_vs_seeker_rewards(game,db,player,body):
    req=p.ClaimVsSeekerRewardsProto.FromString(body)
    value=pvp.profile(game,db,player);slot=req.win_index
    definition=pvp.reward_table(game,db,player).get(str(slot+1))
    if not definition or not 0<=slot<game.pvp['set_size']:
        return p.ClaimVsSeekerRewardsOutProto(result=3)
    key=(player,game.pvp['season'],value['set_id'],slot)
    old=db.execute('SELECT loot FROM pvp_claims WHERE player_id=? AND season=? AND set_id=? AND slot=?',key).fetchone()
    if old:return p.ClaimVsSeekerRewardsOutProto(result=4)
    wins=db.execute('SELECT COUNT(*) FROM pvp_set_battles WHERE player_id=? AND season=? AND set_id=? AND finish=0',key[:3]).fetchone()[0]
    if wins<slot+1:return p.ClaimVsSeekerRewardsOutProto(result=3)
    if definition.get('species'):return p.ClaimVsSeekerRewardsOutProto(result=3)
    loot=grant(game,db,player,definition)
    if loot is None:return p.ClaimVsSeekerRewardsOutProto(result=5)
    db.execute('INSERT INTO pvp_claims VALUES(?,?,?,?,?,?)',(*key,loot.SerializeToString(),now_ms()))
    return p.ClaimVsSeekerRewardsOutProto(result=1,rewards=loot)


def complete_vs_seeker_and_restart_charging(game,db,player,body):
    p.CompleteVsSeekerAndRestartChargingProto.FromString(body)
    value=pvp.profile(game,db,player);key=(player,game.pvp['season'],value['set_id'])
    results=db.execute('SELECT finish FROM pvp_set_battles WHERE player_id=? AND season=? AND set_id=?',key).fetchall()
    if len(results)<game.pvp['set_size']:return p.CompleteVsSeekerAndRestartChargingOutProto(result=3)
    wins=sum(v[0]==0 for v in results)
    claimed=db.execute('SELECT COUNT(*) FROM pvp_claims WHERE player_id=? AND season=? AND set_id=? AND slot>=0',key).fetchone()[0]
    if claimed<wins:return p.CompleteVsSeekerAndRestartChargingOutProto(result=7)
    beginning=db.execute('SELECT * FROM pvp_set_starts WHERE player_id=? AND season=? AND set_id=?',key).fetchone()
    # Migration for a set already started before the start snapshot existed.
    previous_rank=beginning['rank'] if beginning else max(i for i,definition in enumerate(game.pvp['ranks'],1) if value['battles']-len(results)>=definition['battles'] and value['wins']-wins>=definition['wins'])
    loot=grant(game,db,player,dict(stardust=game.pvp['set_stardust']))
    db.execute('INSERT INTO pvp_claims VALUES(?,?,?,?,?,?)',(*key,-1,loot.SerializeToString(),now_ms()))
    db.execute('UPDATE pvp_profiles SET set_id=set_id+1,active=0 WHERE player_id=? AND season=?',key[:2])
    rank_start=db.execute('SELECT * FROM pvp_rank_starts WHERE player_id=? AND season=? AND rank=?',(player,game.pvp['season'],value['rank'])).fetchone()
    return p.CompleteVsSeekerAndRestartChargingOutProto(result=1,vs_seeker=pvp.attributes(game,db,player),loot_proto=loot,
        current_season_result=pvp.get_vs_seeker_status(game,db,player,b'').combat_log.current_season_results,
        previous_rank=previous_rank,previous_rating=beginning['rating'] if beginning else value['rating'],
        stats_at_rank_start=p.CombatBaseStatsProto(total_battles=rank_start['battles'],wins=rank_start['wins'],rating=rank_start['rating']))


def complete_competitive_season(game,db,player,body):
    p.CompleteCompetitiveSeasonProto.FromString(body)
    last=game.pvp['season'] if now_ms()>=game.pvp['season_end_ms'] else game.pvp['season']-1
    value=db.execute('SELECT * FROM pvp_profiles WHERE player_id=? AND season<=? ORDER BY season DESC LIMIT 1',(player,last)).fetchone()
    if not value:return p.CompleteCompetitiveSeasonOutProto(result=2)
    if db.execute('SELECT 1 FROM pvp_season_claims WHERE player_id=? AND season=?',(player,value['season'])).fetchone():
        return p.CompleteCompetitiveSeasonOutProto(result=3)
    current=pvp.profile(game,db,player)
    out=p.CompleteCompetitiveSeasonOutProto(result=1,new_rank=current['rank'],new_rating=current['rating'],was_player_active=value['battles']>0)
    out.loot_proto.SetInParent()
    dust=game.pvp['season_stardust'] if value['battles'] else 0
    if dust:
        db.execute('UPDATE players SET stardust=stardust+? WHERE id=?',(dust,player));game.touch(db,player)
        db.execute('UPDATE pvp_profiles SET dust=dust+? WHERE player_id=? AND season=?',(dust,player,value['season']))
        out.loot_proto.loot_item.add(stardust=True,count=dust)
    out.last_season_result.CopyFrom(p.CombatSeasonResult(season=value['season'],rank=value['rank'],rating=value['rating'],
        total_battles=value['battles'],total_wins=value['wins'],longest_win_streak=value['longest'],current_streak=value['streak'],stardust_earned=value['dust']+dust))
    db.execute('INSERT INTO pvp_season_claims VALUES(?,?,?)',(player,value['season'],out.SerializeToString()))
    return out


def install(game):
    for name in ('claim_vs_seeker_rewards','complete_vs_seeker_and_restart_charging'):
        setattr(game,'rpc_'+name,MethodType(globals()[name],game))
    game.rpc_complete_combat_competitive_season_action=MethodType(complete_competitive_season,game)
