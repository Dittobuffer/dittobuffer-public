"""Fresh-only native tutorial transactions; not Android visual acceptance."""
import json,tempfile,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dittobuffer.core.database import Database
from dittobuffer.gameplay.game import Game,ROOT,p,wire

class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds');self.counter=0
        self.cfg=json.loads((ROOT/'config/development.json').read_text());self.cfg['onboarding']={'enabled':True}
        self.db=Database(Path(self.tmp.name)/'intro.sqlite3');self.game=Game(self.cfg,self.db)
        self.tickets=[self.game.authenticate(wire.AuthRequest(auth_id='m24-intro-'+str(i))).ticket for i in range(2)]
        with self.db.transaction() as db:self.players=[db.execute('SELECT player_id FROM sessions WHERE ticket=?',(t,)).fetchone()[0] for t in self.tickets]
    def tearDown(self):self.tmp.cleanup()
    def call(self,method,body,typ,index=0):
        self.counter+=1;req=wire.RpcRequest(rpc_id=self.counter);req.requests.add(method=method,body=body.SerializeToString())
        return typ.FromString(self.game.rpc(req,self.tickets[index]).payloads[0].payload)
    def avatar(self,index=0):
        with self.db.transaction() as db:avatar=self.game.appearance(db,self.players[index])
        out=self.call(408,p.SetNeutralAvatarProto(player_neutral_avatar_proto=avatar),p.SetNeutralAvatarOutProto,index)
        self.assertEqual(out.status,1);self.assertIn(1,out.player.tutorial_complete)
    def flags(self,index=0):return set(self.call(2,p.GetPlayerProto(),p.GetPlayerOutProto,index).player.tutorial_complete)
    def test_fresh_detection_and_legacy_trainer_preservation(self):
        self.assertEqual(self.flags(),{0,2})
        self.assertFalse(self.call(406,p.MarkTutorialCompleteProto(tutorial_complete=[3,7]),p.MarkTutorialCompleteOutProto).success)
        with self.db.transaction() as db:db.execute('DELETE FROM onboarding_players WHERE player_id=?',(self.players[1],))
        self.assertTrue({0,1,3,4,7}<=self.flags(1));self.assertEqual(self.flags(),{0,2})
    def test_avatar_mark_race_acknowledges_ui_without_granting_entitlement(self):
        out=self.call(406,p.MarkTutorialCompleteProto(tutorial_complete=[1]),p.MarkTutorialCompleteOutProto)
        self.assertTrue(out.success);self.assertIn(1,out.player.tutorial_complete)
        self.game=Game(self.cfg,self.db);self.assertNotIn(1,self.flags())
        self.assertEqual(self.call(127,p.EncounterTutorialCompleteProto(pokedex_id=7),p.EncounterTutorialCompleteOutProto).result,2)
        self.avatar();self.assertIn(1,self.flags())
    def test_actual_starter_choice_once_and_restart(self):
        req=p.EncounterTutorialCompleteProto(pokedex_id=1)
        self.assertEqual(self.call(127,req,p.EncounterTutorialCompleteOutProto).result,2)
        self.avatar();out=self.call(127,req,p.EncounterTutorialCompleteOutProto)
        self.assertEqual(out.result,1);self.assertEqual(out.pokemon.pokemon_id,1)
        self.game=Game(self.cfg,self.db)
        self.assertEqual(self.call(127,p.EncounterTutorialCompleteProto(pokedex_id=4),p.EncounterTutorialCompleteOutProto),out)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pokemon').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT captures FROM players WHERE id=?',(self.players[0],)).fetchone()[0],1)
        self.assertTrue({1,3,24}<=self.flags());self.assertNotIn(7,self.flags())
        self.assertEqual(self.call(127,req,p.EncounterTutorialCompleteOutProto,1).result,2)
    def test_simultaneous_starter_completions_share_one_receipt(self):
        self.avatar()
        def complete(i):
            req=wire.RpcRequest(rpc_id=9000+i)
            req.requests.add(method=127,body=p.EncounterTutorialCompleteProto(pokedex_id=7).SerializeToString())
            return p.EncounterTutorialCompleteOutProto.FromString(self.game.rpc(req,self.tickets[0]).payloads[0].payload)
        with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(complete,range(2)))
        self.assertEqual(responses[0],responses[1]);self.assertEqual(responses[0].result,1)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pokemon').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT captures FROM players WHERE id=?',(self.players[0],)).fetchone()[0],1)
    def test_nickname_validation_availability_owner_and_no_reclaim(self):
        self.assertEqual(self.call(403,p.ClaimCodenameRequestProto(codename='bad name!'),p.CodenameResultProto).status,3)
        out=self.call(403,p.ClaimCodenameRequestProto(codename='M24Trainer',generate_suggested_codenames=True),p.CodenameResultProto)
        self.assertTrue(out.is_assignable);self.assertNotIn(4,self.flags())
        self.assertEqual(self.call(403,p.ClaimCodenameRequestProto(codename='M24Trainer'),p.CodenameResultProto).status,1)
        self.assertEqual(self.call(403,p.ClaimCodenameRequestProto(codename='m24trainer'),p.CodenameResultProto,1).status,2)
        self.assertEqual(self.call(403,p.ClaimCodenameRequestProto(codename='OtherName'),p.CodenameResultProto).status,5)
        self.assertEqual(self.call(403,p.ClaimCodenameRequestProto(codename='M24Trainer'),p.CodenameResultProto).status,4)
    def test_completion_requires_avatar_starter_and_name_then_survives_restart(self):
        self.avatar();self.call(127,p.EncounterTutorialCompleteProto(pokedex_id=7),p.EncounterTutorialCompleteOutProto)
        self.assertFalse(self.call(406,p.MarkTutorialCompleteProto(tutorial_complete=[7]),p.MarkTutorialCompleteOutProto).success)
        self.call(403,p.ClaimCodenameRequestProto(codename='M24Complete'),p.CodenameResultProto)
        self.assertTrue(self.call(406,p.MarkTutorialCompleteProto(tutorial_complete=[7]),p.MarkTutorialCompleteOutProto).success)
        self.game=Game(self.cfg,self.db);self.assertTrue({0,1,2,3,4,7,24}<=self.flags())
        self.assertFalse(self.call(406,p.MarkTutorialCompleteProto(tutorial_complete=[7,99999]),p.MarkTutorialCompleteOutProto).success)
    def test_unsupported_starter_and_capacity_fail_without_reward(self):
        self.avatar()
        self.assertEqual(self.call(127,p.EncounterTutorialCompleteProto(pokedex_id=150),p.EncounterTutorialCompleteOutProto).result,2)
        self.game.config['pokemon_capacity']=0
        self.assertEqual(self.call(127,p.EncounterTutorialCompleteProto(pokedex_id=1),p.EncounterTutorialCompleteOutProto).result,2)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM pokemon').fetchone()[0],0)

if __name__=='__main__':unittest.main()
