"""Configurable cosmetic bundles for level, research and future event callers."""
from dittobuffer.gameplay.game import now_ms
from dittobuffer.gameplay.progression import bounds,cap
from dittobuffer.gameplay.wardrobe import registry,grant


def validate(config):
    rules=config.get('trainer_rewards',{})
    known={x['id'] for x in registry()['articles']}
    bundles=rules.get('definitions',{})
    for name,value in bundles.items():
        if not isinstance(name,str) or not name or len(name)>100:raise ValueError('Bounded cosmetic reward identifier required')
        articles=value.get('cosmetics',[])
        if not articles or len(articles)!=len(set(articles)) or any(x not in known for x in articles):raise ValueError('Cosmetic reward requires unique verified article IDs')
        level=value.get('minimum_level',1)
        if type(level) is not int or not 1<=level<=cap(config):raise ValueError('Cosmetic reward level outside supported progression cap')
    for category in ('level','research'):
        if any(v not in bundles for v in rules.get(category,{}).values()):raise ValueError('Unknown configured cosmetic reward bundle')
    return rules


def claim(game,db,player,name,source,key):
    if source not in ('level','research','event','campaign') or not isinstance(key,str) or not 1<=len(key)<=200:raise ValueError('Cosmetic reward needs an explicit source identity')
    rule=validate(game.config)['definitions'][name]
    if bounds(game.config,game.player(db,player)['xp'])[0]<rule.get('minimum_level',1):return []
    source_key=source+':'+key
    marker='bundle:'+name
    if db.execute('SELECT 1 FROM trainer_unlock_claims WHERE player_id=? AND source_key=? AND resource=?',(player,source_key,marker)).fetchone():return []
    # A bundle receipt freezes the entire grant across later configuration edits.
    db.execute('INSERT INTO trainer_unlock_claims VALUES(?,?,?,?)',(player,source_key,marker,now_ms()))
    added=[]
    for article in rule['cosmetics']:
        if grant(game,db,player,article,source_key):added.append(article)
        db.execute('INSERT OR IGNORE INTO trainer_unlock_claims VALUES(?,?,?,?)',(player,source_key,article,now_ms()))
    from dittobuffer.gameplay.trainer import event
    event(game,db,player,'cosmetic:'+source_key+':'+name,'cosmetic_reward',dict(bundle=name,cosmetics=list(rule['cosmetics'])))
    return added


def trigger(game,db,player,source,definition,key):
    rules=validate(game.config);name=rules.get(source,{}).get(str(definition))
    return claim(game,db,player,name,source,key) if name else []
