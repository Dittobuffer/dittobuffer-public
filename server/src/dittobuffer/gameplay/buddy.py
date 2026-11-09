"""Persistent buddies sharing the egg system's validated walking samples."""
import json
import math
from pathlib import Path
from functools import lru_cache
from dittobuffer.gameplay.game import p,now_ms
from dittobuffer.core.management import owned,update_owned,add_candy


@lru_cache(maxsize=1)
def reference():return json.loads(Path(__file__).with_name('trainer-reference.json').read_text())


def templates(config):
    from dittobuffer.gameplay.buddy_perks import rules
    perks=rules(config)
    values=[]
    for number in range(5):
        source=reference()['buddy']['BUDDY_LEVEL_'+str(number)]['buddyLevelSettings']
        value=p.GameMasterClientTemplateProto(template_id='BUDDY_LEVEL_'+str(number))
        value.buddy_level_settings.level=number+1
        value.buddy_level_settings.min_non_cumulative_points_required=source.get('minNonCumulativePointsRequired',0)
        # Perks are explicit releases. CP boosts and training
        # remain absent until their full native/server behavior is accepted.
        if number:value.buddy_level_settings.unlocked_traits.extend([1,3])
        if number>=2 and perks['catch_assist']:value.buddy_level_settings.unlocked_traits.append(2)
        if number>=2 and perks['gifts']:value.buddy_level_settings.unlocked_traits.append(4)
        if number>=3 and perks['souvenirs']:value.buddy_level_settings.unlocked_traits.append(5)
        if number>=4 and perks['ribbon']:value.buddy_level_settings.unlocked_traits.append(7)
        values.append(value)
    if perks['catch_assist']:
        source=reference()['buddy']['BUDDY_ENCOUNTER_CAMEO_SETTINGS']['buddyEncounterCameoSettings']
        settings=p.BuddyEncounterCameoSettings()
        for field,key in [('wild','Wild'),('quest','Quest'),('raid','Raid'),('invasion','Invasion')]:
            setattr(settings,'buddy_'+field+'_encounter_cameo_chance_percent',
                    perks.get('cameo_test_probability',source['buddy'+key+'EncounterCameoChancePercent']))
        values.append(p.GameMasterClientTemplateProto(template_id='BUDDY_ENCOUNTER_CAMEO_SETTINGS',buddy_encounter_cameo_settings=settings))
    for category,name in enumerate(['FEED','CARE','SNAPSHOT','WALK','BATTLE','EXPLORE','BONUS','ROUTE'],1):
        value=p.GameMasterClientTemplateProto(template_id='BUDDY_CATEGORY_'+name)
        value.buddy_activity_category_settings.activity_category=category
        value.buddy_activity_category_settings.max_points_per_day=reference()['buddy']['BUDDY_CATEGORY_'+name]['buddyActivityCategorySettings']['maxPointsPerDay']
        values.append(value)
    value=p.GameMasterClientTemplateProto(template_id='BUDDY_HUNGER_SETTINGS')
    value.buddy_hunger_settings.CopyFrom(p.BuddyHungerSettings(num_hunger_points_required_for_full=6,decay_points_per_bucket=2,
        milliseconds_per_bucket=3600000,cooldown_duration_ms=3600000,decay_duration_after_full_ms=86400000))
    values.append(value)
    values.append(p.GameMasterClientTemplateProto(template_id='BUDDY_WALK_SETTINGS',buddy_walk_settings=p.BuddyWalkSettings(km_required_per_affection_point=2)))
    values.append(p.GameMasterClientTemplateProto(template_id='BUDDY_SWAP_SETTINGS',buddy_swap_settings=p.BuddySwapSettings(max_swaps_per_day=20,enable_swap_free_evolution=True,enable_quick_swap=True)))
    values.append(p.GameMasterClientTemplateProto(template_id='BUDDY_INTERACTION_SETTINGS',buddy_interaction_settings=p.BuddyInteractionSettings(feed_item_whitelist=[701])))
    for number in range(7):
        source=reference()['buddy']['BUDDY_EMOTION_LEVEL_'+str(number)]['buddyEmotionLevelSettings']
        values.append(p.GameMasterClientTemplateProto(template_id='BUDDY_EMOTION_LEVEL_'+str(number),
            buddy_emotion_level_settings=p.BuddyEmotionLevelSettings(emotion_level=number+1,
                min_emotion_points_required=source.get('minEmotionPointsRequired',0),
                emotion_animation=2 if source['emotionAnimation']=='BUDDY_ANIMATION_HATE' else 1)))
    return values


def day():return str(now_ms()//86400000)


def active(db,player):
    row=db.execute('SELECT * FROM buddy_active WHERE player_id=?',(player,)).fetchone()
    if not row:return None
    mon=owned(db,player,row['pokemon_id'])
    if mon is None:return None
    saved=db.execute('SELECT data FROM buddy_state WHERE player_id=? AND pokemon_id=?',(player,mon.id)).fetchone()
    return row,mon,json.loads(saved['data'])


def save(game,db,player,mon,state):
    from dittobuffer.gameplay.buddy_perks import rules
    if rules(game.config)['ribbon'] and state['points']>=300 and mon.pokemon_display.pokemon_badge!=1:
        mon.pokemon_display.pokemon_badge=1
        update_owned(game,db,player,mon)
    state['pokemon']=mon.SerializeToString().hex()
    db.execute('INSERT INTO buddy_state VALUES(?,?,?) ON CONFLICT(player_id,pokemon_id) DO UPDATE SET data=excluded.data',
               (player,mon.id,json.dumps(state,sort_keys=True,separators=(',',':'))))
    game.touch(db,player)


def distance_km(game,mon):
    rule=game.forms.get((mon.pokemon_id,mon.pokemon_display.form),game.species[mon.pokemon_id])
    return rule['buddy_distance_km']


def hunger(state):
    # The supplied hunger settings decay two points per one-hour bucket.
    cfg=reference()['buddy']['BUDDY_HUNGER_SETTINGS']['buddyHungerSettings']
    bucket=int(cfg['millisecondsPerBucket']);elapsed=max(0,(now_ms()-state['hunger_ms'])//bucket)
    return max(0,state['hunger']-elapsed*cfg['decayPointsPerBucket'])


def heart(db,player,mon,state,category,maximum,game=None):
    current=db.execute('SELECT hearts FROM buddy_daily WHERE player_id=? AND pokemon_id=? AND day=? AND category=?',(player,mon.id,day(),category)).fetchone()
    if current and current[0]>=maximum:return False
    db.execute('INSERT INTO buddy_daily VALUES(?,?,?,?,1) ON CONFLICT(player_id,pokemon_id,day,category) DO UPDATE SET hearts=hearts+1',(player,mon.id,day(),category))
    if game is not None:
        from dittobuffer.gameplay.trainer import event
        event(game,db,player,'buddy-heart:'+str(mon.id)+':'+day()+':'+str(category)+':'+str((current[0] if current else 0)+1),'buddy_heart',{'pokemon':mon.SerializeToString().hex(),'category':category})
    state['points']+=1
    if day() not in state['days']:state['days'].append(day())
    return True


def observed(db,player,mon,state):
    value=p.BuddyObservedData(current_points_earned=state['points'],buddy_validation_result=1,num_days_spent_with_buddy=len(state['days']))
    if state.get('gift'):value.buddy_gift_picked_up.ParseFromString(bytes.fromhex(state['gift']))
    for key,data in state.get('souvenirs',{}).items():
        value.souvenirs_collected.add(key=int(key)).value.ParseFromString(bytes.fromhex(data))
    value.total_stats.CopyFrom(p.BuddyStats(km_walked=state['walk_m']/1000,berries_fed=state['berries'],communication=state['care'],
        battles=state.get('battles',0),photos=state.get('photos',0),new_visits=state.get('new_visits',0)))
    # Mood follows current hunger. Excited bonuses remain disabled until their
    # interaction/cooldown definitions and Android behavior are verified.
    value.current_emotion_points=min(4,hunger(state))
    value.today_stats_shown_hearts.buddy_affection_km_in_progress=state.get('heart_walk_m',0)/1000 if state.get('walk_day')==day() else 0
    earned={r['category']:r['hearts'] for r in db.execute('SELECT category,hearts FROM buddy_daily WHERE player_id=? AND pokemon_id=? AND day=?',(player,mon.id,day()))}
    for category,name in enumerate(['FEED','CARE','SNAPSHOT','WALK','BATTLE','EXPLORE','BONUS','ROUTE'],1):
        maximum=reference()['buddy']['BUDDY_CATEGORY_'+name]['buddyActivityCategorySettings']['maxPointsPerDay']
        filled=min(maximum,earned.get(category,0))
        value.today_stats_shown_hearts.buddy_shown_hearts_per_category.add(key=category).value.buddy_shown_heart_types.extend([1]*filled+[0]*(maximum-filled))
    value.buddy_feed_stats.map_expiration_ms=state['map_until']
    value.buddy_feed_stats.pre_map_fullness_percentage=hunger(state)/6
    value.buddy_feed_stats.fullness_expiration_ms=state['hunger_ms']+3600000 if hunger(state)==6 else 0
    return value


def player_data(game,db,player,client):
    current=active(db,player)
    if not current:return
    row,mon,state=current
    from dittobuffer.gameplay.buddy_perks import offer
    offer(game,db,player,mon,state)
    client.buddy_pokemon_proto.CopyFrom(p.BuddyPokemonProto(buddy_pokemon_id=mon.id,
        start_km_walked=state['start_km'],last_km_awarded=state['award_km'],last_km_awarded_ms=state['award_ms'],last_set_timestamp_ms=state['set_ms']))
    client.buddy_pokemon_proto.daily_buddy_swaps.CopyFrom(p.DailyCounterProto(window=int(day()),count=row['swaps_count'] if row['swaps_day']==day() else 0,buckets_per_day=1))
    client.buddy_observed_data.CopyFrom(observed(db,player,mon,state))


def select(game,db,player,body):
    request=p.SetBuddyPokemonProto.FromString(body);mon=owned(db,player,request.pokemon_id)
    if mon is None:return p.SetBuddyPokemonOutProto(result=3)
    if not math.isfinite(distance_km(game,mon)) or distance_km(game,mon)<=0:return p.SetBuddyPokemonOutProto(result=5)
    if mon.is_egg:return p.SetBuddyPokemonOutProto(result=4)
    if mon.deployed_fort_id or mon.deployed_station_id:return p.SetBuddyPokemonOutProto(result=2)
    from dittobuffer.gameplay.gyms import battle_busy
    if battle_busy(db,player,[mon.id]) or mon.is_fusion or mon.is_component:return p.SetBuddyPokemonOutProto(result=5)
    current=active(db,player);stamp=now_ms();count=0
    if current:
        old,previous,state=current
        count=old['swaps_count'] if old['swaps_day']==day() else 0
        if previous.id==mon.id:return selection_response(game,db,player,mon,state)
        if count>=20:return p.SetBuddyPokemonOutProto(result=6)
        state.update(unset_ms=stamp,hunger=0,hunger_ms=stamp,map_until=0)
        save(game,db,player,previous,state)
    saved=db.execute('SELECT data FROM buddy_state WHERE player_id=? AND pokemon_id=?',(player,mon.id)).fetchone()
    state=json.loads(saved[0]) if saved else dict(walk_m=0,candy_m=0,candy=0,pending_candy=0,award_km=0,award_ms=0,
        start_km=game.player(db,player)['km_walked'],points=0,berries=0,care=0,hunger=0,hunger_ms=stamp,map_until=0,days=[])
    state.update(set_ms=stamp,unset_ms=0)
    if day() not in state['days']:state['days'].append(day())
    db.execute('INSERT INTO buddy_active VALUES(?,?,?,?,?) ON CONFLICT(player_id) DO UPDATE SET pokemon_id=excluded.pokemon_id,swapped_ms=excluded.swapped_ms,swaps_day=excluded.swaps_day,swaps_count=excluded.swaps_count',
               (player,mon.id,stamp,day(),count+1))
    save(game,db,player,mon,state)
    return selection_response(game,db,player,mon,state)


def selection_response(game,db,player,mon,state):
    client=p.ClientPlayerProto();player_data(game,db,player,client)
    return p.SetBuddyPokemonOutProto(result=1,updated_buddy=client.buddy_pokemon_proto,observed_data=client.buddy_observed_data,
        km_remaining=max(0,distance_km(game,mon)-state['candy_m']/1000))


def stats(game,db,player,body):
    p.BuddyStatsProto.FromString(body);current=active(db,player)
    if not current:return p.BuddyStatsOutProto(result=2)
    _,mon,state=current
    from dittobuffer.gameplay.buddy_perks import offer
    offer(game,db,player,mon,state)
    return p.BuddyStatsOutProto(result=1,observed_data=observed(db,player,mon,state))


def feed(game,db,player,body):
    request=p.BuddyFeedingProto.FromString(body);current=active(db,player)
    if not current:return p.BuddyFeedingOutProto(result=2)
    _,mon,state=current
    # Razz is the supported encounter/gym berry; other food stays unavailable
    # until its native bag and interaction paths have been exercised.
    if request.item!=701 or not 1<=request.count<=3:return p.BuddyFeedingOutProto(result=4)
    fullness=hunger(state)
    if fullness+request.count*2>6:return p.BuddyFeedingOutProto(result=4)
    from dittobuffer.gameplay.items import consume
    if not consume(db,player,request.item,request.count):return p.BuddyFeedingOutProto(result=3)
    stamp=now_ms();state.update(hunger=fullness+request.count*2,hunger_ms=stamp)
    state['berries']+=request.count
    gained=False
    if state['hunger']==6:
        gained=heart(db,player,mon,state,1,3,game=game)
        state['map_until']=stamp+3*3600000
    elif state['map_until']>stamp:state['map_until']=stamp+state['hunger']*1800000
    save(game,db,player,mon,state)
    return p.BuddyFeedingOutProto(result=1,observed_data=observed(db,player,mon,state),shown_hearts=1 if gained else 0)


def pet(game,db,player,body):
    p.BuddyPettingProto.FromString(body);current=active(db,player)
    if not current:return p.BuddyPettingOutProto(result=2)
    _,mon,state=current;gained=heart(db,player,mon,state,2,1,game=game)
    if gained:state['care']+=1;save(game,db,player,mon,state)
    return p.BuddyPettingOutProto(result=1,observed_data=observed(db,player,mon,state),shown_hearts=1 if gained else 0)


def activity(game,db,player,kind,key,party=None):
    """Credit a committed source action once, inside its owning transaction."""
    current=active(db,player)
    if not current:return False
    _,mon,state=current
    if kind in ('battle','explore') and state['map_until']<=now_ms():return False
    if party is not None and mon.id not in party:return False
    if db.execute('SELECT 1 FROM buddy_action_receipts WHERE player_id=? AND pokemon_id=? AND kind=? AND action_key=?',
                  (player,mon.id,kind,key)).fetchone():return False
    category,maximum,stat={'battle':(5,3,'battles'),'explore':(6,1,'new_visits'),'snapshot':(3,1,'photos')}[kind]
    gained=heart(db,player,mon,state,category,maximum,game=game)
    state[stat]=state.get(stat,0)+1
    db.execute('INSERT INTO buddy_action_receipts VALUES(?,?,?,?,?,?)',(player,mon.id,kind,key,now_ms(),b''))
    save(game,db,player,mon,state)
    return gained


def snapshot(game,db,player,body):
    request=p.CompleteSnapshotSessionProto.FromString(body)
    mon=owned(db,player,request.photo_pokemon_id)
    if mon is None or mon.is_egg:return p.CompleteSnapshotSessionOutProto(status=2)
    stamp=request.snapshot_session_start_time
    if not 1<=request.num_photos_taken<=100 or not now_ms()-86400000<=stamp<=now_ms()+30000:
        return p.CompleteSnapshotSessionOutProto(status=3)
    current=active(db,player)
    if current and current[1].id==mon.id:activity(game,db,player,'snapshot',str(stamp))
    return p.CompleteSnapshotSessionOutProto(status=1)


def map_data(game,db,player,body):
    p.BuddyMapProto.FromString(body);current=active(db,player)
    if not current:return p.BuddyMapOutProto(result=2)
    _,mon,state=current
    from dittobuffer.gameplay.buddy_perks import offer
    offer(game,db,player,mon,state)
    return p.BuddyMapOutProto(result=1,expiration_ms=state['map_until'],applied_ms=state['hunger_ms'],observed_data=observed(db,player,mon,state))


def walk(game,db,player,meters):
    # Called only after eggs.sample accepts a new device fix. No old distance
    # or caller-supplied cumulative totals can earn buddy Candy here.
    if not math.isfinite(meters) or meters<=0:return
    current=active(db,player)
    if not current:return
    _,mon,state=current;required=distance_km(game,mon)*1000
    state['walk_m']+=meters;state['candy_m']+=meters
    count=int(state['candy_m']//required)
    if count:
        state['candy_m']-=count*required;state['candy']+=count;state['pending_candy']+=count
        state['award_ms']=now_ms();state['award_km']=game.player(db,player)['km_walked']
        rule=game.forms.get((mon.pokemon_id,mon.pokemon_display.form),game.species[mon.pokemon_id])
        add_candy(db,player,rule['family'],count)
        mon.buddy_candy_awarded+=count
        from dittobuffer.gameplay.trainer import event
        event(game,db,player,'buddy-candy:'+str(mon.id)+':'+str(state['candy']),'buddy_candy',dict(pokemon=mon.SerializeToString().hex(),candy=count))
    mon.buddy_km_walked=state['walk_m']/1000;update_owned(game,db,player,mon)
    if state.get('walk_day')!=day():state.update(walk_day=day(),heart_walk_m=0)
    if state['map_until']>now_ms() and state['points']>=1:
        state['heart_walk_m']+=meters
        while state['heart_walk_m']>=2000 and heart(db,player,mon,state,4,3,game=game):state['heart_walk_m']-=2000
    if day() not in state['days']:state['days'].append(day())
    save(game,db,player,mon,state)


def walked(game,db,player,body):
    p.GetBuddyWalkedProto.FromString(body);current=active(db,player)
    if not current:return p.GetBuddyWalkedOutProto(success=True)
    _,mon,state=current;rule=game.forms.get((mon.pokemon_id,mon.pokemon_display.form),game.species[mon.pokemon_id])
    out=p.GetBuddyWalkedOutProto(success=True,family_candy_id=rule['family'],candy_earned_count=state['pending_candy'],
        km_remaining=max(0,distance_km(game,mon)-state['candy_m']/1000),last_km_awarded=state['award_km'])
    if state['pending_candy']:state['pending_candy']=0;save(game,db,player,mon,state)
    return out


def history(game,db,player,body):
    p.GetBuddyHistoryProto.FromString(body);out=p.GetBuddyHistoryOutProto(result=1)
    for row in db.execute('SELECT pokemon_id,data FROM buddy_state WHERE player_id=? ORDER BY pokemon_id',(player,)):
        state=json.loads(row['data']);mon=owned(db,player,row['pokemon_id']) or p.PokemonProto.FromString(bytes.fromhex(state['pokemon']))
        out.buddy_history.add(pokemon_id=mon.id,pokedex_id=mon.pokemon_id,pokemon_display=mon.pokemon_display,nickname=mon.nickname,
            creation_timestamp_ms=mon.creation_time_ms,pokeball=mon.pokeball,current_points_earned=state['points'],
            # The native history screen formats both endpoints as dates. An
            # active relationship has no stored unset time; project its range
            # through now rather than displaying January 1970.
            total_stats=observed(db,player,mon,state).total_stats,last_set_timestamp_ms=state['set_ms'],last_unset_timestamp_ms=state['unset_ms'] or now_ms(),
            num_days_spent_with_buddy=len(state['days']),ditched=owned(db,player,mon.id) is None,km_candy_progress=state['candy_m']/1000)
        for key,data in state.get('souvenirs',{}).items():
            out.buddy_history[-1].souvenirs_collected.add(key=int(key)).value.ParseFromString(bytes.fromhex(data))
    return out
