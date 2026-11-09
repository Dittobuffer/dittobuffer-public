"""Authenticated durable league queue. Bots are explicitly labelled private AI."""
import json
import secrets
from dittobuffer.gameplay.game import p, now_ms
from dittobuffer.gameplay import pvp, pvp_engine as engine, pvp_rules as rules


def challenge(game,db,state,viewer):
    from dittobuffer.gameplay.gyms import public
    out=p.CombatChallengeProto(challenge_id=state['id'],combat_id=state['id'],type=6 if state['kind']=='league' else 3,
        combat_league_template_id=state['league'],state=6,created_timestamp_ms=state['start']-3000,
        expiration_timestamp_ms=state['end'],is_vnext_battle=False)
    for side,target in [(0,out.challenger),(1,out.opponent)]:
        member=state['members'][side]
        ident=member['player'] or pvp.bot_identity(state['bot'])
        target.player_id=pvp.wire_player(ident)
        target.nia_account_id=ident
        target.attacking_pokemon_id.extend(rules.source(v).id for v in member['party'])
        if member['player']:
            target.public_profile.CopyFrom(public(game,db,ident))
            target.public_profile.player_id=pvp.wire_player(ident)
        else:
            from dittobuffer.gameplay.avatar import default_avatar
            target.public_profile.CopyFrom(p.PlayerPublicProfileProto(player_id=pvp.wire_player(ident),name=state['bot']['name'],level=20,team=1))
            target.public_profile.neutral_avatar.CopyFrom(default_avatar(p))
        target.player_avatar.CopyFrom(target.public_profile.avatar)
    return out


def expire(db,season):
    db.execute("UPDATE pvp_queue SET state='expired' WHERE state='queued' AND (expires_ms<=? OR COALESCE((SELECT season FROM pvp_queue_seasons s WHERE s.queue_id=pvp_queue.id),-1)<>?)",(now_ms(),season))


def match(game,db,queue):
    if queue['state']!='queued':return queue
    peers=db.execute("SELECT * FROM pvp_queue WHERE state='queued' AND player_id<>? AND league=? ORDER BY created_ms,id",(queue['player_id'],queue['league'])).fetchall()
    opponent=None
    for peer in peers:
        window=game.pvp['rating_window']+game.pvp['rating_expansion_per_second']*(now_ms()-min(peer['created_ms'],queue['created_ms']))/1000
        if abs(peer['rating']-queue['rating'])<=window:
            opponent=peer;break
    ids=json.loads(queue['party'])
    party=pvp.party(game,db,queue['player_id'],ids,queue['league'])
    if party is None:
        db.execute("UPDATE pvp_queue SET state='expired' WHERE id=?",(queue['id'],))
        return db.execute('SELECT * FROM pvp_queue WHERE id=?',(queue['id'],)).fetchone()
    if opponent:
        other=pvp.party(game,db,opponent['player_id'],json.loads(opponent['party']),opponent['league'])
        if other is None:
            db.execute("UPDATE pvp_queue SET state='expired' WHERE id=?",(opponent['id'],));return queue
        state=engine.create(game,db,[queue['player_id'],opponent['player_id']],[party,other],queue['league'],'league')
        db.execute("UPDATE pvp_queue SET state='matched',battle_id=? WHERE id IN (?,?)",(state['id'],queue['id'],opponent['id']))
    elif game.pvp['ai_backfill_enabled'] and now_ms()-queue['created_ms']>=game.pvp['ai_backfill_seconds']*1000:
        difficulty=game.pvp['ai_difficulty']
        bot=dict(id='league-'+difficulty,name='Libre AI ('+difficulty+')',difficulty=difficulty)
        state=engine.create(game,db,[queue['player_id'],None],[party,pvp.bot_party(game,queue['league'])],queue['league'],'league',bot)
        db.execute("UPDATE pvp_queue SET state='matched',battle_id=? WHERE id=?",(state['id'],queue['id']))
    return db.execute('SELECT * FROM pvp_queue WHERE id=?',(queue['id'],)).fetchone()


def reply(game,db,value,cls):
    if value['state']=='matched':
        state=json.loads(db.execute('SELECT state FROM pvp_battles WHERE id=?',(value['battle_id'],)).fetchone()[0])
        return cls(result=1,queue_id=value['id'],challenge=challenge(game,db,state,value['player_id']))
    return cls(result=2 if value['state']=='queued' else 3,queue_id=value['id'])


def vs_seeker_start_matchmaking(game,db,player,body):
    req=p.VsSeekerStartMatchmakingProto.FromString(body);expire(db,game.pvp['season'])
    # A reconnect resumes the same authority and countdown, not a second match.
    previous=db.execute("SELECT q.* FROM pvp_queue q JOIN pvp_battles b ON b.id=q.battle_id WHERE q.player_id=? AND q.state='matched' AND b.finished=0 ORDER BY q.created_ms DESC LIMIT 1",(player,)).fetchone()
    if previous:
        state=json.loads(db.execute('SELECT state FROM pvp_battles WHERE id=?',(previous['battle_id'],)).fetchone()[0])
        engine.advance(game,state);engine.save(db,state)
        from dittobuffer.gameplay.pvp_rewards import settle
        settle(game,db,state)
        if state['phase'] not in (6,7,8):
            if req.combat_league_template_id!=previous['league'] or list(req.attacking_pokemon_id)!=json.loads(previous['party']):
                return p.VsSeekerStartMatchmakingOutProto(result=4,queue_id=previous['id'])
            return reply(game,db,previous,p.VsSeekerStartMatchmakingOutProto)
    old=db.execute("SELECT * FROM pvp_queue WHERE player_id=? AND state='queued'",(player,)).fetchone()
    if old:
        if old['league']!=req.combat_league_template_id or json.loads(old['party'])!=list(req.attacking_pokemon_id):
            return p.VsSeekerStartMatchmakingOutProto(result=4,queue_id=old['id'])
        return reply(game,db,match(game,db,old),p.VsSeekerStartMatchmakingOutProto)
    cfg=game.pvp;stamp=now_ms()
    if not cfg['season_start_ms']<=stamp<cfg['season_end_ms']:
        return p.VsSeekerStartMatchmakingOutProto(result=5)
    value=pvp.profile(game,db,player)
    if not value['active']:
        return p.VsSeekerStartMatchmakingOutProto(result=9)
    if not pvp.attributes(game,db,player).battle_granted_remaining:
        return p.VsSeekerStartMatchmakingOutProto(result=3)
    if pvp.party(game,db,player,list(req.attacking_pokemon_id),req.combat_league_template_id) is None:
        return p.VsSeekerStartMatchmakingOutProto(result=8)
    day=stamp//86400000*86400000
    count=db.execute("SELECT COUNT(*) FROM pvp_results r JOIN pvp_battles b ON b.id=r.battle_id WHERE r.player_id=? AND b.kind='league' AND r.claimed_ms>=?",(player,day)).fetchone()[0]
    if count>=cfg['set_size']*cfg['daily_sets']:
        return p.VsSeekerStartMatchmakingOutProto(result=11)
    ident='pvp-queue-'+secrets.token_hex(12)
    db.execute("INSERT INTO pvp_queue VALUES(?,?,?,?,?,?,?,'queued',NULL)",(ident,player,req.combat_league_template_id,json.dumps(list(req.attacking_pokemon_id)),stamp,stamp+cfg['queue_seconds']*1000,value['rating']))
    db.execute('INSERT INTO pvp_queue_seasons VALUES(?,?)',(ident,cfg['season']))
    return reply(game,db,match(game,db,db.execute('SELECT * FROM pvp_queue WHERE id=?',(ident,)).fetchone()),p.VsSeekerStartMatchmakingOutProto)


def get_matchmaking_status(game,db,player,body):
    req=p.GetMatchmakingStatusProto.FromString(body);expire(db,game.pvp['season'])
    value=db.execute('SELECT * FROM pvp_queue WHERE id=? AND player_id=?',(req.queue_id,player)).fetchone()
    if not value:return p.GetMatchmakingStatusOutProto(result=5)
    if req.roster_pokemon_id and list(req.roster_pokemon_id)!=json.loads(value['party']):
        return p.GetMatchmakingStatusOutProto(result=6)
    return reply(game,db,match(game,db,value),p.GetMatchmakingStatusOutProto)


def cancel_matchmaking(game,db,player,body):
    req=p.CancelMatchmakingProto.FromString(body);expire(db,game.pvp['season'])
    value=db.execute('SELECT * FROM pvp_queue WHERE id=? AND player_id=?',(req.queue_id,player)).fetchone()
    if not value:return p.CancelMatchmakingOutProto(result=4)
    if value['state']=='matched':return p.CancelMatchmakingOutProto(result=2)
    db.execute("UPDATE pvp_queue SET state='cancelled' WHERE id=?",(value['id'],))
    return p.CancelMatchmakingOutProto(result=1)


def install(game):
    from types import MethodType
    for name in ('vs_seeker_start_matchmaking','get_matchmaking_status','cancel_matchmaking'):
        setattr(game,'rpc_'+name,MethodType(globals()[name],game))
