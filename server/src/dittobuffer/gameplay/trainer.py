"""Additive trainer records and once-only committed gameplay events."""
import json
import time

SCHEMA='''
CREATE TABLE cosmetic_ownership(player_id TEXT NOT NULL REFERENCES players(id),article_id TEXT NOT NULL,
 acquired_ms INTEGER NOT NULL,source TEXT NOT NULL,viewed INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(player_id,article_id));
CREATE TABLE trainer_preferences(player_id TEXT PRIMARY KEY REFERENCES players(id),data BLOB NOT NULL);
CREATE TABLE trainer_events(player_id TEXT NOT NULL REFERENCES players(id),source_key TEXT NOT NULL,kind TEXT NOT NULL,
 created_ms INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(player_id,source_key));
CREATE INDEX trainer_events_recent ON trainer_events(player_id,created_ms);
CREATE TABLE trainer_counters(player_id TEXT NOT NULL REFERENCES players(id),name TEXT NOT NULL,value REAL NOT NULL DEFAULT 0,
 PRIMARY KEY(player_id,name));
CREATE TABLE trainer_counter_imports(player_id TEXT PRIMARY KEY REFERENCES players(id),policy TEXT NOT NULL,created_ms INTEGER NOT NULL);
CREATE TABLE trainer_medals(player_id TEXT NOT NULL REFERENCES players(id),badge INTEGER NOT NULL,rank INTEGER NOT NULL DEFAULT 0,
 notified_rank INTEGER NOT NULL DEFAULT 0,claimed_rank INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(player_id,badge));
CREATE TABLE trainer_unlock_claims(player_id TEXT NOT NULL REFERENCES players(id),source_key TEXT NOT NULL,resource TEXT NOT NULL,
 created_ms INTEGER NOT NULL,PRIMARY KEY(player_id,source_key,resource));
CREATE TABLE buddy_active(player_id TEXT PRIMARY KEY REFERENCES players(id),pokemon_id INTEGER NOT NULL,
 swapped_ms INTEGER NOT NULL,swaps_day TEXT NOT NULL,swaps_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE buddy_state(player_id TEXT NOT NULL REFERENCES players(id),pokemon_id INTEGER NOT NULL,data TEXT NOT NULL,
 PRIMARY KEY(player_id,pokemon_id));
CREATE TABLE buddy_daily(player_id TEXT NOT NULL REFERENCES players(id),pokemon_id INTEGER NOT NULL,day TEXT NOT NULL,
 category INTEGER NOT NULL,hearts INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(player_id,pokemon_id,day,category));
CREATE TABLE buddy_action_receipts(player_id TEXT NOT NULL REFERENCES players(id),pokemon_id INTEGER NOT NULL,
 kind TEXT NOT NULL,action_key TEXT NOT NULL,created_ms INTEGER NOT NULL,response BLOB NOT NULL,
 PRIMARY KEY(player_id,pokemon_id,kind,action_key));
'''


def event(game,db,player,key,kind,data,stamp=None):
    """Call inside the existing action transaction; source IDs deduplicate retries."""
    inserted=db.execute('INSERT OR IGNORE INTO trainer_events VALUES(?,?,?,?,?)',
        (player,key,kind,time.time_ns()//1000000 if stamp is None else stamp,json.dumps(data,sort_keys=True,separators=(',',':')))).rowcount
    if inserted:
        game.touch(db,player)
        mapped={'gym_battle':'gym_win' if data.get('result')==2 else None,'pvp_battle':'pvp_win' if data.get('won') else None}.get(kind,kind)
        if mapped in ('hatch','evolve','buddy_candy','gym_win','pvp_win','mega_evolve','buddy_heart','trade','zygarde_cell','super_effective_charge'):
            from dittobuffer.gameplay.research import progress
            context=dict(data)
            if data.get('pokemon'):
                from dittobuffer.gameplay.game import p
                mon=p.PokemonProto.FromString(bytes.fromhex(data['pokemon']));context.update({'species':mon.pokemon_id,'form':mon.pokemon_display.form,'gender':mon.pokemon_display.gender,'shiny':mon.pokemon_display.shiny,'alignment':mon.pokemon_display.alignment,'costume':mon.pokemon_display.costume,'max_mode':mon.pokemon_display.bread_mode_enum,'types':game.forms.get((mon.pokemon_id,mon.pokemon_display.form),game.species[mon.pokemon_id])['types']})
            progress(game,db,player,mapped,amount=data.get('amount',data.get('candy',1) if kind=='buddy_candy' else 1),context=context)
    return bool(inserted)


def increment(db,player,name,value=1):
    db.execute('INSERT INTO trainer_counters VALUES(?,?,?) ON CONFLICT(player_id,name) DO UPDATE SET value=value+excluded.value',
               (player,name,value))
