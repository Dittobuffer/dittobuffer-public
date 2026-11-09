"""Owned neutral-avatar articles from the dependency-checked local registry."""
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from dittobuffer.gameplay.game import p,now_ms


@lru_cache(maxsize=1)
def registry():
    return json.loads(Path(__file__).with_name('avatar-catalog.json').read_text(encoding='utf8'))


def catalog(config):
    """Explicit local releases may price reviewed event articles; never unlock all."""
    items={v['id']:dict(v) for v in registry()['articles']};seen=set()
    for offer in config.get('wardrobe',{}).get('offers',[]):
        ident=offer.get('article_id');price=offer.get('coins');sku=offer.get('sku','')
        if ident in seen or ident not in items or items[ident]['ownership'] not in ('event','purchase'):
            raise ValueError('Unique reviewed event/purchase cosmetic required')
        if items[ident]['asset_state']!='DEPENDENCIES_AVAILABLE' or type(price) is not int or not 1<=price<=100000:
            raise ValueError('Complete cosmetic assets and bounded local price required')
        if not isinstance(sku,str) or not re.fullmatch(r'dittobuffer\.avatar\.[a-z0-9.-]{1,64}',sku):raise ValueError('Local cosmetic SKU required')
        seen.add(ident);items[ident].update(ownership='purchase',coins=price,sku=sku)
    skus=[v['sku'] for v in items.values() if v['ownership']=='purchase']
    if len(skus)!=len(set(skus)):raise ValueError('Duplicate cosmetic SKU')
    return list(items.values())


def templates(config):
    # Required by the native sheet initializer, even when no sale is active.
    result=[p.GameMasterClientTemplateProto(template_id='AVATAR_FEATURE_FLAGS',
        avatar_feature_flags=p.AvatarFeatureFlagsProto(corndog_enabled=True,discounted_items_enabled=False)),
        p.GameMasterClientTemplateProto(template_id='avatar_store_subcategory_filtering_flags',
        avatar_store_subcategory_filtering_flags=p.AvatarStoreSubcategoryFilteringEnabledProto(enabled=True)),
        p.GameMasterClientTemplateProto(template_id='avatar_store_footer_flags',
        avatar_store_footer_flags=p.AvatarStoreFooterEnabledProto(enabled=True))]
    for article in registry()['articles']:
        value=p.GameMasterClientTemplateProto(template_id='N_DISPLAY_'+article['id'])
        value.avatar_item_display.icon_address=article['display'].get('iconAddress','')
        value.avatar_item_display.display_string_id=article['display'].get('displayStringId','')
        result.append(value)
    # Native appearance listings also resolve swatch display templates while
    # initializing their tab. They do not constitute clothing ownership.
    from dittobuffer.gameplay.buddy import reference
    for article,display in reference().get('appearance_displays',{}).items():
        value=p.GameMasterClientTemplateProto(template_id='N_DISPLAY_'+article)
        value.avatar_item_display.icon_address=display.get('iconAddress','')
        value.avatar_item_display.display_string_id=display.get('displayStringId','')
        result.append(value)
    return result


def grant(game,db,player,article,source):
    if article not in {v['id'] for v in registry()['articles']}:raise ValueError('Unknown cosmetic')
    added=db.execute('INSERT OR IGNORE INTO cosmetic_ownership VALUES(?,?,?,?,0)',
                     (player,article,now_ms(),source)).rowcount
    if added:game.touch(db,player)
    return bool(added)


def ensure(game,db,player):
    for item in registry()['articles']:
        if item['ownership']=='starter':grant(game,db,player,item['id'],'starter')
    # Preserve equipped articles even if their assets are outside the new shop.
    for field,value in game.appearance(db,player).articles.ListFields():
        if value.article_id:
            added=db.execute('INSERT OR IGNORE INTO cosmetic_ownership VALUES(?,?,?,?,1)',
                (player,value.article_id,now_ms(),'existing-outfit')).rowcount
            if added:game.touch(db,player)


def inventory(game,db,player):
    for row in db.execute('SELECT * FROM cosmetic_ownership WHERE player_id=? ORDER BY article_id',(player,)):
        value=p.HoloInventoryItemProto()
        value.neutral_avatar_item.neutral_avatar_article_template_id=row['article_id']
        value.neutral_avatar_item.gained_ms=row['acquired_ms']
        yield value


def listings(game,db,player,body,appearance=False):
    p.ListAvatarStoreItemsProto.FromString(body)
    ensure(game,db,player)
    owned={r[0] for r in db.execute('SELECT article_id FROM cosmetic_ownership WHERE player_id=?',(player,))}
    out=p.ListAvatarStoreItemsOutProto(result=1)
    out.sale.SetInParent()
    for index,item in enumerate(catalog(game.config)):
        category={'pose':'POSES','gloves':'ACCESSORIES','eye_brow':'EYEBROW','eye_lash':'EYELASH'}.get(item['slot'],item['slot'].upper())
        entry=out.listings.add(sort_order=index,group_name='group_'+('poses' if item['slot']=='pose' else item['slot']),
            icon_address=item['display'].get('iconAddress',''),
            display_name_string_id=item['display'].get('displayStringId',''))
        entry.display.icon_address=entry.icon_address
        entry.display.display_string_id=entry.display_name_string_id
        entry.items.add(article_id=item['id'],iap_sku=item['sku'],is_owned=item['id'] in owned,
            is_purchasable=item['ownership']=='purchase' and item['id'] not in owned,slot=item['avatar_slot'])
        entry.lock.is_locked=item['id'] not in owned and item['ownership']!='purchase'
        # Legacy GroupName and neutral subcategory are distinct namespaces.
        # Using CAT_* for both makes the native catalog index one listing twice.
        subcategory='CAT_GLOVES' if item['slot']=='gloves' else 'CAT_'+category
        if item['slot']=='hair':subcategory='CAT_HAIR_SHAPE_LONG' if item['id']=='n_hair_default_a_0' else 'CAT_HAIR_SHAPE_SHORT'
        entry.subcategories.add(subcategory=subcategory,sort_order=index)
    # The appearance UI indexes these palettes directly during OnTabSelected;
    # absent groups abort initialization before Open/back/color controls bind.
    # Colors use the gradient slots, not wearable article slots/ownership.
    from dittobuffer.gameplay.buddy import reference
    for name,slot in [('SKIN',21),('EYE',22),('HAIR',23)]:
        gradients=sorted((v for v in registry()['appearance_addresses'] if v.startswith('n_gradient_'+name.lower()+'_')),
                         key=lambda v:int(v.rsplit('_',1)[1]))
        keys=['BAR','PRIMARY','SECONDARY'] if name!='SKIN' else ['BAR','PRIMARY']
        if name=='EYE':keys.append('FEATURED')
        for suffix in keys:
            entry=out.listings.add(group_name='CAT_'+name+'_COLOR_'+suffix)
            for ident in gradients:
                entry.items.add(article_id=ident,is_owned=True,slot=slot)
                display=reference().get('appearance_displays',{}).get(ident,{})
                entry.displays.add(icon_address=display.get('iconAddress',''),display_string_id=display.get('displayStringId',''))
    return out


def valid(avatar,old,owned):
    known={v['id']:v for v in registry()['articles']}
    appearance=set(registry()['appearance_addresses'])
    for field,value in avatar.articles.ListFields():
        ident=value.article_id
        if not ident:continue
        previous=getattr(old.articles,field.name).article_id
        if ident==previous:continue
        if field.name=='face_preset':
            if ident not in registry().get('face_presets',['FacePresetSeven']):return False
        elif field.name=='body_preset':
            if ident not in registry()['body_presets']:return False
        elif ident not in owned or ident not in known or known[ident]['slot']!=field.name:return False
        if value.color!=0 or value.slot_id<0:return False
    for field,prefix in [('skin_gradient_id','n_gradient_skin_'),('hair_gradient_id','n_gradient_hair_'),('eye_gradient_id','n_gradient_eye_')]:
        value=getattr(avatar,field)
        if value and value!=getattr(old,field) and (value not in appearance or not value.startswith(prefix)):return False
    def safe_numbers(message):
        for field,value in message.ListFields():
            if field.type==field.TYPE_MESSAGE:
                if not safe_numbers(value):return False
            elif field.type in (field.TYPE_FLOAT,field.TYPE_DOUBLE):
                if not math.isfinite(value) or not 0<=value<=1:return False
            elif field.type==field.TYPE_ENUM and value not in field.enum_type.values_by_number:return False
        return True
    return safe_numbers(avatar)


def save(game,db,player,body):
    request=p.SetNeutralAvatarProto.FromString(body)
    ensure(game,db,player)
    old=game.appearance(db,player)
    owned={r[0] for r in db.execute('SELECT article_id FROM cosmetic_ownership WHERE player_id=?',(player,))}
    avatar=request.player_neutral_avatar_proto
    # PlayerNeutralAvatarProto.UNEQUIP_ID is a native editing command, not a
    # wearable asset. Persist a cleared optional slot; core-slot checks below
    # still reject removing required hair/shirt/pants/shoes.
    for field,article in avatar.articles.ListFields():
        if article.article_id=='unequip':getattr(avatar.articles,field.name).Clear()
    if not request.HasField('player_neutral_avatar_proto') or any(not getattr(avatar.articles,k).article_id for k in ('hair','shirt','pants','shoes')) or not valid(avatar,old,owned):
        return p.SetNeutralAvatarOutProto(status=5)
    data=avatar.SerializeToString()
    if data!=old.SerializeToString():
        db.execute('UPDATE player_appearance SET data=? WHERE player_id=?',(data,player))
        game.touch(db,player)
    out=p.SetNeutralAvatarOutProto(status=1)
    out.player.CopyFrom(game.rpc_get_player(db,player,b'').player)
    out.neutral_avatar.CopyFrom(avatar)
    return out


