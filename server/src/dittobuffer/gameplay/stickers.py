"""Reviewed native sticker definitions; ownership stays in the existing Gift tables."""
import json
from dittobuffer.gameplay.game import ROOT, p

def catalog(config):
    path=config.get('social',{}).get('sticker_catalog')
    if not path:return {}
    target=(ROOT/path).resolve()
    if not target.is_relative_to(ROOT):raise ValueError('Project-local sticker catalog required')
    records={}
    for row in json.loads(target.read_text())['records']:
        value=p.GameMasterClientTemplateProto.FromString(bytes.fromhex(row['template_hex']))
        ident=value.sticker_metadata.sticker_id
        if not ident or ident!=row['id'] or ident in records or type(row['max_count']) is not int or not 1<=row['max_count']<=1000 or value.sticker_metadata.max_count!=row['max_count'] or row['address']!=ident.lower():raise ValueError('Invalid reviewed sticker definition')
        records[ident]=row
    return records

def templates(config):
    return [p.GameMasterClientTemplateProto.FromString(bytes.fromhex(row['template_hex'])) for row in catalog(config).values()]

def grant(game,db,player,ident,count):
    record=game.sticker_catalog.get(ident)
    if record is None or type(count) is not int or count<0:raise ValueError('Unreleased sticker reward')
    row=db.execute('SELECT count FROM social_stickers WHERE player_id=? AND sticker=?',(player,ident)).fetchone()
    added=min(count,max(0,record['max_count']-(row[0] if row else 0)))
    if added:
        db.execute('INSERT INTO social_stickers VALUES(?,?,?,0) ON CONFLICT(player_id,sticker) DO UPDATE SET count=count+excluded.count',(player,ident,added));game.touch(db,player)
    return added
