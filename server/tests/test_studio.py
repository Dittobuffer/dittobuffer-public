"""Real HTTP authorization, CSRF, publication and stale-review safety."""
from contextlib import closing
import http.cookiejar,json,tempfile,threading,unittest,urllib.request,urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from dittobuffer.paths import ROOT
from dittobuffer.core.database import Database
from dittobuffer.admin.admin import handler
from dittobuffer.admin.studio import Studio,Security
from dittobuffer.gameplay.liveops.catalog import Catalog
from dittobuffer.gameplay.liveops.generator import base,template

class StudioApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.catalog=Catalog(json.loads((ROOT/'config/family.json').read_text()))
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds');self.root=Path(self.tmp.name)
        cfg=json.loads((ROOT/'config/family.json').read_text());cfg['liveops']={'database':str((self.root/'content.sqlite3').relative_to(ROOT)),'admin_auth':True}
        self.admin=SimpleNamespace(readonly=lambda:False,config=cfg,lock=threading.Lock())
        self.security=Security(self.root/'private-auth.json');self.password=self.security.path.with_suffix('.bootstrap.txt').read_text().split('Password: ',1)[1].splitlines()[0]
        self.admin.studio=Studio(self.admin,self.catalog,self.security)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.admin));self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
        self.jar=http.cookiejar.CookieJar();self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar));self.csrf=''
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
    def call(self,path,body=None,headers=None,client=None):
        req=urllib.request.Request(self.origin+'/api/studio/'+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json','X-Studio-CSRF':self.csrf,**(headers or {})})
        try:
            with (client or self.client).open(req) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)
    def login(self,role='publisher'):
        self.security.users['admin']['role']=role
        code,v=self.call('login',{'username':'admin','password':self.password});self.assertEqual(code,200);self.csrf=v['csrf']
    def test_player_token_and_missing_cookie_cannot_manage_studio(self):
        self.assertEqual(self.call('content')[0],401)
        self.assertEqual(self.call('content',headers={'Authorization':'Bearer player-token'})[0],401)
    def test_login_cookie_csrf_and_origin(self):
        self.login();cookie=next(iter(self.jar));self.assertIn('HttpOnly',cookie._rest);self.assertEqual(cookie._rest['SameSite'],'Strict')
        doc=base('event','Allowed','2026-10-28T10:00Z')
        self.assertEqual(self.call('content',{'document':doc},headers={'X-Studio-CSRF':''})[0],403)
        self.assertEqual(self.call('content',{'document':doc},headers={'Origin':'https://untrusted.example'})[0],403)
        self.assertEqual(self.call('content',{'document':doc})[0],200)
    def test_roles_prevent_publication_and_mutation(self):
        self.login('viewer');doc=base('event','Viewer','2026-10-28T10:00Z');self.assertEqual(self.call('content',{'document':doc})[0],403)
        self.login('editor');code,row=self.call('content',{'document':doc});self.assertEqual(code,200)
        self.assertEqual(self.call('content/'+row['id']+'/publish',{'confirm':True,'digest':row['digest']})[0],403)
    def test_template_bundle_requires_confirmation_and_publishes_atomically(self):
        self.login();code,bundle=self.call('templates',{'template':'community-day','start':'2026-10-28T10:00Z','featured':1});self.assertEqual(code,200)
        key=bundle['main']['id'];code,preview=self.call('content/'+key+'/validate',{});self.assertEqual(code,200);self.assertTrue(preview['valid'],preview)
        self.assertEqual(len(preview['plans']),2)
        self.assertEqual(self.call('content/'+key+'/publish',{'digest':preview['digest']})[0],400)
        self.assertEqual(self.admin.studio.store.published(),[])
        self.assertEqual(self.call('content/'+key+'/publish',{'digest':preview['digest'],'confirm':True})[0],200)
        self.assertEqual(len(self.admin.studio.store.published()),2)
    def test_changed_dependency_rejects_stale_confirmation_without_partial_activation(self):
        self.login();_,bundle=self.call('templates',{'template':'community-day','start':'2026-10-28T10:00Z','featured':1});key=bundle['main']['id']
        _,preview=self.call('content/'+key+'/validate',{});child=next(r for r in bundle['items'] if r['kind']=='research')
        self.call('content',{'document':child['document']|{'name':'Edited dependency'},'expected_revision':child['revision']})
        self.assertEqual(self.call('content/'+key+'/publish',{'digest':preview['digest'],'confirm':True})[0],400)
        self.assertEqual(self.admin.studio.store.published(),[])
    def test_invalid_import_never_partially_saves_or_executes_rules(self):
        self.login();doc=base('event','Invalid code','2026-10-28T10:00Z');doc['rules']={'execute_python':'raise RuntimeError'}
        self.assertEqual(self.call('import',{'format':'dittobuffer-studio-v1','content':[doc]})[0],400)
        self.assertEqual(self.admin.studio.store.list(),[])
    def test_unknown_species_and_incomplete_templates_cannot_publish(self):
        self.login();_,bundle=self.call('templates',{'template':'wild-area','start':'2026-10-28T10:00Z','featured':1});key=bundle['main']['id']
        _,check=self.call('content/'+key+'/validate',{});self.assertFalse(check['valid'])
        self.assertEqual(self.admin.studio.store.published(),[])

    def test_import_preserves_region_geometry_and_stable_references(self):
        from dittobuffer.gameplay.liveops.bundle import make_bundle
        self.login();region={'id':'original-city','type':'polygon','name':'Operator area','points':[[4,52],[5,52],[5,53],[4,53]]}
        doc=base('event','Regional import','2026-10-28T10:00Z');doc['id']='regional';doc['scope']={'type':'region','id':region['id']}
        bundle=make_bundle([doc],[region]);code,review=self.call('import/preview',{'bundle':bundle});self.assertEqual(code,200);self.assertTrue(review['valid'])
        code,result=self.call('import',{'bundle':bundle,'confirm':True,'fingerprint':review['fingerprint']});self.assertEqual(code,200)
        saved=self.admin.studio.store.regions();self.assertEqual(saved,[region]);self.assertEqual(result['items'][0]['document']['scope']['id'],region['id'])
    def test_missing_geographic_region_cannot_be_published(self):
        self.login();doc=base('event','Missing area','2026-10-28T10:00Z');doc['scope']={'type':'region','id':'missing'}
        _,row=self.call('content',{'document':doc});_,check=self.call('content/'+row['id']+'/validate',{});self.assertFalse(check['valid'])

    def test_shared_website_authorization_needs_no_second_studio_password(self):
        from dittobuffer.admin.access import access
        self.admin.studio.authentication_required=False;self.admin.studio.security=access(self.admin)
        self.assertEqual(self.call('session')[0],401)
        token,row=self.admin.studio.security.exchange(self.admin.studio.security.key)
        headers={'Cookie':'dittobuffer_admin='+token}
        code,session=self.call('session',headers=headers);self.assertEqual(code,200);self.assertFalse(session['authentication_required']);self.csrf=session['csrf']
        self.assertEqual(self.call('content',headers=headers)[0],200)
        doc=base('event','Shared admin authorization','2026-10-28T10:00Z')
        self.assertEqual(self.call('content',{'document':doc},headers=headers|{'X-Studio-CSRF':''})[0],403)
        self.assertEqual(self.call('content',{'document':doc},headers=headers|{'Origin':'https://untrusted.example'})[0],403)
        self.assertEqual(self.call('content',{'document':doc},headers=headers)[0],200)
        # The same server-side session protects the rest of the website too.
        req=urllib.request.Request(self.origin+'/api/players')
        with self.assertRaises(urllib.error.HTTPError) as denied:urllib.request.urlopen(req)
        self.assertEqual(denied.exception.code,401)

    def test_preview_confirm_duplicate_replay_export_and_upload_limits(self):
        from dittobuffer.gameplay.liveops.bundle import make_bundle
        self.login();doc=base('event','HTTP import','2026-10-28T10:00Z');doc['id']='http-import';doc['rules']={'spawns':[{'species':'BULBASAUR','weight':1}]};bundle=make_bundle([doc])
        code,r=self.call('import/preview',{'json':json.dumps(bundle)});self.assertEqual(code,200);self.assertTrue(r['valid']);self.assertEqual(self.admin.studio.store.list(),[])
        payload={'bundle':r['bundle'],'fingerprint':r['fingerprint'],'confirm':True}
        self.assertEqual(self.call('import',payload|{'confirm':False})[0],400)
        _,first=self.call('import',payload);_,second=self.call('import',payload);self.assertEqual(first['receipt_id'],second['receipt_id']);self.assertTrue(second['replayed'])
        code,exported=self.call('export?id=http-import');self.assertEqual(code,200);self.assertEqual(exported['format'],'dittobuffer.liveops.bundle')
        self.assertEqual(self.call('import/preview',{'json':'{"id":1,"id":2}'})[0],400)
        code,invalid=self.call('import/preview',{'bundle':bundle|{'schema_version':99}});self.assertEqual(code,200);self.assertFalse(invalid['valid']);self.assertTrue(invalid['errors'][0]['path'].startswith('$'))

    def test_lazy_shared_studio_initialization_does_not_reenter_admin_lock(self):
        from dittobuffer.admin.access import access
        self.admin.config['liveops']['admin_auth']=False
        del self.admin.studio
        boundary=access(self.admin);token,_=boundary.exchange(boundary.key)
        request=urllib.request.Request(self.origin+'/api/studio/session',headers={'Cookie':'dittobuffer_admin='+token})
        with urllib.request.urlopen(request,timeout=5) as response:self.assertEqual(response.status,200)
        self.assertIs(self.admin.studio.security,boundary)
