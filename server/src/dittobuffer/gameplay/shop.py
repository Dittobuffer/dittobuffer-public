"""Local coin-only catalog. A durable, owned offer is the purchase retry key."""
import json
import secrets
from pathlib import Path
from dittobuffer.gameplay.game import p,now_ms
from dittobuffer.gameplay.gyms import team

PERMITTED_ITEMS={1,2,3,101,201,701} # Existing ball, Potion, Revive and Razz handlers.

def catalog(config):
    rules=json.loads(Path(__file__).with_name('shop.json').read_text(encoding='utf8'))|config.get('shop',{})
    if type(rules['offer_seconds']) is not int or not 30<=rules['offer_seconds']<=86400:
        raise ValueError('Shop offer lifetime must be 30-86400 seconds')
    seen=set()
    rules['products']=[dict(v) for v in rules['products']]
    for item in rules['products']:
        if not isinstance(item['sku'],str) or not item['sku'] or item['sku'] in seen:raise ValueError('Unique shop SKU required')
        seen.add(item['sku'])
        if type(item['item']) is not int or item['item'] not in PERMITTED_ITEMS:raise ValueError('Unsupported local shop item')
        if any(type(item[k]) is not int or not 1<=item[k]<=100000 for k in ('coins','quantity')):raise ValueError('Invalid local price/quantity')
        if not item.get('sprite'):raise ValueError('Client sprite reference required')
    from dittobuffer.gameplay.wardrobe import catalog as wardrobe_catalog
    rules['products']+= [dict(sku=v['sku'],article=v['id'],coins=v['coins'],quantity=1,
        sprite=v['display'].get('iconAddress',''),kind='cosmetic') for v in wardrobe_catalog(config) if v['ownership']=='purchase']
    skus=[v['sku'] for v in rules['products']]
    if len(skus)!=len(set(skus)):raise ValueError('Duplicate item/cosmetic shop SKU')
    return rules

def templates(config):
    from dittobuffer.gameplay.raids import client_settings
    result=[p.GameMasterClientTemplateProto(template_id='IAP_SETTINGS',iap_settings=p.IapSettingsProto()),
            p.GameMasterClientTemplateProto(template_id='RAID_SETTINGS',raid_settings_proto=client_settings(config)),
            p.GameMasterClientTemplateProto(template_id='IAP_CATEGORY_ITEMS',
                iap_category_display=p.IapItemCategoryDisplayProto(category=2,sort_order=1,display_rows=3))]
    for index,item in enumerate(catalog(config)['products']):
        result.append(p.GameMasterClientTemplateProto(template_id=item['sku'],iap_item_display=p.IapItemDisplayProto(
            sku=item['sku'],category=2,sort_order=index,sprite_id=item['sprite'],hidden=item.get('kind')=='cosmetic',min_level=1,max_level=100)))
    return result

def available(game,db,player,body):
    p.GetAvailableSkusAndBalancesProto.FromString(body)
    rules=catalog(game.config);stamp=now_ms()
    # The platform IAP manager refuses even coin purchases without this opaque
    # local-store identifier. Authentication and offer ownership are checked by
    # the RPC session; this is not a production billing token.
    out=p.GetAvailableSkusAndBalancesOutProto(status=1,processed_at_ms=stamp,player_token='local-'+player)
    out.balance.add(currency_type='POKECOIN',quantity=team(db,player)['coins'])
    for item in rules['products']:
        if item.get('kind')=='cosmetic' and db.execute('SELECT 1 FROM cosmetic_ownership WHERE player_id=? AND article_id=?',(player,item['article'])).fetchone():continue
        definition=json.dumps(item,sort_keys=True,separators=(',',':'))
        offer=db.execute('SELECT id FROM shop_offers WHERE player_id=? AND sku=? AND definition=? AND expires_ms>? AND response IS NULL ORDER BY created_ms DESC LIMIT 1',
                         (player,item['sku'],definition,stamp)).fetchone()
        if offer is None:
            ident='local-'+secrets.token_hex(16)
            db.execute('INSERT INTO shop_offers VALUES(?,?,?,?,?,?,NULL)',(ident,player,item['sku'],definition,stamp,stamp+rules['offer_seconds']*1000))
        else:ident=offer['id']
        sku=out.available_sku.add(id=item['sku'],can_be_purchased=True,offer_id=ident)
        sku.price.add(currency_type='POKECOIN',quantity=item['coins'])
        # IapServiceUtil.StringToItemType understands ITEM_ + snake-case names.
        if item.get('kind')=='cosmetic':
            sku.game_item_content.add(type=item['article'],quantity=1)
        else:
            name=p.Item.Name(item['item']).removeprefix('Item_')
            import re
            name=re.sub(r'(?<!^)(?=[A-Z])','_',name).upper()
            sku.game_item_content.add(type='ITEM_'+name,quantity=item['quantity'])
    return out

def is_available(game,db,player,body):
    request=p.IsSkuAvailableProto.FromString(body)
    item=next((v for v in catalog(game.config)['products'] if v['sku']==request.sku_id),None)
    return p.IsSkuAvailableOutProto(is_sku_available=item is not None and (not request.verify_price or request.coin_cost==item['coins']))

def purchase(game,db,player,body):
    request=p.PurchaseSkuProto.FromString(body)
    offer=db.execute('SELECT * FROM shop_offers WHERE id=? AND player_id=? AND sku=?',(request.offer_id,player,request.sku_id)).fetchone()
    if offer is None:return p.PurchaseSkuOutProto(status=6)
    if offer['response'] is not None:return p.PurchaseSkuOutProto.FromString(offer['response'])
    current=next((v for v in catalog(game.config)['products'] if v['sku']==request.sku_id),None)
    if current is None:return p.PurchaseSkuOutProto(status=4)
    if offer['expires_ms']<now_ms() or json.loads(offer['definition'])!=current:return p.PurchaseSkuOutProto(status=6)
    cosmetic=current.get('kind')=='cosmetic'
    if cosmetic and db.execute('SELECT 1 FROM cosmetic_ownership WHERE player_id=? AND article_id=?',(player,current['article'])).fetchone():return p.PurchaseSkuOutProto(status=6)
    if team(db,player)['coins']<current['coins']:return p.PurchaseSkuOutProto(status=3)
    total=game.bag_count(db,player)
    if not cosmetic and total+current['quantity']>game.capacity(db,player,'item_capacity'):return p.PurchaseSkuOutProto(status=5)
    db.execute('UPDATE trainer_teams SET coins=coins-? WHERE player_id=?',(current['coins'],player))
    if cosmetic:
        from dittobuffer.gameplay.wardrobe import grant
        grant(game,db,player,current['article'],'shop:'+offer['id'])
    else:
        db.execute('INSERT INTO items VALUES(?,?,?) ON CONFLICT(player_id,item) DO UPDATE SET count=count+excluded.count',
                   (player,current['item'],current['quantity']))
    game.touch(db,player)
    out=p.PurchaseSkuOutProto(status=1)
    out.currency_update.add(currency_name='POKECOIN',currency_delta=-current['coins'],currency_balance=team(db,player)['coins'])
    db.execute('UPDATE shop_offers SET response=? WHERE id=?',(out.SerializeToString(),offer['id']))
    return out
