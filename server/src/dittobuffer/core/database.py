import contextlib
import json
from pathlib import Path
import sqlite3
import threading
from collections import deque

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES(1);
CREATE TABLE IF NOT EXISTS players(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, created_ms INTEGER NOT NULL,
 xp INTEGER NOT NULL DEFAULT 0, stardust INTEGER NOT NULL DEFAULT 0,
 revision INTEGER NOT NULL, captures INTEGER NOT NULL DEFAULT 0,
 throws INTEGER NOT NULL DEFAULT 0, stops INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions(ticket TEXT PRIMARY KEY, player_id TEXT NOT NULL REFERENCES players(id));
CREATE TABLE IF NOT EXISTS items(player_id TEXT NOT NULL REFERENCES players(id), item INTEGER NOT NULL,
 count INTEGER NOT NULL CHECK(count>=0), PRIMARY KEY(player_id,item));
CREATE TABLE IF NOT EXISTS spawns(id INTEGER PRIMARY KEY, spawnpoint TEXT NOT NULL,
 species INTEGER NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL, expires_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS encounters(player_id TEXT NOT NULL REFERENCES players(id),
 spawn_id INTEGER NOT NULL REFERENCES spawns(id), state TEXT NOT NULL DEFAULT 'active',
 result BLOB, PRIMARY KEY(player_id,spawn_id));
CREATE TABLE IF NOT EXISTS pokemon(id INTEGER PRIMARY KEY AUTOINCREMENT, player_id TEXT NOT NULL REFERENCES players(id),
 spawn_id INTEGER NOT NULL REFERENCES spawns(id), species INTEGER NOT NULL, data BLOB NOT NULL,
 UNIQUE(player_id,spawn_id));
CREATE TABLE IF NOT EXISTS candy(player_id TEXT NOT NULL REFERENCES players(id), family INTEGER NOT NULL,
 count INTEGER NOT NULL, PRIMARY KEY(player_id,family));
CREATE TABLE IF NOT EXISTS mega_catch_xl(player_id TEXT NOT NULL REFERENCES players(id), family INTEGER NOT NULL,
 count INTEGER NOT NULL CHECK(count>=0), PRIMARY KEY(player_id,family));
CREATE TABLE IF NOT EXISTS forts(id TEXT PRIMARY KEY, kind INTEGER NOT NULL, name TEXT NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL);
CREATE TABLE IF NOT EXISTS fort_visits(player_id TEXT NOT NULL REFERENCES players(id),
 fort_id TEXT NOT NULL REFERENCES forts(id), cooldown_ms INTEGER NOT NULL, result BLOB NOT NULL,
 PRIMARY KEY(player_id,fort_id));
CREATE TABLE IF NOT EXISTS rpc_receipts(ticket TEXT NOT NULL REFERENCES sessions(ticket),
 session_run TEXT NOT NULL, rpc_id INTEGER NOT NULL, request_hash TEXT NOT NULL, response BLOB NOT NULL,
 PRIMARY KEY(ticket,session_run,rpc_id));
CREATE TABLE IF NOT EXISTS tutorials(player_id TEXT NOT NULL REFERENCES players(id), value INTEGER NOT NULL,
 PRIMARY KEY(player_id,value));
CREATE TABLE IF NOT EXISTS player_appearance(player_id TEXT PRIMARY KEY REFERENCES players(id), data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS spawn_attributes(spawn_id INTEGER PRIMARY KEY REFERENCES spawns(id), data BLOB NOT NULL, level REAL NOT NULL);
CREATE TABLE IF NOT EXISTS spawn_generations(spawn_id INTEGER PRIMARY KEY REFERENCES spawns(id), generation INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS spawns_expiration ON spawns(expires_ms);
CREATE INDEX IF NOT EXISTS spawns_location ON spawns(lat,lng,expires_ms);
CREATE INDEX IF NOT EXISTS forts_location ON forts(lat,lng);
CREATE TABLE IF NOT EXISTS pokemon_levels(pokemon_id INTEGER PRIMARY KEY REFERENCES pokemon(id) ON DELETE CASCADE, level REAL NOT NULL);
CREATE TABLE IF NOT EXISTS pokemon_tombstones(player_id TEXT NOT NULL REFERENCES players(id), pokemon_id INTEGER NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY(player_id,pokemon_id));
CREATE TABLE IF NOT EXISTS encounter_deadlines(player_id TEXT NOT NULL, spawn_id INTEGER NOT NULL, deadline_ms INTEGER NOT NULL,
 PRIMARY KEY(player_id,spawn_id), FOREIGN KEY(player_id,spawn_id) REFERENCES encounters(player_id,spawn_id));
CREATE TABLE IF NOT EXISTS trainer_storage_rewards(player_id TEXT PRIMARY KEY REFERENCES players(id),pokemon INTEGER NOT NULL DEFAULT 0,items INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS trainer_level_entitlements(player_id TEXT NOT NULL REFERENCES players(id),level INTEGER NOT NULL,definition TEXT NOT NULL,pending TEXT NOT NULL,PRIMARY KEY(player_id,level));
CREATE TABLE IF NOT EXISTS trainer_level_caps(player_id TEXT PRIMARY KEY REFERENCES players(id),cap INTEGER NOT NULL CHECK(cap BETWEEN 70 AND 80),policy TEXT NOT NULL,created_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS research_task_state(player_id TEXT NOT NULL REFERENCES players(id),task_id TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(player_id,task_id));
CREATE TABLE IF NOT EXISTS level_reward_claims(player_id TEXT NOT NULL REFERENCES players(id), level INTEGER NOT NULL,
 PRIMARY KEY(player_id,level));
CREATE TABLE IF NOT EXISTS player_stats_history(player_id TEXT NOT NULL REFERENCES players(id),level INTEGER NOT NULL,
 reason INTEGER NOT NULL,data BLOB NOT NULL,PRIMARY KEY(player_id,level));
CREATE TABLE IF NOT EXISTS encounter_items(player_id TEXT NOT NULL, spawn_id INTEGER NOT NULL, item INTEGER NOT NULL,
 PRIMARY KEY(player_id,spawn_id), FOREIGN KEY(player_id,spawn_id) REFERENCES encounters(player_id,spawn_id));
CREATE TABLE IF NOT EXISTS walking(player_id TEXT PRIMARY KEY REFERENCES players(id),total_m REAL NOT NULL DEFAULT 0,
 hatches INTEGER NOT NULL DEFAULT 0,lat REAL,lng REAL,fix_ms INTEGER,arrival_ms INTEGER,seen_ms INTEGER);
CREATE TABLE IF NOT EXISTS admin_locations(player_id TEXT PRIMARY KEY REFERENCES players(id),lat REAL NOT NULL,lng REAL NOT NULL,
 accuracy REAL NOT NULL DEFAULT 0,seen_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS admin_spawns(spawn_id INTEGER PRIMARY KEY,created_ms INTEGER NOT NULL,label TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS admin_raids(seed INTEGER PRIMARY KEY,created_ms INTEGER NOT NULL,label TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS eggs(id INTEGER PRIMARY KEY,player_id TEXT NOT NULL REFERENCES players(id),distance_km REAL NOT NULL,
 data BLOB NOT NULL,created_ms INTEGER NOT NULL,fort_id TEXT NOT NULL REFERENCES forts(id),incubator_id TEXT);
CREATE TABLE IF NOT EXISTS incubators(id TEXT PRIMARY KEY,player_id TEXT NOT NULL REFERENCES players(id),item INTEGER NOT NULL,
 uses INTEGER NOT NULL,egg_id INTEGER UNIQUE REFERENCES eggs(id),start_km REAL NOT NULL DEFAULT 0,target_km REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS initial_incubator_grants(player_id TEXT PRIMARY KEY REFERENCES players(id));
CREATE TABLE IF NOT EXISTS hatches(egg_id INTEGER PRIMARY KEY,player_id TEXT NOT NULL REFERENCES players(id),
 data BLOB NOT NULL,xp INTEGER NOT NULL,candy INTEGER NOT NULL,dust INTEGER NOT NULL,distance_km REAL NOT NULL,reported INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS research_tasks(player_id TEXT NOT NULL REFERENCES players(id),id TEXT NOT NULL,
 definition TEXT NOT NULL,progress INTEGER NOT NULL DEFAULT 0,claimed INTEGER NOT NULL DEFAULT 0,
 abandoned INTEGER NOT NULL DEFAULT 0,created_ms INTEGER NOT NULL,updated_ms INTEGER NOT NULL,
 completed_ms INTEGER NOT NULL DEFAULT 0,fort_id TEXT REFERENCES forts(id),PRIMARY KEY(player_id,id));
CREATE TABLE IF NOT EXISTS research_visits(player_id TEXT NOT NULL REFERENCES players(id),fort_id TEXT NOT NULL REFERENCES forts(id),
 day TEXT NOT NULL,quest_id TEXT NOT NULL,PRIMARY KEY(player_id,fort_id,day),
 FOREIGN KEY(player_id,quest_id) REFERENCES research_tasks(player_id,id));
CREATE TABLE IF NOT EXISTS research_encounters(player_id TEXT NOT NULL REFERENCES players(id),quest_id TEXT NOT NULL,
 spawn_id INTEGER UNIQUE NOT NULL REFERENCES spawns(id),created_ms INTEGER NOT NULL,PRIMARY KEY(player_id,quest_id));
CREATE TABLE IF NOT EXISTS research_cards(player_id TEXT PRIMARY KEY REFERENCES players(id),cycle INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS research_breakthroughs(
 player_id TEXT NOT NULL REFERENCES players(id),cycle INTEGER NOT NULL,definition TEXT NOT NULL,
 earned_ms INTEGER NOT NULL,claimed_ms INTEGER NOT NULL DEFAULT 0,result BLOB,
 PRIMARY KEY(player_id,cycle));
CREATE TABLE IF NOT EXISTS special_research_encounters(
 player_id TEXT NOT NULL REFERENCES players(id),spawn_id INTEGER NOT NULL REFERENCES spawns(id),
 definition TEXT NOT NULL,created_ms INTEGER NOT NULL,opened_ms INTEGER NOT NULL DEFAULT 0,
 open_count INTEGER NOT NULL DEFAULT 0,playback_state TEXT NOT NULL DEFAULT 'ENTITLED',
 captured_id INTEGER,captured_ms INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(player_id,spawn_id));
CREATE TABLE IF NOT EXISTS research_stamps(player_id TEXT NOT NULL REFERENCES players(id),cycle INTEGER NOT NULL,
 day TEXT NOT NULL,stamp_ms INTEGER NOT NULL,PRIMARY KEY(player_id,day));
CREATE TABLE IF NOT EXISTS research_stories(player_id TEXT NOT NULL REFERENCES players(id),id TEXT NOT NULL,
 definition TEXT NOT NULL,stage_index INTEGER NOT NULL DEFAULT 0,completed INTEGER NOT NULL DEFAULT 0,
 created_ms INTEGER NOT NULL,PRIMARY KEY(player_id,id));
CREATE TABLE IF NOT EXISTS research_stage_claims(player_id TEXT NOT NULL,story_id TEXT NOT NULL,stage_index INTEGER NOT NULL,
 claimed_ms INTEGER NOT NULL,PRIMARY KEY(player_id,story_id,stage_index),
 FOREIGN KEY(player_id,story_id) REFERENCES research_stories(player_id,id));
CREATE TABLE IF NOT EXISTS research_dialog_reads(player_id TEXT NOT NULL REFERENCES players(id),quest_id TEXT NOT NULL,
 PRIMARY KEY(player_id,quest_id));
CREATE TABLE IF NOT EXISTS trainer_teams(player_id TEXT PRIMARY KEY REFERENCES players(id),team INTEGER NOT NULL DEFAULT 0 CHECK(team BETWEEN 0 AND 3),coins INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS gyms(fort_id TEXT PRIMARY KEY REFERENCES forts(id),team INTEGER NOT NULL DEFAULT 0,
 modified_ms INTEGER NOT NULL,occupied_ms INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS gym_defenders(pokemon_id INTEGER PRIMARY KEY REFERENCES pokemon(id),fort_id TEXT NOT NULL REFERENCES gyms(fort_id),
 owner_id TEXT NOT NULL REFERENCES players(id),deploy_ms INTEGER NOT NULL,motivation REAL NOT NULL DEFAULT 1,berries INTEGER NOT NULL DEFAULT 0,
 last_feed_ms INTEGER NOT NULL DEFAULT 0,battles_won INTEGER NOT NULL DEFAULT 0,battles_lost INTEGER NOT NULL DEFAULT 0,
 motivation_ms INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS gym_badges(player_id TEXT NOT NULL REFERENCES players(id),fort_id TEXT NOT NULL REFERENCES forts(id),
 score INTEGER NOT NULL DEFAULT 0,battles_won INTEGER NOT NULL DEFAULT 0,battles_lost INTEGER NOT NULL DEFAULT 0,
 deploys INTEGER NOT NULL DEFAULT 0,berries INTEGER NOT NULL DEFAULT 0,defended_ms INTEGER NOT NULL DEFAULT 0,updated_ms INTEGER NOT NULL,
 PRIMARY KEY(player_id,fort_id));
CREATE TABLE IF NOT EXISTS gym_returns(pokemon_id INTEGER NOT NULL,deploy_ms INTEGER NOT NULL,player_id TEXT NOT NULL REFERENCES players(id),
 returned_ms INTEGER NOT NULL,duration_ms INTEGER NOT NULL,coins INTEGER NOT NULL,PRIMARY KEY(pokemon_id,deploy_ms));
CREATE TABLE IF NOT EXISTS gym_battles(id TEXT PRIMARY KEY,player_id TEXT NOT NULL REFERENCES players(id),fort_id TEXT NOT NULL REFERENCES gyms(fort_id),
 defender_id INTEGER NOT NULL,created_ms INTEGER NOT NULL,end_ms INTEGER NOT NULL,state TEXT NOT NULL,settled INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS gym_battles_expiration ON gym_battles(settled,end_ms);
CREATE TABLE IF NOT EXISTS shop_offers(id TEXT PRIMARY KEY,player_id TEXT NOT NULL REFERENCES players(id),sku TEXT NOT NULL,
 definition TEXT NOT NULL,created_ms INTEGER NOT NULL,expires_ms INTEGER NOT NULL,response BLOB);
CREATE INDEX IF NOT EXISTS shop_offers_player ON shop_offers(player_id,sku,expires_ms);
CREATE TABLE IF NOT EXISTS gym_notifications(id TEXT PRIMARY KEY,player_id TEXT NOT NULL REFERENCES players(id),
 created_ms INTEGER NOT NULL,data BLOB NOT NULL,state INTEGER NOT NULL DEFAULT 0,updated_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS raids(seed INTEGER PRIMARY KEY,fort_id TEXT NOT NULL REFERENCES forts(id),
 definition TEXT NOT NULL,pokemon BLOB NOT NULL,spawn_ms INTEGER NOT NULL,battle_ms INTEGER NOT NULL,end_ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS raids_fort_time ON raids(fort_id,end_ms);
CREATE TABLE IF NOT EXISTS raid_members(seed INTEGER NOT NULL REFERENCES raids(seed),player_id TEXT NOT NULL REFERENCES players(id),
 joined_ms INTEGER NOT NULL,lobby_start_ms INTEGER NOT NULL,pass_item INTEGER NOT NULL,left_lobby INTEGER NOT NULL DEFAULT 0,
 battle_id TEXT NOT NULL DEFAULT '',reward BLOB,spawn_id INTEGER UNIQUE REFERENCES spawns(id),balls INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(seed,player_id));
CREATE TABLE IF NOT EXISTS raid_battles(id TEXT PRIMARY KEY,seed INTEGER NOT NULL REFERENCES raids(seed),
 player_id TEXT NOT NULL REFERENCES players(id),state TEXT NOT NULL,end_ms INTEGER NOT NULL,settled INTEGER NOT NULL DEFAULT 0,
 acknowledged INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS raid_combat_sessions(battle_id TEXT NOT NULL REFERENCES raid_battles(id),generation INTEGER NOT NULL,
 player_id TEXT NOT NULL REFERENCES players(id),token TEXT UNIQUE NOT NULL,created_ms INTEGER NOT NULL,revoked_ms INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(battle_id,generation));
CREATE TABLE IF NOT EXISTS raid_daily_passes(player_id TEXT NOT NULL REFERENCES players(id),day TEXT NOT NULL,
 granted_ms INTEGER NOT NULL,PRIMARY KEY(player_id,day));
CREATE TABLE IF NOT EXISTS raid_tokens(seed INTEGER NOT NULL,player_id TEXT NOT NULL,token TEXT UNIQUE NOT NULL,
 PRIMARY KEY(seed,player_id),FOREIGN KEY(seed,player_id) REFERENCES raid_members(seed,player_id));
CREATE TABLE IF NOT EXISTS raid_pass_receipts(seed INTEGER NOT NULL,player_id TEXT NOT NULL,item INTEGER NOT NULL,paid_ms INTEGER NOT NULL,
 PRIMARY KEY(seed,player_id),FOREIGN KEY(seed,player_id) REFERENCES raid_members(seed,player_id));
CREATE TABLE IF NOT EXISTS raid_event_receipts(battle_id TEXT NOT NULL REFERENCES raid_battles(id),sequence INTEGER NOT NULL,
 request_hash TEXT NOT NULL,response BLOB NOT NULL,generation INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(battle_id,generation,sequence));
INSERT OR IGNORE INTO schema_version VALUES(13);
INSERT OR IGNORE INTO schema_version VALUES(2);
"""

class Database:
    def enable_fair_transactions(self):
        # SQLite remains the cross-process authority. This queue prevents one
        # busy family client from starving other workers inside this process.
        self._family_condition=threading.Condition();self._family_queue=deque()

    @contextlib.contextmanager
    def admission(self):
        condition=getattr(self,'_family_condition',None)
        if condition is None:
            yield
            return
        marker=object()
        with condition:
            self._family_queue.append(marker)
            try:condition.wait_for(lambda:self._family_queue[0] is marker)
            except BaseException:
                self._family_queue.remove(marker);condition.notify_all();raise
        try:yield
        finally:
            with condition:
                self._family_queue.popleft();condition.notify_all()
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(self.connect()) as db:
            db.executescript(SCHEMA)
            # Additive extension: existing schema-version/player rows remain
            # unchanged, and no owned data is rewritten by Mega installation.
            from dittobuffer.gameplay.mega import SCHEMA as mega_schema
            db.executescript(mega_schema)
            if not db.execute('SELECT 1 FROM schema_version WHERE version=3').fetchone():
                # One fixed location can host many successive encounters. Keep
                # IDs and all child references intact while removing UNIQUE.
                with contextlib.closing(sqlite3.connect(str(self.path)+'.before-v3')) as backup:
                    db.backup(backup)
                db.execute('PRAGMA foreign_keys=OFF')
                try:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute('CREATE TABLE spawns_v3(id INTEGER PRIMARY KEY, spawnpoint TEXT NOT NULL, species INTEGER NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL, expires_ms INTEGER NOT NULL)')
                    db.execute('INSERT INTO spawns_v3 SELECT * FROM spawns')
                    db.execute('DROP TABLE spawns')
                    db.execute('ALTER TABLE spawns_v3 RENAME TO spawns')
                    if db.execute('PRAGMA foreign_key_check').fetchall():
                        raise RuntimeError('Spawn migration failed foreign-key validation')
                    db.execute('INSERT INTO schema_version VALUES(3)')
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise

                finally:
                    db.execute('PRAGMA foreign_keys=ON')
            db.execute('INSERT OR IGNORE INTO schema_version VALUES(4)')
            db.execute('INSERT OR IGNORE INTO schema_version VALUES(5)')
            db.execute('INSERT OR IGNORE INTO schema_version VALUES(6)')
            db.execute('INSERT OR IGNORE INTO schema_version VALUES(7)')
            if 'parent_id' not in {row['name'] for row in db.execute('PRAGMA table_info(research_tasks)')}:
                db.execute('ALTER TABLE research_tasks ADD COLUMN parent_id TEXT')
            if 'encounter_type' not in {row['name'] for row in db.execute('PRAGMA table_info(research_encounters)')}:
                db.execute('ALTER TABLE research_encounters ADD COLUMN encounter_type INTEGER NOT NULL DEFAULT 6')
            if 'client_quest_id' not in {row['name'] for row in db.execute('PRAGMA table_info(research_encounters)')}:
                db.execute("ALTER TABLE research_encounters ADD COLUMN client_quest_id TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE research_encounters SET client_quest_id=COALESCE((SELECT parent_id FROM research_tasks t WHERE t.player_id=research_encounters.player_id AND t.id=research_encounters.quest_id),quest_id)")
            db.execute('INSERT OR IGNORE INTO schema_version VALUES(8)')
            if 'motivation_ms' not in {row['name'] for row in db.execute('PRAGMA table_info(gym_defenders)')}:
                db.execute('ALTER TABLE gym_defenders ADD COLUMN motivation_ms INTEGER NOT NULL DEFAULT 0')
            if not db.execute('SELECT 1 FROM schema_version WHERE version=9').fetchone():
                # Previous clients always received Mystic. Preserve that existing choice.
                db.execute('INSERT OR IGNORE INTO trainer_teams(player_id,team) SELECT id,1 FROM players')
                db.execute('INSERT INTO schema_version VALUES(9)')
            if not db.execute('SELECT 1 FROM schema_version WHERE version=10').fetchone():
                # Battle history must survive a later legitimate transfer of a
                # returned defender. Only the active deployment owns a live FK.
                db.execute('BEGIN IMMEDIATE')
                try:
                    if any(row['table']=='pokemon' for row in db.execute('PRAGMA foreign_key_list(gym_battles)')):
                        db.execute('CREATE TABLE gym_battles_v10(id TEXT PRIMARY KEY,player_id TEXT NOT NULL REFERENCES players(id),fort_id TEXT NOT NULL REFERENCES gyms(fort_id),defender_id INTEGER NOT NULL,created_ms INTEGER NOT NULL,end_ms INTEGER NOT NULL,state TEXT NOT NULL,settled INTEGER NOT NULL DEFAULT 0)')
                        db.execute('INSERT INTO gym_battles_v10 SELECT * FROM gym_battles')
                        db.execute('DROP TABLE gym_battles')
                        db.execute('ALTER TABLE gym_battles_v10 RENAME TO gym_battles')
                        db.execute('CREATE INDEX gym_battles_expiration ON gym_battles(settled,end_ms)')
                    db.execute('INSERT INTO schema_version VALUES(10)')
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise

            db.execute('INSERT OR IGNORE INTO schema_version VALUES(11)')
            db.execute('INSERT OR IGNORE INTO schema_version VALUES(12)')
            if not db.execute('SELECT 1 FROM schema_version WHERE version=14').fetchone():
                # Preserve every old battle and receipt while allowing multiple
                # attempts. Child foreign keys still reference raid_battles(id).
                with contextlib.closing(sqlite3.connect(str(self.path)+'.before-v14')) as backup:db.backup(backup)
                db.execute('PRAGMA foreign_keys=OFF')
                try:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute('CREATE TABLE raid_battles_v14(id TEXT PRIMARY KEY,seed INTEGER NOT NULL REFERENCES raids(seed),player_id TEXT NOT NULL REFERENCES players(id),state TEXT NOT NULL,end_ms INTEGER NOT NULL,settled INTEGER NOT NULL DEFAULT 0,acknowledged INTEGER NOT NULL DEFAULT 0)')
                    ack='acknowledged' if 'acknowledged' in {r['name'] for r in db.execute('PRAGMA table_info(raid_battles)')} else '0'
                    db.execute('INSERT INTO raid_battles_v14 SELECT id,seed,player_id,state,end_ms,settled,'+ack+' FROM raid_battles')
                    db.execute('DROP TABLE raid_battles');db.execute('ALTER TABLE raid_battles_v14 RENAME TO raid_battles')
                    db.execute('CREATE INDEX raid_battles_seed_player ON raid_battles(seed,player_id)')
                    for row in db.execute('SELECT b.*,t.token FROM raid_battles b JOIN raid_members m ON m.battle_id=b.id JOIN raid_tokens t ON t.seed=m.seed AND t.player_id=m.player_id').fetchall():
                        state=json.loads(row['state'])
                        db.execute('INSERT OR IGNORE INTO raid_combat_sessions(battle_id,generation,player_id,token,created_ms) VALUES(?,?,?,?,?)',
                            (row['id'],state.get('generation',0),row['player_id'],row['token'],state['start']))
                    if db.execute('PRAGMA foreign_key_check').fetchall():raise RuntimeError('Raid attempt migration failed foreign-key validation')
                    db.execute('INSERT INTO schema_version VALUES(14)');db.commit()
                except BaseException:
                    db.rollback();raise
                finally:db.execute('PRAGMA foreign_keys=ON')

            if 'generation' not in {r['name'] for r in db.execute('PRAGMA table_info(raid_event_receipts)')}:
                db.execute('BEGIN IMMEDIATE')
                try:
                    db.execute('CREATE TABLE raid_event_receipts_v15(battle_id TEXT NOT NULL REFERENCES raid_battles(id),sequence INTEGER NOT NULL,request_hash TEXT NOT NULL,response BLOB NOT NULL,generation INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(battle_id,generation,sequence))')
                    # Original M8 native receipts use their full int64 IDs. The
                    # short-lived v14 generation namespace only admitted uint32.
                    for receipt in db.execute('SELECT e.*,b.state FROM raid_event_receipts e JOIN raid_battles b ON b.id=e.battle_id').fetchall():
                        state=json.loads(receipt['state']);generation=state.get('generation',0)
                        seq=receipt['sequence']
                        legacy_generation=min(generation,seq//2**32) if generation and seq<(generation+1)*2**32 else 0
                        db.execute('INSERT INTO raid_event_receipts_v15 VALUES(?,?,?,?,?)',(receipt['battle_id'],seq-legacy_generation*2**32,receipt['request_hash'],receipt['response'],legacy_generation))
                    db.execute('DROP TABLE raid_event_receipts');db.execute('ALTER TABLE raid_event_receipts_v15 RENAME TO raid_event_receipts')
                    db.commit()
                except BaseException:db.rollback();raise
            db.execute('INSERT OR IGNORE INTO schema_version VALUES(15)')
            if not db.execute('SELECT 1 FROM schema_version WHERE version=16').fetchone():
                with contextlib.closing(sqlite3.connect(str(self.path)+'.before-v16')) as backup:db.backup(backup)
                try:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute('CREATE TABLE pokedex_observations(player_id TEXT NOT NULL REFERENCES players(id),source_key TEXT NOT NULL,species INTEGER NOT NULL,form INTEGER NOT NULL,costume INTEGER NOT NULL,gender INTEGER NOT NULL,shiny INTEGER NOT NULL,caught INTEGER NOT NULL DEFAULT 0,data BLOB NOT NULL,PRIMARY KEY(player_id,source_key))')
                    db.execute('CREATE INDEX pokedex_player_species ON pokedex_observations(player_id,species)')
                    db.execute('CREATE TABLE pokedex_imports(player_id TEXT PRIMARY KEY REFERENCES players(id))')
                    db.execute('INSERT INTO schema_version VALUES(16)');db.commit()
                except BaseException:db.rollback();raise
            if not db.execute('SELECT 1 FROM schema_version WHERE version=17').fetchone():
                with contextlib.closing(sqlite3.connect(str(self.path)+'.before-v17')) as backup:db.backup(backup)
                try:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute('CREATE TABLE pokemon_tags(id INTEGER PRIMARY KEY AUTOINCREMENT,player_id TEXT NOT NULL REFERENCES players(id),name TEXT NOT NULL,color INTEGER NOT NULL,sort_index INTEGER NOT NULL,UNIQUE(player_id,name))')
                    db.execute('INSERT INTO schema_version VALUES(17)');db.commit()
                except BaseException:db.rollback();raise

            if not db.execute('SELECT 1 FROM schema_version WHERE version=18').fetchone():
                with contextlib.closing(sqlite3.connect(str(self.path)+'.before-v18')) as backup:db.backup(backup)
                from dittobuffer.gameplay.trainer import SCHEMA as trainer_schema
                try:
                    db.executescript('BEGIN IMMEDIATE;'+trainer_schema+'\nINSERT INTO schema_version VALUES(18);COMMIT;')
                except BaseException:db.rollback();raise

            if not db.execute('SELECT 1 FROM schema_version WHERE version=19').fetchone():
                with contextlib.closing(sqlite3.connect(str(self.path)+'.before-v19')) as backup:db.backup(backup)
                try:
                    db.executescript('''BEGIN IMMEDIATE;
CREATE TABLE trainer_settings(player_id TEXT NOT NULL REFERENCES players(id),kind TEXT NOT NULL,
 data BLOB NOT NULL,PRIMARY KEY(player_id,kind));
INSERT INTO schema_version VALUES(19);COMMIT;''')
                except BaseException:db.rollback();raise

            if not db.execute('SELECT 1 FROM schema_version WHERE version=20').fetchone():
                # Earlier builds materialized a "Dittobuffer Stop"/"Dittobuffer
                # Gym" on every map tile. That feature is gone; purge its
                # persisted forts and the state attached to them once. Rows
                # owned by real world/admin/OSM POIs are untouched.
                with contextlib.closing(sqlite3.connect(str(self.path)+'.before-v20')) as backup:db.backup(backup)
                db.execute('PRAGMA foreign_keys=OFF')
                try:
                    db.executescript('''BEGIN IMMEDIATE;
DELETE FROM gym_defenders WHERE fort_id LIKE 'dittobuffer:%';
DELETE FROM gym_battles WHERE fort_id LIKE 'dittobuffer:%';
DELETE FROM gym_badges WHERE fort_id LIKE 'dittobuffer:%';
DELETE FROM gyms WHERE fort_id LIKE 'dittobuffer:%';
DELETE FROM incubators WHERE egg_id IN (SELECT id FROM eggs WHERE fort_id LIKE 'dittobuffer:%');
DELETE FROM eggs WHERE fort_id LIKE 'dittobuffer:%';
DELETE FROM fort_visits WHERE fort_id LIKE 'dittobuffer:%';
DELETE FROM research_visits WHERE fort_id LIKE 'dittobuffer:%';
UPDATE research_tasks SET fort_id=NULL WHERE fort_id LIKE 'dittobuffer:%';
DELETE FROM raid_tokens WHERE seed IN (SELECT seed FROM raids WHERE fort_id LIKE 'dittobuffer:%');
DELETE FROM raid_pass_receipts WHERE seed IN (SELECT seed FROM raids WHERE fort_id LIKE 'dittobuffer:%');
DELETE FROM raid_members WHERE seed IN (SELECT seed FROM raids WHERE fort_id LIKE 'dittobuffer:%');
DELETE FROM raid_combat_sessions WHERE battle_id IN (SELECT id FROM raid_battles WHERE seed IN (SELECT seed FROM raids WHERE fort_id LIKE 'dittobuffer:%'));
DELETE FROM raid_event_receipts WHERE battle_id IN (SELECT id FROM raid_battles WHERE seed IN (SELECT seed FROM raids WHERE fort_id LIKE 'dittobuffer:%'));
DELETE FROM raid_battles WHERE seed IN (SELECT seed FROM raids WHERE fort_id LIKE 'dittobuffer:%');
DELETE FROM raids WHERE fort_id LIKE 'dittobuffer:%';
DELETE FROM forts WHERE id LIKE 'dittobuffer:%';
INSERT INTO schema_version VALUES(20);COMMIT;''')
                except BaseException:
                    db.rollback();raise
                finally:
                    db.execute('PRAGMA foreign_keys=ON')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        return db

    @contextlib.contextmanager
    def transaction(self):
        with self.admission():
            with self._transaction() as db:yield db

    @contextlib.contextmanager
    def _transaction(self):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
