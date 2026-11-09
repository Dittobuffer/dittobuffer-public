"""Native social proxy, authorized by Game.rpc's principal and transaction.

One canonical pair owns progression. No caller-supplied identity is trusted.
Opaque fields below are from the 0.429.2 dump's nested protobuf definitions;
the supplied Python exporter left those nested types empty.
"""
import datetime
import json
import secrets
import re
from contextlib import closing
from zoneinfo import ZoneInfo
from protocol.opaque import byte_field, varint
from dittobuffer.gameplay.game import p, now_ms

SCHEMA='''
CREATE TABLE IF NOT EXISTS social_codes(player_id TEXT PRIMARY KEY REFERENCES players(id),code TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS social_requests(sender TEXT NOT NULL REFERENCES players(id),receiver TEXT NOT NULL REFERENCES players(id),created_ms INTEGER NOT NULL,state TEXT NOT NULL,PRIMARY KEY(sender,receiver),CHECK(sender<>receiver));
CREATE TABLE IF NOT EXISTS social_pairs(a TEXT NOT NULL REFERENCES players(id),b TEXT NOT NULL REFERENCES players(id),active INTEGER NOT NULL DEFAULT 1,created_ms INTEGER NOT NULL,points INTEGER NOT NULL DEFAULT 0,last_day TEXT NOT NULL DEFAULT '',lucky INTEGER NOT NULL DEFAULT 0,remote_credit INTEGER NOT NULL DEFAULT 0,remote_progress INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(a,b),CHECK(a<b));
CREATE TABLE IF NOT EXISTS social_history(id TEXT PRIMARY KEY,a TEXT NOT NULL,b TEXT NOT NULL,kind TEXT NOT NULL,at_ms INTEGER NOT NULL,points INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS social_milestones(a TEXT NOT NULL,b TEXT NOT NULL,player_id TEXT NOT NULL REFERENCES players(id),milestone INTEGER NOT NULL,xp INTEGER NOT NULL,claimed INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(a,b,player_id,milestone));
CREATE TABLE IF NOT EXISTS social_settings(player_id TEXT PRIMARY KEY REFERENCES players(id),data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS social_nicknames(player_id TEXT NOT NULL,friend_id TEXT NOT NULL,nickname TEXT NOT NULL,PRIMARY KEY(player_id,friend_id));
CREATE TABLE IF NOT EXISTS social_locations(player_id TEXT PRIMARY KEY REFERENCES players(id),lat REAL NOT NULL,lng REAL NOT NULL,accuracy REAL NOT NULL,fix_ms INTEGER NOT NULL,arrival_ms INTEGER NOT NULL);
'''
MILESTONES=[0,1,7,30,90,180]

def enabled(game):return bool(game.config.get('social',{}).get('enabled',False))

def fixture_location_scope(game):
    """Explicit isolated rehearsal storage; never enables walking credit."""
    from dittobuffer.gameplay.game import ROOT
    profile=game.config.get('profile','');path=game.db.path.resolve()
    if profile.startswith('m16-') and path.is_relative_to(ROOT/'builds'):return 'm16'
    if profile=='m182-power-spots' and path.is_relative_to(ROOT/'.private'):
        parts=path.relative_to(ROOT/'.private').parts
        if len(parts)==3 and parts[0].startswith('m182-') and parts[1].startswith('fixture-'):
            return 'm182'
    # Explicit private acceptance storage; this enables proximity, never walking.
    if profile=='m25-isolated' and path==(ROOT/'.private/m251/trainer.sqlite3').resolve():
        return 'm251'
    return None
def templates(config):
    if not config.get('social',{}).get('enabled'):return []
    result=[];cfg=config.get('social',{})
    from dittobuffer.gameplay.stickers import templates as sticker_templates
    result.extend(sticker_templates(config))
    for i,points in enumerate(MILESTONES):
        t=p.GameMasterClientTemplateProto(template_id='FRIENDSHIP_LEVEL_'+str(i))
        t.friendship_milestone_settings.CopyFrom(p.FriendshipLevelMilestoneSettingsProto(min_points_to_reach=points,relative_points_to_reach=points-(MILESTONES[i-1] if i else 0),milestone_xp_reward=cfg.get('milestone_xp',[0,3000,10000,50000,100000,200000])[i],attack_bonus_percentage=cfg.get('attack_bonus',[0,.03,.05,.07,.1,.12])[i],raid_ball_bonus=[0,0,1,2,4,4][i],trading_discount=[0,0,.2,.92,.96,.96][i],unlocked_trading=[1] if i==0 else list(range(1,8))))
        result.append(t)
    gift=p.GameMasterClientTemplateProto(template_id='ITEM_FRIEND_GIFT_BOX')
    gift.item.CopyFrom(p.ItemSettingsProto(unique_id=1405,item_type=18,category=17,drop_trainer_level=1,ignore_inventory_space=True,item_cap=cfg.get('gift_capacity',20)))
    result.append(gift)
    lucky=p.GameMasterClientTemplateProto(template_id='LUCKY_POKEMON_SETTINGS')
    lucky.lucky_pokemon_settings.power_up_stardust_discount_percent=.5
    result.append(lucky)
    gifting=p.GameMasterClientTemplateProto(template_id='GIFTING_SETTINGS')
    # Retain the verified Gift selection path. These flags alone do not fix
    # the remaining blank native reward reveal (see MILESTONE_16_1.md).
    gifting.gifting_settings.flow_polish_enabled=False
    gifting.gifting_settings.multi_major_reward_ui_enabled=True
    result.append(gifting)
    postcard=p.GameMasterClientTemplateProto(template_id='LIBRE_POSTCARD_COLLECTION_SETTINGS')
    postcard.postcard_collection_settings.enabled=True
    postcard.postcard_collection_settings.background_pattern_tile_scale=1
    postcard.postcard_collection_settings.postcard_ui_element_color='#426B6B'
    postcard.postcard_collection_settings.postcard_ui_text_stroke_color='#FFFFFF'
    result.append(postcard)
    return result
def install(game):
    if not enabled(game):return
    from dittobuffer.gameplay.game import ROOT
    defaults=json.loads((ROOT/'src/dittobuffer/gameplay/social-rules.json').read_text())
    cfg=game.config.get('social',{})
    merged=defaults|cfg;cfg.clear();cfg.update(merged)
    from dittobuffer.gameplay.stickers import catalog as sticker_catalog
    game.sticker_catalog=sticker_catalog(game.config)
    sticker_rewards=cfg.get('gift_stickers',{})
    if not isinstance(sticker_rewards,dict) or any(ident not in game.sticker_catalog or type(count) is not int or not 1<=count<=game.sticker_catalog[ident]['max_count'] for ident,count in sticker_rewards.items()):raise ValueError('Invalid reviewed Gift sticker rewards')
    if type(cfg.get('friend_capacity',550)) is not int or not 1<=cfg.get('friend_capacity',550)<=1000:raise ValueError('Invalid friend capacity')
    ZoneInfo(cfg.get('timezone','Europe/Amsterdam'))
    import math
    for key in ('gift_capacity','gift_open_daily','gift_send_xp','gift_dust','postcard_capacity','trade_range_m','location_max_age_seconds','trade_daily','special_trade_daily'):
        if type(cfg[key]) is not int or not 0<=cfg[key]<=1000000:raise ValueError('Invalid social rule '+key)
    for key in ('gift_rate','lucky_rate','lucky_friend_rate'):
        if not isinstance(cfg[key],(int,float)) or not math.isfinite(cfg[key]) or not 0<=cfg[key]<=1:raise ValueError('Invalid social probability '+key)
    for event in cfg.get('friendship_events',[]):
        if not isinstance(event,dict) or set(event)!={'start_day','end_day','kinds','points','daily_cap'}:raise ValueError('Invalid friendship event')
        start=datetime.date.fromisoformat(event['start_day']);end=datetime.date.fromisoformat(event['end_day'])
        if start>end or not event['kinds'] or set(event['kinds'])-{'gift','trade','raid'}:raise ValueError('Invalid friendship event range/actions')
        if any(type(event[k]) is not int or not 1<=event[k]<=100 for k in ('points','daily_cap')):raise ValueError('Invalid friendship event points/cap')
    for key in ('attack_bonus','trade_cost_multipliers'):
        if len(cfg[key])!=6 or any(not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in cfg[key]):raise ValueError('Invalid social multiplier '+key)
    if len(cfg['milestone_xp'])!=6 or any(type(v) is not int or not 0<=v<=1000000 for v in cfg['milestone_xp']):raise ValueError('Invalid milestone XP')
    if set(cfg['trade_costs'])!=set(defaults['trade_costs']) or any(type(v) is not int or not 0<=v<=1000000000 for v in cfg['trade_costs'].values()):raise ValueError('Invalid trade costs')
    if len(cfg['candy_distance_m'])!=2 or any(type(v) is not int or v<0 for v in cfg['candy_distance_m']) or cfg['candy_distance_m']!=sorted(cfg['candy_distance_m']):raise ValueError('Invalid trade candy distances')
    if any(type(b.get('days')) is not int or b['days']<0 or not isinstance(b.get('rate'),(int,float)) or not 0<=b['rate']<=1 for b in cfg['lucky_age_rates']):raise ValueError('Invalid local Lucky age bands')
    if not isinstance(cfg['gift_items'],dict) or any(not str(k).isdigit() or int(k) not in {1,2,3,101,102,103,104,201,202,701} or type(v) is not int or not 1<=v<=100 for k,v in cfg['gift_items'].items()):raise ValueError('Invalid gift reward table')
    if cfg['allow_fixture_location']:
        scope=fixture_location_scope(game)
        if scope is None:raise ValueError('Social fixture location requires isolated rehearsal storage')
        if scope in ('m182','m251'):
            sites=game.config.get('geographic_fixture_locations')
            if not isinstance(sites,list) or not sites or any(not isinstance(site,dict) or
                type(site.get('lat')) not in (int,float) or type(site.get('lng')) not in (int,float) or
                not math.isfinite(site['lat']) or not math.isfinite(site['lng']) or
                not -85<=site['lat']<=85 or not -180<=site['lng']<=180 for site in sites):
                raise ValueError('M18.2 fixture requires explicit finite geographic sites')
    with closing(game.db.connect()) as db:db.executescript(SCHEMA)
    from dittobuffer.gameplay.gifts import install as install_gifts
    install_gifts(game)
    from dittobuffer.gameplay.trading import install as install_trading
    install_trading(game)
    from dittobuffer.gameplay.remote_trading import install as install_remote
    install_remote(game)
def day(game,stamp=None):return datetime.datetime.fromtimestamp((now_ms() if stamp is None else stamp)/1000,ZoneInfo(game.config.get('social',{}).get('timezone','Europe/Amsterdam'))).date().isoformat()
def observe(game,db,player,fix):
    """Current position for proximity; never grants walking distance or rewards."""
    if not enabled(game) or not fix:return
    if game.config.get('social',{}).get('trace_fixture_location'):
        from dittobuffer.gameplay.game import ROOT
        if game.config.get('profile','').startswith('m16-') and game.db.path.resolve().is_relative_to(ROOT/'builds'):
            import logging
            logging.info('M16 fixture fix source=%s provider=%s accuracy=%s age_s=%s',fix.get('source'),fix.get('provider'),fix.get('accuracy'),None if fix.get('time') is None else round(now_ms()/1000-float(fix['time']),1))
    import math
    try:lat,lng,accuracy=float(fix['lat']),float(fix['lng']),float(fix['accuracy']);stamp=round(float(fix['time'])*1000);provider=int(fix['provider'])
    except (ValueError,TypeError,KeyError,OverflowError):return
    now=now_ms();cfg=game.config.get('social',{})
    if not all(math.isfinite(v) for v in (lat,lng,accuracy)) or not -85<=lat<=85 or not -180<=lng<=180 or not 0<accuracy<=25 or provider!=3 or not now-30000<=stamp<=now+2000:return
    source=fix.get('source')
    scope=fixture_location_scope(game) if source=='debug' and cfg.get('allow_fixture_location') is True else None
    fixture=scope=='m16' or scope in ('m182','m251') and any(
        abs(lat-site['lat'])<=1e-7 and abs(lng-site['lng'])<=1e-7
        for site in game.config.get('geographic_fixture_locations',[]))
    if source!='device' and not fixture:return
    db.execute('INSERT INTO social_locations VALUES(?,?,?,?,?,?) ON CONFLICT(player_id) DO UPDATE SET lat=excluded.lat,lng=excluded.lng,accuracy=excluded.accuracy,fix_ms=excluded.fix_ms,arrival_ms=excluded.arrival_ms WHERE excluded.fix_ms>social_locations.fix_ms',(player,lat,lng,accuracy,stamp,now))
def pair(db,a,b,active=True):
    return db.execute('SELECT * FROM social_pairs WHERE a=? AND b=?'+(' AND active=1' if active else ''),tuple(sorted((a,b)))).fetchone()
def partners(db,player):return [r['b'] if r['a']==player else r['a'] for r in db.execute('SELECT a,b FROM social_pairs WHERE active=1 AND (a=? OR b=?)',(player,player))]
def code(db,player,regenerate=False):
    old=db.execute('SELECT code FROM social_codes WHERE player_id=?',(player,)).fetchone()
    if old and not regenerate:return old[0]
    while True:
        value=''.join(str(secrets.randbelow(10)) for _ in range(12))
        if not db.execute('SELECT 1 FROM social_codes WHERE code=?',(value,)).fetchone():break
    db.execute('INSERT INTO social_codes VALUES(?,?) ON CONFLICT(player_id) DO UPDATE SET code=excluded.code',(player,value));return value
def summary(game,db,player):
    row=game.player(db,player)
    from dittobuffer.gameplay.gyms import team
    return p.PlayerSummaryProto(player_id=player,nia_account_id=player,codename=row['name'],display_name=row['name'],level=game.player_stats(row).level,team=str(team(db,player)['team']),public_data=profile(game,db,player).SerializeToString())
def profile(game,db,player):
    row=game.player(db,player)
    from dittobuffer.gameplay.gyms import team
    from dittobuffer.gameplay.buddy import active
    buddy=active(db,player)
    # Supply a real prop identifier alongside the current neutral appearance.
    ball=buddy[1].pokeball if buddy and buddy[1].pokeball else 1
    value=p.PlayerPublicProfileProto(name=row['name'],player_id=player,level=game.player_stats(row).level,experience=row['xp'],team=team(db,player)['team'],caught_pokemon=row['captures'],km_walked=row['km_walked'],neutral_avatar=game.appearance(db,player),buddy_pokeball=ball)
    # Match GetPlayer's legacy-message presence. The native neutral pose path
    # retains this legacy object in BuildAvatarParameters as well.
    value.avatar.SetInParent()
    return value
def level(points):return max(i for i,n in enumerate(MILESTONES) if points>=n)
def level_data(game,row):
    value=level(row['points']);previous=MILESTONES[value];following=MILESTONES[value+1] if value<5 else previous+90
    progress=row['points']-previous if value<5 else row['remote_progress']
    return p.FriendshipLevelDataProto(bucket=row['points'],points_earned_today=int(row['last_day']==day(game)),current_friendship_milestone=value+1,awarded_friendship_milestone=value+1,points_toward_next_milestone=progress,next_friendship_milestone_progress_percentage=progress/(following-previous),last_milestone_award_points=previous,has_progressed_forever_friends=value==5)
def interact(game,db,a,b,kind,key):
    row=pair(db,a,b)
    if row is None:return False
    a,b=sorted((a,b));stamp=now_ms()
    if not db.execute('INSERT OR IGNORE INTO social_history VALUES(?,?,?,?,?,0)',(key,a,b,kind,stamp)).rowcount:return False
    today=day(game);award=1;cap=1
    for event in game.config.get('social',{}).get('friendship_events',[]):
        if event['start_day']<=today<=event['end_day'] and kind in event['kinds']:
            award=max(award,event['points']);cap=max(cap,event['daily_cap'])
    start=datetime.datetime.combine(datetime.date.fromisoformat(today),datetime.time(),ZoneInfo(game.config.get('social',{}).get('timezone','Europe/Amsterdam')))
    stop=start+datetime.timedelta(days=1)
    earned=db.execute('SELECT COALESCE(SUM(points),0) FROM social_history WHERE a=? AND b=? AND at_ms>=? AND at_ms<?',(a,b,int(start.timestamp()*1000),int(stop.timestamp()*1000))).fetchone()[0]
    # Historical pair state can predate its event ledger. Preserve its daily cap.
    if row['last_day']==today:earned=max(1,earned)
    award=min(award,max(0,cap-earned))
    if not award:return False
    points=row['points']+award;credit=row['remote_credit'];progress=row['remote_progress']
    if row['points']<180<=points:credit=1
    progress+=max(0,points-max(180,row['points']))
    if progress>=90:credit=1;progress%=90
    lucky=row['lucky']
    if row['last_day']!=today and row['points']>=90 and secrets.randbelow(1000000)<game.config.get('social',{}).get('lucky_friend_rate',0)*1000000:lucky=1
    db.execute('UPDATE social_pairs SET points=?,last_day=?,lucky=?,remote_credit=?,remote_progress=? WHERE a=? AND b=?',(points,day(game),lucky,credit,progress,a,b))
    db.execute('UPDATE social_history SET points=? WHERE id=?',(award,key))
    for tier in range(level(row['points'])+1,level(points)+1):
        # These explicit local defaults can be replaced with reviewed GM values.
        xp=game.config.get('social',{}).get('milestone_xp',[0,3000,10000,50000,100000,200000])[tier]
        for player in (a,b):db.execute('INSERT OR IGNORE INTO social_milestones VALUES(?,?,?,?,?,0)',(a,b,player,tier+1,xp))
    for player in (a,b):game.touch(db,player)
    return True
def raid_bonus(game,db,player,peers):
    if not enabled(game):return 0,0,1
    tiers=[level(row['points']) for other in peers if other!=player and (row:=pair(db,player,other))]
    tier=max(tiers,default=0)
    return tier+1,[0,0,1,2,4,4][tier],1+game.config.get('social',{}).get('attack_bonus',[0,.03,.05,.07,.1,.12])[tier]
def rawstr(n,s):return byte_field(n,s.encode())
def rawint(n,i):return varint(n<<3)+varint(i)
def relationship_data(game,db,me,other):
    row=pair(db,me,other)
    value=p.FriendshipDataProto(friendship_level_data=level_data(game,row),is_lucky=bool(row['lucky']))
    pending=db.execute('SELECT MIN(milestone) FROM social_milestones WHERE a=? AND b=? AND player_id=? AND claimed=0',(row['a'],row['b'],me)).fetchone()[0]
    if pending:value.friendship_level_data.awarded_friendship_milestone=pending-1
    nick=db.execute('SELECT nickname FROM social_nicknames WHERE player_id=? AND friend_id=?',(me,other)).fetchone()
    if nick:value.nickname=nick[0]
    from dittobuffer.gameplay.gifts import attach_friend
    attach_friend(db,me,other,value)
    return value

def claim(game,db,player,body):
    req=p.GetFriendshipRewardsProto.FromString(body);row=pair(db,player,req.friend_id)
    if row is None:return p.GetFriendshipRewardsOutProto(result=3)
    rewards=db.execute('SELECT SUM(xp),COUNT(*) FROM social_milestones WHERE a=? AND b=? AND player_id=? AND claimed=0',(row['a'],row['b'],player)).fetchone()
    if not rewards[1]:return p.GetFriendshipRewardsOutProto(result=4,friend_id=req.friend_id)
    db.execute('UPDATE social_milestones SET claimed=1 WHERE a=? AND b=? AND player_id=?',(row['a'],row['b'],player))
    db.execute('UPDATE players SET xp=xp+? WHERE id=?',(rewards[0],player));game.touch(db,player)
    acknowledge_claimed_notifications(db,player)
    return p.GetFriendshipRewardsOutProto(result=1,xp_reward=rewards[0],friend_id=req.friend_id)
def acknowledge_claimed_notifications(db,player):
    # Claiming is the authoritative acknowledgement. Animation interruption
    # must not leave a reward notification actionable on every cold start.
    for row in db.execute('SELECT a,b,milestone FROM social_milestones WHERE player_id=? AND claimed=1',(player,)):
        ident='friendship:'+row['a']+':'+row['b']+':'+player+':'+str(row['milestone'])
        db.execute('UPDATE gym_notifications SET state=MAX(state,1),updated_ms=? WHERE id=? AND player_id=? AND state=0',(now_ms(),ident,player))
def pending_notifications(game,db,player):
    if not enabled(game):return
    acknowledge_claimed_notifications(db,player)
    for row in db.execute('SELECT m.*,p.created_ms FROM social_milestones m JOIN social_pairs p ON p.a=m.a AND p.b=m.b WHERE m.player_id=? AND m.claimed=0 AND p.active=1',(player,)):
        other=row['b'] if row['a']==player else row['a'];stamp=now_ms()
        ident='friendship:'+row['a']+':'+row['b']+':'+player+':'+str(row['milestone'])
        notification=p.ClientInbox.Notification(notification_id=ident,category='FRIENDSHIP_MILESTONE_REWARD',create_timestamp_ms=stamp,expire_time_ms=stamp+30*86400000,labels=[1,2,3])
        details=p.FriendshipMilestoneRewardProto(friend_id=other,nia_account_id=other,friendship_milestone=row['milestone'])
        notification.variables.add(name='FRIENDSHIP_MILESTONE_REWARD_DETAILS',byte_value=details.SerializeToString())
        notification.variables.add(name='FRIEND_CODENAME',literal=game.player(db,other)['name'])
        db.execute('INSERT OR IGNORE INTO gym_notifications VALUES(?,?,?,?,0,?)',(ident,player,stamp,notification.SerializeToString(),stamp))
def friend_wire(game,db,me,other):
    row=pair(db,me,other);s=summary(game,db,other)
    shared=p.TwoWaySharedFriendshipDataProto(is_lucky=bool(row['lucky'])).SerializeToString()
    from dittobuffer.gameplay.gifts import one_way
    outgoing=one_way(db,me,other,row).SerializeToString();incoming=one_way(db,other,me,row).SerializeToString()
    data=byte_field(1,shared)+rawint(2,now_ms())+byte_field(3,outgoing)+byte_field(4,incoming)
    return rawstr(1,other)+rawstr(2,s.codename)+rawstr(3,s.team)+rawint(4,row['points'])+byte_field(5,relationship_data(game,db,me,other).SerializeToString())+rawint(6,now_ms())+rawint(7,row['created_ms'])+byte_field(10,data)+rawint(11,3)+rawstr(12,other)+rawstr(13,s.codename)+rawint(14,1)
def detail(game,db,me,other):
    row=pair(db,me,other)
    from dittobuffer.gameplay.gifts import one_way
    from dittobuffer.gameplay.buddy import active
    visible=p.PlayerFriendDisplayProto()
    buddy=active(db,other)
    if buddy:
        _,mon,_=buddy
        visible.buddy.CopyFrom(mon.pokemon_display)
        visible.buddy_display_pokemon_id=mon.pokemon_id
        visible.buddy_pokemon_nickname=mon.nickname
        visible.buddy_height_m=mon.height_m
        visible.buddy_weight_kg=mon.weight_kg
        visible.buddy_was_traded=bool(mon.traded_time_ms)
    # Share the current buddy appearance, never private catches or location.
    return p.FriendDetailsProto(player=summary(game,db,other),friend_visible_data=visible.SerializeToString(),score=row['points'],data_with_me=relationship_data(game,db,me,other).SerializeToString(),online_status=3,created_ms=row['created_ms'],shared_data=p.TwoWaySharedFriendshipDataProto(is_lucky=bool(row['lucky'])).SerializeToString(),data_from_me=one_way(db,me,other,row).SerializeToString(),data_to_me=one_way(db,other,me,row).SerializeToString())
def full(game,db,player):return len(partners(db,player))>=game.config.get('social',{}).get('friend_capacity',550)
def target(request):return getattr(request,'nia_account_id','') or getattr(request,'player_id','')
def proxy(game,db,player,request):
    if not enabled(game):return p.ProxyResponseProto(status=3)
    db.execute("UPDATE social_requests SET state='expired' WHERE state='pending' AND created_ms<?",(now_ms()-7*86400000,))
    action=request.action;body=request.payload;out=None
    if len(body)>65536:return p.ProxyResponseProto(status=7)
    if action==10013:
        req=p.GetFriendCodeProto.FromString(body);out=p.GetFriendCodeOutProto(result=1,friend_code=code(db,player,req.force_generate_code))
    elif action==10000:
        req=p.SearchPlayerProto.FromString(body)
        row=db.execute('SELECT player_id FROM social_codes WHERE code=?',(req.friend_code,)).fetchone() if re.fullmatch('[0-9]{12}',req.friend_code) else None
        out=p.SearchPlayerOutProto(result=1 if row else 3)
        if row:out.player.CopyFrom(summary(game,db,row[0]))
    elif action==10002:
        req=p.SendFriendInviteProto.FromString(body);other=None
        if re.fullmatch('[0-9]{12}',req.friend_code):
            r=db.execute('SELECT player_id FROM social_codes WHERE code=?',(req.friend_code,)).fetchone();other=r[0] if r else None
        elif target(req) and game.player(db,target(req)):other=target(req)
        # Never allow injected initial friendship score or custom public data.
        result=1
        if other is None:result=4
        elif other==player:result=9
        elif pair(db,player,other):result=3
        elif full(game,db,player):result=7
        elif full(game,db,other):result=11
        elif db.execute("SELECT 1 FROM social_requests WHERE sender=? AND receiver=? AND state='pending'",(player,other)).fetchone():result=8
        elif db.execute("SELECT 1 FROM social_requests WHERE sender=? AND receiver=? AND state='pending'",(other,player)).fetchone():result=10
        elif not req.read_only:db.execute("INSERT INTO social_requests VALUES(?,?,?,'pending') ON CONFLICT(sender,receiver) DO UPDATE SET created_ms=excluded.created_ms,state='pending'",(player,other,now_ms()))
        out=p.SendFriendInviteOutProto(result=result)
    elif action in (10003,10004,10005,10009):
        typ={10003:'CancelFriendInvite',10004:'AcceptFriendInvite',10005:'DeclineFriendInvite',10009:'RemoveFriend'}[action]
        # RemoveFriend's request was omitted by the supplied exporter; its
        # player_id/nia_account_id fields match CancelFriendInvite.
        req=getattr(p,('CancelFriendInvite' if action==10009 else typ)+'Proto').FromString(body);other=target(req);result=1
        if other==player or not game.player(db,other):result=3
        elif action==10009:
            if not pair(db,player,other):result=3
            else:db.execute('UPDATE social_pairs SET active=0 WHERE a=? AND b=?',tuple(sorted((player,other))))
        else:
            sender,receiver=(player,other) if action==10003 else (other,player)
            row=db.execute("SELECT * FROM social_requests WHERE sender=? AND receiver=? AND state='pending'",(sender,receiver)).fetchone()
            if row is None:result=3
            elif action==10004 and (full(game,db,player) or full(game,db,other)):result=7
            else:
                db.execute('UPDATE social_requests SET state=? WHERE sender=? AND receiver=?',({10003:'cancelled',10004:'accepted',10005:'declined'}[action],sender,receiver))
                if action==10004:db.execute('INSERT INTO social_pairs(a,b,created_ms) VALUES(?,?,?) ON CONFLICT(a,b) DO UPDATE SET active=1',(*sorted((player,other)),now_ms()))
        out=getattr(p,typ+'OutProto')(result=result)
        if result==1:
            for ident in (player,other):game.touch(db,ident)
            if action==10004:out.friend.CopyFrom(summary(game,db,other))
    elif action in (10007,10008):
        incoming=action==10008;out=(p.GetIncomingFriendInvitesOutProto if incoming else p.GetOutgoingFriendInvitesOutProto)(result=1)
        for row in db.execute("SELECT * FROM social_requests WHERE "+('receiver' if incoming else 'sender')+"=? AND state='pending' ORDER BY created_ms",(player,)):
            other=row['sender' if incoming else 'receiver'];s=summary(game,db,other)
            it=(p.IncomingFriendInviteProto if incoming else p.OutgoingFriendInviteProto)(status=1,player_id=other,nia_account_id=other,created_ms=row['created_ms'])
            # Nested duplicate display types are empty in the supplied exporter.
            out.invites.add().ParseFromString(byte_field(1,it.SerializeToString())+byte_field(2,s.SerializeToString()))
    elif action in (10006,20006):
        if action==10006:
            out=p.GetFriendsListOutProto(result=1)
            for other in partners(db,player):out.friend.add().ParseFromString(friend_wire(game,db,player,other))
        else:
            out=p.ListFriendsResponse(result=1)
            for other in partners(db,player):
                s=summary(game,db,other)
                prof=p.ProfileDetailsProto(profile_name=s.codename,profile_name_app_key='pg',nickname=s.codename).SerializeToString()
                out.friend_summary.add().ParseFromString(rawstr(1,other)+rawint(2,1)+byte_field(3,friend_wire(game,db,player,other))+byte_field(4,prof)+rawstr(7,other))
    elif action in (10010,10019,20007):
        if action in (10010,10019):
            req=p.GetFriendDetailsProto.FromString(body);out=p.GetFriendDetailsOutProto(result=1);ids=list(req.player_id)+list(req.nia_account_id)
            for other in dict.fromkeys(ids):
                if pair(db,player,other):out.friend.add().ParseFromString(detail(game,db,player,other).SerializeToString())
        else:
            req=p.GetFriendDetailsRequest.FromString(body);out=p.GetFriendDetailsResponse(result=1);ids=list(req.friend_id)+list(req.friend_nia_account_id)
            if len(ids)>200:return p.ProxyResponseProto(status=7)
            for other in dict.fromkeys(ids):
                if pair(db,player,other):
                    prof=p.ProfileDetailsProto(profile_name=game.player(db,other)['name'],profile_name_app_key='pg').SerializeToString()
                    out.friend_details.add().ParseFromString(rawstr(1,other)+byte_field(2,prof)+byte_field(4,detail(game,db,player,other).SerializeToString())+rawstr(7,other))
    elif action==20003:
        req=p.GetProfileRequest.FromString(body);other=target(req);out=p.GetProfileResponse(result=1 if other==player or pair(db,player,other) else 3)
        if out.result==1:out.profile_details.CopyFrom(p.ProfileDetailsProto(profile_name=game.player(db,other)['name'],profile_name_app_key='pg'))
    elif action==20008:out=p.GetClientFeatureFlagsResponse()
    elif action in (10016,10017):
        if action==10016:
            req=p.SavePlayerSettingsProto.FromString(body);data=req.settings.SerializeToString()
            db.execute('INSERT INTO social_settings VALUES(?,?) ON CONFLICT(player_id) DO UPDATE SET data=excluded.data',(player,data))
            out=p.SavePlayerSettingsOutProto(result=1)
        else:
            row=db.execute('SELECT data FROM social_settings WHERE player_id=?',(player,)).fetchone();out=p.GetPlayerSettingsOutProto(result=1,settings=p.PlayerSettingsProto.FromString(row[0]) if row else p.PlayerSettingsProto(opt_out_online_status=True))
    return p.ProxyResponseProto(status=1,payload=out.SerializeToString()) if out is not None else p.ProxyResponseProto(status=3)
