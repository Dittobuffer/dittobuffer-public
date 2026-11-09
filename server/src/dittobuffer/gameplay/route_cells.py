"""Opt-in Cells and source-backed Zygarde transformations.

Cell award tuning is explicit local policy. Form costs are read from the supplied
Game Master wire data; legacy forms lacking a transition are never guessed.
All writes use the enclosing authenticated gameplay transaction.
"""
import gzip,secrets
from functools import lru_cache
from protocol.opaque import fields
from dittobuffer.gameplay.game import ROOT,p,now_ms

SCHEMA='''
CREATE TABLE IF NOT EXISTS route_cell_receipts(attempt TEXT PRIMARY KEY REFERENCES route_attempts(id),player TEXT NOT NULL REFERENCES players(id),route TEXT NOT NULL,day INTEGER NOT NULL,count INTEGER NOT NULL CHECK(count>=0),created_ms INTEGER NOT NULL,UNIQUE(player,route,day));
CREATE TABLE IF NOT EXISTS zygarde_form_receipts(player TEXT NOT NULL REFERENCES players(id),pokemon INTEGER NOT NULL,source_form INTEGER NOT NULL,target_form INTEGER NOT NULL,request_id TEXT NOT NULL,response BLOB NOT NULL,created_ms INTEGER NOT NULL,PRIMARY KEY(player,request_id));
'''

def settings(game):
    cfg=game.routes.get('cells',{'enabled':False})
    if type(cfg.get('enabled',False)) is not bool:raise ValueError('Invalid Cells enable flag')
    if cfg.get('enabled'):
        for key in ('daily_limit','capacity','per_route'):
            if type(cfg.get(key)) is not int or not 1<=cfg[key]<=10000:raise ValueError('Explicit custom Cells '+key+' required')
        if not -840<=cfg.get('day_offset_minutes',0)<=840:raise ValueError('Invalid Cells day boundary')
    return cfg

def quantity(db,player,item):
    row=db.execute('SELECT count FROM items WHERE player_id=? AND item=?',(player,item)).fetchone()
    return row[0] if row else 0

def award(game,db,attempt):
    cfg=settings(game)
    if not cfg.get('enabled') or not quantity(db,attempt['player'],651):return 0
    day=(now_ms()+cfg.get('day_offset_minutes',0)*60000)//86400000
    if db.execute('SELECT 1 FROM route_cell_receipts WHERE player=? AND route=? AND day=?',(attempt['player'],attempt['route'],day)).fetchone():return 0
    used=db.execute('SELECT COALESCE(SUM(count),0) FROM route_cell_receipts WHERE player=? AND day=?',(attempt['player'],day)).fetchone()[0]
    count=max(0,min(cfg['per_route'],cfg['daily_limit']-used,cfg['capacity']-quantity(db,attempt['player'],650)))
    db.execute('INSERT INTO route_cell_receipts VALUES(?,?,?,?,?,?)',(attempt['id'],attempt['player'],attempt['route'],day,count,now_ms()))
    if count:db.execute('INSERT INTO items VALUES(?,650,?) ON CONFLICT(player_id,item) DO UPDATE SET count=count+excluded.count',(attempt['player'],count))
    if count:
        from dittobuffer.gameplay.trainer import event
        event(game,db,attempt['player'],'route-cells:'+attempt['id'],'zygarde_cell',{'amount':count})
    return count

@lru_cache(maxsize=1)
def transitions():
    result={}
    for n,k,_,raw in fields(gzip.decompress((ROOT/'data/static/gmts.binpb.gz').read_bytes())):
        if n!=2 or k!=2:continue
        template={a:d for a,b,_,d in fields(raw)}
        if not template.get(1,b'').startswith(b'V0718_POKEMON_') or 2 not in template:continue
        data={a:d for a,b,_,d in fields(template[2])}
        # PokemonSettings native writer: FormChange is tag 63, Form tag 28.
        if 63 not in data:continue
        change=p.FormChangeProto.FromString(data[63])
        # Only fully understood ordinary transitions; reject other conditions.
        if change.quest_requirement or change.component_pokemon_settings.ByteSize() or change.move_reassignment.ByteSize() or change.required_quick_moves or change.required_cinematic_moves or change.required_bread_moves or change.form_change_bonus_attributes or change.location_card_settings:continue
        for target in change.available_form:
            result[data.get(28,0),target]={'candy':change.candy_cost,'stardust':change.stardust_cost,'item':change.item_cost,'count':change.item_cost_count,'template':template[1].decode()}
    return result

def change_form(game,db,player,body,request_id=None,preview=False):
    req=p.ChangePokemonFormProto.FromString(body);out=p.ChangePokemonFormOutProto(result=7)
    if not game.routes['enabled'] or not settings(game).get('enabled'):return out
    from dittobuffer.core.management import owned,candy,add_candy,level_of,save
    from dittobuffer.gameplay.species import apply_stats
    mon=owned(db,player,req.pokemon_id)
    if mon is None:out.result=2;return out
    if mon.pokemon_id!=718 or mon.is_egg:out.result=5;return out
    # Game.rpc provides durable ticket/run/RPC-ID replay protection. Optional
    # explicit IDs are useful for service-level consumers using the same DB.
    request_id=request_id or secrets.token_hex(16)
    old=None if preview else db.execute('SELECT * FROM zygarde_form_receipts WHERE player=? AND request_id=?',(player,request_id)).fetchone()
    if old:
        if old['pokemon']!=req.pokemon_id or old['target_form']!=req.target_form:out.result=5;return out
        return p.ChangePokemonFormOutProto.FromString(old['response'])
    if mon.pokemon_display.form==req.target_form:
        out.result=1;out.changed_pokemon.CopyFrom(mon);return out
    rule=transitions().get((mon.pokemon_display.form,req.target_form))
    if not rule or (718,req.target_form) not in game.forms:out.result=5;return out
    from dittobuffer.gameplay.trading import locked
    from dittobuffer.gameplay.gyms import battle_busy
    from dittobuffer.gameplay.mega import active
    if mon.deployed_fort_id or mon.deployed_station_id or locked(game,db,mon.id) or battle_busy(db,player,[mon.id]) or active(db,mon.id):out.result=6;return out
    family=game.forms[718,req.target_form]['family']
    if candy(db,player,family)<rule['candy'] or game.player(db,player)['stardust']<rule['stardust'] or rule['item'] and quantity(db,player,rule['item'])<rule['count']:out.result=3;return out
    previous=mon.pokemon_display.form;level=level_of(db,mon);fainted=mon.stamina==0
    mon.pokemon_display.form=req.target_form
    apply_stats(mon,game.forms[718,req.target_form],level,heal=False)
    if fainted:mon.stamina=0
    out.result=1;out.changed_pokemon.CopyFrom(mon)
    # Native FormChangeService.cgih (0x7a741dc) sends method 3021 using
    # the same request and response as method 1722. Preview never saves the
    # projected Pokemon, consumes resources or creates a mutation receipt.
    if preview:return out
    if rule['candy']:add_candy(db,player,family,-rule['candy'])
    if rule['stardust']:db.execute('UPDATE players SET stardust=stardust-? WHERE id=?',(rule['stardust'],player))
    if rule['item']:db.execute('UPDATE items SET count=count-? WHERE player_id=? AND item=?',(rule['count'],player,rule['item']))
    save(game,db,player,mon,level)
    db.execute('INSERT INTO zygarde_form_receipts VALUES(?,?,?,?,?,?,?)',(player,mon.id,previous,req.target_form,request_id,out.SerializeToString(),now_ms()))
    return out

def templates(config):
    if not config.get('routes',{}).get('enabled') or not config.get('routes',{}).get('cells',{}).get('enabled'):return []
    values=[]
    for ident,name,kind,hidden in [(650,'ITEM_BEANS',13,True),(651,'ITEM_BREAKFAST',27,False)]:
        value=p.GameMasterClientTemplateProto(template_id=name)
        value.item.unique_id=ident;value.item.item_type=kind;value.item.category=5
        value.item.ignore_inventory_space=True;value.item.hide_item_in_inventory=hidden
        values.append(value)
    return values
