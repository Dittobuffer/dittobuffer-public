"""Authenticated studio API, embedded in the existing loopback admin service."""
from collections import defaultdict,deque
from copy import deepcopy
from contextlib import closing
import hashlib,hmac,json,secrets,threading,time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse
from dittobuffer.paths import ROOT
from dittobuffer.admin.access import AccessDenied
from dittobuffer.gameplay.liveops.catalog import Catalog
from dittobuffer.gameplay.liveops.store import Store,Conflict,digest
from dittobuffer.gameplay.liveops.validation import validate
from dittobuffer.gameplay.liveops.rules import resolve,interval
from dittobuffer.gameplay.liveops.runtime import path as content_path

class HttpError(Exception):
    def __init__(self,status,message):self.status=status;super().__init__(message)

class Security:
    def __init__(self,path,initialize=True):
        self.path=Path(path);self.lock=threading.Lock();self.sessions={};self.attempts=defaultdict(deque)
        if initialize and not self.path.exists():
            self.path.parent.mkdir(parents=True,exist_ok=True);password=secrets.token_urlsafe(24);salt=secrets.token_hex(16)
            value={'admin':{'salt':salt,'hash':self.hash(password,salt),'role':'publisher'}}
            self.path.write_text(json.dumps(value),encoding='utf-8')
            self.path.with_suffix('.bootstrap.txt').write_text('DittoBuffer Studio local administrator\nUsername: admin\nPassword: '+password+'\nKeep this file private. Configure roles/passwords using the documented local setup command.\n',encoding='utf-8')
        self.users=json.loads(self.path.read_text(encoding='utf-8')) if initialize and self.path.exists() else {}

    def _issue(self,user,role):
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        row={'user':user,'role':role,'csrf':csrf,'expires':time.monotonic()+8*3600}
        self.sessions[hashlib.sha256(token.encode()).hexdigest()]=row
        return token,row

    def local_admin(self):
        with self.lock:return self._issue('local-admin','publisher')

    @staticmethod
    def hash(password,salt):return hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),310000).hex()

    def login(self,user,password,peer):
        if not isinstance(user,str) or not isinstance(password,str) or len(password)>1024:raise HttpError(400,'Invalid login')
        with self.lock:
            hits=self.attempts[peer]
            while hits and hits[0]<time.monotonic()-60:hits.popleft()
            if len(hits)>=8:raise HttpError(429,'Too many login attempts; try again in a minute')
            hits.append(time.monotonic())
            row=self.users.get(user);actual=self.hash(password,row['salt'] if row else '00'*16)
            if not row or not hmac.compare_digest(actual,row['hash']):raise HttpError(401,'Invalid administrator sign-in')
            return self._issue(user,row['role'])

    def session(self,headers,write=False,publish=False):
        try:
            cookie=SimpleCookie();cookie.load(headers.get('Cookie',''));token=cookie['dittobuffer_studio'].value
        except (KeyError,ValueError):raise HttpError(401,'Sign in to the Studio') from None
        with self.lock:row=self.sessions.get(hashlib.sha256(token.encode()).hexdigest())
        if not row or row['expires']<time.monotonic():raise HttpError(401,'Studio session expired')
        if write:
            if row['role'] not in ('editor','publisher'):raise HttpError(403,'Editor permission required')
            if not hmac.compare_digest(headers.get('X-Studio-CSRF',''),row['csrf']):raise HttpError(403,'Refresh the page before making changes')
        if publish and row['role']!='publisher':raise HttpError(403,'Publisher permission required')
        return row

class Studio:
    def __init__(self,admin,catalog=None,security=None):
        self.admin=admin;cfg=admin.config.get('liveops',{})
        self.store=Store(content_path(admin.config));self.catalog=catalog or Catalog(admin.config)
        # Fixed project-private credential location; request bodies cannot choose paths.
        self.authentication_required=bool(cfg.get('admin_auth',False))
        from dittobuffer.admin.access import access
        self.security=security or (Security(ROOT/'.private/studio-admin.json') if self.authentication_required else access(admin))
        from dittobuffer.gameplay.random_spawns import settings,build_pool
        self.default_spawn_pools=deepcopy(admin.config.get('spawn_pools',[]));self.default_spawn_settings=settings(admin.config)
        if self.default_spawn_settings['enabled']:self.default_spawn_pools,_=build_pool(self.catalog.game(),self.default_spawn_settings)

    def session_response(self,handler,token,session):
        value={k:session[k] for k in ('user','role','csrf')};value['authentication_required']=self.authentication_required
        raw=json.dumps(value).encode()
        handler.send_response(200);handler.send_header('Content-Type','application/json');handler.send_header('Cache-Control','no-store')
        if token:handler.send_header('Set-Cookie','dittobuffer_studio='+token+'; HttpOnly; SameSite=Strict; Path=/api/studio; Max-Age=28800')
        handler.send_header('Content-Length',str(len(raw)));handler.end_headers();handler.wfile.write(raw)

    def publication_items(self,item):
        result={item['id']:item}
        if item['document']['kind']=='season':
            for row in self.store.list():
                if row['document'].get('season_id')==item['id']:result[row['id']]=self.store.get(row['id'])
        for row in list(result.values()):
            spec=row['document']
            rules=[spec.get('rules',{}),*[w.get('rules',{}) for w in spec.get('weeks',[])],*[w.get('rules',{}) for w in spec.get('rotations',[])]]
            for rule in rules:
                for key in rule.get('research',[]):
                    ref=self.store.get(key)
                    if ref['document']['kind']!='research':raise ValueError('Referenced content is not Research')
                    result[key]=ref
        return list(result.values())

    def validation(self,item):
        try:
            items=self.publication_items(item)
            for entry in items:
                validate(entry['document'],self.catalog)
                scope=entry['document'].get('scope',{})
                if scope.get('type')=='region' and not any(v['id']==scope.get('id') for v in self.store.regions()):raise ValueError('Import or choose the missing geographic region before publishing')
                missing=entry['document'].get('required_capabilities',[])
                if missing:raise ValueError('Template requires capabilities: '+', '.join(missing))
            plans=[{'id':row['id'],'revision':row['selected_revision'],'digest':row['digest']} for row in items]
            rules=item['document'].get('rules',{});pools=rules.get('spawns',[]);total=sum(v.get('weight',1) for v in pools)
            return {'valid':True,'digest':digest(plans),'plans':plans,'effects':{'publications':[row['document']['name'] for row in items],
                'spawns':[dict(v,probability=v.get('weight',1)/total) for v in pools],
                'raids':rules.get('raids',[]),'bonuses':rules.get('bonuses',{}),'weeks':item['document'].get('weeks',[]),'research':rules.get('research',[])},'errors':[]}
        except (ValueError,KeyError,TypeError) as e:return {'valid':False,'digest':item['digest'],'errors':[str(e)]}

    def create_bundle(self,bundle,actor):
        doc=deepcopy(bundle['document']);refs=[];saved=[]
        for research in bundle.get('research',[]):
            row=self.store.save(research,actor);refs.append(row['id']);saved.append(row)
        if doc['kind']!='research':doc.setdefault('rules',{})['research']=refs
        if doc['kind']=='season':
            for week in doc.get('weeks',[]):week.setdefault('rules',{})['research']=refs
        main=self.store.save(doc,actor)
        for event in bundle.get('events',[]):
            child=deepcopy(event);child['document']['season_id']=main['id'];saved.extend(self.create_bundle(child,actor)['items'])
        return {'main':main,'items':[main]+saved,'warnings':bundle.get('warnings',[])}

    def preview(self,at,lat=None,lng=None,biome=None):
        import math
        if lat is not None or lng is not None:
            if type(lat) not in (int,float) or type(lng) not in (int,float) or not math.isfinite(lat) or not math.isfinite(lng) or not -90<=lat<=90 or not -180<=lng<=180:raise ValueError('Choose valid preview coordinates')
        value=resolve(self.store.published(),at,lat,lng,biome,self.store.regions())
        cfg=self.admin.config
        defaults={'spawns':deepcopy(self.default_spawn_pools),'raids':deepcopy(cfg.get('raids',{}).get('pools',[])),
            'eggs':deepcopy(cfg.get('eggs',{}).get('pools',[])),'max':deepcopy(cfg.get('max_system',{}).get('pools',[])),
            'rocket':deepcopy(cfg.get('rocket',{}))}
        # Only expose gameplay defaults; no transport, account, credential or
        # machine configuration enters the preview response.
        defaults['rocket']={k:v for k,v in defaults['rocket'].items() if k in ('balloon_period_seconds','balloon_themes','hideout_probability')}
        value['using_defaults']=[key for key in defaults if key not in value]
        for key,fallback in defaults.items():value.setdefault(key,fallback)
        value['spawn_probabilities']=[]
        value['default_spawn_settings']=deepcopy(self.default_spawn_settings)
        if 'spawns' in value['using_defaults'] and len(value['spawns'])==1:
            entries=value['spawns'][0].get('species',[]);total=sum(v.get('weight',1) for v in entries)
            value['spawn_probabilities']=[dict(v,species=v.get('id'),probability=v.get('weight',1)/total) for v in entries] if total else []
        if 'spawns' not in value['using_defaults']:
            total=sum(v.get('weight',1) for v in value['spawns'])
            value['spawn_probabilities']=[dict(v,probability=v.get('weight',1)/total) for v in value['spawns']]
        value['read_only']=True;value['note']='No player accounts or gameplay database are changed. Default wild entries retain their habitat/time eligibility.'
        return value

    def handle(self,handler,method,parts,query):
        # Return False only for non-studio routes.
        if parts[:2]!=['api','studio']:return False
        try:
            host=handler.headers.get('Host','');origin=handler.headers.get('Origin')
            if origin and urlparse(origin).netloc!=host:raise HttpError(403,'Cross-origin studio requests are not allowed')
            tail=parts[2:]
            if not self.authentication_required and method=='GET' and tail==['session']:
                session=self.security.session(handler.headers)
                self.session_response(handler,None,session);return True
            if method=='POST' and tail==['login']:
                if not self.authentication_required:raise HttpError(400,'No Studio password is required; open the admin website directly')
                body=handler.body();token,session=self.security.login(body.get('username'),body.get('password'),handler.client_address[0])
                self.session_response(handler,token,session);return True
            write=method!='GET';publish=bool(tail and tail[-1] in ('publish','rollback','pause','resume','end','archive','entitlements','clock'))
            session=self.security.session(handler.headers,write,publish);actor=session['user']
            body=handler.body() if write else {}
            if method=='GET' and tail==['session']:value={k:session[k] for k in ('user','role','csrf')}
            elif method=='GET' and tail==['catalog']:value={'species':self.catalog.rows(),'capabilities':self.catalog.capabilities(),'regions':self.store.regions()}
            elif method=='GET' and tail==['research-level-gates']:
                from dittobuffer.gameplay.level_research import reference,readiness
                value=dict(read_only=True,**readiness(),quests=reference()['quests'])
            elif method=='GET' and tail==['content']:value=self.store.list()
            elif method=='POST' and tail==['content']:
                doc=body.get('document',{});encoded=json.dumps(doc,allow_nan=False)
                if len(encoded)>150000:raise ValueError('Draft is too large')
                if doc.get('kind') not in ('season','event','research') or not isinstance(doc.get('name'),str):raise ValueError('Draft needs a kind and name')
                value=self.store.save(doc,actor,body.get('expected_revision'))
            elif len(tail)>=2 and tail[0]=='content':
                key=tail[1]
                if method=='GET' and len(tail)==2:value=self.store.get(key,int(query['revision'][0]) if query.get('revision') else None)
                elif method=='GET' and tail[2:]==['preview']:
                    from dittobuffer.gameplay.liveops.research_preview import preview
                    selected=self.store.get(key)['document']
                    if selected['kind']!='research':raise ValueError('Choose Research to preview')
                    value=preview(selected,self.catalog,[r['document'] for r in self.store.list()],self.default_spawn_pools)
                elif method=='GET' and tail[2:]==['history']:value={'revisions':self.store.revisions(key),'actions':self.store.history(key)}
                elif method=='POST' and tail[2:]==['validate']:
                    value=self.validation(self.store.get(key,body.get('revision')))
                    if body.get('rollback') and value['valid']:
                        value['plans']=[plan for plan in value['plans'] if plan['id']==key];value['digest']=digest(value['plans']);value['effects']['publications']=[self.store.get(key,body.get('revision'))['document']['name']]
                elif method=='POST' and tail[2:]==['duplicate']:
                    doc=self.store.get(key)['document'];doc.pop('id',None);doc['name']+=' (copy)';value=self.store.save(doc,actor)
                elif method=='POST' and len(tail)==3:
                    action=tail[2]
                    if action in ('publish','rollback'):
                        row=self.store.get(key,body.get('revision'));check=self.validation(row)
                        if action=='rollback' and check['valid']:
                            check['plans']=[plan for plan in check['plans'] if plan['id']==key];check['digest']=digest(check['plans'])
                        if not check['valid']:raise ValueError('; '.join(check['errors']))
                        if body.get('confirm') is not True or body.get('digest')!=check['digest']:raise ValueError('Preview and explicitly confirm these effects before publishing')
                    if action=='publish':
                        self.store.publish_bundle(check['plans'],actor,body['digest']);value=self.store.get(key)
                    else:
                        # Rollback intentionally selects just the reviewed historical revision.
                        value=self.store.action(key,action,actor,row['digest'] if action=='rollback' else body.get('digest'),body.get('revision'))
                else:raise HttpError(404,'Studio endpoint not found')
            elif method=='GET' and tail==['templates']:
                from dittobuffer.gameplay.liveops.generator import TEMPLATES
                value=[dict(id=k,name=v[0],description=v[1],runnable=v[2]) for k,v in TEMPLATES.items()]
            elif method=='POST' and tail==['templates']:
                from dittobuffer.gameplay.liveops.generator import template
                bundle=template(body['template'],self.catalog,body.get('start'),body.get('featured'))
                value=self.create_bundle(bundle,actor)
            elif method=='POST' and tail==['generate']:
                from dittobuffer.gameplay.liveops.generator import generate
                bundle=generate(self.catalog,body);value=self.create_bundle(bundle,actor)
            elif method=='POST' and tail==['preview']:
                from dittobuffer.gameplay.liveops.rules import instant
                at=instant(body['at'],body.get('timezone','Europe/Amsterdam'),body.get('fold'))
                value=self.preview(at,body.get('lat'),body.get('lng'),body.get('biome'))
                value['read_only']=True;value['note']='No player accounts or gameplay database are changed by previews.'
            elif method=='GET' and tail==['overview']:
                from dittobuffer.gameplay.liveops.store import stamp
                pubs=self.store.published();at=self.store.fixture_clock() if self.admin.config.get('liveops',{}).get('isolated_fixture') else None;at=at or stamp()
                try:
                    with closing(self.admin.connect()) as db:location=self.admin.player_location(db) or {}
                except Exception:location={}
                value={'active':self.preview(at,location.get('lat'),location.get('lng')),'publications':pubs,'history':self.store.history(),
                     'scheduler':{'mode':'request-driven deterministic evaluation','healthy':True,'clock_ms':at},'draft_count':len(self.store.list())}
            elif method=='GET' and tail==['history']:value=self.store.history()
            elif method=='GET' and tail==['regions','sources']:
                from dittobuffer.gameplay.liveops.geography import sources
                value=[{k:v for k,v in row.items() if k!='geometry'} for row in sources(self.admin.config)]
            elif method=='POST' and tail==['regions','import-world']:
                from dittobuffer.gameplay.liveops.geography import sources
                row=next((v for v in sources(self.admin.config) if v['id']==body.get('id')),None)
                if row is None:raise ValueError('Published region is unavailable; draw a custom city/area boundary instead')
                value=self.store.save_region(row,actor)
            elif method=='GET' and tail==['regions']:value=self.store.regions()
            elif method=='POST' and tail==['regions']:value=self.store.save_region(body,actor)
            elif method=='POST' and tail==['entitlements']:
                player=body.get('player','');key=body.get('entitlement','')
                with closing(self.admin.connect()) as db:
                    if not db.execute('SELECT 1 FROM players WHERE id=?',(player,)).fetchone():raise ValueError('Choose an existing account')
                if not key or len(key)>100:raise ValueError('Entitlement name required')
                self.store.grant_entitlement(player,key,int(body.get('expires_ms',0)),actor);value={'granted':True}
            elif method=='POST' and tail==['clock']:
                cfg=self.admin.config.get('liveops',{})
                if not cfg.get('isolated_fixture') or self.admin.config.get('environment','development')!='development' or not any(self.admin.db_path.is_relative_to(ROOT/p) for p in ('.private','builds')):raise HttpError(403,'Clock controls are restricted to isolated test profiles')
                from dittobuffer.gameplay.liveops.rules import instant
                at=instant(body['at'],body.get('timezone','Europe/Amsterdam'),body.get('fold'))
                value={'fixture_clock_ms':self.store.fixture_clock(at,actor),'isolated':True}
            elif method=='GET' and tail==['schema']:
                from dittobuffer.gameplay.liveops.bundle import SCHEMA
                value=SCHEMA
            elif method=='GET' and tail==['export']:
                from dittobuffer.gameplay.liveops.bundle import export
                ids=query.get('id')
                if query.get('receipt'):ids=self.store.imported_ids(query['receipt'][0])
                value=export(self.store,ids)
            elif method=='POST' and tail==['export-template']:
                from dittobuffer.gameplay.liveops.bundle import from_generated
                from dittobuffer.gameplay.liveops.generator import template,generate
                generated=generate(self.catalog,body['options']) if 'options' in body else template(body['template'],self.catalog,body.get('start'),body.get('featured'))
                value=from_generated(generated)
            elif method=='POST' and tail in (['import','preview'],['import']):
                from dittobuffer.gameplay.liveops.bundle import inspect,loads
                # Files and pasted JSON travel as text, retaining duplicate-key detection.
                bundle=loads(body['json']) if 'json' in body else body.get('bundle',body)
                review=inspect(bundle,self.catalog,self.store)
                if tail==['import','preview']:value=review
                else:
                    if body.get('confirm') is not True:raise ValueError('Preview and confirm Import as Draft first')
                    if not review['valid']:
                        handler.send_json(400,{'error':'Bundle validation failed','errors':review['errors']});return True
                    if body.get('fingerprint')!=review['fingerprint']:raise Conflict('Bundle changed after preview')
                    value=self.store.import_bundle(review['bundle'],actor,body.get('mode','create'),body.get('expected_revisions'))
                    value['items']=[self.store.get(key) for key in value['ids']]
            else:raise HttpError(404,'Studio endpoint not found')
            handler.send_json(200,value)
        except (HttpError,AccessDenied) as e:handler.send_json(e.status,{'error':str(e)})
        except Conflict as e:handler.send_json(409,{'error':str(e)})
        except (ValueError,KeyError,TypeError) as e:handler.send_json(400,{'error':str(e)})
        return True
