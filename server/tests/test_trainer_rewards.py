import unittest
from tests import test_game as base
from dittobuffer.gameplay.game import Game,p
from dittobuffer.core.database import Database
from dittobuffer.gameplay import trainer_rewards


class TrainerRewardTests(unittest.TestCase):
    setUp=base.GameTests.setUp
    tearDown=base.GameTests.tearDown
    call=base.GameTests.call

    def test_level_grant_restart_and_changed_bundle_cannot_regrant(self):
        self.config['trainer_rewards']={'definitions':{'local-level':{'cosmetics':['n_backpack_movie2020_0']}},'level':{'2':'local-level'}}
        with self.database.transaction() as db:db.execute('UPDATE players SET xp=1000')
        out=self.call(128,p.LevelUpRewardsProto(level=2),p.LevelUpRewardsOutProto)
        self.assertEqual([v.item_template_id for v in out.neutral_avatar_item_templates],['n_backpack_movie2020_0'])
        self.game=Game(self.config,Database(self.database.path))
        self.assertFalse(self.call(128,p.LevelUpRewardsProto(level=2),p.LevelUpRewardsOutProto).neutral_avatar_item_templates)
        self.config['trainer_rewards']['definitions']['local-level']['cosmetics'].append('n_shirt_svdlc1_0')
        with self.database.transaction() as db:
            player=db.execute('SELECT id FROM players').fetchone()[0]
            self.assertEqual(trainer_rewards.claim(self.game,db,player,'local-level','level','2'),[])
            self.assertFalse(db.execute("SELECT 1 FROM cosmetic_ownership WHERE article_id='n_shirt_svdlc1_0'").fetchone())

    def test_unknown_reward_assets_rejected_before_gameplay(self):
        self.config['trainer_rewards']={'definitions':{'bad':{'cosmetics':['not-a-client-article']}}}
        with self.assertRaises(ValueError):Game(self.config,self.database)

    def test_xp_cannot_skip_unimplemented_high_level_challenges(self):
        from dittobuffer.gameplay.progression import bounds,requirements
        self.assertEqual(bounds(self.config,10**12)[0],len(requirements(self.config)))
        with self.assertRaises(ValueError):requirements(dict(level_requirements=[0]+[1]*50))
        with self.database.transaction() as db:db.execute('UPDATE players SET xp=?',(10**12,))
        # An RPC for 51+ cannot claim rewards even with excess XP.
        from dittobuffer.gameplay.game import RpcError
        with self.database.transaction() as db:
            player=db.execute('SELECT id FROM players').fetchone()[0]
            with self.assertRaises(RpcError):self.game.rpc_level_up_rewards(db,player,p.LevelUpRewardsProto(level=51).SerializeToString())
