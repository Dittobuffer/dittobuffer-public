"""Original tiny card artwork and native branch display fields (no client cover)."""
from functools import lru_cache
import struct,zlib
from urllib.parse import urlsplit,urlunsplit
from dittobuffer.gameplay.game import p

CARD_PATH='/assets/dittobuffer-research-card.png'
PRESENTATION_VERSION=2

@lru_cache(maxsize=1)
def card_png():
    # Native branch prefabs contain a white Mask graphic above the button tint.
    # A real background Sprite is required; otherwise white labels sit on white.
    # Original project-owned gradient, generated in memory; no game imagery/font.
    width,height=512,128
    raw=b''.join(b'\x00'+bytes((14+y//12,99+y//6,99+y//8))*width for y in range(height))
    def chunk(kind,body):return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')

def card_url(game):
    world=game.config.get('world',{})
    origin=world.get('map_tiles_base')
    if not origin:origin='http://127.0.0.1:'+str(world.get('map_tiles_port',8081))
    url=urlsplit(origin)
    if url.scheme not in ('http','https') or not url.netloc:raise ValueError('Research artwork requires a valid configured image origin')
    return urlunsplit((url.scheme,url.netloc,CARD_PATH,'',''))

def literal(value):return str(value).replace('<','\u2039').replace('>','\u203a')

def branch(game,path):
    title=literal(path['title']);description=literal(path.get('description',''))
    short=description[:96]+('...' if len(description)>96 else '')
    label='<size=30>'+title+'</size>'
    if short:label+='\n<size=18>'+short+'</size>'
    return p.QuestBranchDisplayProto(title_key=path['title'],description_key=path.get('description',path['title']),
        button_text_key=label,button_background_color='#FFFFFF',button_text_color='#FFFFFF',arrow_button_color='#FFFFFF',
        button_background_image_url=card_url(game))
