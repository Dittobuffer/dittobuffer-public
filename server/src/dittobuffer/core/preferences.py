"""Bounded server-owned player preferences; device toggles stay on the device."""
from dittobuffer.gameplay.game import p


def valid(message):
    for field,value in message.ListFields():
        values=value if field.is_repeated else [value]
        for item in values:
            if field.type==field.TYPE_ENUM and item not in field.enum_type.values_by_number:return False
            if field.type==field.TYPE_MESSAGE and not valid(item):return False
    return True


GROUPS={
    'social':(p.SocialPlayerSettingsProto,p.SaveSocialPlayerSettingsProto,'settings',p.SaveSocialPlayerSettingsOutProto,'result'),
    'combat':(p.CombatPlayerPreferencesProto,p.SaveCombatPlayerPreferencesProto,'preferences',p.SaveCombatPlayerPreferencesOutProto,'result'),
    'contact':(p.ContactSettingsProto,p.SetContactSettingsProto,'contact_settings_proto',p.SetContactSettingsOutProto,'status')}


def load_group(db,player,kind):
    row=db.execute('SELECT data FROM trainer_settings WHERE player_id=? AND kind=?',(player,kind)).fetchone()
    if row:return GROUPS[kind][0].FromString(row[0])
    if kind=='social':return p.SocialPlayerSettingsProto(disable_last_pokemon_caught=True,disable_lucky_friend_applicator_requests=True)
    if kind=='combat':return p.CombatPlayerPreferencesProto(friends_combat_opt_out=True)
    return p.ContactSettingsProto()


def save_group(game,db,player,body,kind):
    _,request_type,field,response_type,status=GROUPS[kind]
    result=response_type(**{status:2})
    if len(body)>65536:return result
    request=request_type.FromString(body)
    if not request.HasField(field) or not valid(getattr(request,field)):return result
    data=getattr(request,field).SerializeToString()
    old=db.execute('SELECT data FROM trainer_settings WHERE player_id=? AND kind=?',(player,kind)).fetchone()
    if old is None or old[0]!=data:
        db.execute('INSERT INTO trainer_settings VALUES(?,?,?) ON CONFLICT(player_id,kind) DO UPDATE SET data=excluded.data',(player,kind,data))
        game.touch(db,player)
    setattr(result,status,1)
    if kind=='contact':result.player.CopyFrom(game.rpc_get_player(db,player,b'').player)
    return result


def load(db,player):
    row=db.execute('SELECT data FROM trainer_preferences WHERE player_id=?',(player,)).fetchone()
    value=p.PlayerPreferencesProto.FromString(row[0]) if row else p.PlayerPreferencesProto()
    # Supply empty settings records expected by native General settings; their
    # presence does not enable any party, wearable, advertising or social service.
    for name in ('battle_parties','waina_preference','party_play_preference','pokedex_preference','activity_sharing_preference'):
        if not value.HasField(name):getattr(value,name).SetInParent()
    return value


def save(game,db,player,body):
    if len(body)>65536:return p.SavePlayerPreferencesOutProto(result=2)
    request=p.SavePlayerPreferencesProto.FromString(body)
    if not request.HasField('player_preferences_proto'):return p.SavePlayerPreferencesOutProto(result=2)
    if not valid(request.player_preferences_proto):return p.SavePlayerPreferencesOutProto(result=2)
    data=request.player_preferences_proto.SerializeToString()
    old=db.execute('SELECT data FROM trainer_preferences WHERE player_id=?',(player,)).fetchone()
    if old is None or old[0]!=data:
        db.execute('INSERT INTO trainer_preferences VALUES(?,?) ON CONFLICT(player_id) DO UPDATE SET data=excluded.data',(player,data))
        game.touch(db,player)
    return p.SavePlayerPreferencesOutProto(result=1)
