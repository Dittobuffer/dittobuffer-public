"""Opt-in native NPC exchange using contributed Gifts or reviewed virtual origins.

All mutations share Game.rpc's authenticated SQLite transaction. This is a private
server exchange pool, not a connection to the production game's participants.
The explicitly configured worldwide extension never creates a real sender/fort.
"""
import secrets
import datetime
from zoneinfo import ZoneInfo
from dittobuffer.gameplay.game import p,now_ms,distance

SCHEMA='''
CREATE TABLE IF NOT EXISTS route_gift_pool(gift INTEGER PRIMARY KEY REFERENCES social_gifts(id),contributor TEXT NOT NULL REFERENCES players(id),route TEXT NOT NULL,contributed_ms INTEGER NOT NULL,withdrawn_by TEXT REFERENCES players(id),withdrawn_ms INTEGER);
CREATE TABLE IF NOT EXISTS route_npc_encounters(id TEXT PRIMARY KEY,attempt TEXT UNIQUE NOT NULL REFERENCES route_attempts(id),player TEXT NOT NULL REFERENCES players(id),day TEXT NOT NULL,current_step TEXT NOT NULL,sent_gift INTEGER REFERENCES social_gifts(id),received_gift INTEGER REFERENCES social_gifts(id),send_response BLOB,open_response BLOB,created_ms INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS route_npc_daily_exchange ON route_npc_encounters(player,day) WHERE sent_gift IS NOT NULL;
'''

def settings(game):
    cfg=game.routes.get('mateo',{})
    if type(cfg.get('enabled',False)) is not bool:raise ValueError('Invalid Mateo enabled flag')
    if cfg.get('enabled'):
        if not game.config.get('social',{}).get('enabled'):raise ValueError('Mateo requires the existing Gift/Postcard system')
        if type(cfg.get('dust')) is not int or not 0<=cfg['dust']<=100000 or not isinstance(cfg.get('items'),dict):raise ValueError('Explicit custom Mateo rewards required')
        fallback=cfg.get('full_bag_stardust')
        if fallback is not None and (type(fallback) is not int or not 1<=fallback<=10000):raise ValueError('Invalid optional Mateo full-bag Stardust reward')
        for item,count in cfg['items'].items():
            if int(item) not in (1,2,3,101,102,103,104,201,202,701,703,705) or type(count) is not int or not 0<count<=1000:raise ValueError('Invalid Mateo item reward')
        from dittobuffer.gameplay.world_postcards import settings as world_settings
        world_settings(game)
    return cfg

def day(game):
    # Reuse the configured social calendar/time zone, including DST boundaries.
    from dittobuffer.gameplay.friends import day as social_day
    return social_day(game,now_ms())

def day_start(game):
    return int(datetime.datetime.combine(datetime.date.fromisoformat(day(game)),datetime.time(),ZoneInfo(game.config.get('social',{}).get('timezone','Europe/Amsterdam'))).timestamp()*1000)

def contribute(game,db,player,gift_id,route_id='',share_name=False):
    """Explicit owner/admin service operation; never called by a normal spin.

    An unsent, owned Gift is reserved once. Sharing a name requires explicit
    consent as well as the owner's existing Postcard preference.
    """
    if not settings(game).get('enabled') or type(share_name) is not bool:raise ValueError('Gift pool disabled/invalid consent')
    row=db.execute('SELECT * FROM social_gifts WHERE id=? AND owner=? AND receiver IS NULL AND deleted=0',(gift_id,player)).fetchone()
    if row is None or db.execute('SELECT 1 FROM route_gift_pool WHERE gift=?',(gift_id,)).fetchone():raise ValueError('Owned unsent Gift required')
    if route_id and not db.execute("SELECT 1 FROM routes WHERE id=? AND status='PUBLISHED'",(route_id,)).fetchone():raise ValueError('Contributed Route provenance must be published')
    from dittobuffer.core.preferences import load
    share=share_name and load(db,player).postcard_trainer_info_sharing_preference==1
    db.execute('UPDATE social_gifts SET deleted=1,share_name=? WHERE id=?',(int(share),gift_id))
    db.execute('INSERT INTO route_gift_pool(gift,contributor,route,contributed_ms) VALUES(?,?,?,?)',(gift_id,player,route_id,now_ms()));game.touch(db,player)

def available(db,player):
    return db.execute('''SELECT g.*,pool.route AS source_route FROM route_gift_pool pool JOIN social_gifts g ON g.id=pool.gift
        WHERE pool.withdrawn_by IS NULL AND pool.contributor<>? AND g.receiver IS NULL AND g.deleted=1
        AND NOT EXISTS(SELECT 1 FROM social_gifts prior WHERE prior.owner=g.owner AND prior.receiver=? AND prior.opened_ms=0)
        ORDER BY pool.contributed_ms,pool.gift LIMIT 1''',(player,player)).fetchone()

def can_exchange(game,db,player):
    from dittobuffer.gameplay.world_postcards import settings as world_settings
    if world_settings(game).get('enabled'):
        return not db.execute("SELECT 1 FROM social_gifts WHERE owner=? AND receiver=? AND opened_ms=0 AND sent_day LIKE 'mateo:%'",(player,player)).fetchone()
    return available(db,player) is not None

def near_end(game,db,attempt):
    from dittobuffer.gameplay.routes import points_of
    loc=db.execute('SELECT * FROM route_locations WHERE player=?',(attempt['player'],)).fetchone()
    return bool(loc and now_ms()-loc['arrival_ms']<=30000 and distance(loc['lat'],loc['lng'],*points_of(attempt)[-1])<=game.routes['end_range_m'])

def attach(game,db,attempt,play):
    if not settings(game).get('enabled') or attempt['state'] not in ('ACTIVE','PAUSED','ELIGIBLE','COMPLETED'):return
    row=db.execute('SELECT * FROM route_npc_encounters WHERE attempt=?',(attempt['id'],)).fetchone()
    if not row:
        if attempt['state']=='PAUSED':return
        if db.execute('SELECT 1 FROM route_npc_encounters WHERE player=? AND day=? AND sent_gift IS NOT NULL',(attempt['player'],day(game))).fetchone():return
        # Only the first completed walk of the day offers a new encounter.
        if db.execute("SELECT 1 FROM route_attempts WHERE player=? AND state='COMPLETED' AND id<>? AND finished_ms>=?",(attempt['player'],attempt['id'],day_start(game))).fetchone():return
        # Native Route setup schedules endpoint NPC creation once, after its
        # own endpoint-loaded/half-distance gate. That callback reads this
        # payload before the server's completion eligibility/proximity gate.
        # Project the stable encounter from Route start; this grants no Gift
        # or exchange eligibility. send() still enforces earned walk, endpoint
        # proximity, expiry, ownership and the daily exchange receipt.
        ident='mateo-'+secrets.token_hex(16)
        db.execute('INSERT INTO route_npc_encounters(id,attempt,player,day,current_step,created_ms) VALUES(?,?,?,?,?,?)',(ident,attempt['id'],attempt['player'],day(game),'exchange',now_ms()))
        from dittobuffer.gameplay.exploration_events import emit
        emit(game,db,attempt['player'],'mateo.available',ident,dict(encounter=ident,attempt=attempt['id'],route=attempt['route']))
        row=db.execute('SELECT * FROM route_npc_encounters WHERE id=?',(ident,)).fetchone()
    if row['current_step']=='done':return
    npc=play.npc_encounter;npc.encounter_id=row['id'];npc.character=94;npc.map_character=16;npc.current_step=row['current_step']
    step=npc.steps.add(step_id='exchange',next_step=['done'])
    # Original server dialogue, not copied event dialogue. Model address is
    # independently verified: dialogue_explorer -> Mateo, traveler -> Mia.
    from dittobuffer.gameplay.world_postcards import settings as world_settings
    text=('Exchange a Route Gift for a virtual city Postcard from around the world.' if world_settings(game).get('enabled') else 'Let us exchange a Gift from this Route with another participating Trainer.') if can_exchange(game,db,attempt['player']) or row['received_gift'] else 'No exchange is available. Please open your pending exchange Gift or try again later.'
    step.dialog.add(text=text,character=94,expression=1,side=1)
    # 0.429.2 tests legacy Dialog.Count before opening its conversation GUI,
    # but passes only NpcDialog to that GUI (NpcEncounterState 0x7E4A79C/
    # 0x7E4A7D8). The two character enums are distinct: Invasion Explorer=94,
    # Quest dialogue Explorer=16. Keep both payloads populated and consistent.
    step.npc_dialog.add(text=text,character=16,expression=1)
    if can_exchange(game,db,attempt['player']) or row['received_gift']:
        step.event.event=2
        if row['send_response']:
            entry=step.event.cached_gift_exchange_entry
            entry.CopyFrom(p.NpcSendGiftOutProto.FromString(row['send_response']).retrived_giftbox)
            # The immutable exchange receipt predates later pin/delete actions.
            # Refresh only this owned read projection, so native recovery shows
            # the current saved state without replaying the Gift mutation.
            entry.gift_box.ClearField('pinned_postcard_id')
            saved=db.execute('SELECT id FROM social_postcards WHERE player_id=? AND gift_id=?',(attempt['player'],row['received_gift'])).fetchone()
            if saved:entry.gift_box.pinned_postcard_id=saved['id']
    else:step.event.event=1
    npc.steps.add(step_id='done').event.event=4

def owned(game,db,player,ident):
    if not settings(game).get('enabled'):return None
    return db.execute('SELECT * FROM route_npc_encounters WHERE id=? AND player=?',(ident,player)).fetchone()

def route_gifts(game,db,player,body):
    req=p.NpcRouteGiftProto.FromString(body);out=p.NpcRouteGiftOutProto();row=owned(game,db,player,req.encounter_id)
    if not row:return out
    attempt=db.execute('SELECT * FROM route_attempts WHERE id=?',(row['attempt'],)).fetchone()
    route=p.SharedRouteProto.FromString(attempt['snapshot'])
    ids={route.start_poi.anchor.fort_id,route.end_poi.anchor.fort_id}
    for gift in db.execute('SELECT * FROM social_gifts WHERE owner=? AND receiver IS NULL AND deleted=0 ORDER BY created_ms,id',(player,)):
        if gift['fort_id'] in ids and not any(v.id==gift['fort_id'] for v in out.route_poi_details):out.route_poi_details.add(id=gift['fort_id'],name=gift['name'],latitude=gift['lat'],longitude=gift['lng'],image_url=gift['image'])
    return out

def update(game,db,player,body):
    req=p.NpcUpdateStateProto.FromString(body);row=owned(game,db,player,req.encounter_id);out=p.NpcUpdateStateOutProto(state=2)
    if not row:return out
    out.current_step=row['current_step']
    if req.set_current_step==row['current_step']:out.state=1;return out
    unavailable=not row['send_response'] and not can_exchange(game,db,player)
    if req.set_current_step!='done' or not (row['open_response'] or unavailable):out.state=3;return out
    db.execute("UPDATE route_npc_encounters SET current_step='done' WHERE id=?",(row['id'],));game.touch(db,player);out.state=1;out.current_step='done';return out

def send(game,db,player,body):
    req=p.NpcSendGiftProto.FromString(body);out=p.NpcSendGiftOutProto(result=2);row=owned(game,db,player,req.encounter_id)
    if not row:return out
    if row['send_response']:
        gift=db.execute('SELECT fort_id,sticker FROM social_gifts WHERE id=?',(row['sent_gift'],)).fetchone()
        if gift['fort_id']==req.fort_id and list(req.stickers_sent)==([gift['sticker']] if gift['sticker'] else []):return p.NpcSendGiftOutProto.FromString(row['send_response'])
        return out
    if row['day']!=day(game) or db.execute('SELECT 1 FROM route_npc_encounters WHERE player=? AND day=? AND sent_gift IS NOT NULL',(player,day(game))).fetchone():out.result=3;return out
    attempt=db.execute('SELECT * FROM route_attempts WHERE id=?',(row['attempt'],)).fetchone()
    if attempt['state'] not in ('ELIGIBLE','COMPLETED') or now_ms()>attempt['expires_ms'] or not near_end(game,db,attempt):return out
    options=route_gifts(game,db,player,p.NpcRouteGiftProto(encounter_id=row['id']).SerializeToString())
    if req.fort_id not in {v.id for v in options.route_poi_details} or len(req.stickers_sent)>1:return out
    from dittobuffer.gameplay.world_postcards import settings as world_settings,create as virtual_gift
    worldwide=world_settings(game).get('enabled',False)
    incoming=None if worldwide else available(db,player)
    if not can_exchange(game,db,player):return out # No Gift is consumed when unavailable.
    gift=db.execute('SELECT * FROM social_gifts WHERE owner=? AND fort_id=? AND receiver IS NULL AND deleted=0 ORDER BY created_ms,id LIMIT 1',(player,req.fort_id)).fetchone()
    sticker=req.stickers_sent[0] if req.stickers_sent else ''
    if sticker and (sticker not in game.sticker_catalog or not db.execute('UPDATE social_stickers SET count=count-1,used=used+1 WHERE player_id=? AND sticker=? AND count>0',(player,sticker)).rowcount):out.result=4;return out
    contribute(game,db,player,gift['id'],attempt['route'])
    db.execute('UPDATE social_gifts SET sticker=? WHERE id=?',(sticker,gift['id']))
    if worldwide:incoming=virtual_gift(game,db,player)
    else:db.execute('UPDATE route_gift_pool SET withdrawn_by=?,withdrawn_ms=? WHERE gift=?',(player,now_ms(),incoming['id']))
    db.execute('UPDATE social_gifts SET receiver=?,sent_ms=?,sent_day=?,deleted=0 WHERE id=?',(player,now_ms(),'mateo:'+row['id'],incoming['id']))
    from dittobuffer.gameplay.gifts import box
    received=db.execute('SELECT * FROM social_gifts WHERE id=?',(incoming['id'],)).fetchone();entry=out.retrived_giftbox;entry.gift_box.CopyFrom(box(db,received))
    if received['share_name']:
        from dittobuffer.gameplay.friends import profile
        entry.sender_profile.CopyFrom(profile(game,db,received['owner']))
    else:
        # Provenance stays in the server audit; no anonymous player is invented.
        for field in ('sender_id','sender_nia_id','sender_codename'):entry.gift_box.ClearField(field)
    entry.source_route_id=incoming['source_route']
    source=db.execute('SELECT proto FROM routes WHERE id=?',(incoming['source_route'],)).fetchone()
    if source:entry.route_name=p.SharedRouteProto.FromString(source[0]).name
    if worldwide:entry.route_name='Virtual World Postcard'
    out.result=1
    db.execute('UPDATE route_npc_encounters SET sent_gift=?,received_gift=?,send_response=? WHERE id=?',(gift['id'],incoming['id'],out.SerializeToString(),row['id']));game.touch(db,player);game.touch(db,incoming['owner'])
    from dittobuffer.gameplay.exploration_events import emit
    emit(game,db,player,'mateo.gift_exchanged',row['id'],dict(encounter=row['id'],outgoing=gift['id'],incoming=incoming['id'],virtual=worldwide))
    return out

def open_gift(game,db,player,body):
    req=p.NpcOpenGiftProto.FromString(body);out=p.NpcOpenGiftOutProto(result=3);row=owned(game,db,player,req.encounter_id)
    if not row:return out
    if row['open_response']:return p.NpcOpenGiftOutProto.FromString(row['open_response'])
    gift=db.execute('SELECT * FROM social_gifts WHERE id=? AND receiver=? AND deleted=0',(row['received_gift'],player)).fetchone()
    if not gift:out.result=4;return out
    if gift['opened_ms']:out.result=5;return out
    cfg=settings(game)
    full=game.bag_count(db,player)+sum(cfg['items'].values())>game.capacity(db,player,'item_capacity')
    # Native OpenSurpriseGift copies the device's "open Gifts with full bag"
    # preference into this flag even when the bag has room (0x7E48344).
    # It permits fallback; it does not demand conversion of ordinary loot.
    if full and (not req.convert_to_stardust or cfg.get('full_bag_stardust') is None):out.result=6;return out
    items={} if full else cfg['items']
    dust=cfg['full_bag_stardust'] if full else cfg['dust']
    for item,count in items.items():
        db.execute('INSERT INTO items VALUES(?,?,?) ON CONFLICT(player_id,item) DO UPDATE SET count=count+excluded.count',(player,int(item),count));out.loot.loot_item.add(item=int(item),count=count)
    if dust:db.execute('UPDATE players SET stardust=stardust+? WHERE id=?',(dust,player));out.loot.loot_item.add(stardust=True,count=dust)
    out.result=1;out.current_step='done'
    db.execute('UPDATE social_gifts SET opened_ms=?,open_day=? WHERE id=?',(now_ms(),'mateo:'+row['day'],gift['id']))
    db.execute("UPDATE route_npc_encounters SET open_response=?,current_step='done' WHERE id=?",(out.SerializeToString(),row['id']));game.touch(db,player);return out
