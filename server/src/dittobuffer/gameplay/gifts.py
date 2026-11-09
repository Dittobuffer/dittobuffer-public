"""Published-stop Gifts; mutations run in the authenticated RPC transaction."""
import secrets,hashlib
from contextlib import closing
from dittobuffer.gameplay.game import p,now_ms
from dittobuffer.gameplay import friends as f
SCHEMA='''
CREATE TABLE IF NOT EXISTS social_gifts(id INTEGER PRIMARY KEY,owner TEXT NOT NULL REFERENCES players(id),receiver TEXT REFERENCES players(id),fort_id TEXT NOT NULL,name TEXT NOT NULL,image TEXT NOT NULL,lat REAL NOT NULL,lng REAL NOT NULL,created_ms INTEGER NOT NULL,sent_ms INTEGER NOT NULL DEFAULT 0,sent_day TEXT NOT NULL DEFAULT '',opened_ms INTEGER NOT NULL DEFAULT 0,open_day TEXT NOT NULL DEFAULT '',sticker TEXT NOT NULL DEFAULT '',share_name INTEGER NOT NULL DEFAULT 0,deleted INTEGER NOT NULL DEFAULT 0,response BLOB);
CREATE UNIQUE INDEX IF NOT EXISTS social_gifts_unopened ON social_gifts(owner,receiver) WHERE receiver IS NOT NULL AND opened_ms=0;
CREATE UNIQUE INDEX IF NOT EXISTS social_gifts_daily_send ON social_gifts(owner,receiver,sent_day) WHERE receiver IS NOT NULL;
CREATE TABLE IF NOT EXISTS social_stickers(player_id TEXT NOT NULL REFERENCES players(id),sticker TEXT NOT NULL,count INTEGER NOT NULL CHECK(count>=0),used INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(player_id,sticker));
CREATE TABLE IF NOT EXISTS social_postcards(id TEXT PRIMARY KEY,player_id TEXT NOT NULL REFERENCES players(id),gift_id INTEGER NOT NULL REFERENCES social_gifts(id),data BLOB NOT NULL,UNIQUE(player_id,gift_id));
'''
def install(game):
    with closing(game.db.connect()) as db:
        db.executescript(SCHEMA)
        from dittobuffer.gameplay.world_postcards import SCHEMA as virtual_schema
        db.executescript(virtual_schema)
        with db:
            db.execute('BEGIN IMMEDIATE')
            if 'send_response' not in {r[1] for r in db.execute('PRAGMA table_info(social_gifts)')}:
                db.execute('ALTER TABLE social_gifts ADD COLUMN send_response BLOB')
def award(game,db,player,fort):
    if not f.enabled(game):return None
    cfg=game.config.get('social',{})
    if db.execute('SELECT COUNT(*) FROM social_gifts WHERE owner=? AND receiver IS NULL AND deleted=0',(player,)).fetchone()[0]>=cfg.get('gift_capacity',20):return None
    if secrets.randbelow(1000000)>=cfg.get('gift_rate',.5)*1000000:return None
    metadata=game.world.details(fort['id']) if game.world.path else None
    image=metadata['url'] if metadata else 'http://127.0.0.1:8080/assets/dittobuffer-stop.png'
    identifier=secrets.randbits(62) or 1
    db.execute('INSERT INTO social_gifts(id,owner,fort_id,name,image,lat,lng,created_ms) VALUES(?,?,?,?,?,?,?,?)',(identifier,player,fort['id'],fort['name'],image,fort['lat'],fort['lng'],now_ms()))
    game.touch(db,player);return box(db,db.execute('SELECT * FROM social_gifts WHERE id=?',(identifier,)).fetchone())
def box(db,row):
    name=db.execute('SELECT name FROM players WHERE id=?',(row['owner'],)).fetchone()[0]
    value=p.GiftBoxProto(giftbox_id=row['id'],sender_id=row['owner'],sender_nia_id=row['owner'],sender_codename=name,receiver_id=row['receiver'] or '',fort_id=row['fort_id'],fort_lat=row['lat'],fort_lng=row['lng'],fort_name=row['name'],fort_image_url=row['image'],creation_timestamp=row['created_ms'],sent_timestamp=row['sent_ms'],stickers_sent=[row['sticker']] if row['sticker'] else [],share_trainer_info_with_postcard=bool(row['share_name']))
    from dittobuffer.gameplay.world_postcards import origin,anonymize
    return anonymize(value) if origin(db,row['id']) else value
def details(db,row,viewer=None):
    name=db.execute('SELECT name FROM players WHERE id=?',(row['owner'],)).fetchone()[0]
    value=p.GiftBoxDetailsProto(giftbox_id=row['id'],sender_id=row['owner'],sender_nia_account_id=row['owner'],sender_codename=name,receiver_id=row['receiver'] or '',fort_id=row['fort_id'],fort_name=row['name'],fort_lat=row['lat'],fort_lng=row['lng'],fort_image_url=row['image'],creation_timestamp=row['created_ms'],sent_timestamp=row['sent_ms'])
    # The supplied exporter emitted an empty message for this nested enum.
    # Native field 16 is a varint: ShareWithFriends=1, DoNotShare=2.
    value.ParseFromString(value.SerializeToString()+f.rawint(16,1 if row['share_name'] else 2))
    if row['sticker']:value.stickers_sent.add(sticker_id=row['sticker'])
    if viewer:
        saved=db.execute('SELECT id,data FROM social_postcards WHERE player_id=? AND gift_id=?',(viewer,row['id'])).fetchone()
        if saved:
            value.pinned_postcard_id=saved['id']
            value.pin_update_timestamp_ms=p.PostcardDisplayProto.FromString(saved['data']).creation_timestamp_ms
    from dittobuffer.gameplay.world_postcards import origin
    if origin(db,row['id']) or viewer==row['receiver'] and row['sent_day'].startswith('mateo:') and not row['share_name']:
        for field in ('sender_id','sender_nia_account_id','sender_codename'):value.ClearField(field)
    return value
def inventory(game,db,player):
    if not f.enabled(game):return []
    out=p.HoloInventoryItemProto();out.gift_boxes.SetInParent()
    for row in db.execute('SELECT * FROM social_gifts WHERE owner=? AND receiver IS NULL AND deleted=0 ORDER BY id',(player,)):out.gift_boxes.gifts.add().CopyFrom(box(db,row))
    values=[out]
    for row in db.execute('SELECT * FROM social_stickers WHERE player_id=?',(player,)):
        item=p.HoloInventoryItemProto();item.sticker.CopyFrom(p.StickerProto(sticker_id=row['sticker'],count=row['count'],used=row['used']));values.append(item)
    return values
def attach_friend(db,me,other,value):
    for row in db.execute("SELECT * FROM social_gifts WHERE receiver=? AND owner=? AND opened_ms=0 AND sent_day NOT LIKE 'mateo:%'",(me,other)):value.giftbox_details.add().CopyFrom(details(db,row,me))
def one_way(db,sender,receiver,pair):
    value=p.OneWaySharedFriendshipDataProto(remote_trade_eligible=bool(pair['remote_credit']),best_friends_plus_count=pair['remote_progress'])
    for row in db.execute("SELECT * FROM social_gifts WHERE owner=? AND receiver=? AND opened_ms=0 AND sent_day NOT LIKE 'mateo:%'",(sender,receiver)):value.giftbox_details.add().CopyFrom(details(db,row,receiver))
    return value
def can_send(game,db,me,other):
    if not f.pair(db,me,other):return 3
    if db.execute('SELECT 1 FROM social_gifts WHERE owner=? AND receiver=? AND sent_day=?',(me,other,f.day(game))).fetchone():return 5
    if db.execute('SELECT 1 FROM social_gifts WHERE owner=? AND receiver=? AND opened_ms=0',(me,other)).fetchone():return 6
    return 1
def send(game,db,player,body):
    req=p.SendGiftProto.FromString(body)
    if not 0<req.giftbox_id<2**63:return p.SendGiftOutProto(result=4)
    if len(req.stickers_sent)>1:return p.SendGiftOutProto(result=8)
    sticker=req.stickers_sent[0].sticker_id if req.stickers_sent else ''
    previous=db.execute('SELECT * FROM social_gifts WHERE id=? AND owner=?',(req.giftbox_id,player)).fetchone()
    if previous and previous['send_response']:
        if previous['receiver']==req.player_id and previous['sticker']==sticker:return p.SendGiftOutProto.FromString(previous['send_response'])
        return p.SendGiftOutProto(result=4)
    if sticker and game.config.get('social',{}).get('sticker_catalog') and sticker not in game.sticker_catalog:return p.SendGiftOutProto(result=8)
    result=can_send(game,db,player,req.player_id)
    if result!=1:return p.SendGiftOutProto(result=result)
    if not 0<req.giftbox_id<2**63:return p.SendGiftOutProto(result=4)
    row=db.execute('SELECT * FROM social_gifts WHERE id=? AND owner=? AND receiver IS NULL AND deleted=0',(req.giftbox_id,player)).fetchone()
    if row is None:return p.SendGiftOutProto(result=4)
    if len(req.stickers_sent)>1:return p.SendGiftOutProto(result=8)
    sticker=req.stickers_sent[0].sticker_id if req.stickers_sent else ''
    if sticker and not db.execute('UPDATE social_stickers SET count=count-1,used=used+1 WHERE player_id=? AND sticker=? AND count>0',(player,sticker)).rowcount:return p.SendGiftOutProto(result=8)
    from dittobuffer.core.preferences import load
    share=load(db,player).postcard_trainer_info_sharing_preference==1
    db.execute('UPDATE social_gifts SET receiver=?,sent_ms=?,sent_day=?,sticker=?,share_name=? WHERE id=?',(req.player_id,now_ms(),f.day(game),sticker,int(share),row['id']))
    xp=game.config.get('social',{}).get('gift_send_xp',200);db.execute('UPDATE players SET xp=xp+? WHERE id=?',(xp,player))
    for ident in (player,req.player_id):game.touch(db,ident)
    out=p.SendGiftOutProto(result=1,awarded_xp=xp)
    db.execute('UPDATE social_gifts SET send_response=? WHERE id=?',(out.SerializeToString(),row['id']))
    from dittobuffer.gameplay.research import progress
    progress(game,db,player,'gift_send')
    return out
def open_gift(game,db,player,body):
    req=p.OpenGiftProto.FromString(body)
    if not f.pair(db,player,req.player_id):return p.OpenGiftOutProto(result=6)
    if not 0<req.giftbox_id<2**63:return p.OpenGiftOutProto(result=5)
    row=db.execute("SELECT * FROM social_gifts WHERE id=? AND receiver=? AND owner=? AND sent_day NOT LIKE 'mateo:%'",(req.giftbox_id,player,req.player_id)).fetchone()
    if row is None:return p.OpenGiftOutProto(result=5)
    if row['response']:return p.OpenGiftOutProto.FromString(row['response'])
    cfg=game.config.get('social',{})
    if db.execute('SELECT COUNT(*) FROM social_gifts WHERE receiver=? AND open_day=?',(player,f.day(game))).fetchone()[0]>=cfg.get('gift_open_daily',20):return p.OpenGiftOutProto(result=4)
    rewards=cfg.get('gift_items',{'1':5,'101':1})
    total=game.bag_count(db,player)
    if total+sum(rewards.values())>game.capacity(db,player,'item_capacity'):return p.OpenGiftOutProto(result=3)
    out=p.OpenGiftOutProto(result=1)
    for item,count in rewards.items():
        db.execute('INSERT INTO items VALUES(?,?,?) ON CONFLICT(player_id,item) DO UPDATE SET count=count+excluded.count',(player,int(item),count));out.items.loot_item.add(item=int(item),count=count)
    dust=cfg.get('gift_dust',100);db.execute('UPDATE players SET stardust=stardust+? WHERE id=?',(dust,player));out.items.loot_item.add(stardust=True,count=dust)
    from dittobuffer.gameplay.stickers import grant as grant_sticker
    for ident,count in cfg.get('gift_stickers',{}).items():
        granted=grant_sticker(game,db,player,ident,count)
        if granted:out.items.loot_item.add(sticker_id=ident,count=granted)
    if cfg.get('gift_eggs',False):
        from dittobuffer.gameplay.eggs import award as award_egg,ensure
        ensure(game,db,player);egg=award_egg(game,db,player,{'id':row['fort_id']})
        if egg:out.egg_pokemon.CopyFrom(egg)
    f.interact(game,db,player,req.player_id,'gift','gift:'+str(row['id']))
    out.updated_friendship_data.CopyFrom(f.level_data(game,f.pair(db,player,req.player_id)));out.friend_profile.CopyFrom(f.profile(game,db,req.player_id))
    db.execute('UPDATE social_gifts SET opened_ms=?,open_day=?,response=? WHERE id=?',(now_ms(),f.day(game),out.SerializeToString(),row['id']))
    from dittobuffer.gameplay.research import progress
    progress(game,db,player,'gift_open')
    for ident in (player,req.player_id):game.touch(db,ident)
    return out
def get_details(game,db,player,body):
    req=p.GetGiftBoxDetailsProto.FromString(body);out=p.GetGiftBoxDetailsOutProto(result=1)
    if len(req.giftbox_id)>100 or any(not 0<v<2**63 for v in req.giftbox_id):return p.GetGiftBoxDetailsOutProto(result=3)
    for ident in dict.fromkeys(req.giftbox_id):
        for row in db.execute('SELECT * FROM social_gifts WHERE id=? AND (owner=? OR receiver=?) AND deleted=0',(ident,player,player)):out.gift_boxes.add().CopyFrom(details(db,row,player))
    if not out.gift_boxes:out.result=3
    return out
def delete(game,db,player,body):
    req=p.DeleteGiftFromInventoryProto.FromString(body)
    ids=list(req.giftbox_id)
    if not 1<=len(ids)<=100 or len(set(ids))!=len(ids) or any(not 0<v<2**63 for v in ids):return p.DeleteGiftFromInventoryOutProto(result=3)
    for ident in ids:
        if not db.execute('SELECT 1 FROM social_gifts WHERE id=? AND owner=? AND receiver IS NULL AND deleted=0',(ident,player)).fetchone():return p.DeleteGiftFromInventoryOutProto(result=3)
    db.executemany('UPDATE social_gifts SET deleted=1 WHERE id=? AND owner=?',[(ident,player) for ident in ids]);game.touch(db,player)
    return p.DeleteGiftFromInventoryOutProto(result=1)
def postcard(game,db,player,body):
    req=p.CreatePostcardProto.FromString(body)
    if not 0<req.giftbox_id<2**63:return p.CreatePostcardOutProto(result=3)
    row=db.execute('SELECT * FROM social_gifts WHERE id=? AND (owner=? OR receiver=?)',(req.giftbox_id,player,player)).fetchone()
    if row is None:return p.CreatePostcardOutProto(result=3)
    old=db.execute('SELECT data FROM social_postcards WHERE player_id=? AND gift_id=?',(player,row['id'])).fetchone()
    if old:return p.CreatePostcardOutProto(result=1,postcard=p.PostcardDisplayProto.FromString(old[0]))
    # A saved card remains available, but a new pin must precede sending/opening.
    from dittobuffer.gameplay.world_postcards import origin
    virtual=origin(db,row['id'])
    if virtual and row['receiver']!=player:return p.CreatePostcardOutProto(result=3)
    if row['deleted'] or (row['owner']==player and row['receiver'] is not None and not virtual) or (row['receiver']==player and row['opened_ms']):return p.CreatePostcardOutProto(result=3)
    if db.execute('SELECT COUNT(*) FROM social_postcards WHERE player_id=?',(player,)).fetchone()[0]>=game.config.get('social',{}).get('postcard_capacity',350):return p.CreatePostcardOutProto(result=5)
    value=p.PostcardDisplayProto(postcard_id=secrets.token_hex(16),fort_id=row['fort_id'],fort_lat=row['lat'],fort_lng=row['lng'],creation_timestamp_ms=now_ms(),image_url=row['image'],fort_name=row['name'],giftbox_id=row['id'],source_giftbox_id=row['id'],sticker_id=[row['sticker']] if row['sticker'] else [])
    value.postcard_source=3 if virtual else 1 if player==row['owner'] else 2 if row['share_name'] else 3
    if row['share_name']:
        value.postcard_creator_id=row['owner'];value.postcard_creator_nia_account_id=row['owner'];value.postcard_creator_codename=game.player(db,row['owner'])['name']
    db.execute('INSERT INTO social_postcards VALUES(?,?,?,?)',(value.postcard_id,player,row['id'],value.SerializeToString()));game.touch(db,player)
    out=p.CreatePostcardOutProto(result=1,postcard=value)
    from dittobuffer.gameplay.research import progress
    progress(game,db,player,'postcard_pin')
    from dittobuffer.gameplay.vivillon import pin
    medal=pin(game,db,player,row,virtual)
    if medal:out.butterfly_collector_updated_region.CopyFrom(medal)
    from dittobuffer.gameplay.exploration_events import emit
    emit(game,db,player,'postcard.pinned',value.postcard_id,dict(postcard=value.postcard_id,gift=row['id'],virtual=bool(virtual),region=medal.region if medal else 0))
    return out

def mementos(game,db,player,body):
    req=p.GetMementoListProto.FromString(body)
    if any(v!=0 for v in req.memento_types):return p.GetMementoListOutProto(status=3)
    rows=db.execute('SELECT data FROM social_postcards WHERE player_id=? ORDER BY id',(player,)).fetchall()
    # A hash from an unfiltered list must not suppress a different time window.
    scope=str((req.time_bound_start_ms,req.time_bound_end_ms,tuple(req.memento_types))).encode()
    digest=hashlib.sha256(scope+b''.join(r[0] for r in rows)).hexdigest()
    out=p.GetMementoListOutProto(status=4 if req.memento_list_hash==digest else 1,memento_list_hash=digest)
    if out.status==1:
        for row in rows:
            card=p.PostcardDisplayProto.FromString(row[0])
            if req.time_bound_start_ms and card.creation_timestamp_ms<req.time_bound_start_ms:continue
            if req.time_bound_end_ms and card.creation_timestamp_ms>req.time_bound_end_ms:continue
            out.mementos.add(memento_type=0,latitude=card.fort_lat,longitude=card.fort_lng,added_timestamp_ms=card.creation_timestamp_ms,memento_hash=hashlib.sha256(row[0]).hexdigest(),postcard_display=card)
    return out
def update_postcard(game,db,player,body):
    req=p.UpdatePostcardProto.FromString(body);row=db.execute('SELECT data FROM social_postcards WHERE id=? AND player_id=?',(req.postcard_id,player)).fetchone()
    if row is None:return p.UpdatePostcardOutProto(result=2)
    card=p.PostcardDisplayProto.FromString(row[0]);card.favorite=req.favorite
    db.execute('UPDATE social_postcards SET data=? WHERE id=? AND player_id=?',(card.SerializeToString(),req.postcard_id,player));game.touch(db,player)
    return p.UpdatePostcardOutProto(result=1,postcard=card)
def delete_postcards(game,db,player,body):
    req=p.DeletePostcardsProto.FromString(body)
    if not 1<=len(req.postcard_ids)<=350 or len(set(req.postcard_ids))!=len(req.postcard_ids):return p.DeletePostcardsOutProto(result=2)
    cards=[]
    for ident in req.postcard_ids:
        row=db.execute('SELECT data FROM social_postcards WHERE id=? AND player_id=?',(ident,player)).fetchone()
        if row is None:return p.DeletePostcardsOutProto(result=2)
        card=p.PostcardDisplayProto.FromString(row[0])
        if card.favorite:return p.DeletePostcardsOutProto(result=3)
        cards.append(card)
    out=p.DeletePostcardsOutProto(result=1)
    for card in cards:
        db.execute('DELETE FROM social_postcards WHERE id=? AND player_id=?',(card.postcard_id,player));out.postcards.add().CopyFrom(card)
    game.touch(db,player);return out
