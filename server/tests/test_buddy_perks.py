import unittest
from tests import test_buddy as base
from dittobuffer.gameplay import buddy, buddy_perks
from dittobuffer.gameplay.game import Game,p
from dittobuffer.core.database import Database


class BuddyPerkTests(unittest.TestCase):
    setUp=base.BuddyTests.setUp
    tearDown=base.BuddyTests.tearDown
    call=base.BuddyTests.call
    spawn=base.BuddyTests.spawn
    encounter=base.BuddyTests.encounter
    catch=base.BuddyTests.catch
    player=base.BuddyTests.player
    select=base.BuddyTests.select

    def prepare(self,points=70):
        self.config['buddy_perks']={'gifts':True,'souvenirs':True}
        ident=self.catch(self.encounter()).captured_pokemon_id;self.select(ident)
        player=self.player()
        with self.database.transaction() as db:
            _,mon,state=buddy.active(db,player);state['points']=points
            buddy.save(self.game,db,player,mon,state)
        return player,ident

    def stats(self):return self.call(1351,p.BuddyStatsProto(),p.BuddyStatsOutProto).observed_data
    def open(self):return self.call(1353,p.OpenBuddyGiftProto(),p.OpenBuddyGiftOutProto)

    def test_native_gift_wire_tags_are_not_exporter_gaps(self):
        out=p.OpenBuddyGiftOutProto(observed_data=p.BuddyObservedData(),shown_hearts=1)
        self.assertEqual(out.SerializeToString(),b'\x22\x00\x28\x01')

    def test_catch_assist_uses_pinned_odds_and_fixture_override_is_isolated(self):
        cfg={'buddy_perks':{'catch_assist':True}}
        values={v.template_id:v for v in buddy.templates(cfg)}
        self.assertNotIn(2,values['BUDDY_LEVEL_1'].buddy_level_settings.unlocked_traits)
        self.assertIn(2,values['BUDDY_LEVEL_2'].buddy_level_settings.unlocked_traits)
        self.assertAlmostEqual(values['BUDDY_ENCOUNTER_CAMEO_SETTINGS'].buddy_encounter_cameo_settings.buddy_wild_encounter_cameo_chance_percent,.2)
        with self.assertRaises(ValueError):buddy_perks.rules({'buddy_perks':{'cameo_test_probability':1}})
        cfg.update(profile='m10-assist',fixture_player='fixture');cfg['buddy_perks']['cameo_test_probability']=1
        self.assertEqual(next(v for v in buddy.templates(cfg) if v.template_id=='BUDDY_ENCOUNTER_CAMEO_SETTINGS').buddy_encounter_cameo_settings.buddy_raid_encounter_cameo_chance_percent,1)

    def test_threshold_gift_freezing_atomic_claim_retry_and_restart(self):
        player,ident=self.prepare(69)
        self.assertFalse(self.stats().HasField('buddy_gift_picked_up'))
        with self.database.transaction() as db:
            _,mon,state=buddy.active(db,player);state['points']=70;buddy.save(self.game,db,player,mon,state)
            before={r['item']:r['count'] for r in db.execute('SELECT * FROM items WHERE player_id=?',(player,))}
        gift=self.stats().buddy_gift_picked_up
        self.config['buddy_perks']['gift_items']={'1':19}
        self.assertEqual(self.stats().buddy_gift_picked_up,gift)
        out=self.open();self.assertEqual((out.result,out.shown_hearts),(2,1))
        self.game=Game(self.config,Database(self.database.path))
        self.assertEqual(self.open().result,4)
        self.assertFalse(self.stats().HasField('buddy_gift_picked_up'))
        with self.database.transaction() as db:
            after={r['item']:r['count'] for r in db.execute('SELECT * FROM items WHERE player_id=?',(player,))}
        self.assertEqual(after[701],before[701]+2);self.assertEqual(after[101],before[101]+1)
        log=self.call(801,p.GetActionLogRequest(),p.GetActionLogResponse)
        self.assertEqual(sum(x.HasField('buddy_consumables') for x in log.log),1)
        self.assertEqual(self.stats().current_points_earned,71)

    def test_full_bag_keeps_pending_gift_and_souvenir_history_survives_restart(self):
        player,ident=self.prepare(150);self.stats()
        capacity=self.config['item_capacity'];self.config['item_capacity']=0
        self.assertEqual(self.open().result,5);self.assertTrue(self.stats().HasField('buddy_gift_picked_up'))
        self.config['item_capacity']=capacity;self.assertEqual(self.open().result,2)
        with self.database.transaction() as db:
            _,mon,state=buddy.active(db,player);state['next_gift_ms']=0;buddy.save(self.game,db,player,mon,state)
        self.assertEqual(self.stats().buddy_gift_picked_up.souvenir.souvenir_type_id,3)
        self.assertEqual(self.open().result,3);self.assertEqual(self.open().result,4)
        self.game=Game(self.config,Database(self.database.path));observed=self.stats()
        self.assertEqual(observed.souvenirs_collected[0].value.souvenir_type_id,3)
        history=self.call(1355,p.GetBuddyHistoryProto(),p.GetBuddyHistoryOutProto)
        self.assertEqual(history.buddy_history[0].souvenirs_collected[0].value.souvenir_type_id,3)

    def test_unavailable_perks_reject_config_and_never_generate_rewards(self):
        player,ident=self.prepare(300);self.config['buddy_perks']={}
        self.assertFalse(self.stats().HasField('buddy_gift_picked_up'));self.assertEqual(self.open().result,4)
        for value in ({'gift_cooldown_seconds':0},{'souvenir_pool':[999]},{'gift_items':{'999':1}},{'gifts':1}):
            with self.subTest(value=value),self.assertRaises(ValueError):buddy_perks.rules({'buddy_perks':value})

    def test_best_buddy_medal_counts_relationship_once_after_final_heart(self):
        from dittobuffer.gameplay import medals
        player,ident=self.prepare(299)
        self.config['buddy_perks']['ribbon']=True
        self.assertEqual(self.call(1354,p.BuddyPettingProto(),p.BuddyPettingOutProto).shown_hearts,1)
        for _ in range(3):
            with self.database.transaction() as db:self.assertEqual(medals.counters(self.game,db,player)['best_buddies'],1)
        badges=self.call(129,p.CheckAwardedBadgesProto(),p.CheckAwardedBadgesOutProto)
        self.assertIn(60,badges.awarded_badges)
        with self.database.transaction() as db:
            self.assertEqual(buddy.active(db,player)[1].pokemon_display.pokemon_badge,1)
        self.game=Game(self.config,Database(self.database.path))
        self.assertNotIn(60,self.call(129,p.CheckAwardedBadgesProto(),p.CheckAwardedBadgesOutProto).awarded_badges)


if __name__=='__main__':unittest.main()
