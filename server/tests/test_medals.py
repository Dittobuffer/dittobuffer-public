import unittest
from tests import test_game as base
from dittobuffer.gameplay.game import Game,p
from dittobuffer.core.database import Database


class MedalTests(unittest.TestCase):
    setUp=base.GameTests.setUp
    tearDown=base.GameTests.tearDown
    call=base.GameTests.call
    spawn=base.GameTests.spawn
    encounter=base.GameTests.encounter
    catch=base.GameTests.catch
    inventory=base.GameTests.inventory

    def player(self):
        with self.database.transaction() as db:return db.execute('SELECT player_id FROM sessions WHERE ticket=?',(self.ticket,)).fetchone()[0]

    def test_real_action_crosses_tier_and_duplicate_claims_survive_restart(self):
        player=self.player()
        with self.database.transaction() as db:db.execute('UPDATE players SET captures=29 WHERE id=?',(player,))
        self.assertEqual(list(self.call(129,p.CheckAwardedBadgesProto(),p.CheckAwardedBadgesOutProto).awarded_badges),[])
        spawn=self.encounter();self.assertEqual(self.catch(spawn).status,1);self.assertEqual(self.catch(spawn).status,1)
        awarded=self.call(129,p.CheckAwardedBadgesProto(),p.CheckAwardedBadgesOutProto)
        self.assertIn(3,awarded.awarded_badges)
        self.game=Game(self.config,Database(self.database.path))
        self.assertEqual(list(self.call(129,p.CheckAwardedBadgesProto(),p.CheckAwardedBadgesOutProto).awarded_badges),[])
        first=self.call(450,p.NeutralAvatarBadgeRewardProto(),p.NeutralAvatarBadgeRewardOutProto)
        self.assertEqual(len(first.avatar_badge_display),5)
        self.assertEqual(len(self.call(450,p.NeutralAvatarBadgeRewardProto(),p.NeutralAvatarBadgeRewardOutProto).avatar_badge_display),0)
        with self.database.transaction() as db:
            self.assertEqual(db.execute('SELECT captures FROM players WHERE id=?',(player,)).fetchone()[0],30)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM trainer_events WHERE player_id=? AND kind='catch'",(player,)).fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM trainer_events WHERE player_id=? AND source_key='medal:3:1'",(player,)).fetchone()[0],1)

    def test_profile_uses_source_thresholds_and_transfer_does_not_erase_type_catches(self):
        caught=self.catch(self.encounter()).captured_pokemon_id
        self.call(112,p.ReleasePokemonProto(pokemon_id=caught),p.ReleasePokemonOutProto)
        profile=self.call(121,p.PlayerProfileProto(),p.PlayerProfileOutProto)
        values={v.badge_type:v for v in profile.badges}
        self.assertEqual(values[3].end_value,30)
        self.assertEqual(values[29].current_value,1)
        self.assertEqual(values[21].current_value,1)
        self.assertNotIn(49,values) # Trading is not implemented.
        stats=next(v.player_stats for v in self.inventory() if v.HasField('player_stats'))
        self.assertEqual(stats.num_unique_pokedex_entries,1)
        self.assertEqual(stats.num_pokemon_captured,1)

    def test_gold_platinum_tiers_and_restart_do_not_repeat_awards(self):
        from dittobuffer.gameplay.medals import definitions
        rule=next(v for v in definitions() if v['counter']=='captures');player=self.player()
        for rank,target in enumerate(rule['targets'],1):
            self.config['spawn_generation']=1000+rank
            with self.database.transaction() as db:db.execute('UPDATE players SET captures=? WHERE id=?',(target-1,player))
            self.catch(self.encounter())
            result=self.call(129,p.CheckAwardedBadgesProto(),p.CheckAwardedBadgesOutProto)
            self.assertIn((rule['badge'],rank),list(zip(result.awarded_badges,result.awarded_badge_levels)))
            self.game=Game(self.config,Database(self.database.path))
            self.assertEqual(list(self.call(129,p.CheckAwardedBadgesProto(),p.CheckAwardedBadgesOutProto).awarded_badges),[])
            badge=next(v for v in self.call(121,p.PlayerProfileProto(),p.PlayerProfileOutProto).badges if v.badge_type==rule['badge'])
            self.assertEqual((badge.rank,badge.current_value),(rank,target))

    def test_journal_spin_uses_native_awarded_items_and_retries_do_not_duplicate(self):
        self.spawn();player=self.player()
        with self.database.transaction() as db:
            db.execute("INSERT INTO forts VALUES('test-stop',1,'Test Stop',52.37,4.89)")
            fort=db.execute('SELECT * FROM forts WHERE kind=1 LIMIT 1').fetchone()
        request=p.FortSearchProto(id=fort['id'],player_lat_degrees=fort['lat'],player_lng_degrees=fort['lng'])
        self.assertEqual(self.call(101,request,p.FortSearchOutProto).result,1)
        self.call(101,request,p.FortSearchOutProto)
        self.game=Game(self.config,Database(self.database.path))
        spins=[v.fort_search for v in self.call(801,p.GetActionLogRequest(),p.GetActionLogResponse).log if v.HasField('fort_search')]
        self.assertEqual(len(spins),1)
        self.assertEqual([(v.item,v.count) for v in spins[0].awarded_items],[(1,3)])
        self.assertEqual(len(spins[0].items),0)
