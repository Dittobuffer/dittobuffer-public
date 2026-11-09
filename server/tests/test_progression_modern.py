import unittest
from itertools import accumulate
from tests import test_game as base
from dittobuffer.gameplay.game import Game,p,RpcError
from dittobuffer.core.database import Database
from dittobuffer.gameplay.progression import requirements,bounds,cap,pinned


class ModernProgressionTests(unittest.TestCase):
    setUp=base.GameTests.setUp
    tearDown=base.GameTests.tearDown
    call=base.GameTests.call
    spawn=base.GameTests.spawn
    encounter=base.GameTests.encounter
    catch=base.GameTests.catch
    inventory=base.GameTests.inventory

    def test_opt_in_pinned_schedule_preserves_local_and_does_not_bypass_challenge_cap(self):
        local=list(requirements(self.config));self.config['progression_schedule']='pinned-modern'
        self.assertEqual(list(accumulate(requirements(self.config))),pinned()['requiredExperience'])
        self.assertEqual((len(requirements(self.config)),cap(self.config)),(80,70))
        self.assertEqual(bounds(self.config,10**12)[0],70)
        self.game=Game(self.config,Database(self.database.path))
        with self.database.transaction() as db:
            player=db.execute('SELECT id FROM players').fetchone()[0]
            db.execute('UPDATE players SET xp=?',(10**12,))
            with self.assertRaises(RpcError):self.game.rpc_level_up_rewards(db,player,p.LevelUpRewardsProto(level=71).SerializeToString())
        self.config.pop('progression_schedule');self.assertEqual(requirements(self.config),local)

    def test_real_final_catch_level_41_item_claim_restart_and_retry(self):
        self.config['progression_schedule']='pinned-modern';self.config['level_up_rewards']['41']={'1':22,'101':2}
        self.game=Game(self.config,Database(self.database.path));target=pinned()['requiredExperience'][40]
        with self.database.transaction() as db:
            player=db.execute('SELECT id FROM players').fetchone()[0]
            db.execute('UPDATE players SET xp=?',(target-50,))
            db.executemany('INSERT INTO level_reward_claims VALUES(?,?)',[(player,v) for v in range(2,41)])
        self.catch(self.encounter())
        stats=next(v.player_stats for v in self.inventory() if v.HasField('player_stats'))
        self.assertEqual((stats.level,stats.level_cap,stats.prev_level_exp),(41,70,target))
        result=self.call(128,p.LevelUpRewardsProto(level=41),p.LevelUpRewardsOutProto)
        self.assertEqual({v.item:v.item_count for v in result.items},{1:22,101:2})
        self.game=Game(self.config,Database(self.database.path))
        self.assertEqual(self.call(128,p.LevelUpRewardsProto(level=41),p.LevelUpRewardsOutProto).result,2)


if __name__=='__main__':unittest.main()
