"""Transactional native-message tests; not Android encounter verification."""
import unittest
from unittest.mock import patch
from tests import test_routes as support
from dittobuffer.gameplay.game import p,Game
from dittobuffer.core.database import Database
from dittobuffer.gameplay import route_mateo as m,routes,gifts

class MateoTests(unittest.TestCase):
    def setUp(self):
        self.f=support.RouteTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f
        f.game.config['social']={'enabled':True,'timezone':'Europe/Amsterdam'}
        from dittobuffer.gameplay.friends import install
        install(f.game)
        f.game.routes['mateo']={'enabled':True,'dust':100,'items':{'1':2}}
        f.game.config['routes']=dict(f.game.routes)
        value=patch('dittobuffer.gameplay.route_mateo.now_ms',lambda:f.clock);value.start();self.addCleanup(value.stop)
        with f.db.transaction() as db:
            # Genuine Gift records with known stop provenance; explicit pool
            # contribution by its actual owner, not a synthetic player profile.
            for ident,owner,fort in [(100,f.players[0],'route-b'),(200,f.players[1],'route-a')]:
                db.execute('INSERT INTO social_gifts(id,owner,fort_id,name,image,lat,lng,created_ms) VALUES(?,?,?,?,?,?,?,?)',(ident,owner,fort,'Test stop','https://example.invalid/stop.png',52.08,5.12,f.clock))
            m.contribute(f.game,db,f.players[1],200,'unit-route')
        self.initial_start=f.start()
        for i in range(1,26):f.fix(i)
        self.enc=f.progress().route_play.npc_encounter.encounter_id
        self.assertTrue(self.enc)

    def send(self,index=0):return self.f.call(2401,p.NpcSendGiftProto(encounter_id=self.enc,fort_id='route-b'),p.NpcSendGiftOutProto,index)
    def open(self,index=0):return self.f.call(2402,p.NpcOpenGiftProto(encounter_id=self.enc),p.NpcOpenGiftOutProto,index)

    def test_actual_gift_exchange_postcard_restart_and_replays(self):
        f=self.f;out=self.send();self.assertEqual(out.result,1)
        self.assertEqual(out.retrived_giftbox.gift_box.giftbox_id,200)
        self.assertFalse(out.retrived_giftbox.gift_box.sender_id)
        self.assertFalse(out.retrived_giftbox.HasField('sender_profile'))
        self.assertEqual(self.send().SerializeToString(),out.SerializeToString())
        with f.db.transaction() as db:
            card=gifts.postcard(f.game,db,f.players[0],p.CreatePostcardProto(giftbox_id=200).SerializeToString())
            self.assertEqual(card.result,1);self.assertFalse(card.postcard.postcard_creator_id)
            before=f.game.player(db,f.players[0])['stardust']
        earned=self.open();self.assertEqual(earned.result,1);self.assertEqual(earned.current_step,'done')
        self.assertEqual(self.open().SerializeToString(),earned.SerializeToString())
        restarted=Game(f.game.config,Database(f.db.path))
        with restarted.db.transaction() as db:
            self.assertEqual(restarted.player(db,f.players[0])['stardust'],before+100)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM route_gift_pool WHERE withdrawn_by IS NOT NULL').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM social_postcards WHERE player_id=?',(f.players[0],)).fetchone()[0],1)
            self.assertEqual(m.open_gift(restarted,db,f.players[0],p.NpcOpenGiftProto(encounter_id=self.enc).SerializeToString()).SerializeToString(),earned.SerializeToString())

    def test_ownership_steps_daily_limit_and_no_pool_are_safe(self):
        f=self.f
        self.assertEqual(self.send(1).result,2);self.assertEqual(self.open(1).result,3)
        bad=f.call(2400,p.NpcUpdateStateProto(encounter_id=self.enc,set_current_step='done'),p.NpcUpdateStateOutProto)
        self.assertEqual(bad.state,3)
        with f.db.transaction() as db:db.execute('UPDATE route_gift_pool SET withdrawn_by=?',(f.players[1],))
        self.assertEqual(self.send().result,2)
        with f.db.transaction() as db:
            self.assertEqual(db.execute('SELECT deleted FROM social_gifts WHERE id=100').fetchone()[0],0)
            db.execute('UPDATE route_gift_pool SET withdrawn_by=NULL')
        self.assertEqual(self.send().result,1);self.assertEqual(self.open().result,1)
        self.assertEqual(f.progress(True).status,1)
        f.fix(0,advance=200000);f.start()
        for i in range(1,26):f.fix(i)
        self.assertFalse(f.progress().route_play.HasField('npc_encounter'))

    def test_no_proximity_wrong_fort_bag_full_and_duplicate_contribution(self):
        f=self.f
        with f.db.transaction() as db:
            with self.assertRaises(ValueError):m.contribute(f.game,db,f.players[0],200)
        bad=f.call(2401,p.NpcSendGiftProto(encounter_id=self.enc,fort_id='route-a'),p.NpcSendGiftOutProto)
        self.assertEqual(bad.result,2)
        self.assertEqual(self.send().result,1)
        with f.db.transaction() as db:db.execute('UPDATE items SET count=10000 WHERE player_id=?',(f.players[0],))
        self.assertEqual(self.open().result,6)
        with f.db.transaction() as db:
            self.assertEqual(db.execute('SELECT opened_ms FROM social_gifts WHERE id=200').fetchone()[0],0)
            db.execute('UPDATE items SET count=0 WHERE player_id=?',(f.players[0],))
        self.assertEqual(self.open().result,1)
    def test_npc_gift_cannot_be_opened_again_through_friends_and_details_are_private(self):
        f=self.f;self.assertEqual(self.send().result,1)
        with f.db.transaction() as db:
            detail=gifts.details(db,db.execute('SELECT * FROM social_gifts WHERE id=200').fetchone(),f.players[0])
            self.assertFalse(detail.sender_id);self.assertFalse(detail.sender_codename)
            # Even an existing real friendship cannot switch reward domains.
            a,b=sorted(f.players);db.execute('INSERT INTO social_pairs(a,b,created_ms) VALUES(?,?,?)',(a,b,f.clock))
            result=gifts.open_gift(f.game,db,f.players[0],p.OpenGiftProto(giftbox_id=200,player_id=f.players[1]).SerializeToString())
            self.assertEqual(result.result,5)
        self.assertEqual(self.open().result,1)
    def test_unavailable_pool_terminates_without_consuming_a_gift(self):
        f=self.f
        with f.db.transaction() as db:db.execute('UPDATE route_gift_pool SET withdrawn_by=?',(f.players[1],))
        state=f.progress().route_play.npc_encounter
        self.assertEqual(state.steps[0].event.event,1)
        closed=f.call(2400,p.NpcUpdateStateProto(encounter_id=self.enc,set_current_step='done'),p.NpcUpdateStateOutProto)
        self.assertEqual(closed.state,1)
        with f.db.transaction() as db:self.assertEqual(db.execute('SELECT deleted FROM social_gifts WHERE id=100').fetchone()[0],0)

    def test_initial_projection_survives_pause_and_restart_without_early_exchange(self):
        # Native RouteSetup 0x8295B04 schedules a one-shot endpoint spawn;
        # its callback 0x829F6B8 reads RoutePlay.NpcEncounter at half distance.
        # An initial payload is required even though claiming remains gated.
        f=self.f
        self.assertTrue(self.initial_start.route_play.HasField('npc_encounter'))
        self.assertFalse(self.initial_start.route_play.completed_walk)
        self.assertEqual(self.initial_start.route_play.total_distance_travelled_meters,0)
        self.assertEqual(f.call(1410,p.CancelRouteProto(),p.CancelRouteOutProto).status,1)
        f.fix(0,advance=200000)
        started=f.start();self.assertEqual(started.status,1)
        self.enc=started.route_play.npc_encounter.encounter_id;self.assertTrue(self.enc)
        self.assertEqual(self.send().result,2)
        with f.db.transaction() as db:
            self.assertEqual(db.execute('SELECT deleted FROM social_gifts WHERE id=100').fetchone()[0],0)
            record=db.execute('SELECT * FROM route_npc_encounters WHERE id=?',(self.enc,)).fetchone()
            self.assertIsNone(record['sent_gift']);self.assertIsNone(record['received_gift'])
        paused=f.call(1406,p.ProgressRouteProto(pause=True),p.ProgressRouteOutProto)
        self.assertTrue(paused.route_play.paused)
        self.assertEqual(paused.route_play.npc_encounter.encounter_id,self.enc)
        self.assertEqual(self.send().result,2)
        f.game=Game(f.game.config,Database(f.db.path))
        self.assertEqual(f.progress().route_play.npc_encounter.encounter_id,self.enc)
        resumed=f.call(1406,p.ProgressRouteProto(pause=False),p.ProgressRouteOutProto)
        self.assertFalse(resumed.route_play.paused)
        self.assertEqual(resumed.route_play.npc_encounter.encounter_id,self.enc)
        self.assertEqual(self.send().result,2)

    def test_actual_route_payload_contains_legacy_guard_and_quest_conversation(self):
        from protocol.opaque import fields
        npc=self.initial_start.route_play.npc_encounter
        # Native 0x7E4A79C tests legacy Dialog.Count, then 0x7E4A7D8
        # fills ConfigData.Conversation from NpcDialog instead. Sending only
        # either representation skips the dialogue or opens an empty GUI.
        step=next(value for value in npc.steps if value.step_id==npc.current_step)
        self.assertEqual((len(step.dialog),len(step.npc_dialog)),(1,1))
        self.assertEqual(step.dialog[0].text,step.npc_dialog[0].text)
        self.assertTrue(step.npc_dialog[0].text)
        self.assertEqual((step.dialog[0].character,step.npc_dialog[0].character),(94,16))
        self.assertEqual((step.dialog[0].expression,step.npc_dialog[0].expression),(1,1))
        self.assertEqual((step.event.event,list(step.next_step)),(2,['done']))
        # Inspect the serialized actual RPC response, not a fabricated GUI
        # config, and require both independently audited message wire tags.
        payload=next(value for number,kind,_,value in fields(npc.SerializeToString()) if number==3 and kind==2 and p.NpcEncounterProto.NpcEncounterStep.FromString(value).step_id==npc.current_step)
        nested={number:value for number,kind,_,value in fields(payload) if kind==2 and number in (2,5)}
        self.assertEqual(set(nested),{2,5})
        self.assertEqual(p.ClientDialogueLineProto.FromString(nested[2]).character,94)
        self.assertEqual(p.QuestDialogProto.FromString(nested[5]).character,16)

    def test_recovered_gift_pin_projection_tracks_owned_cards_without_recredit(self):
        from dittobuffer.gameplay import vivillon,world_postcards
        f=self.f;f.game.config['vivillon']={'enabled':True};vivillon.install(f.game)
        anchor=next(value for value in world_postcards.registry()['origins'] if value['region']=='OCEAN')
        with f.db.transaction() as db:
            # Declared synthetic Gift origin in this isolated unit fixture.
            db.execute('UPDATE social_gifts SET lat=?,lng=? WHERE id=200',(anchor['lat'],anchor['lng']))
        sent=self.send();self.assertEqual(sent.result,1)
        original=sent.SerializeToString();gift=sent.retrived_giftbox.gift_box.giftbox_id
        with f.db.transaction() as db:
            pinned=gifts.postcard(f.game,db,f.players[0],p.CreatePostcardProto(giftbox_id=gift).SerializeToString())
            self.assertEqual(pinned.result,1);first=pinned.postcard.postcard_id
            self.assertEqual(pinned.butterfly_collector_updated_region.progress,1)
        f.game=Game(f.game.config,Database(f.db.path))
        def projected():
            npc=f.progress().route_play.npc_encounter
            return next(step.event.cached_gift_exchange_entry.gift_box for step in npc.steps if step.step_id==npc.current_step)
        self.assertEqual(projected().pinned_postcard_id,first)
        with f.db.transaction() as db:
            self.assertEqual(gifts.delete_postcards(f.game,db,f.players[0],p.DeletePostcardsProto(postcard_ids=[first]).SerializeToString()).result,1)
        self.assertFalse(projected().pinned_postcard_id)
        with f.db.transaction() as db:
            repinned=gifts.postcard(f.game,db,f.players[0],p.CreatePostcardProto(giftbox_id=gift).SerializeToString())
            self.assertEqual(repinned.result,1);second=repinned.postcard.postcard_id
            self.assertNotEqual(first,second)
        self.assertEqual(projected().pinned_postcard_id,second)
        self.assertEqual(self.send().SerializeToString(),original)
        with f.db.transaction() as db:
            self.assertEqual(db.execute('SELECT send_response FROM route_npc_encounters WHERE id=?',(self.enc,)).fetchone()[0],original)
            self.assertEqual(db.execute('SELECT progress FROM vivillon_regions WHERE player=? AND region=?',(f.players[0],world_postcards.PATTERNS['OCEAN'])).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vivillon_pin_credits WHERE player=? AND gift=?',(f.players[0],gift)).fetchone()[0],1)

    def test_native_full_bag_preference_keeps_normal_loot_when_space_available(self):
        expected=None
        for allow in (False,True):
            with self.subTest(allow_full_bag_conversion=allow):
                other=MateoTests();other.setUp()
                try:
                    f=other.f;f.game.routes['mateo']['full_bag_stardust']=50
                    self.assertEqual(other.send().result,1)
                    with f.db.transaction() as db:
                        before_dust=f.game.player(db,f.players[0])['stardust']
                        before_balls=db.execute('SELECT count FROM items WHERE player_id=? AND item=1',(f.players[0],)).fetchone()[0]
                    req=p.NpcOpenGiftProto(encounter_id=other.enc,convert_to_stardust=allow)
                    out=f.call(2402,req,p.NpcOpenGiftOutProto);self.assertEqual(out.result,1)
                    if expected is None:expected=out.loot
                    else:self.assertEqual(out.loot,expected)
                    with f.db.transaction() as db:
                        self.assertEqual(f.game.player(db,f.players[0])['stardust'],before_dust+100)
                        self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1',(f.players[0],)).fetchone()[0],before_balls+2)
                    # A retry with a changed device preference retains the
                    # already committed authoritative result and cannot regrant.
                    req.convert_to_stardust=not allow
                    self.assertEqual(f.call(2402,req,p.NpcOpenGiftOutProto),out)
                finally:other.doCleanups()

    def test_full_bag_requires_permission_and_explicit_dust_policy_once(self):
        f=self.f;self.assertEqual(self.send().result,1)
        with f.db.transaction() as db:
            db.execute('UPDATE items SET count=10000 WHERE player_id=? AND item=1',(f.players[0],))
            before_dust=f.game.player(db,f.players[0])['stardust']
            before_items=[tuple(row) for row in db.execute('SELECT item,count FROM items WHERE player_id=? ORDER BY item',(f.players[0],))]
        for allow in (False,True):
            result=f.call(2402,p.NpcOpenGiftProto(encounter_id=self.enc,convert_to_stardust=allow),p.NpcOpenGiftOutProto)
            self.assertEqual(result.result,6)
        with f.db.transaction() as db:
            self.assertEqual(f.game.player(db,f.players[0])['stardust'],before_dust)
            self.assertIsNone(db.execute('SELECT open_response FROM route_npc_encounters WHERE id=?',(self.enc,)).fetchone()[0])
        # Explicit custom test balance, not a claim about official conversion.
        f.game.routes['mateo']['full_bag_stardust']=50
        self.assertEqual(self.open().result,6)
        converted=f.call(2402,p.NpcOpenGiftProto(encounter_id=self.enc,convert_to_stardust=True),p.NpcOpenGiftOutProto)
        self.assertEqual(converted.result,1)
        self.assertEqual([(value.item,value.count,value.stardust) for value in converted.loot.loot_item],[(0,50,True)])
        f.game=Game(f.game.config,Database(f.db.path))
        self.assertEqual(self.open(),converted)
        with f.db.transaction() as db:
            self.assertEqual(f.game.player(db,f.players[0])['stardust'],before_dust+50)
            self.assertEqual([tuple(row) for row in db.execute('SELECT item,count FROM items WHERE player_id=? ORDER BY item',(f.players[0],))],before_items)

    def test_invalid_optional_full_bag_policy_is_rejected(self):
        cfg=self.f.game.routes['mateo']
        for invalid in (True,False,0,-1,10001,50.0,'50',{}):
            cfg['full_bag_stardust']=invalid
            with self.assertRaisesRegex(ValueError,'full-bag'):m.settings(self.f.game)
        cfg['full_bag_stardust']=None
        m.settings(self.f.game)

    def test_completed_first_walk_without_exchange_does_not_project_second_npc(self):
        f=self.f
        self.assertEqual(f.progress(True).status,1)
        with f.db.transaction() as db:
            before=db.execute('SELECT COUNT(*) FROM route_npc_encounters WHERE player=?',(f.players[0],)).fetchone()[0]
            self.assertEqual(db.execute('SELECT deleted FROM social_gifts WHERE id=100').fetchone()[0],0)
        f.fix(0,advance=200000)
        started=f.start();self.assertEqual(started.status,1)
        self.assertFalse(started.route_play.HasField('npc_encounter'))
        for i in range(1,26):f.fix(i)
        self.assertFalse(f.progress().route_play.HasField('npc_encounter'))
        with f.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM route_npc_encounters WHERE player=?',(f.players[0],)).fetchone()[0],before)
            self.assertEqual(db.execute('SELECT deleted FROM social_gifts WHERE id=100').fetchone()[0],0)

if __name__=='__main__':unittest.main()
