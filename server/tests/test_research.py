import copy,json,sqlite3,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
from dittobuffer.gameplay.game import Game,Database,ROOT,p,wire,RpcError,now_ms
from dittobuffer.gameplay import research

class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds')
        self.cfg=json.loads((ROOT/'config/development.json').read_text())
        self.cfg['research']=json.loads((ROOT/'src/dittobuffer/gameplay/research.json').read_text())
        self.cfg['research'].update(enabled=True,field_pool=[{'task':'first-catch','weight':1}],stories=[])
        self.cfg['fort_cooldown_seconds']=0;self.cfg['eggs']['stop_drop_probability']=0
        self.db=Database(Path(self.tmp.name)/'research.sqlite3');self.game=Game(self.cfg,self.db)
        self.ticket=self.game.authenticate(wire.AuthRequest(auth_id='research-test')).ticket;self.counter=0
        self.methods={v:k for k,v in self.game.methods.items()}
        with self.db.transaction() as db:
            self.game.seed(db,52.37,4.89)
            db.execute("INSERT INTO forts VALUES('research-stop',1,'Research Stop',52.37,4.89)")
            self.player=db.execute('SELECT id FROM players').fetchone()[0]
            self.fort=dict(db.execute('SELECT * FROM forts WHERE kind=1').fetchone())
    def tearDown(self):self.tmp.cleanup()
    def call(self,name,body,response,rpc_id=None,ticket=None):
        self.counter+=1;req=wire.RpcRequest(rpc_id=rpc_id or self.counter)
        req.requests.add(method=self.methods[name],body=body.SerializeToString())
        return response.FromString(self.game.rpc(req,ticket or self.ticket).payloads[0].payload)
    def spin(self,fort=None,rpc_id=None):
        fort=fort or self.fort
        return self.call('FORT_SEARCH',p.FortSearchProto(id=fort['id'],player_lat_degrees=fort['lat'],player_lng_degrees=fort['lng']),p.FortSearchOutProto,rpc_id)
    def task(self):return self.spin().challenge_quest.quest.quest_id
    def complete(self,identifier,rpc_id=None,ticket=None):
        return self.call('COMPLETE_QUEST',p.CompleteQuestProto(quest_id=identifier),p.CompleteQuestOutProto,rpc_id,ticket)
    def catch(self,hit=True):
        with self.db.transaction() as db:
            # Reward encounters share spawns but have no wild generation and
            # may intentionally have no map expiration. Exercise a real wild
            # catch here regardless of random reward/wild ID ordering.
            self.game.seed(db,52.37,4.89)
            spawn=dict(db.execute("SELECT s.* FROM spawns s JOIN spawn_generations g ON g.spawn_id=s.id WHERE s.expires_ms>? AND g.generation=? AND NOT EXISTS(SELECT 1 FROM encounters e WHERE e.player_id=? AND e.spawn_id=s.id AND e.state='caught') ORDER BY s.id LIMIT 1",(now_ms(),self.cfg.get('spawn_generation',1),self.player)).fetchone())
        self.call('ENCOUNTER',p.EncounterProto(encounter_id=spawn['id'],spawnpoint_id=spawn['spawnpoint'],player_lat_degrees=spawn['lat'],player_lng_degrees=spawn['lng']),p.EncounterOutProto)
        body=p.CatchPokemonProto(encounter_id=spawn['id'],spawn_point_guid=spawn['spawnpoint'],pokeball=3,hit_pokemon=hit)
        result=self.call('CATCH_POKEMON',body,p.CatchPokemonOutProto)
        return body,result
    def progress(self,identifier):
        with self.db.transaction() as db:return db.execute('SELECT progress FROM research_tasks WHERE player_id=? AND id=?',(self.player,identifier)).fetchone()[0]
    def objective(self,key,target=None):
        self.game.research['field_pool']=[{'task':key,'weight':1}]
        if target:self.game.research['field_tasks'][key]['target']=target
        return self.task()
    def test_literal_encounter_id_and_quest_wire(self):
        self.assertEqual(p.QuestEncounterProto(pokemon_id=1).SerializeToString(),b'\x09\x01'+b'\0'*7)
        self.assertEqual(p.TodayViewSectionProto(challenge_quests=p.ChallengeQuestSectionProto(quest_id=['q'])).SerializeToString(),b'\x5a\x03\x0a\x01q')
        identifier=self.task()
        out=self.call('GET_NEW_QUESTS',p.GetNewQuestsProto(),p.GetNewQuestsOutProto)
        self.assertEqual(out.quests[0].quest.quest_id,identifier)
        self.assertEqual(out.quests[0].quest.goal.target,1)
        inventory=self.call('GET_HOLOHOLO_INVENTORY',p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
        record=next(p.HoloInventoryItemProto.FromString(v.item).quests for v in inventory.inventory_delta.inventory_item if p.HoloInventoryItemProto.FromString(v.item).HasField('quests'))
        self.assertEqual(record.quest[0].quest_id,identifier)
    def test_progress_from_real_catches_restart_and_new_id_retry(self):
        identifier=self.objective('first-catch',2)
        body,result=self.catch();self.assertEqual(result.status,1)
        self.call('CATCH_POKEMON',body,p.CatchPokemonOutProto)
        self.assertEqual(self.progress(identifier),1)
        self.assertEqual(self.complete(identifier).status,3)
        self.game=Game(self.cfg,Database(self.db.path));self.assertEqual(self.progress(identifier),1)
        self.catch();self.assertEqual(self.progress(identifier),2)
        with self.db.transaction() as db:before=db.execute('SELECT count FROM items WHERE player_id=? AND item=1',(self.player,)).fetchone()[0]
        body=p.CompleteQuestProto(quest_id=identifier,choice_id=-1)
        result=self.call('COMPLETE_QUEST',body,p.CompleteQuestOutProto,1000);self.assertEqual(result.status,1)
        self.assertEqual(self.call('COMPLETE_QUEST',body,p.CompleteQuestOutProto,1000),result)
        self.assertEqual(self.complete(identifier).status,4)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1',(self.player,)).fetchone()[0],before+3)
    def test_landed_throw_and_spin_only_count_committed_actions(self):
        identifier=self.objective('steady-throw')
        self.catch(hit=False);self.assertEqual(self.progress(identifier),0)
        self.catch();self.assertEqual(self.progress(identifier),1)
        # A transaction containing an unknown RPC rolls back its spin/progress.
        self.game.research['field_pool']=[{'task':'local-stop','weight':1}]
        with self.db.transaction() as db:db.execute('INSERT INTO forts VALUES(?,1,?,?,?)',('second','Second',self.fort['lat'],self.fort['lng']))
        fort=dict(self.fort,id='second');spin_id=self.spin(fort).challenge_quest.quest.quest_id
        req=wire.RpcRequest(rpc_id=2000);req.requests.add(method=self.methods['FORT_SEARCH'],body=p.FortSearchProto(id=fort['id'],player_lat_degrees=fort['lat'],player_lng_degrees=fort['lng']).SerializeToString());req.requests.add(method=999999)
        with self.assertRaises(RpcError):self.game.rpc(req,self.ticket)
        self.assertEqual(self.progress(spin_id),0)
        self.spin(fort);self.assertEqual(self.progress(spin_id),1)
    def test_capacity_daily_assignment_and_abandonment(self):
        identifiers=[self.task()]
        self.assertFalse(self.spin().HasField('challenge_quest'))
        for i in range(1,4):
            fort=dict(self.fort,id=f'fort-{i}')
            with self.db.transaction() as db:db.execute('INSERT INTO forts VALUES(?,1,?,?,?)',(fort['id'],'Extra',fort['lat'],fort['lng']))
            out=self.spin(fort)
            if i<3:identifiers.append(out.challenge_quest.quest.quest_id)
            else:self.assertFalse(out.HasField('challenge_quest'))
        self.assertEqual(self.call('REMOVE_QUEST',p.RemoveQuestProto(quest_id=identifiers[0]),p.RemoveQuestOutProto).status,1)
        self.assertFalse(self.spin().HasField('challenge_quest')) # Abandon is not a reward reroll.
        self.assertTrue(self.spin(fort).HasField('challenge_quest'))
        with patch('dittobuffer.gameplay.research.now_ms',return_value=__import__('time').time_ns()//1000000+86400000):
            self.call('REMOVE_QUEST',p.RemoveQuestProto(quest_id=identifiers[1]),p.RemoveQuestOutProto)
            self.assertTrue(self.spin().HasField('challenge_quest'))
    def test_full_bag_and_cross_owner_cannot_claim(self):
        identifier=self.task();self.catch()
        other=self.game.authenticate(wire.AuthRequest(auth_id='other-research-owner')).ticket
        self.assertEqual(self.complete(identifier,ticket=other).status,2)
        with self.db.transaction() as db:db.execute('UPDATE items SET count=? WHERE player_id=? AND item=1',(self.cfg['item_capacity'],self.player))
        self.assertEqual(self.complete(identifier).status,11)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT claimed FROM research_tasks WHERE player_id=? AND id=?',(self.player,identifier)).fetchone()[0],0)
    def test_encounter_reward_reopens_and_uses_generic_catch(self):
        identifier=self.objective('new-neighbor',1);self.catch()
        self.assertEqual(self.complete(identifier).status,1)
        with self.db.transaction() as db:spawn=db.execute('SELECT spawn_id FROM research_encounters WHERE player_id=? AND quest_id=?',(self.player,identifier)).fetchone()[0]
        request=p.QuestEncounterProto(pokemon_id=spawn,quest_id=identifier)
        first=self.call('QUEST_ENCOUNTER',request,p.QuestEncounterOutProto)
        self.assertEqual(first.result,1);self.assertEqual(first.pokemon.pokemon_id,16)
        self.game=Game(self.cfg,Database(self.db.path))
        self.assertEqual(self.call('QUEST_ENCOUNTER',request,p.QuestEncounterOutProto),first)
        caught=self.call('CATCH_POKEMON',p.CatchPokemonProto(encounter_id=spawn,spawn_point_guid='',pokeball=3,hit_pokemon=True),p.CatchPokemonOutProto)
        self.assertEqual(caught.status,1)
        self.assertEqual(self.call('QUEST_ENCOUNTER',request,p.QuestEncounterOutProto).result,3)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT species FROM pokemon WHERE id=?',(caught.captured_pokemon_id,)).fetchone()[0],16)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
    def test_acquired_definition_is_frozen_and_invalid_config_rejected(self):
        identifier=self.task();self.game.research['field_tasks']['first-catch']['rewards'][0]['amount']=999
        self.catch();result=self.complete(identifier)
        self.assertEqual(result.rewards[0].item.amount,3)
        self.cfg['research']['field_tasks']['first-catch']['event']='unsupported'
        with self.assertRaises(ValueError):Game(self.cfg,self.db)

    def test_daily_stamps_and_breakthrough_are_persistent_and_once_only(self):
        self.game.research['field_pool']=[{'task':'local-stop','weight':1}]
        for index in range(7):
            with patch('dittobuffer.gameplay.research.now_ms',return_value=1800000000000+index*86400000):
                identifier=self.task();self.spin()
                result=self.complete(identifier);self.assertEqual(result.status,1);self.assertEqual(len(result.stamp),1)
                with self.db.transaction() as db:
                    card=research.card(self.game,db,self.player)
                    self.assertEqual(card.target,7);self.assertEqual(card.remaining_daily_stamps,0);self.assertEqual(len(card.stamp),index+1)
        self.game=Game(self.cfg,Database(self.db.path))
        result=self.call('COMPLETE_QUEST_STAMP_CARD',p.CompleteQuestStampCardProto(),p.CompleteQuestStampCardOutProto,rpc_id=4000)
        self.assertEqual(result.status,1)
        self.assertEqual(self.call('COMPLETE_QUEST_STAMP_CARD',p.CompleteQuestStampCardProto(),p.CompleteQuestStampCardOutProto,rpc_id=4000),result)
        self.assertEqual(self.call('COMPLETE_QUEST_STAMP_CARD',p.CompleteQuestStampCardProto(),p.CompleteQuestStampCardOutProto).status,2)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT cycle FROM research_cards').fetchone()[0],2)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def story(self):
        self.game.research['stories']=json.loads((ROOT/'src/dittobuffer/gameplay/research.json').read_text())['stories']
        with self.db.transaction() as db:research.ensure(self.game,db,self.player)
        return self.story_quest()
    def story_quest(self):
        from dittobuffer.gameplay import stories
        with self.db.transaction() as db:return stories.current(self.game,db,self.player)[0]
    def story_claim(self,root,child='',rpc_id=None,ticket=None):
        return self.call('COMPLETE_QUEST',p.CompleteQuestProto(quest_id=root,sub_quest_id=child,choice_id=-1),p.CompleteQuestOutProto,rpc_id,ticket)
    def test_story_two_stages_actual_events_restart_and_claim_ledgers(self):
        from dittobuffer.gameplay import stories
        first=self.story();root=first.quest.quest_id
        self.assertEqual(len(first.quest.multi_part.sub_quests),3)
        self.assertEqual(self.story_claim(root).status,3)
        self.assertEqual(self.call('REMOVE_QUEST',p.RemoveQuestProto(quest_id=root),p.RemoveQuestOutProto).status,3)
        self.catch();self.spin();ready=self.story_quest()
        for child in ready.quest.multi_part.sub_quests:
            self.assertEqual(child.progress,child.goal.target)
            result=self.story_claim(root,child.quest_id,rpc_id=5000+self.counter)
            self.assertEqual(result.status,1)
            self.assertEqual(self.story_claim(root,child.quest_id).status,4)
        self.assertEqual(self.story_claim(root,rpc_id=6000).status,1)
        self.assertEqual(self.story_claim(root).status,4)
        second=self.story_quest();self.assertNotEqual(second.quest.quest_id,root)
        self.assertTrue(all(child.progress==0 for child in second.quest.multi_part.sub_quests))
        self.game=Game(self.cfg,Database(self.db.path)) # Persisted story remains despite operator removing definition.
        self.assertEqual(self.story_quest(),second)
        for _ in range(3):self.catch()
        self.spin();self.spin()
        root=second.quest.quest_id
        for child in self.story_quest().quest.multi_part.sub_quests:self.assertEqual(self.story_claim(root,child.quest_id).status,1)
        result=self.story_claim(root,rpc_id=7000);self.assertEqual(result.status,1)
        self.assertEqual(self.story_claim(root,rpc_id=7000),result)
        self.assertEqual(self.story_claim(root).status,4)
        with self.db.transaction() as db:
            self.assertEqual(stories.current(self.game,db,self.player),[])
            self.assertEqual(len(stories.completed(db,self.player)),1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM research_stage_claims').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM research_encounters WHERE encounter_type=4').fetchone()[0],2)
            pending=[dict(v) for v in db.execute('SELECT * FROM research_encounters WHERE encounter_type=4 ORDER BY created_ms')]
            self.assertEqual({v['client_quest_id'] for v in pending},{root})
            self.assertEqual(len({v['quest_id'] for v in pending}),2)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
        # A deferred individual encounter and the stage reward share a visible
        # story ID. Both must remain independently claimable after restart.
        self.game=Game(self.cfg,Database(self.db.path))
        for entry in pending:
            request=p.QuestEncounterProto(quest_id=root,pokemon_id=entry['spawn_id'])
            opened=self.call('QUEST_ENCOUNTER',request,p.QuestEncounterOutProto)
            self.assertEqual(opened.result,1)
            result=self.call('CATCH_POKEMON',p.CatchPokemonProto(encounter_id=entry['spawn_id'],pokeball=3,hit_pokemon=True),p.CatchPokemonOutProto)
            self.assertEqual(result.status,1)
            self.assertEqual(self.call('QUEST_ENCOUNTER',request,p.QuestEncounterOutProto).result,3)
    def test_story_claim_cross_owner_capacity_and_transaction_rollback(self):
        root=self.story().quest.quest_id;self.catch();self.spin()
        ready=self.story_quest();child=ready.quest.multi_part.sub_quests[0].quest_id
        other=self.game.authenticate(wire.AuthRequest(auth_id='other-story-owner')).ticket
        self.assertEqual(self.story_claim(root,child,ticket=other).status,2)
        self.assertEqual(self.story_claim(root,'wrong-child').status,5)
        self.assertEqual(self.complete(child).status,2) # Cannot bypass parent/stage validation.
        with self.db.transaction() as db:db.execute('UPDATE items SET count=? WHERE player_id=? AND item=1',(self.cfg['item_capacity'],self.player))
        self.assertEqual(self.story_claim(root,child).status,11)
        with self.db.transaction() as db:db.execute('UPDATE items SET count=10 WHERE player_id=? AND item=1',(self.player,))
        req=wire.RpcRequest(rpc_id=8000)
        req.requests.add(method=self.methods['COMPLETE_QUEST'],body=p.CompleteQuestProto(quest_id=root,sub_quest_id=child).SerializeToString())
        req.requests.add(method=999999)
        with self.assertRaises(RpcError):self.game.rpc(req,self.ticket)
        self.assertEqual(self.story_quest(),ready)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1',(self.player,)).fetchone()[0],10)
        self.assertEqual(self.story_claim(root,child).status,1)
    def test_story_dialog_read_and_definition_validation(self):
        root=self.story().quest.quest_id
        self.assertEqual(self.call('READ_QUEST_DIALOG',p.ReadQuestDialogProto(quest_id=root),p.ReadQuestDialogOutProto).status,1)
        self.game=Game(self.cfg,Database(self.db.path))
        self.assertTrue(self.story_quest().quest.dialog_read)
        self.assertEqual(self.call('READ_QUEST_DIALOG',p.ReadQuestDialogProto(quest_id='not-owned'),p.ReadQuestDialogOutProto).status,2)
        self.cfg['research']['stories']=[{'id':'invalid','title':'Bad','stages':[]}]
        with self.assertRaises(ValueError):Game(self.cfg,self.db)

if __name__=='__main__':unittest.main()
