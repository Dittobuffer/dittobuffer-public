"""One byte-preserved local placeholder shared by every photo-pending POI."""
from functools import lru_cache
import hashlib
import json
import struct
from dittobuffer.world.world_store import ROOT,project_path


@lru_cache(maxsize=1)
def placeholder():
    config=json.loads((ROOT/'src/dittobuffer/world/world-images.json').read_text(encoding='utf8'))
    # A packaged install may not contain the repository-root copy. Fall back to
    # the bundled default stop artwork instead of failing the whole map reply.
    path=None
    for value in (config.get('placeholder'),'assets/static/pokestopimage.png',
                  'src/dittobuffer/resources/dittobuffer-stop.png'):
        if not value:continue
        candidate=project_path(value)
        if candidate.is_file():path=candidate;break
    if path is None:raise ValueError('No bundled world placeholder artwork')
    raw=path.read_bytes()
    if not raw.startswith(b'\x89PNG\r\n\x1a\n') or len(raw)<33 or len(raw)>12*1024*1024:raise ValueError('Supplied world placeholder must be a bounded PNG')
    width,height=struct.unpack('>II',raw[16:24])
    if not width or width!=height:raise ValueError('Supplied world placeholder must remain square; do not alter its aspect ratio')
    digest=hashlib.sha256(raw).hexdigest()
    return dict(body=raw,sha256=digest,width=width,height=height,url='http://127.0.0.1:8080/world/placeholders/'+digest+'.png')
