"""Opt-in Explorer NPC metadata from a pinned, independently restored cache.

The compact distributed Game Master omits NPC display records. The native map
decoration requires CHARACTER_EXPLORER before requesting any Mateo model. Keep
the proprietary input local; only the one verified record is projected.
"""
import hashlib
from pathlib import Path
from protocol import game_pb2 as p
from protocol.opaque import fields

from dittobuffer.paths import ROOT
DEFAULT_SOURCE='assets/pokemon/local/npc/game-master.cache'
DEFAULT_SOURCE_SHA256='dc68848c8b7b003a7a00aff17849db6b554b3898ab399bbfaa159bafcf34c5d4'
EXPLORER_TEMPLATE_SHA256='8c214ce17e76d75e875def5ef1dc1fbbc1cb913029700c1be6d2ae11a48d333d'

def explorer_record(raw):
    """Extract the inner template from the source cache's item envelope."""
    matches=[]
    for number,kind,_,outer in fields(raw):
        if number!=2 or kind!=2:continue
        parts=list(fields(outer));name=next((v.decode('utf8') for n,k,_,v in parts if n==1 and k==2),'')
        if name!='CHARACTER_EXPLORER':continue
        matches.extend(v for n,k,_,v in parts if n==2 and k==2)
    if len(matches)!=1:raise ValueError('Exactly one authorized CHARACTER_EXPLORER record required')
    value=matches[0]
    if hashlib.sha256(value).hexdigest()!=EXPLORER_TEMPLATE_SHA256:raise ValueError('Explorer NPC record differs from the reviewed compatibility input')
    template=p.GameMasterClientTemplateProto.FromString(value)
    settings=template.invasion_npc_display_settings
    if template.template_id!='CHARACTER_EXPLORER' or settings.model_name!='combat_explorer' or settings.avatar.avatar!=2:raise ValueError('Invalid native Explorer model contract')
    return template

def templates(config):
    routes=config.get('routes',{});mateo=routes.get('mateo',{})
    if not routes.get('enabled') or not mateo.get('enabled'):return []
    source=mateo.get('npc_display_source',DEFAULT_SOURCE);expected=mateo.get('npc_display_source_sha256',DEFAULT_SOURCE_SHA256)
    if not isinstance(source,str) or not isinstance(expected,str):raise ValueError('Local NPC compatibility source and SHA256 required')
    path=(ROOT/source).resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():raise ValueError('Restore the authorized local Explorer NPC Game Master source: '+source)
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Explorer NPC source integrity failure')
    template=explorer_record(raw)
    # Explicit local display policy. Preserve the source's exact model/avatar
    # identity; do not use its unused Spark fallback art or production image URL.
    settings=template.invasion_npc_display_settings;settings.display_on_map=True
    for name in ('icon_url','trainer_quote','backdrop_image_bundle'):settings.ClearField(name)
    return [template]
