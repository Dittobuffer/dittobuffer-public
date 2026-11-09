"""Studio persistence, real gameplay adapters and publication safety."""
import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from dittobuffer.paths import ROOT
from dittobuffer.gameplay.liveops.store import Store,Conflict,digest
from dittobuffer.gameplay.liveops.rules import resolve,instant,interval,matches,validate_scope
from dittobuffer.gameplay.liveops.generator import base,template,generate,task,research as make_research
from dittobuffer.gameplay.liveops.catalog import Catalog
from dittobuffer.gameplay.liveops.validation import validate
from dittobuffer.gameplay.liveops.runtime import project,clock
from dittobuffer.gameplay.game import Game,p,now_ms
from tests import test_research as research_fixture

class StoreTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'studio.sqlite3')
    def tearDown(self):self.tmp.cleanup()
    def doc(self):return base('event','Test','2026-10-28T10:00:00Z')
    def test_drafts_are_not_public_live_edits_are_frozen(self):
        row=self.store.save(self.doc(),'editor');self.assertEqual(self.store.published(),[])
        self.store.action(row['id'],'publish','publisher',row['digest'])
        doc=copy.deepcopy(row['document']);doc['name']='Edited'
        newer=self.store.save(doc,'editor',1)
        self.assertEqual(newer['revision'],2);self.assertEqual(self.store.published()[0]['document']['name'],'Test')
        with self.assertRaises(Conflict):self.store.save(doc,'other',1)
        with self.assertRaises(Conflict):self.store.action(row['id'],'publish','publisher',row['digest'])
        self.store.action(row['id'],'publish','publisher',newer['digest'])
        self.store.action(row['id'],'rollback','publisher',row['digest'],1)
        self.assertEqual(self.store.published()[0]['revision'],1)
    def test_pause_resume_end_archive_and_restart(self):
        row=self.store.save(self.doc(),'editor');self.store.action(row['id'],'publish','publisher',row['digest'])
        self.store.action(row['id'],'pause','publisher');self.assertEqual(self.store.published(),[])
        restored=Store(self.store.path);restored.action(row['id'],'resume','publisher');self.assertEqual(len(restored.published()),1)
        restored.action(row['id'],'end','publisher');restored.action(row['id'],'archive','publisher');self.assertEqual(restored.published(),[])
        self.assertEqual(len(restored.history()),6)
    def test_interrupted_transaction_does_not_save(self):
        with self.assertRaises(RuntimeError):
            with self.store.connect(True) as db:
                self.store.log(db,'test','uncommitted','editor',1);raise RuntimeError('interruption')
        self.assertEqual(self.store.history(),[])
    def test_concurrent_editor_cannot_overwrite_newer_revision(self):
        row=self.store.save(self.doc(),'editor')
        def save(n):
            try:return self.store.save(row['document']|{'name':str(n)},'editor',1)['revision']
            except Conflict:return 'conflict'
        with ThreadPoolExecutor(2) as workers:results=list(workers.map(save,range(2)))
        self.assertCountEqual(results,[2,'conflict'])
    def test_account_specific_entitlements_expire(self):
        self.store.grant_entitlement('one','ticket',2000,'publisher')
        self.assertTrue(self.store.entitled('one','ticket',1999));self.assertFalse(self.store.entitled('two','ticket',1000));self.assertFalse(self.store.entitled('one','ticket',2000))
    def test_reader_during_first_initialization_preserves_ordinary_gameplay(self):
        file=Path(self.tmp.name)/'opening.sqlite3'
        sqlite3.connect(file).close()
        self.assertEqual(Store(file,False).published(),[])
        Store(file);self.assertEqual(Store(file,False).published(),[])
    def test_import_failure_rolls_back_every_draft(self):
        one=self.doc()|{'id':'same'}
        with self.assertRaises(Conflict):self.store.save_import([one,one],'editor')
        self.assertEqual(self.store.list(),[]);self.assertEqual(self.store.history(),[])
    def test_invalid_region_rejected(self):
        with self.assertRaises(ValueError):self.store.save_region({'type':'polygon','points':[[0,0],[1,1],[0,1],[1,0]]},'editor')

class RuleTests(unittest.TestCase):
    def row(self,key='event',rules=None,priority=50):
        d=base('event',key,'2026-10-28T10:00:00Z');d.update(priority=priority,rules=rules or {})
        return dict(id=key,revision=1,document=d)
    def test_boundaries_half_open(self):
        row=self.row();start,end=interval(row['document'])
        self.assertEqual(resolve([row],start-1)['sources'],[]);self.assertTrue(resolve([row],start)['sources']);self.assertEqual(resolve([row],end)['sources'],[])
    def test_dst_gap_and_fold_are_explicit(self):
        with self.assertRaisesRegex(ValueError,'does not exist'):instant('2026-03-29T02:30','Europe/Amsterdam')
        with self.assertRaisesRegex(ValueError,'Ambiguous'):instant('2026-10-25T02:30','Europe/Amsterdam')
        self.assertEqual(instant('2026-10-25T02:30','Europe/Amsterdam',1)-instant('2026-10-25T02:30','Europe/Amsterdam',0),3600000)
    def test_weekly_rotation_uses_local_wall_time_across_dst(self):
        row=self.row();row['document'].update(kind='season',start='2026-10-19T09:00',end='2026-11-09T09:00',weeks=[{'rules':{'bonuses':{'catch_xp':2}}},{'rules':{'bonuses':{'catch_xp':3}}}])
        self.assertEqual(resolve([row],instant('2026-10-26T08:59'))['bonuses']['catch_xp'],2)
        self.assertEqual(resolve([row],instant('2026-10-26T09:00'))['bonuses']['catch_xp'],3)
    def test_priority_determinism_and_nonmultiplicative_default(self):
        a=self.row('a',{'bonuses':{'catch_dust':2},'spawns':[{'species':1}]},10);b=self.row('b',{'bonuses':{'catch_dust':2},'spawns':[{'species':4}]},50)
        at=instant('2026-10-28T11:00Z');v=resolve([b,a],at)
        self.assertEqual(v['bonuses']['catch_dust'],2);self.assertEqual(v['spawns'][0]['species'],4)
        b['document']['rules']['bonus_policy']='multiply';self.assertEqual(resolve([a,b],at)['bonuses']['catch_dust'],4)
    def test_spawn_only_events_do_not_reroll_unchanged_raids_or_max_spots(self):
        season=self.row('season',{'raids':[{'species':144}],'max':[{'species':831}],'spawns':[{'species':1}]},10)
        community=self.row('community',{'spawns':[{'species':4}],'bonuses':{'catch_xp':2}},50)
        at=instant('2026-10-28T11:00Z');before=resolve([season],at);after=resolve([season,community],at)
        self.assertEqual(before['generations']['raids'],after['generations']['raids'])
        self.assertEqual(before['generations']['max'],after['generations']['max'])
        self.assertNotEqual(before['generations']['spawns'],after['generations']['spawns'])
    def test_region_and_entitlement_are_fail_closed(self):
        row=self.row();row['document'].update(entitlement='ticket',scope={'type':'region','id':'city'})
        regions=[{'id':'city','type':'polygon','points':[[4,52],[5,52],[5,53],[4,53]]}]
        at=instant('2026-10-28T11:00Z')
        self.assertEqual(resolve([row],at,52.5,4.5,regions=regions)['sources'],[])
        self.assertTrue(resolve([row],at,52.5,4.5,regions=regions,entitled=lambda k:True)['sources'])
        self.assertEqual(resolve([row],at,0,0,regions=regions,entitled=lambda k:True)['sources'],[])

class GameplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.catalog=Catalog(json.loads((ROOT/'config/family.json').read_text()))
    def setUp(self):
        research_fixture.ResearchTests.setUp(self)
        self.store=Store(Path(self.tmp.name)/'studio.sqlite3')
        self.cfg['liveops']={'database':str(self.store.path.relative_to(ROOT)),'isolated_fixture':True,'fixture_clock_ms':instant('2026-10-28T11:00Z')}
    tearDown=research_fixture.ResearchTests.tearDown
    call=research_fixture.ResearchTests.call
    spin=research_fixture.ResearchTests.spin
    catch=research_fixture.ResearchTests.catch
    complete=research_fixture.ResearchTests.complete
    def publish(self,doc):
        validate(doc,self.catalog)
        row=self.store.save(doc,'test');self.store.action(row['id'],'publish','test',row['digest']);return row
    def event(self,species=1):
        d=base('event','Isolated event','2026-10-28T10:00Z');d['rules']={'spawns':[{'species':species,'weight':10,'level_range':[5,5],'shiny_rate':0}],'bonuses':{'catch_xp':2}}
        return d
    def view(self):
        with self.db.transaction() as db:return project(self.game,db,self.player)
    def test_an_unconfigured_isolated_database_never_reads_normal_content(self):
        self.publish(self.event())
        self.cfg.pop('liveops')
        with patch('dittobuffer.gameplay.liveops.runtime.Store',side_effect=AssertionError('Normal content must not be opened')):
            with self.db.transaction() as db:self.assertIs(project(self.game,db,self.player),self.game)
    def test_publish_affects_real_spawns_without_mutating_base_game(self):
        before=copy.deepcopy(self.game.spawn_pools);self.publish(self.event());view=self.view()
        with self.db.transaction() as db:
            ids=view.seed(db,52.37,4.89)
            species=[r[0] for r in db.execute('SELECT species FROM spawns WHERE id IN ('+','.join('?' for _ in ids)+')',ids)]
        self.assertEqual(set(species),{1});self.assertEqual(self.game.spawn_pools,before)
        self.assertNotEqual(view.config['spawn_generation'],self.game.config.get('spawn_generation',1))
    def test_end_returns_to_base_generation_and_preserves_owned_data(self):
        row=self.publish(self.event());self.assertIsNot(self.view(),self.game)
        self.store.action(row['id'],'end','publisher');self.assertIs(self.view(),self.game)
    def test_four_templates_are_valid_and_drive_gameplay_not_only_editor(self):
        self.game.raids.update(enabled=True,staggered=False,rotation_seconds=86400,egg_seconds=1,active_seconds=86399)
        self.game.raids.pop('epoch_ms',None)
        for key in ['community-day','spotlight-hour','research-day','raid-hour']:
            with self.subTest(template=key):
                bundle=template(key,self.catalog,'2026-10-28T10:30Z',1);self.assertTrue(bundle['runnable'])
                field=bundle['research'][0];field['stages'][0]['objectives'][0]['target']=1
                quest=self.publish(field);bundle['document']['rules']['research']=[quest['id']]
                row=self.publish(bundle['document']);view=self.view();self.assertTrue(view.liveops_effective['sources'])
                with self.db.transaction() as db:
                    ids=view.seed(db,52.37,4.89);self.assertTrue(ids)
                    if key=='raid-hour':
                        # main intentionally removed per-tile synthetic gyms;
                        # this isolated acceptance case owns its explicit fixture.
                        db.execute("INSERT OR IGNORE INTO forts VALUES('studio-test-gym',0,'Studio Test Gym',52.37,4.89)")
                        from dittobuffer.gameplay import raids
                        fort=db.execute('SELECT * FROM forts WHERE kind=0 LIMIT 1').fetchone()
                        raid=raids.schedule(view,db,fort);self.assertIsNotNone(raid)
                        self.assertEqual(json.loads(raid['definition'])['species'],1)
                    fort=dict(self.fort,id='template-'+key)
                    db.execute('INSERT INTO forts VALUES(?,1,?,?,?)',(fort['id'],'Template Field Station',fort['lat'],fort['lng']))
                earned=self.spin(fort);qid=earned.challenge_quest.quest.quest_id;self.assertTrue(qid)
                _,caught=self.catch();self.assertEqual(caught.status,1)
                self.assertEqual(self.complete(qid).status,1);self.assertEqual(self.complete(qid).status,4)
                self.store.action(row['id'],'end','publisher');self.store.action(quest['id'],'end','publisher')
    def test_research_uses_existing_progress_claim_tables_and_is_idempotent(self):
        doc=make_research('Studio field','2026-10-28T10:00Z',special=False);doc['event_only']=False;doc['stages'][0]['objectives'][0]['target']=1
        self.publish(doc)
        result=self.spin();qid=result.challenge_quest.quest.quest_id
        view=self.view()
        with self.db.transaction() as db:
            from dittobuffer.gameplay import research
            research.progress(view,db,self.player,'catch')
        first=self.complete(qid);second=self.complete(qid)
        self.assertEqual(first.status,1);self.assertEqual(second.status,4)
        self.game=Game(self.cfg,self.db);self.assertEqual(self.complete(qid).status,4)
    def test_generator_seed_and_constraints_use_real_catalog(self):
        opts=dict(name='Repeatable',start='2026-10-28T10:00Z',weeks=4,seed='same',generations=[1,2],legendary_count=4)
        a=generate(self.catalog,opts);b=generate(self.catalog,opts);self.assertEqual(a,b)
        validate(a['document'],self.catalog)
        bosses=[v['species'] for w in a['document']['weeks'] for v in w['rules']['raids'] if v['tier']==5]
        self.assertEqual(len(set(bosses)),4)
        with self.assertRaises(ValueError):generate(self.catalog,opts|{'legendary_count':99})
    def test_unavailable_assets_and_unsupported_tasks_cannot_publish(self):
        doc=self.event(999999)
        with self.assertRaises(ValueError):validate(doc,self.catalog)
        doc=make_research('Impossible','2026-10-28T10:00Z');doc['stages'][0]['objectives'][0]['event']='party_challenge'
        with self.assertRaises(ValueError):validate(doc,self.catalog)
    def test_live_clock_cannot_be_changed(self):
        self.cfg['liveops']['isolated_fixture']=False
        with self.assertRaises(ValueError):clock(self.game)

    def test_real_catch_wrapper_uses_event_projection_with_pvp_enabled(self):
        self.publish(self.event())
        self.cfg['pokedex_entry_xp']=0
        # Force the same installed instance wrapper used by the real family client.
        from dittobuffer.gameplay import pvp_encounters
        with self.db.transaction() as db:db.execute('CREATE TABLE IF NOT EXISTS pvp_reward_encounters(player_id TEXT,season TEXT,set_id INTEGER,slot INTEGER,spawn_id INTEGER)')
        pvp_encounters.install(self.game)
        _,result=self.catch()
        self.assertEqual(result.status,1)
        self.assertEqual(sum(result.scores.exp),self.cfg['catch_xp']*2)
    def test_legacy_and_studio_two_x_do_not_become_four_x(self):
        from dittobuffer.gameplay.liveops.runtime import score
        self.game.reward_bonuses.update(enabled=True,events=[dict(start_ms=0,end_ms=2**62,xp=2,dust=2,candy=2)])
        self.game.liveops_effective={'bonuses':dict(catch_xp=2,catch_candy=2,catch_dust=3)}
        self.assertEqual(score(self.game,[[1,200,6,200]]),[[1,200,6,300]])
        self.game.liveops_effective['bonuses']['catch_xp']=3
        self.assertEqual(score(self.game,[[1,200,6,200]])[0][1],300)
    def test_filtered_research_progress_uses_authoritative_context(self):
        from dittobuffer.gameplay.liveops.runtime import task_matches
        spec={'conditions':{'species':[1],'types':[12],'shiny':True,'throw_quality':'great','curveball':True,'ball':3}}
        context=dict(species=1,types=[12,4],shiny=True,quality=1.8,curveball=True,ball=3)
        self.assertTrue(task_matches(self.game,spec,context))
        for key,value in [('species',4),('types',[10]),('shiny',False),('quality',float('nan')),('curveball',False),('ball',1)]:
            self.assertFalse(task_matches(self.game,spec,context|{key:value}))
    def test_field_step_reward_is_included_and_claimed_once(self):
        doc=make_research('Bundled field','2026-10-28T10:00Z',special=False);doc['event_only']=False;doc['stages'][0]['objectives'][0]['target']=1
        self.publish(doc);qid=self.spin().challenge_quest.quest.quest_id
        view=self.view()
        with self.db.transaction() as db:
            from dittobuffer.gameplay import research
            research.progress(view,db,self.player,'catch')
            before=db.execute('SELECT count FROM items WHERE player_id=? AND item=1',(self.player,)).fetchone()[0]
        self.assertEqual(self.complete(qid).status,1);self.assertEqual(self.complete(qid).status,4)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1',(self.player,)).fetchone()[0],before+5)
    def test_generator_includes_community_day_and_each_week_mini_event(self):
        bundle=generate(self.catalog,dict(name='Balanced',start='2026-10-28T10:00Z',weeks=4,seed='a',generations=[1,2],legendary_count=4))
        events=bundle['events'];self.assertEqual(len([v for v in events if v['document']['event_type']=='community-day']),1)
        self.assertEqual(len([v for v in events if v['document']['event_type']=='research-day']),4)
        for v in events:validate(v['document'],self.catalog)
        discovery=template('season-discovery',self.catalog,'2026-10-28T10:00Z');validate(discovery['research'][0],self.catalog)
    def test_weighted_egg_projection_and_request_local_max_window(self):
        doc=self.event();doc['rules']['eggs']=[dict(species=1,km=2,weight=9),dict(species=4,km=2,weight=1)]
        self.publish(doc);view=self.view()
        self.assertEqual([r['weight'] for r in view.config['eggs']['pools']],[9,1])
        self.assertNotEqual(view.config['eggs']['pools'],self.game.config['eggs']['pools'])
    def test_imported_multipolygon_preserves_holes(self):
        scope={'type':'polygon','geometry':{'type':'MultiPolygon','coordinates':[[[[4,52],[5,52],[5,53],[4,53],[4,52]],[[4.3,52.3],[4.7,52.3],[4.7,52.7],[4.3,52.7],[4.3,52.3]]]]}}
        validate_scope(scope);self.assertTrue(matches(scope,52.1,4.1));self.assertFalse(matches(scope,52.5,4.5))


class MaxStudioTests(unittest.TestCase):
    # Existing synthetic Max fixture, kept distinct from native acceptance.
    from tests import test_max_system as fixture
    setUp=fixture.MaxTests.setUp
    tearDown=fixture.MaxTests.tearDown
    def test_studio_pause_resume_and_end_keep_one_station_per_candidate(self):
        from dittobuffer.gameplay import max_system as mx
        store=Store(self.path/'studio.sqlite3')
        self.cfg['liveops']={'database':str(store.path.relative_to(ROOT)),'isolated_fixture':True,'fixture_clock_ms':self.clock}
        doc=base('event','Max overlay',self.clock-1000)
        doc['rules']={'max':self.game.max_config['pools']}
        row=store.save(doc,'publisher');store.action(row['id'],'publish','publisher',row['digest'])
        for action in (None,'pause','resume','end'):
            if action:store.action(row['id'],action,'publisher')
            with self.db.transaction() as db:
                view=project(self.game,db,self.players[0]);mx.materialize(view,db,[52,53,5,6])
                active=db.execute('SELECT source_id,COUNT(*) n FROM max_stations WHERE end_ms>? GROUP BY source_id',(self.clock,)).fetchall()
                self.assertEqual(len(active),20,(action,len(active)))
                self.assertTrue(all(r['n']==1 for r in active),action)
        with self.db.transaction() as db:self.assertGreater(db.execute('SELECT COUNT(*) FROM max_stations').fetchone()[0],20)
