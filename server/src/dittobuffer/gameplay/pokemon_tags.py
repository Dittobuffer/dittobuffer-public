"""Owned storage tags, serialized in the client's native tag records."""
import unicodedata
from dittobuffer.gameplay.game import p,RpcError
from dittobuffer.core.management import owned,update_owned
from dittobuffer.gameplay.progression import bounds

def rules(game):return {'minimum_level':5,'capacity':100,'name_limit':20}|game.config.get('pokemon_tags',{})
def allowed(game,db,player):return bounds(game.config,game.player(db,player)['xp'])[0]>=rules(game)['minimum_level']
def message(row):return p.PokemonTagProto(id=row['id'],name=row['name'],color=row['color'],sort_index=row['sort_index'])
def listing(game,db,player,body):
    p.GetPokemonTagsProto.FromString(body)
    out=p.GetPokemonTagsOutProto(result=1 if allowed(game,db,player) else 2)
    if out.result==1:
        for row in db.execute('SELECT * FROM pokemon_tags WHERE player_id=? ORDER BY sort_index,id',(player,)):out.tag.add().CopyFrom(message(row))
    return out
def name(game,value):
    value=unicodedata.normalize('NFC',value).strip()
    return value if 1<=len(value)<=rules(game)['name_limit'] and not any(unicodedata.category(c).startswith('C') for c in value) else None
def create(game,db,player,body):
    req=p.CreatePokemonTagProto.FromString(body)
    if not allowed(game,db,player):return p.CreatePokemonTagOutProto(result=2)
    value=name(game,req.name)
    if not value or req.color not in range(1,9):raise RpcError('Invalid storage tag name or color')
    old=db.execute('SELECT * FROM pokemon_tags WHERE player_id=?',(player,)).fetchall()
    if any(r['name'].casefold()==value.casefold() for r in old):return p.CreatePokemonTagOutProto(result=3)
    if len(old)>=rules(game)['capacity']:return p.CreatePokemonTagOutProto(result=4)
    ident=db.execute('INSERT INTO pokemon_tags(player_id,name,color,sort_index) VALUES(?,?,?,?)',(player,value,req.color,len(old))).lastrowid
    game.touch(db,player)
    return p.CreatePokemonTagOutProto(result=1,created_tag=message(db.execute('SELECT * FROM pokemon_tags WHERE id=?',(ident,)).fetchone()))
def edit(game,db,player,body):
    req=p.EditPokemonTagProto.FromString(body);out=p.EditPokemonTagOutProto()
    for tag in req.tag_to_edit:
        result=1;value=name(game,tag.name)
        if not allowed(game,db,player):result=2
        elif not db.execute('SELECT 1 FROM pokemon_tags WHERE id=? AND player_id=?',(tag.id,player)).fetchone():result=3
        elif not value or tag.color not in range(1,9):result=4
        elif not 0<=tag.sort_index<rules(game)['capacity']:result=5
        elif any(r['name'].casefold()==value.casefold() for r in db.execute('SELECT name FROM pokemon_tags WHERE player_id=? AND id<>?',(player,tag.id))):result=4
        if result==1:
            db.execute('UPDATE pokemon_tags SET name=?,color=?,sort_index=? WHERE id=?',(value,tag.color,tag.sort_index,tag.id));game.touch(db,player)
        out.edit_result.append(result)
    return out
def delete(game,db,player,body):
    req=p.DeletePokemonTagProto.FromString(body)
    if not allowed(game,db,player):return p.DeletePokemonTagOutProto(result=2)
    if not db.execute('DELETE FROM pokemon_tags WHERE id=? AND player_id=?',(req.tag_id,player)).rowcount:return p.DeletePokemonTagOutProto(result=3)
    for row in db.execute('SELECT data FROM pokemon WHERE player_id=?',(player,)).fetchall():
        mon=p.PokemonProto.FromString(row['data'])
        if req.tag_id in mon.pokemon_tag_ids:
            mon.pokemon_tag_ids.remove(req.tag_id);update_owned(game,db,player,mon)
    game.touch(db,player);return p.DeletePokemonTagOutProto(result=1)
def assign(game,db,player,body):
    req=p.SetPokemonTagsForPokemonProto.FromString(body)
    if not allowed(game,db,player):return p.SetPokemonTagsForPokemonOutProto(status=2)
    known={r[0] for r in db.execute('SELECT id FROM pokemon_tags WHERE player_id=?',(player,))};pending={}
    for change in req.tag_changes:
        mon=pending.get(change.pokemon_id) or owned(db,player,change.pokemon_id)
        if not mon:return p.SetPokemonTagsForPokemonOutProto(status=3)
        add=set(change.tags_to_add);remove=set(change.tags_to_remove)
        if add & remove or not add.union(remove)<=known:return p.SetPokemonTagsForPokemonOutProto(status=4)
        ids=(set(mon.pokemon_tag_ids)|add)-remove
        del mon.pokemon_tag_ids[:];mon.pokemon_tag_ids.extend(sorted(ids));pending[mon.id]=mon
    for mon in pending.values():update_owned(game,db,player,mon)
    return p.SetPokemonTagsForPokemonOutProto(status=1)

def templates(config):
    cfg={'minimum_level':5,'capacity':100,'name_limit':20}|config.get('pokemon_tags',{})
    t=p.GameMasterClientTemplateProto(template_id='POKEMON_TAG_SETTINGS')
    t.pokemon_tag_settings.min_player_level_for_pokemon_tagging=cfg['minimum_level']
    t.pokemon_tag_settings.max_num_tags_allowed=cfg['capacity'];t.pokemon_tag_settings.tag_name_character_limit=cfg['name_limit']
    for color,code in enumerate(('4D90FE','4CAF50','9C59B6','FFCC33','E54B4B','F29238','9A9A9A','444444'),1):
        t.pokemon_tag_settings.color_binding.add(color=color,hex_code='#'+code)
    return [t]
