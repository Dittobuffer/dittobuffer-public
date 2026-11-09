"""Opt-in original collectible cards, separate from ordinary type backdrops."""
import math
from dittobuffer.gameplay.game import p

def definitions(config):
    cfg=config.get('pokemon_backgrounds',{})
    if not cfg.get('enabled',False):return []
    result=cfg.get('cards',[]);seen=set()
    for card in result:
        ident=card['id']
        if type(ident) is not int or not 32000<=ident<33000 or ident in seen:raise ValueError('Use unique project custom card IDs 32000-32999')
        seen.add(ident)
        if card.get('image_url')!='http://127.0.0.1:8080/assets/dittobuffer-stop.png':raise ValueError('Only the existing original local artwork is currently approved')
        if card.get('type') not in (1,2):raise ValueError('Card type must be location or special')
        probability=card.get('probability',0)
        if type(probability) not in (int,float) or not math.isfinite(probability) or not 0<=probability<=1:raise ValueError('Invalid background rate')
        if not card.get('sources') or set(card['sources'])-{'wild','egg','raid','research'}:raise ValueError('Invalid background sources')
    return result

def assign(game,pokemon,rng,source,requested=0):
    eligible=[c for c in definitions(game.config) if source in c['sources'] and
        (not c.get('species') or pokemon.pokemon_id in c['species']) and
        (not c.get('forms') or pokemon.pokemon_display.form in c['forms']) and (not requested or c['id']==requested)]
    for card in eligible:
        if rng.random()<card.get('probability',0):
            pokemon.pokemon_display.location_card.location_card=card['id'];return

def templates(config):
    cards=definitions(config)
    if not cards:return []
    result=[]
    gate=p.GameMasterClientTemplateProto(template_id='LOCATION_CARD_BACKGROUNDS_FEATURE_GATE')
    gate.feature_gate.status=1;gate.feature_gate.rollout_percentage=100
    gate.feature_gate.sub_feature_gate_list.add(name='BACKGROUNDS_PDP_RENDERER_SUB_FEATURE_GATE',status=1,rollout_percentage=100)
    result.append(gate)
    feature=p.GameMasterClientTemplateProto(template_id='LOCATION_CARD_FEATURE_SETTINGS')
    feature.location_card_feature_settings.background_display_seconds=5
    feature.location_card_feature_settings.background_auto_loop=1;result.append(feature)
    for card in cards:
        template=p.GameMasterClientTemplateProto(template_id='LIBRE_LOCATION_CARD_'+str(card['id']))
        template.location_card_settings.CopyFrom(p.LocationCardSettingsProto(location_card=card['id'],image_url=card['image_url'],
            card_type=card['type'],background_eligibility_status=1,background_auto_loop_override=1,background_display_seconds_override=5,card_treatment=1))
        result.append(template)
    return result
