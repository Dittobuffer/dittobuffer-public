"""Source-gated buddy gifts with explicit local scheduling and reward rules."""
from dittobuffer.gameplay.game import p,now_ms


def rules(config):
    value={'gifts':False,'souvenirs':False,'ribbon':False,'catch_assist':False,'gift_cooldown_seconds':86400,
           'gift_items':{'701':2,'101':1},'souvenir_pool':[3]}|config.get('buddy_perks',{})
    if any(type(value[k]) is not bool for k in ('gifts','souvenirs','ribbon','catch_assist')):raise ValueError('Buddy perk flags must be boolean')
    if 'cameo_test_probability' in value:
        probability=value['cameo_test_probability']
        if not config.get('fixture_player') or not config.get('profile','').startswith('m10-') or type(probability) not in (int,float) or not 0<=probability<=1:
            raise ValueError('Cameo probability overrides require an isolated M10 fixture')
    if value['souvenirs'] and not value['gifts']:raise ValueError('Souvenirs require the buddy gift service')
    if type(value['gift_cooldown_seconds']) is not int or not 3600<=value['gift_cooldown_seconds']<=604800:raise ValueError('Buddy gift cooldown must be 1 hour to 7 days')
    if not value['gift_items'] or any(str(k) not in ('1','2','3','101','201','701') or type(v) is not int or not 1<=v<=20 for k,v in value['gift_items'].items()):raise ValueError('Supported, bounded buddy gift items required')
    if not value['souvenir_pool'] or len(value['souvenir_pool'])!=len(set(value['souvenir_pool'])) or any(type(v) is not int or v not in p.SouvenirTypeId.values() or not v for v in value['souvenir_pool']):raise ValueError('Known souvenir pool required')
    return value


def offer(game,db,player,mon,state):
    from dittobuffer.gameplay.buddy import save
    cfg=rules(game.config)
    if state.get('gift') or state['points']<70 or not cfg['gifts'] or state.get('next_gift_ms',0)>now_ms():return
    sequence=state.get('gift_sequence',0)+1
    # Alternation and rewards are local configuration, not asserted official odds.
    souvenir=state['points']>=150 and cfg['souvenirs'] and sequence%2==0
    gift=p.BuddyGiftProto()
    if souvenir:
        gift.souvenir.souvenir_type_id=cfg['souvenir_pool'][(sequence//2-1)%len(cfg['souvenir_pool'])]
        # Snapshot the last accepted position; never trust a gift-open location.
        fix=db.execute('SELECT lat,lng FROM walking WHERE player_id=?',(player,)).fetchone()
        gift.souvenir.souvenirs_details.add(time_picked_up=now_ms(),latitude=fix['lat'] if fix else 0,longitude=fix['lng'] if fix else 0)
    else:
        for item,count in sorted(cfg['gift_items'].items()):gift.loot_proto.loot_item.add(item=int(item),count=count)
    state.update(gift=gift.SerializeToString().hex(),gift_sequence=sequence,
                 next_gift_ms=now_ms()+cfg['gift_cooldown_seconds']*1000)
    save(game,db,player,mon,state)


def open_gift(game,db,player,body):
    from dittobuffer.gameplay.buddy import active,heart,observed,save
    from dittobuffer.gameplay.trainer import event
    p.OpenBuddyGiftProto.FromString(body);current=active(db,player)
    if not current:return p.OpenBuddyGiftOutProto(result=1)
    _,mon,state=current
    # Opening never creates a new gift: only a prior authoritative offer can be
    # claimed, so new RPC IDs cannot mint inventory by replaying this empty RPC.
    if not state.get('gift'):return p.OpenBuddyGiftOutProto(result=4)
    gift=p.BuddyGiftProto.FromString(bytes.fromhex(state['gift']))
    if gift.HasField('loot_proto'):
        total=game.bag_count(db,player)
        if total+sum(v.count for v in gift.loot_proto.loot_item)>game.capacity(db,player,'item_capacity'):return p.OpenBuddyGiftOutProto(result=5)
        for item in gift.loot_proto.loot_item:
            db.execute('INSERT INTO items VALUES(?,?,?) ON CONFLICT(player_id,item) DO UPDATE SET count=count+excluded.count',(player,item.item,item.count))
        result=2
    else:
        collection=state.setdefault('souvenirs',{});key=str(gift.souvenir.souvenir_type_id)
        previous=p.SouvenirProto.FromString(bytes.fromhex(collection[key])) if key in collection else p.SouvenirProto(souvenir_type_id=int(key))
        previous.souvenirs_details.extend(gift.souvenir.souvenirs_details);collection[key]=previous.SerializeToString().hex()
        result=3
    state.pop('gift');gained=heart(db,player,mon,state,7,6,game=game)
    save(game,db,player,mon,state)
    event(game,db,player,'buddy-gift:'+str(mon.id)+':'+str(state['gift_sequence']),
          'buddy_consumables' if result==2 else 'buddy_souvenir',dict(gift=gift.SerializeToString().hex(),pokemon_id=mon.id))
    return p.OpenBuddyGiftOutProto(result=result,buddy_gift=gift,observed_data=observed(db,player,mon,state),shown_hearts=1 if gained else 0)
