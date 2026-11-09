"""Durable family raid rooms over the existing PvE move/damage engine.

Each trainer retains a private party, AI target and connection generation.
The room owns the countdown, boss HP and ordered event stream. SQLite's existing
BEGIN IMMEDIATE transaction is the sole mutation lock, including across workers.
"""
import json,secrets
from dittobuffer.gameplay.game import p,now_ms,RpcError
from dittobuffer.gameplay import raids, gyms, gym_battle as engine

SCHEMA='''
CREATE TABLE IF NOT EXISTS raid_rooms(id TEXT PRIMARY KEY,seed INTEGER NOT NULL REFERENCES raids(seed),created_ms INTEGER NOT NULL,start_ms INTEGER NOT NULL,end_ms INTEGER NOT NULL,hp INTEGER NOT NULL,status INTEGER NOT NULL DEFAULT 1,clock_ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS raid_rooms_seed ON raid_rooms(seed,status);
CREATE TABLE IF NOT EXISTS raid_room_members(room_id TEXT NOT NULL REFERENCES raid_rooms(id),player_id TEXT NOT NULL REFERENCES players(id),attempt_id TEXT UNIQUE NOT NULL,joined_ms INTEGER NOT NULL,damage INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(room_id,player_id));
CREATE TABLE IF NOT EXISTS raid_room_events(serial INTEGER PRIMARY KEY AUTOINCREMENT,room_id TEXT NOT NULL REFERENCES raid_rooms(id),source TEXT NOT NULL,event BLOB NOT NULL,UNIQUE(room_id,source));
'''

def available(db):return db.execute("SELECT 1 FROM sqlite_master WHERE name='raid_rooms'").fetchone() is not None
def room_for(db,ident):
    if not available(db):return None
    return db.execute('SELECT r.* FROM raid_rooms r JOIN raid_room_members m ON m.room_id=r.id WHERE m.attempt_id=?',(ident,)).fetchone()
def native_id(db,ident):
    room=room_for(db,ident) if db else None
    return room['id'] if room else ident
def individual_id(db,native,player):
    row=db.execute('SELECT attempt_id FROM raid_room_members WHERE room_id=? AND player_id=?',(native,player)).fetchone()
    return row[0] if row else native

def team_contribution(db,attempt):
    """One shared result for every viewer, derived from committed boss damage."""
    room=room_for(db,attempt)
    if not room:return None
    damage={1:0,2:0,3:0}
    for row in db.execute('SELECT t.team,SUM(m.damage) AS damage FROM raid_room_members m JOIN trainer_teams t ON t.player_id=m.player_id WHERE m.room_id=? GROUP BY t.team',(room['id'],)):
        if row['team'] in damage:damage[row['team']]=max(0,row['damage'])
    total=sum(damage.values())
    return {team:amount/total if total else 0 for team,amount in damage.items()}

def admission(game,db,row,player):
    m=raids.member(db,row['seed'],player);old=room_for(db,m['battle_id']) if m else None
    if old and old['status']==1 and not m['left_lobby']:
        b=raids.current_battle(db,m);state=json.loads(b['state']) if b else None
        if not state or state['status'] in (1,3) and not state.get('surrendered'):return True
    room=db.execute('SELECT * FROM raid_rooms WHERE seed=? AND status=1 AND start_ms>? ORDER BY created_ms LIMIT 1',(row['seed'],now_ms())).fetchone()
    if not room:return True
    count=db.execute('SELECT COUNT(*) FROM raid_room_members c JOIN raid_members m ON m.seed=? AND m.player_id=c.player_id WHERE c.room_id=? AND m.left_lobby=0',(row['seed'],room['id'])).fetchone()[0]
    return count<game.raids['participation_limit']

def open_lobby(game,db,row,m):
    old=room_for(db,m['battle_id']);stamp=now_ms()
    if old:advance(game,db,old['id'],stamp)
    old=room_for(db,m['battle_id'])
    b=raids.current_battle(db,m);state=json.loads(b['state']) if b else None
    if state and state['status']==2:return m
    # Existing members can reconnect/relobby inside their live room; a departed
    # member cannot replace an active party by leaving and opening another room.
    if old and old['status']==1:
        if not (state and (state.get('surrendered') or state['status']==4)) and (not m['left_lobby'] or stamp<old['start_ms']):
            m=raids._open_lobby(game,db,row,m)
            db.execute('UPDATE raid_members SET left_lobby=0 WHERE seed=? AND player_id=?',(row['seed'],m['player_id']))
            return raids.member(db,row['seed'],m['player_id'])
    m=raids._open_lobby(game,db,row,m)
    # _open_lobby reuses an unstarted attempt. A closed room needs a new key.
    if old and m['battle_id']==(b['id'] if b else m['battle_id']):
        ident='raid-'+str(row['seed'])+'-'+secrets.token_hex(8)
        db.execute('UPDATE raid_members SET battle_id=? WHERE seed=? AND player_id=?',(ident,row['seed'],m['player_id']))
        m=raids.member(db,row['seed'],m['player_id'])
    room=db.execute('SELECT * FROM raid_rooms WHERE seed=? AND status=1 AND start_ms>? ORDER BY created_ms LIMIT 1',(row['seed'],stamp)).fetchone()
    if not room:
        ident='family-raid-'+secrets.token_hex(16);start=stamp+raids.rules(game,row)['lobby_seconds']*1000
        end=min(row['end_ms'],start+raids.rules(game,row)['battle_seconds']*1000)
        hp=p.PokemonProto.FromString(row['pokemon']).max_stamina
        db.execute('INSERT INTO raid_rooms VALUES(?,?,?,?,?,?,1,?)',(ident,row['seed'],stamp,start,end,hp,stamp))
        room=db.execute('SELECT * FROM raid_rooms WHERE id=?',(ident,)).fetchone()
    db.execute('INSERT INTO raid_room_members(room_id,player_id,attempt_id,joined_ms) VALUES(?,?,?,?) ON CONFLICT(room_id,player_id) DO UPDATE SET attempt_id=excluded.attempt_id',(room['id'],m['player_id'],m['battle_id'],stamp))
    db.execute('UPDATE raid_members SET lobby_start_ms=? WHERE seed=? AND player_id=?',(room['created_ms'],row['seed'],m['player_id']))
    return raids.member(db,row['seed'],m['player_id'])

def lobby(game,db,row,m):
    room=room_for(db,m['battle_id'])
    if not room:raise RpcError('Raid room membership missing')
    db.execute('INSERT OR IGNORE INTO raid_tokens VALUES(?,?,?)',(row['seed'],m['player_id'],secrets.token_hex(32)))
    token=db.execute('SELECT token FROM raid_tokens WHERE seed=? AND player_id=?',(row['seed'],m['player_id'])).fetchone()[0]
    b=raids.current_battle(db,m);state=json.loads(b['state']) if b else {}
    selection=m['lobby_start_ms']+raids.rules(game,row)['relobby_seconds']*1000 if state.get('relobby_prepared') else room['start_ms']
    out=p.LobbyProto(lobby_id=raids.lobby_code(m,db),creation_ms=room['created_ms'],player_join_end_ms=room['start_ms']-1000,pokemon_selection_end_ms=selection,
        raid_battle_start_ms=room['start_ms'],raid_battle_end_ms=room['end_ms'],raid_battle_id=room['id'],battle_plfe_instance=1,rvn_connection=p.RvnConnectionProto(base_uri='/raid/',token=token),rvn_version=1)
    for peer in db.execute('SELECT c.* FROM raid_room_members c JOIN raid_members m ON m.seed=? AND m.player_id=c.player_id AND m.battle_id=c.attempt_id WHERE c.room_id=? AND m.left_lobby=0 ORDER BY c.joined_ms,c.player_id',(row['seed'],room['id'])):
        has_mega=db.execute('SELECT 1 FROM mega_active WHERE player_id=? AND end_ms>?',(peer['player_id'],now_ms())).fetchone() is not None
        out.players.add(player_id=peer['player_id'][:20],trainer_public_profile=gyms.public(game,db,peer['player_id']),has_active_mega_evolved_pokemon=has_mega)
        out.total_mega_evolved_count_in_lobby+=int(has_mega)
    out.total_player_count_in_lobby=len(out.players)
    if out.players:out.owner_nickname=out.players[0].trainer_public_profile.name
    return out

def attach(game,db,b,state):
    room=room_for(db,b['id'])
    if not room or room['status']!=1 or now_ms()>room['end_ms']:raise RpcError('Raid room expired')
    if now_ms()>room['start_ms']+15000 and not state.get('room_id'):raise RpcError('Raid admission deadline passed')
    if not state.get('room_id'):
        delta=room['start_ms']-state['start']
        state.update(start=room['start_ms'],end=room['end_ms'],clock=max(room['start_ms'],state['clock']),room_id=room['id'],ai_due=max(now_ms(),room['start_ms'])+1500)
        state['defender']['hp']=room['hp']
        for value in state['party']:value['ready']=max(value['ready'],room['start_ms'])
        state['events']=[];engine.event(state,6,room['start_ms'],joined_player=raids.participant(game,db,b['player_id'],state))
        db.execute('UPDATE raid_battles SET end_ms=? WHERE id=?',(room['end_ms'],b['id']))
    state['defender']['hp']=room['hp']
    state['room_participants']=db.execute('SELECT COUNT(*) FROM raid_room_members WHERE room_id=?',(room['id'],)).fetchone()[0]
    raids.persist(db,b['id'],state)

def advance_for(game,db,ident):
    room=room_for(db,ident)
    if room:advance(game,db,room['id'],now_ms())

def advance(game,db,ident,stamp):
    room=db.execute('SELECT * FROM raid_rooms WHERE id=?',(ident,)).fetchone()
    if not room or room['status']!=1:return
    rows=db.execute('SELECT b.* FROM raid_battles b JOIN raid_room_members m ON m.attempt_id=b.id WHERE m.room_id=? ORDER BY b.player_id',(ident,)).fetchall()
    states=[(b,json.loads(b['state'])) for b in rows];hp=room['hp'];until=min(stamp,room['end_ms'])
    from dittobuffer.gameplay.friends import raid_bonus
    for b,s in states:s['peer_friendship_boost']=raid_bonus(game,db,b['player_id'],[other['player_id'] for other,_ in states])[2]
    # Walk global event boundaries; tie-break by immutable player ID. Poll order
    # cannot move one actor past another actor's earlier pending damage window.
    steps=0
    while hp>0:
        active=[(b,s) for b,s in states if s['status']==1]
        if not active:break
        due=min(min(s['ai_due'],min((v['at'] for v in s['pending']),default=room['end_ms']+1)) for b,s in active)
        at=min(until,due)
        for b,s in active:
            s['defender']['hp']=hp;before=hp
            if s.get('pending_swap') is not None:engine.process_actions(game,s,[],max(s['clock'],at))
            engine.advance(game,s,at);hp=s['defender']['hp']
            if before>hp:db.execute('UPDATE raid_room_members SET damage=damage+? WHERE attempt_id=?',(before-hp,b['id']))
            if hp<=0:break
        steps+=1
        if at>=until or steps>20000:break
    outcome=2 if hp<=0 else 4 if stamp>=room['end_ms'] else 1
    count=len(states)
    apply_mega_boosts(game,states)
    winners=[b['player_id'] for b,s in states if not s.get('surrendered') and db.execute('SELECT damage FROM raid_room_members WHERE attempt_id=?',(b['id'],)).fetchone()[0]>0]
    for b,s in states:
        s['defender']['hp']=hp;s['room_participants']=count
        damage=db.execute('SELECT damage FROM raid_room_members WHERE attempt_id=?',(b['id'],)).fetchone()[0]
        if outcome==2:s.update(status=2 if damage>0 and not s.get('surrendered') else 4,clock=until,results='')
        elif outcome==4 and s['status']!=2:s.update(status=4,clock=until,results='')
        # Freeze all rewards before the first completion increments friendship.
        # Crossing a milestone must not make the second claimant get extra balls.
        if outcome==2 and s['status']==2:s['friendship_reward_bonus']=list(raid_bonus(game,db,b['player_id'],winners)[:2])
        raids.checkpoint(game,db,b,s)
        if s['status']!=1:raids.finish_battle(game,db,b,s)
        raids.persist(db,b['id'],s)
        record_events(db,b,s)
    db.execute('UPDATE raid_rooms SET hp=?,status=?,clock_ms=MAX(clock_ms,?) WHERE id=?',(hp,outcome,until,ident))

def apply_mega_boosts(game,states):
    # Supplied GM multipliers, highest eligible active ally only; no self-boost,
    # stacking, fainted/sideline grant or client-supplied combat multiplier.
    from dittobuffer.gameplay.mega import _SETTINGS,DATA,combat_rules
    active=[]
    for b,s in states:
        value=s['party'][s['active']];mon=engine.mon(value)
        if s['status']==1 and value['hp']>0 and mon.pokemon_display.current_temp_evolution:
            rules=combat_rules(mon,game.species[mon.pokemon_id]);types=DATA.get('bonus_types',{}).get(str(mon.pokemon_id),rules['types'])
            active.append((b['player_id'],types))
    for b,s in states:
        s['peer_mega_boost']={str(kind):max([1]+[float(_SETTINGS.attack_boost_from_mega_same_type if kind in types else _SETTINGS.attack_boost_from_mega_different_type) for player,types in active if player!=b['player_id']]) for kind in range(1,19)}

def record_events(db,b,state):
    from dittobuffer.gameplay.raid_transport import snapshot
    frame=snapshot(b,state)
    for index,event in enumerate(frame.battle_state.events):
        # START/END drive the native director globally. A peer's party defeat
        # must never end the recipient's still-live battle or replay its intro.
        # These two events are projected separately for each authenticated viewer.
        if event.type in (1,18):continue
        # Stable source cursors survive retries, restarts and new party generations.
        source=f'{b["id"]}:{state.get("generation",0)}:{index}:{event.battle_end.result if event.type==18 else 0}'
        db.execute('INSERT OR IGNORE INTO raid_room_events(room_id,source,event) VALUES(?,?,?)',(state['room_id'],source,event.SerializeToString()))

def projection(game,db,b,state):
    from dittobuffer.gameplay.raid_transport import snapshot,pokemon
    frame=snapshot(b,state);s=frame.battle_state;ident=state['room_id']
    own_start=p.BattleEventProto();own_start.CopyFrom(s.events[0])
    own_end=p.BattleEventProto();terminal=state['status']!=1
    if terminal:own_end.CopyFrom(s.events[-1])
    peers=db.execute('SELECT b.* FROM raid_battles b JOIN raid_room_members m ON m.attempt_id=b.id WHERE m.room_id=? ORDER BY b.player_id',(ident,)).fetchall()
    for peer in peers:
        value=json.loads(peer['state']);actor=peer['player_id'][:20];a=s.actors[actor]
        a.id=actor;a.type=1;a.team=1;a.party_id=actor;a.active_pokemon_id=engine.mon(value['party'][value['active']]).id
        a.pokemon_roster[:]=[engine.mon(v).id for v in value['party']];s.party_member_count[actor]=1
        for mon in value['party']:s.pokemon[engine.mon(mon).id].CopyFrom(pokemon(mon))
    s.active_actor_count=len(peers);s.team_actor_count[1]=len(peers)
    del s.events[:]
    own_start.serial=1;s.events.add().CopyFrom(own_start);last_serial=1
    for row in db.execute('SELECT serial,event FROM raid_room_events WHERE room_id=? ORDER BY serial',(ident,)):
        event=s.events.add();event.ParseFromString(row['event']);event.serial=row['serial']+1;last_serial=event.serial
    if terminal:own_end.serial=last_serial+1;s.events.add().CopyFrom(own_end)
    return frame

def expire(game,db):
    for row in db.execute('SELECT id FROM raid_rooms WHERE status=1 AND end_ms<=?',(now_ms(),)).fetchall():advance(game,db,row['id'],now_ms())
