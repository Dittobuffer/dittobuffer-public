"""Native action-log projection; never disguise unsupported event kinds."""
import json
from dittobuffer.gameplay.game import p,now_ms


def read(game,db,player,body):
    p.GetActionLogRequest.FromString(body)
    out=p.GetActionLogResponse(result=1)
    # Retention limits the UI response, not transaction/reward safety receipts.
    rows=db.execute('SELECT * FROM trainer_events WHERE player_id=? AND created_ms>=? ORDER BY created_ms DESC,source_key DESC LIMIT 500',
                    (player,now_ms()-30*86400000))
    for row in rows:
        data=json.loads(row['data']);entry=p.ActionLogEntry(timestamp_ms=row['created_ms'])
        kind=row['kind']
        if kind in ('catch','hatch'):
            mon=p.PokemonProto.FromString(bytes.fromhex(data['pokemon']))
            entry.catch_pokemon.CopyFrom(p.CatchPokemonLogEntry(result=3 if kind=='hatch' else 1,pokedex_number=mon.pokemon_id,
                combat_points=mon.cp,pokemon_id=mon.id,pokemon_display=mon.pokemon_display))
        elif kind=='spin':
            entry.fort_search.result=1;entry.fort_search.fort_id=data['fort_id'];entry.fort_search.fort_type=1
            # 0.429.2 renders AwardedItems, not the legacy Items collection.
            for item in data['items']:entry.fort_search.awarded_items.add(item=item['item'],count=item['count'])
            if data.get('egg'):
                entry.fort_search.eggs=1
                entry.fort_search.pokemon_eggs.add().ParseFromString(bytes.fromhex(data['egg']))
        elif kind=='raid':
            entry.raid_rewards.result=1;entry.raid_rewards.stardust=data['dust'];entry.raid_rewards.raid_level=data['tier']
            for item,count in data['items'].items():entry.raid_rewards.items.add(item=int(item),count=count)
        elif kind=='buddy_candy':
            mon=p.PokemonProto.FromString(bytes.fromhex(data['pokemon']))
            entry.buddy_pokemon.CopyFrom(p.BuddyPokemonLogEntry(result=1,pokemon_type=mon.pokemon_id,pokemon_id=mon.id,
                pokemon_display=mon.pokemon_display,amount=data['candy']))
        elif kind=='buddy_consumables':
            gift=p.BuddyGiftProto.FromString(bytes.fromhex(data['gift']))
            entry.buddy_consumables.rewards.CopyFrom(gift.loot_proto)
        else:continue
        out.log.append(entry)
        if len(out.log)==200:break
    return out
