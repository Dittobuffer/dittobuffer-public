"""Owned, expiring native friend/QR challenges; never trusts a caller's identity."""
import json
import secrets
from types import MethodType
from dittobuffer.gameplay.game import p,now_ms
from dittobuffer.gameplay import pvp,pvp_engine as e,pvp_matchmaking as mm


def permitted(game,db,a,b):
    from dittobuffer.gameplay.friends import pair
    from dittobuffer.core.preferences import load_group
    return bool(a!=b and pair(db,a,b) and pvp.eligible(game,db,a) and pvp.eligible(game,db,b)
        and not any(load_group(db,player,'combat').friends_combat_opt_out for player in (a,b)))


def rematch(game,db,state,player):
    other=next(m['player'] for m in state['members'] if m['player']!=player)
    out=p.CombatRematchProto()
    # Native results send this ID directly to OpenCombatSession, rather than
    # Create/OpenCombatChallenge (Pixel trace m191-server-02, RPC 515).
    # Both result screens receive one offer; opening it is each player's consent.
    if not other or not permitted(game,db,player,other):return out
    old=db.execute('SELECT * FROM pvp_rematch_sessions WHERE previous_id=?',(state['id'],)).fetchone()
    if old:
        if old['expires_ms']<=now_ms():return out
        ident=old['combat_id']
    else:
        ident='pvp-rematch-'+secrets.token_hex(16);stamp=now_ms()
        db.execute('INSERT INTO pvp_rematch_sessions(previous_id,combat_id,created_ms,expires_ms) VALUES(?,?,?,?)',
            (state['id'],ident,stamp,stamp+game.pvp['rematch_seconds']*1000))
    out.combat_rematch_id=ident;out.combat_league_template_id=state['league']
    return out


def open_rematch(game,db,player,req):
    offer=db.execute('SELECT * FROM pvp_rematch_sessions WHERE combat_id=?',(req.combat_id,)).fetchone()
    if not offer:return None
    previous=e.row(db,offer['previous_id'],player)
    if not previous:return p.OpenCombatSessionOutProto(result=11)
    old=json.loads(previous['state']);players=[m['player'] for m in old['members']]
    if offer['expires_ms']<=now_ms() or not permitted(game,db,*players):return p.OpenCombatSessionOutProto(result=11)
    if req.combat_league_template_id and req.combat_league_template_id!=old['league']:return p.OpenCombatSessionOutProto(result=4)
    if occupied(game,db,player):return p.OpenCombatSessionOutProto(result=11)
    ids=list(req.attacking_pokemon_id);side=previous['side']
    owned=pvp.party(game,db,player,ids,old['league'])
    if owned is None:return p.OpenCombatSessionOutProto(result=4)
    db.execute('UPDATE pvp_rematch_sessions SET party'+str(side)+'=? WHERE combat_id=?',(json.dumps(ids),req.combat_id))
    offer=db.execute('SELECT * FROM pvp_rematch_sessions WHERE combat_id=?',(req.combat_id,)).fetchone()
    selections=[json.loads(offer['party'+str(i)]) for i in (0,1)]
    if all(selections):
        parties=[pvp.party(game,db,ident,ids,old['league']) for ident,ids in zip(players,selections)]
        if not all(parties) or any(occupied(game,db,ident) for ident in players):return p.OpenCombatSessionOutProto(result=11)
        state=e.create(game,db,players,parties,old['league'],ident=req.combat_id)
        e.join(game,state,0);e.join(game,state,1);e.ready(game,state,side);e.save(db,state)
    else:
        # Presentation while waiting uses the preceding *public* battle party,
        # not a consent or a new combat record. No engine clock/reward advances.
        parties=[[e.r.source(v) for v in m['party']] for m in old['members']];parties[side]=owned
        state=e.create(game,db,players,parties,old['league'],ident=req.combat_id,persist=False)
        state['lobby_deadline']=offer['expires_ms']
    return p.OpenCombatSessionOutProto(result=1,combat=e.projection(game,db,state,side))


def cancel_rematch(game,db,player,ident):
    offer=db.execute('SELECT * FROM pvp_rematch_sessions WHERE combat_id=?',(ident,)).fetchone()
    if not offer or not e.row(db,offer['previous_id'],player):return False
    db.execute("UPDATE pvp_rematch_sessions SET expires_ms=?,party0='[]',party1='[]' WHERE combat_id=?",(now_ms(),ident))
    return True


def occupied(game,db,player,except_id=''):
    db.execute('UPDATE pvp_challenges SET state=7 WHERE state IN (1,2,4) AND expires_ms<=?',(now_ms(),))
    for row in db.execute('SELECT b.state FROM pvp_battles b JOIN pvp_members m ON m.battle_id=b.id WHERE m.player_id=? AND b.finished=0',(player,)).fetchall():
        state=json.loads(row['state']);e.advance(game,state);e.save(db,state)
        from dittobuffer.gameplay.pvp_rewards import settle
        settle(game,db,state)
        if state['phase'] not in (6,7,8):return True
    return bool(db.execute("SELECT 1 FROM pvp_queue WHERE player_id=? AND state='queued' AND expires_ms>?",(player,now_ms())).fetchone()
        or db.execute('SELECT 1 FROM pvp_challenges WHERE id<>? AND state IN (2,4) AND (challenger=? OR opponent=?)',(except_id,player,player)).fetchone())


def read(db,ident,player):
    value=db.execute('SELECT * FROM pvp_challenges WHERE id=? AND (challenger=? OR opponent=?)',(ident,player,player)).fetchone()
    if value and value['expires_ms']<=now_ms() and value['state'] not in (3,5,6,7):
        db.execute('UPDATE pvp_challenges SET state=7 WHERE id=?',(ident,))
        value=db.execute('SELECT * FROM pvp_challenges WHERE id=?',(ident,)).fetchone()
    return value


def projection(game,db,value):
    if value['battle_id'] and value['state']==6:
        state=json.loads(db.execute('SELECT state FROM pvp_battles WHERE id=?',(value['battle_id'],)).fetchone()[0])
        out=mm.challenge(game,db,state,value['challenger']);out.challenge_id=value['id'];return out
    from dittobuffer.gameplay.gyms import public
    out=p.CombatChallengeProto(challenge_id=value['id'],type=3,state=value['state'],
        combat_league_template_id=value['league'],created_timestamp_ms=value['created_ms'],expiration_timestamp_ms=value['expires_ms'])
    for side,target in [(0,out.challenger),(1,out.opponent)]:
        ident=value['challenger' if side==0 else 'opponent']
        if ident:
            target.player_id=pvp.wire_player(ident)
            target.nia_account_id=ident
            target.public_profile.CopyFrom(public(game,db,ident))
            target.public_profile.player_id=pvp.wire_player(ident)
            target.player_avatar.CopyFrom(target.public_profile.avatar)
            target.attacking_pokemon_id.extend(json.loads(value['party'+str(side)]))
    return out


def ready(game,db,value):
    if value['state'] not in (4,6) or value['battle_id']:return value
    parties=[json.loads(value['party'+str(i)]) for i in (0,1)]
    if not all(parties):return value
    if not permitted(game,db,value['challenger'],value['opponent']):
        db.execute('UPDATE pvp_challenges SET state=7 WHERE id=?',(value['id'],))
        acknowledge(db,value['id'])
        return read(db,value['id'],value['challenger'])
    values=[pvp.party(game,db,ident,party,value['league']) for ident,party in zip([value['challenger'],value['opponent']],parties)]
    if all(v is not None for v in values):
        state=e.create(game,db,[value['challenger'],value['opponent']],values,value['league'],'friend')
        db.execute('UPDATE pvp_challenges SET battle_id=?,state=6 WHERE id=?',(state['id'],value['id']))
    return db.execute('SELECT * FROM pvp_challenges WHERE id=?',(value['id'],)).fetchone()


def notify(game,db,value,redeliver=False):
    """Existing native inbox delivery; accepting a challenge never grants loot."""
    stamp=now_ms();ident='trainer-challenge:'+value['id']
    notification=p.ClientInbox.Notification(notification_id=ident,category='COMBAT_CHALLENGE_OPENED',
        create_timestamp_ms=stamp,expire_time_ms=value['expires_ms'],labels=[1,2,3])
    notification.variables.add(name='COMBAT_CHALLENGE',byte_value=projection(game,db,value).SerializeToString())
    notification.variables.add(name='COMBAT_CHALLENGER_CODENAME',literal=game.player(db,value['challenger'])['name'])
    db.execute('INSERT OR IGNORE INTO gym_notifications VALUES(?,?,?,?,0,?)',(ident,value['opponent'],stamp,notification.SerializeToString(),stamp))
    if redeliver:
        db.execute('UPDATE gym_notifications SET created_ms=?,data=?,state=0,updated_ms=? WHERE id=? AND player_id=?',
            (stamp,notification.SerializeToString(),stamp,ident,value['opponent']))


def login_notifications(game,db,player):
    """A read popup is not consent. Recover it once per new family RPC session."""
    from dittobuffer.core.accounts import principal_session
    session=principal_session.get()
    if not session:return
    db.execute('DELETE FROM pvp_invitation_logins WHERE created_ms<?',(now_ms()-86400000,))
    if not db.execute('INSERT OR IGNORE INTO pvp_invitation_logins VALUES(?,?,?)',(player,session,now_ms())).rowcount:return
    rows=db.execute('SELECT c.* FROM pvp_challenges c JOIN gym_notifications n ON n.id=?||c.id '
        'WHERE c.opponent=? AND c.state IN(2,4) AND c.battle_id IS NULL AND c.expires_ms>? AND n.state=1',
        ('trainer-challenge:',player,now_ms())).fetchall()
    for value in rows:
        if permitted(game,db,value['challenger'],player):
            if value['state']==4:
                # Native will not reopen a popup marked already accepted.
                # No authority exists yet: re-offer explicit consent/selection
                # on this new login, retaining the same ID and original expiry.
                db.execute("UPDATE pvp_challenges SET state=2,party1='[]' WHERE id=?",(value['id'],))
                value=read(db,value['id'],player)
            notify(game,db,value,redeliver=True)


def acknowledge(db,ident):
    db.execute('UPDATE gym_notifications SET state=MAX(state,1),updated_ms=? WHERE id=?',(now_ms(),'trainer-challenge:'+ident))


def generate_combat_challenge_id(game,db,player,body):
    p.GenerateCombatChallengeIdProto.FromString(body)
    ident='pvp-challenge-'+secrets.token_hex(16);stamp=now_ms()
    db.execute('INSERT INTO pvp_challenges(id,challenger,created_ms,expires_ms) VALUES(?,?,?,?)',(ident,player,stamp,stamp+300000))
    return p.GenerateCombatChallengeIdOutProto(result=1,challenge_id=ident)


def create_combat_challenge(game,db,player,body):
    req=p.CreateCombatChallengeProto.FromString(body);value=read(db,req.challenge_id,player)
    if not value or value['challenger']!=player:return p.CreateCombatChallengeOutProto(result=4)
    if value['state']!=1:return p.CreateCombatChallengeOutProto(result=2)
    return p.CreateCombatChallengeOutProto(result=1,challenge=projection(game,db,value))


def open_combat_challenge(game,db,player,body):
    req=p.OpenCombatChallengeProto.FromString(body);value=read(db,req.challenge_id,player)
    if not value:return p.OpenCombatChallengeOutProto(result=3)
    if value['challenger']!=player or value['state']!=1:return p.OpenCombatChallengeOutProto(result=2)
    if req.type!=3:return p.OpenCombatChallengeOutProto(result=13)
    other=pvp.canonical_player(db,req.opponent_nia_id or req.opponent_player_id)
    if not other or other==player or not game.player(db,other):return p.OpenCombatChallengeOutProto(result=13)
    # The friend path requires the real accepted relationship. Nearby QR
    # challenges are not silently treated as friend invitations.
    from dittobuffer.gameplay.friends import pair
    if req.type==3 and not pair(db,player,other):
        return p.OpenCombatChallengeOutProto(result=10)
    if not permitted(game,db,player,other):return p.OpenCombatChallengeOutProto(result=13)
    if value['opponent'] and value['opponent']!=other:return p.OpenCombatChallengeOutProto(result=13)
    if value['league'] and value['league']!=req.combat_league_template_id:return p.OpenCombatChallengeOutProto(result=5)
    if occupied(game,db,player,value['id']) or occupied(game,db,other,value['id']):return p.OpenCombatChallengeOutProto(result=2)
    if req.combat_league_template_id not in pvp.eligible(game,db,other):return p.OpenCombatChallengeOutProto(result=13)
    ids=list(req.attacking_pokemon_id)
    if ids and pvp.party(game,db,player,ids,req.combat_league_template_id) is None:return p.OpenCombatChallengeOutProto(result=4)
    db.execute('UPDATE pvp_challenges SET opponent=?,league=?,state=2,party0=? WHERE id=?',
        (other,req.combat_league_template_id,json.dumps(ids),value['id']))
    notify(game,db,read(db,value['id'],player))
    return p.OpenCombatChallengeOutProto(result=1,challenge=projection(game,db,read(db,value['id'],player)))


def get_combat_challenge(game,db,player,body):
    req=p.GetCombatChallengeProto.FromString(body);value=read(db,req.challenge_id,player)
    if not value:return p.GetCombatChallengeOutProto(result=3)
    if value['state'] in (2,4) and not permitted(game,db,value['challenger'],value['opponent']):
        db.execute('UPDATE pvp_challenges SET state=7 WHERE id=?',(value['id'],));acknowledge(db,value['id'])
        value=read(db,value['id'],player)
    return p.GetCombatChallengeOutProto(result=1,challenge=projection(game,db,ready(game,db,value)))


def accept_combat_challenge(game,db,player,body):
    req=p.AcceptCombatChallengeProto.FromString(body);value=read(db,req.challenge_id,player)
    if not value:return p.AcceptCombatChallengeOutProto(result=3)
    if value['opponent']!=player:return p.AcceptCombatChallengeOutProto(result=10)
    if value['state'] not in (2,4,6):return p.AcceptCombatChallengeOutProto(result=2)
    if value['battle_id']:return p.AcceptCombatChallengeOutProto(result=1,challenge=projection(game,db,value))
    if not permitted(game,db,value['challenger'],player):return p.AcceptCombatChallengeOutProto(result=10)
    if occupied(game,db,player,value['id']):return p.AcceptCombatChallengeOutProto(result=2)
    ids=list(req.attacking_pokemon_id)
    if ids and pvp.party(game,db,player,ids,value['league']) is None:return p.AcceptCombatChallengeOutProto(result=4)
    db.execute('UPDATE pvp_challenges SET state=4,party1=? WHERE id=?',(json.dumps(ids) if ids else value['party1'],value['id']))
    acknowledge(db,value['id'])
    return p.AcceptCombatChallengeOutProto(result=1,challenge=projection(game,db,ready(game,db,read(db,value['id'],player))))


def submit_combat_challenge_pokemons(game,db,player,body):
    req=p.SubmitCombatChallengePokemonsProto.FromString(body);value=read(db,req.challenge_id,player)
    if not value:return p.SubmitCombatChallengePokemonsOutProto(result=3)
    if value['state'] not in (2,4,6):return p.SubmitCombatChallengePokemonsOutProto(result=2)
    side=int(value['opponent']==player);ids=list(req.attacking_pokemon_id)
    if value['battle_id']:
        ok=ids==json.loads(value['party'+str(side)])
        return p.SubmitCombatChallengePokemonsOutProto(result=1 if ok else 2,challenge=projection(game,db,value))
    if pvp.party(game,db,player,ids,value['league']) is None:return p.SubmitCombatChallengePokemonsOutProto(result=4)
    db.execute('UPDATE pvp_challenges SET party'+str(side)+'=? WHERE id=?',(json.dumps(ids),value['id']))
    return p.SubmitCombatChallengePokemonsOutProto(result=1,challenge=projection(game,db,ready(game,db,read(db,value['id'],player))))


def decline_combat_challenge(game,db,player,body):
    req=p.DeclineCombatChallengeProto.FromString(body);value=read(db,req.challenge_id,player)
    if not value or value['opponent']!=player:return p.DeclineCombatChallengeOutProto(result=3)
    if not close_unstarted(game,db,value):return p.DeclineCombatChallengeOutProto(result=2)
    db.execute('UPDATE pvp_challenges SET state=5 WHERE id=?',(value['id'],))
    acknowledge(db,value['id'])
    return p.DeclineCombatChallengeOutProto(result=1)


def cancel_combat_challenge(game,db,player,body):
    req=p.CancelCombatChallengeProto.FromString(body);value=read(db,req.challenge_id,player)
    if not value or value['challenger']!=player:return p.CancelCombatChallengeOutProto(result=3)
    if not close_unstarted(game,db,value):return p.CancelCombatChallengeOutProto(result=2)
    db.execute('UPDATE pvp_challenges SET state=3 WHERE id=?',(value['id'],))
    acknowledge(db,value['id'])
    return p.CancelCombatChallengeOutProto(result=1)


def close_unstarted(game,db,value):
    if value['state'] in (1,2,4):return True
    if value['state']!=6 or not value['battle_id']:return False
    row=db.execute('SELECT state FROM pvp_battles WHERE id=?',(value['battle_id'],)).fetchone()
    if not row:return False
    state=json.loads(row['state']);e.advance(game,state)
    if state['started'] or state['phase'] in (6,7,8):return False
    e.finish(state,None,'invitation_cancelled');e.save(db,state)
    return True


def install(game):
    original_player=game.rpc_get_player.__func__
    def get_player(self,db,player,body):
        login_notifications(self,db,player)
        return original_player(self,db,player,body)
    game.rpc_get_player=MethodType(get_player,game)
    for name in ('generate_combat_challenge_id','create_combat_challenge','open_combat_challenge',
                 'get_combat_challenge','accept_combat_challenge','submit_combat_challenge_pokemons',
                 'decline_combat_challenge','cancel_combat_challenge'):
        setattr(game,'rpc_'+name,MethodType(globals()[name],game))
