"""Owned persistent GBL rewards using the existing canonical catch transaction."""
import random
import secrets
from types import MethodType
from dittobuffer.gameplay.game import p,now_ms,RpcError
from dittobuffer.gameplay import pvp
from dittobuffer.gameplay.species import make_pokemon,pool_rules,choose_gender


def league_at_reward(db,player,season,set_id,slot):
    row=db.execute('SELECT b.league FROM pvp_set_battles s JOIN pvp_battles b ON b.id=s.battle_id '
        'WHERE s.player_id=? AND s.season=? AND s.set_id=? AND s.finish=0 ORDER BY s.rowid LIMIT 1 OFFSET ?',
        (player,season,set_id,slot)).fetchone()
    return row['league'] if row else ''


def encounter(game,db,player,body):
    req=p.VsSeekerRewardEncounterProto.FromString(body);slot=req.win_index
    value=pvp.profile(game,db,player);key=(player,game.pvp['season'],value['set_id'],slot)
    definition=pvp.reward_table(game,db,player).get(str(slot+1),{})
    if not 0<=slot<game.pvp['set_size'] or not definition.get('species'):
        return p.VsSeekerRewardEncounterOutProto(result=3)
    if db.execute('SELECT 1 FROM pvp_claims WHERE player_id=? AND season=? AND set_id=? AND slot=?',key).fetchone():
        return p.VsSeekerRewardEncounterOutProto(result=2)
    wins=db.execute('SELECT COUNT(*) FROM pvp_set_battles WHERE player_id=? AND season=? AND set_id=? AND finish=0',key[:3]).fetchone()[0]
    if wins<slot+1:return p.VsSeekerRewardEncounterOutProto(result=3)
    from dittobuffer.gameplay.eggs import count
    used=db.execute('SELECT COUNT(*) FROM pokemon WHERE player_id=?',(player,)).fetchone()[0]+count(db,player)
    if used>=game.capacity(db,player,'pokemon_capacity'):return p.VsSeekerRewardEncounterOutProto(result=4)
    row=db.execute('SELECT spawn_id FROM pvp_reward_encounters WHERE player_id=? AND season=? AND set_id=? AND slot=?',key).fetchone()
    if row:ident=row['spawn_id']
    else:
        ident=secrets.randbits(60)+1
        while db.execute('SELECT 1 FROM spawns WHERE id=?',(ident,)).fetchone():ident=secrets.randbits(60)+1
        rng=random.Random(secrets.token_bytes(16));species=definition['species'];level=definition.get('level',20)
        rules=pool_rules(game,definition)
        mon=make_pokemon(p,rules,species,level,[rng.randint(10,15) for _ in range(3)],False,choose_gender(game,species,rng))
        mon.id=ident;mon.creation_time_ms=now_ms()
        mon.origin_detail.vs_seeker_detail.season=key[1]
        mon.origin_detail.vs_seeker_detail.league=league_at_reward(db,*key)
        # The native GBL catch request uses an empty spawn GUID. Only this
        # player's authenticated reward RPC creates an encounter ownership row.
        location=(db.execute('SELECT lat,lng FROM social_locations WHERE player_id=?',(player,)).fetchone()
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='social_locations'").fetchone() else None)
        lat,lng=(location['lat'],location['lng']) if location else (0,0)
        db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',(ident,'',species,lat,lng,2**62))
        db.execute('INSERT INTO spawn_attributes VALUES(?,?,?)',(ident,mon.SerializeToString(),level))
        db.execute('INSERT INTO pvp_reward_encounters VALUES(?,?,?,?,?)',(*key,ident))
    db.execute('INSERT OR IGNORE INTO encounters(player_id,spawn_id) VALUES(?,?)',(player,ident))
    mon=p.PokemonProto.FromString(db.execute('SELECT data FROM spawn_attributes WHERE spawn_id=?',(ident,)).fetchone()[0])
    from dittobuffer.gameplay.items import probabilities,active_berry
    return p.VsSeekerRewardEncounterOutProto(result=1,encounter_id=ident,pokemon=mon,
        capture_probability=probabilities(game,active_berry(db,player,ident)),active_item=active_berry(db,player,ident))


def install(game):
    game.rpc_vs_seeker_reward_encounter=MethodType(encounter,game)
    original_encounter=game.rpc_encounter.__func__
    original_catch=game.rpc_catch_pokemon.__func__
    def wild(self,db,player,body):
        req=p.EncounterProto.FromString(body)
        if db.execute('SELECT 1 FROM pvp_reward_encounters WHERE spawn_id=?',(req.encounter_id,)).fetchone():
            raise RpcError('GBL rewards require the authenticated earned-reward encounter')
        return original_encounter(self,db,player,body)
    def caught(self,db,player,body):
        req=p.CatchPokemonProto.FromString(body)
        row=db.execute('SELECT * FROM pvp_reward_encounters WHERE player_id=? AND spawn_id=?',(player,req.encounter_id)).fetchone()
        if row:
            # The GBL response has no spawn-point field; native reward catches
            # supply a client sentinel rather than the stored wild-spawn GUID.
            # Ownership above is authoritative. Adapt only this owned reward
            # to the canonical catch path, leaving other encounter validation.
            spawn=db.execute('SELECT spawnpoint FROM spawns WHERE id=?',(req.encounter_id,)).fetchone()
            if spawn:
                req.spawn_point_guid=spawn['spawnpoint']
                body=req.SerializeToString()
        out=original_catch(self,db,player,body)
        if row and out.status==1:
            owned=db.execute('SELECT id,data FROM pokemon WHERE player_id=? AND spawn_id=?',(player,req.encounter_id)).fetchone()
            if owned:
                mon=p.PokemonProto.FromString(owned['data'])
                mon.origin_detail.vs_seeker_detail.season=row['season']
                mon.origin_detail.vs_seeker_detail.league=league_at_reward(db,player,row['season'],row['set_id'],row['slot'])
                data=mon.SerializeToString()
                if data!=owned['data']:db.execute('UPDATE pokemon SET data=? WHERE id=?',(data,owned['id']))
            db.execute('INSERT OR IGNORE INTO pvp_claims VALUES(?,?,?,?,?,?)',
                (player,row['season'],row['set_id'],row['slot'],p.LootProto().SerializeToString(),now_ms()))
        return out
    game.rpc_encounter=MethodType(wild,game)
    game.rpc_catch_pokemon=MethodType(caught,game)
