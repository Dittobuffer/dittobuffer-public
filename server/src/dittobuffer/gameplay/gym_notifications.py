"""Durable gym-return inbox records. Delivery never grants rewards."""
from dittobuffer.gameplay.game import p,now_ms

def returned(game,db,row,pokemon,coins,today):
    stamp=now_ms()
    notification=p.ClientInbox.Notification(notification_id=f"gym-return-{pokemon.id}-{row['deploy_ms']}",
        category='GYM_REMOVAL',create_timestamp_ms=stamp,expire_time_ms=stamp+30*86400000,labels=[1,2,3])
    # Names and value locations come from ClientInboxService.bfpk 0x8155EF8:
    # numeric PokemonId/coins use Literal; PokedexEntryNumber uses Key;
    # PokemonDisplay is its serialized ByteValue. These are not dialogue text.
    for name,value in {'POKEMON_ID':pokemon.id,'POKECOIN_AWARDED':coins,'POKECOIN_AWARDED_TODAY':today,
                       'MAX_DAILY_POKECOIN':game.config.get('gyms',{}).get('daily_coin_cap',50)}.items():
        notification.variables.add(name=name,literal=str(value))
    notification.variables.add(name='POKEDEX_ENTRY_NUMBER',key=str(pokemon.pokemon_id))
    notification.variables.add(name='POKEMON_DISPLAY',byte_value=pokemon.pokemon_display.SerializeToString())
    notification.variables.add(name='POKEMON_NICKNAME',literal=pokemon.nickname)
    db.execute('INSERT OR IGNORE INTO gym_notifications VALUES(?,?,?,?,0,?)',
               (notification.notification_id,row['owner_id'],stamp,notification.SerializeToString(),stamp))

def inbox(db,player,body):
    request=p.GetInboxV2Proto.FromString(body)
    out=p.GetNotificationInboxOutProto(result=1)
    out.inbox.SetInParent()
    rows=db.execute('SELECT * FROM gym_notifications WHERE player_id=? AND created_ms>=? ORDER BY created_ms DESC,id DESC LIMIT 50',
                    (player,request.not_before_ms)).fetchall()
    if not request.is_reverse:rows.reverse()
    for row in rows:
        if not request.is_history and row['state']==2:continue
        notification=p.ClientInbox.Notification.FromString(row['data'])
        if row['state'] or notification.expire_time_ms<now_ms():
            if not request.is_history and notification.expire_time_ms<now_ms():continue
            del notification.labels[:]
        out.inbox.notifications.add().CopyFrom(notification)
    return out

def update(db,player,body):
    request=p.UpdateNotificationProto.FromString(body)
    out=p.UpdateNotificationOutProto(state=request.state)
    if request.state not in (1,2) or len(request.notification_ids)!=len(request.create_timestamp_ms):return out
    for ident,created in zip(request.notification_ids,request.create_timestamp_ms):
        if db.execute('UPDATE gym_notifications SET state=MAX(state,?),updated_ms=? WHERE id=? AND player_id=? AND created_ms=?',
                      (request.state,now_ms(),ident,player,created)).rowcount:
            out.notification_ids.append(ident);out.create_timestamp_ms.append(created)
    return out
