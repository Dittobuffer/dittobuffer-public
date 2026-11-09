"""Durable editorial revisions and publications, separate from player ownership."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time

SCHEMA = """
PRAGMA journal_mode=WAL;
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS content(id TEXT PRIMARY KEY,kind TEXT NOT NULL,revision INTEGER NOT NULL,
 published_revision INTEGER,status TEXT NOT NULL,updated_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS revisions(id TEXT NOT NULL,revision INTEGER NOT NULL,document TEXT NOT NULL,
 digest TEXT NOT NULL,actor TEXT NOT NULL,created_ms INTEGER NOT NULL,PRIMARY KEY(id,revision));
CREATE TABLE IF NOT EXISTS history(sequence INTEGER PRIMARY KEY,id TEXT NOT NULL,action TEXT NOT NULL,
 actor TEXT NOT NULL,revision INTEGER,details TEXT NOT NULL,created_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS entitlements(player TEXT NOT NULL,entitlement TEXT NOT NULL,granted_ms INTEGER NOT NULL,
 expires_ms INTEGER NOT NULL,actor TEXT NOT NULL,PRIMARY KEY(player,entitlement));
CREATE TABLE IF NOT EXISTS regions(id TEXT PRIMARY KEY,document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS import_receipts(receipt_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,operation_key TEXT UNIQUE NOT NULL,
 bundle TEXT NOT NULL,content_ids TEXT NOT NULL,actor TEXT NOT NULL,created_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
COMMIT;
"""

def stamp():return int(time.time()*1000)
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()

class Conflict(ValueError):pass

class Store:
    def __init__(self,path,initialize=True):
        self.path=Path(path)
        if initialize:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.connect() as db:db.executescript(SCHEMA)

    @contextmanager
    def connect(self,write=False):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA busy_timeout=15000')
            if write:db.execute('BEGIN IMMEDIATE')
            yield db
            if write:db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.close()

    def log(self,db,key,action,actor,rev,details=None):
        db.execute('INSERT INTO history(id,action,actor,revision,details,created_ms) VALUES(?,?,?,?,?,?)',
                   (key,action,actor,rev,canonical(details or {}),stamp()))

    def save(self,document,actor,expected=None):
        value=deepcopy(document);key=value.setdefault('id','studio-'+secrets.token_hex(10))
        with self.connect(True) as db:
            old=db.execute('SELECT * FROM content WHERE id=?',(key,)).fetchone()
            if old and expected!=old['revision']:raise Conflict('This draft changed; reload before saving.')
            if old and value['kind']!=old['kind']:raise ValueError('Content kind cannot change')
            rev=old['revision']+1 if old else 1
            if not old and expected not in (None,0):raise Conflict('Content no longer exists')
            db.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?)',(key,rev,canonical(value),digest(value),actor,stamp()))
            if old:db.execute('UPDATE content SET revision=?,updated_ms=? WHERE id=?',(rev,stamp(),key))
            else:db.execute("INSERT INTO content VALUES(?,?,?,NULL,'draft',?)",(key,value['kind'],rev,stamp()))
            self.log(db,key,'save',actor,rev)
        return self.get(key)

    def save_import(self,documents,actor,regions=()):
        values=deepcopy(documents)
        with self.connect(True) as db:
            for region in regions:
                db.execute('INSERT INTO regions VALUES(?,?)',(region['id'],canonical(region)))
                self.log(db,region['id'],'import-region',actor,None)
            for value in values:
                key=value.setdefault('id','studio-'+secrets.token_hex(10))
                if db.execute('SELECT 1 FROM content WHERE id=?',(key,)).fetchone():raise Conflict('Import identity already exists')
                db.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?)',(key,1,canonical(value),digest(value),actor,stamp()))
                db.execute("INSERT INTO content VALUES(?,?,1,NULL,'draft',?)",(key,value['kind'],stamp()))
                self.log(db,key,'import',actor,1)
        return [self.get(v['id']) for v in values]

    def get(self,key,revision=None):
        with self.connect() as db:
            row=db.execute('SELECT * FROM content WHERE id=?',(key,)).fetchone()
            if not row:raise ValueError('Content not found')
            entry=db.execute('SELECT * FROM revisions WHERE id=? AND revision=?',(key,revision or row['revision'])).fetchone()
            if not entry:raise ValueError('Revision not found')
            return dict(row)|{'document':json.loads(entry['document']),'digest':entry['digest'],'selected_revision':entry['revision']}

    def list(self):
        if not self.path.exists():return []
        with self.connect() as db:
            return [dict(r)|{'document':json.loads(r['document'])} for r in db.execute(
                'SELECT c.*,r.document,r.digest FROM content c JOIN revisions r ON r.id=c.id AND r.revision=c.revision ORDER BY c.updated_ms DESC,c.id')]

    def action(self,key,action,actor,expected_digest=None,revision=None):
        transitions={'pause':({'published','scheduled'},'paused'),'resume':({'paused'},'published'),
                     'end':({'published','scheduled','paused'},'ended'),'archive':({'draft','ended','paused'},'archived')}
        with self.connect(True) as db:
            row=db.execute('SELECT * FROM content WHERE id=?',(key,)).fetchone()
            if not row:raise ValueError('Content not found')
            rev=revision or row['revision']
            value=db.execute('SELECT digest FROM revisions WHERE id=? AND revision=?',(key,rev)).fetchone()
            if action in ('publish','rollback'):
                if not value or value['digest']!=expected_digest:raise Conflict('Preview is stale; validate and confirm again.')
                status='published';db.execute('UPDATE content SET published_revision=? WHERE id=?',(rev,key))
            elif action in transitions:
                allowed,status=transitions[action]
                if row['status'] not in allowed:raise ValueError('Invalid lifecycle transition')
            else:raise ValueError('Unknown content action')
            db.execute('UPDATE content SET status=?,updated_ms=? WHERE id=?',(status,stamp(),key))
            self.log(db,key,action,actor,rev)
        return self.get(key)

    def publish_bundle(self,plans,actor,expected_digest):
        if digest(plans)!=expected_digest:raise Conflict('Publication plan changed')
        with self.connect(True) as db:
            for plan in plans:
                row=db.execute('SELECT revision FROM content WHERE id=?',(plan['id'],)).fetchone()
                rev=db.execute('SELECT digest FROM revisions WHERE id=? AND revision=?',(plan['id'],plan['revision'])).fetchone()
                if not row or row['revision']!=plan['revision'] or not rev or rev['digest']!=plan['digest']:raise Conflict('A dependency changed; review the publication again')
            for plan in plans:
                db.execute("UPDATE content SET published_revision=?,status='published',updated_ms=? WHERE id=?",(plan['revision'],stamp(),plan['id']))
                self.log(db,plan['id'],'publish',actor,plan['revision'],{'bundle':expected_digest})
        return [self.get(plan['id']) for plan in plans]

    def published(self):
        if not self.path.exists():return []
        with self.connect() as db:
            # A first reader can observe the empty SQLite file before schema
            # initialization commits. No publication exists at this point.
            if db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('content','revisions','history')").fetchone()[0]!=3:return []
            return [dict(r)|{'document':json.loads(r['document'])} for r in db.execute(
                "SELECT c.id,c.kind,c.status,(SELECT MAX(h.sequence) FROM history h WHERE h.id=c.id AND h.action IN ('publish','resume','rollback')) AS activation,r.revision,r.document,r.digest FROM content c JOIN revisions r ON r.id=c.id AND r.revision=c.published_revision WHERE c.status IN ('published','scheduled') ORDER BY c.id")]

    def history(self,key=None):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT sequence,id,action,actor,revision,created_ms FROM history'+(' WHERE id=?' if key else '')+' ORDER BY sequence DESC LIMIT 200',(key,) if key else ())]

    def revisions(self,key):
        with self.connect() as db:return [dict(r) for r in db.execute('SELECT revision,digest,actor,created_ms FROM revisions WHERE id=? ORDER BY revision DESC',(key,))]

    def entitled(self,player,key,at):
        with self.connect() as db:return bool(db.execute('SELECT 1 FROM entitlements WHERE player=? AND entitlement=? AND (expires_ms=0 OR expires_ms>?)',(player,key,at)).fetchone())

    def grant_entitlement(self,player,key,expires,actor):
        with self.connect(True) as db:
            db.execute('INSERT INTO entitlements VALUES(?,?,?,?,?) ON CONFLICT(player,entitlement) DO UPDATE SET expires_ms=excluded.expires_ms,actor=excluded.actor',(player,key,stamp(),expires,actor))
            self.log(db,key,'entitlement',actor,None,{'player':player,'expires_ms':expires})

    def regions(self):
        with self.connect() as db:return [json.loads(r[0]) for r in db.execute('SELECT document FROM regions ORDER BY id')]

    def save_region(self,value,actor):
        from .rules import validate_scope
        validate_scope(value)
        if value['type']!='polygon':raise ValueError('Named regions require a drawn/imported polygon, not guessed boundaries')
        key=value.get('id') or 'region-'+secrets.token_hex(6);value=deepcopy(value)|{'id':key}
        with self.connect(True) as db:
            db.execute('INSERT INTO regions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document',(key,canonical(value)))
            self.log(db,key,'region',actor,None)
        return value

    def fixture_clock(self,value=None,actor='fixture'):
        with self.connect(write=value is not None) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
                if value is None:return None
                db.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            if value is not None:
                db.execute("INSERT INTO settings VALUES('fixture_clock',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(value),))
                self.log(db,'fixture-clock','advance',actor,None,{'at':value})
            row=db.execute("SELECT value FROM settings WHERE key='fixture_clock'").fetchone()
            return int(row[0]) if row else None

    def import_bundle(self, bundle, actor, mode='create', expected=None):
        """Atomically write reviewed drafts and a durable idempotency receipt.

        Only never-published drafts may be updated; published revisions and
        player databases are never touched by this transaction.
        """
        if mode not in ('create','copy','update'):raise ValueError('Unknown import mode')
        fingerprint=digest(bundle);operation=digest([fingerprint,mode]);values=deepcopy(bundle['content']);regions=deepcopy(bundle.get('regions',[]))
        if mode=='copy':
            mapping={v['id']:'copy-'+fingerprint[:12]+'-'+str(i) for i,v in enumerate(values)}
            region_map={v['id']:'region-'+fingerprint[:12]+'-'+str(i) for i,v in enumerate(regions)}
            for region in regions:region['id']=region_map[region['id']]
            for value in values:
                value['id']=mapping[value['id']]
                for field in ('season_id','prerequisite'):
                    if value.get(field) in mapping:value[field]=mapping[value[field]]
                if value.get('scope',{}).get('id') in region_map:value['scope']['id']=region_map[value['scope']['id']]
                for rule in [value.get('rules',{}),*[w['rules'] for w in value.get('weeks',[])],*[r['rules'] for r in value.get('rotations',[])]]:
                    if 'research' in rule:rule['research']=[mapping.get(k,k) for k in rule['research']]
        with self.connect(True) as db:
            prior=db.execute('SELECT * FROM import_receipts WHERE operation_key=?',(operation,)).fetchone()
            if prior:return {'receipt_id':prior['receipt_id'],'fingerprint':fingerprint,'ids':json.loads(prior['content_ids']),'replayed':True}
            for region in regions:
                old=db.execute('SELECT document FROM regions WHERE id=?',(region['id'],)).fetchone()
                if old and old[0]!=canonical(region):raise Conflict('Region identity already exists with different geometry; import a copy')
                if not old:
                    db.execute('INSERT INTO regions VALUES(?,?)',(region['id'],canonical(region)))
                    self.log(db,region['id'],'import-region',actor,None)
            for value in values:
                key=value['id'];old=db.execute('SELECT * FROM content WHERE id=?',(key,)).fetchone()
                if old:
                    if mode!='update':raise Conflict('Content identity already exists; compare, explicitly update a draft or import a copy')
                    if old['published_revision'] is not None or old['status']!='draft':raise Conflict('Published or inactive content cannot be overwritten by import')
                    if old['kind']!=value['kind']:raise Conflict('Content kind cannot change')
                    if not expected or expected.get(key)!=old['revision']:raise Conflict('Draft changed after preview; compare again')
                elif mode=='update' and expected and key in expected:raise Conflict('Expected draft no longer exists')
                revision=old['revision']+1 if old else 1
                db.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?)',(key,revision,canonical(value),digest(value),actor,stamp()))
                if old:db.execute('UPDATE content SET revision=?,updated_ms=? WHERE id=?',(revision,stamp(),key))
                else:db.execute("INSERT INTO content VALUES(?,?,1,NULL,'draft',?)",(key,value['kind'],stamp()))
                self.log(db,key,'import-'+mode,actor,revision,{'fingerprint':fingerprint})
            receipt='import-'+secrets.token_hex(12);ids=[v['id'] for v in values]
            db.execute('INSERT INTO import_receipts VALUES(?,?,?,?,?,?,?)',(receipt,fingerprint,operation,canonical(bundle),canonical(ids),actor,stamp()))
        return {'receipt_id':receipt,'fingerprint':fingerprint,'ids':ids,'replayed':False}

    def imported_ids(self, receipt):
        with self.connect() as db:
            row=db.execute('SELECT content_ids FROM import_receipts WHERE receipt_id=?',(receipt,)).fetchone()
            if not row:raise ValueError('Import receipt not found')
            return json.loads(row[0])
