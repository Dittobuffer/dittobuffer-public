"""Explicit local Route reward tables and durable per-day streak receipts."""
from dittobuffer.gameplay.game import now_ms,p

SCHEMA='''CREATE TABLE IF NOT EXISTS route_completion_days(player TEXT NOT NULL REFERENCES players(id),day INTEGER NOT NULL,attempt TEXT UNIQUE NOT NULL REFERENCES route_attempts(id),streak INTEGER NOT NULL,PRIMARY KEY(player,day));'''

def settings(game):
    cfg=game.routes.get('daily_rewards',{})
    if type(cfg.get('day_offset_minutes',0)) is not int or not -840<=cfg.get('day_offset_minutes',0)<=840:raise ValueError('Invalid Route day boundary')
    for key in ('first','repeat','streak'):
        value=cfg.get(key,{})
        if not isinstance(value,dict) or set(value)-{'xp','stardust','items','days'}:raise ValueError('Invalid Route reward table')
        for name in ('xp','stardust'):
            if type(value.get(name,0)) is not int or not 0<=value.get(name,0)<=1000000:raise ValueError('Invalid Route reward')
        for item,count in value.get('items',{}).items():
            if int(item) not in p.Item.values() or int(item) in (0,650,651) or type(count) is not int or not 0<count<=1000:raise ValueError('Invalid Route reward item')
    if cfg.get('streak') and (type(cfg['streak'].get('days')) is not int or not 2<=cfg['streak']['days']<=365):raise ValueError('Explicit Route streak length required')
    return cfg

def day(game):return (now_ms()+settings(game).get('day_offset_minutes',0)*60000)//86400000

def rewards(game,db,player):
    cfg=settings(game);today=day(game)
    first=not db.execute('SELECT 1 FROM route_completion_days WHERE player=? AND day=?',(player,today)).fetchone()
    yesterday=db.execute('SELECT streak FROM route_completion_days WHERE player=? AND day=?',(player,today-1)).fetchone()
    streak=yesterday[0]+1 if yesterday else 1
    result={'xp':game.routes['xp'],'stardust':game.routes['stardust'],'items':dict(game.routes['items'])}
    tables=[cfg.get('first' if first else 'repeat',{})]
    if first and cfg.get('streak') and streak%cfg['streak']['days']==0:tables.append(cfg['streak'])
    for table in tables:
        for name in ('xp','stardust'):result[name]+=table.get(name,0)
        for item,count in table.get('items',{}).items():result['items'][item]=result['items'].get(item,0)+count
    return result,first,streak

def record(game,db,attempt,streak):
    db.execute('INSERT OR IGNORE INTO route_completion_days VALUES(?,?,?,?)',(attempt['player'],day(game),attempt['id'],streak))
