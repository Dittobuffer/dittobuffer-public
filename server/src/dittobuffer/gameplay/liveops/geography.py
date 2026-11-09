# Copyright (C) Dittobuffer Contributors
# Copyright (C) chucny
# Copyright (C) gekkedavi
#
# This file is part of Dittobuffer. It is distributed under the GNU General
# Public License v3.0 (see LICENSE). The copyright notices in this file must
# be preserved in all copies, forks, and derivative works. Removing or
# altering these notices violates the license and copyright law.

"""Read-only region imports from the published world, never guessed city bounds."""
from functools import lru_cache
import json,sqlite3
from pathlib import Path
from shapely import from_wkb
from shapely.geometry import mapping
from dittobuffer.paths import ROOT

@lru_cache(maxsize=4)
def provinces(manifest):
    pointer=(ROOT/manifest).resolve()
    if not pointer.is_relative_to(ROOT):raise ValueError('Invalid published world pointer')
    doc=json.loads(pointer.read_text(encoding='utf-8'));file=(ROOT/doc['database']).resolve()
    if not file.is_relative_to(ROOT):raise ValueError('Invalid published world database')
    with sqlite3.connect(file.as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT osm,tags,geometry FROM features WHERE layer=-2 LIMIT 100').fetchall()
    result=[]
    for osm,tags,blob in rows:
        tags=json.loads(tags);shape=from_wkb(blob).simplify(.0001,preserve_topology=True)
        if shape.geom_type not in ('Polygon','MultiPolygon') or not shape.is_valid:continue
        result.append(dict(id='osm-'+osm,name=tags.get('name',osm),type='polygon',geometry=mapping(shape),
            source={'osm':osm,'manifest_sha256':doc.get('sha256'),'license':'ODbL-1.0','attribution':'OpenStreetMap contributors','simplification_degrees':.0001}))
    return tuple(result)

def sources(config):
    cfg=config.get('world',{})
    if not cfg.get('enabled'):return []
    return list(provinces(cfg.get('manifest','data/world/current.json')))
