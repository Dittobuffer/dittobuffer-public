"""Real service/transaction tests on synthetic parties; not Pixel verification."""
import json,unittest
from pathlib import Path
from dittobuffer.gameplay.game import p
from dittobuffer.gameplay.species import make_pokemon
from dittobuffer.core.management import owned,update_owned

# This branch carries the defeat regression ahead of its optional Rocket
# extension. Keep all three tests discoverable; never fabricate a battle engine
# or silently import another Git branch. A partial installation remains an error.
_root=Path(__file__).resolve().parents[1]
_dependencies=['src/dittobuffer/gameplay/rocket_native.py','src/dittobuffer/gameplay/rocket_battle.py','tests/test_rocket_native.py']
_present=[(_root/path).is_file() for path in _dependencies]
if any(_present) and not all(_present):
    raise ImportError('Incomplete Rocket extension; missing '+', '.join(path for path,exists in zip(_dependencies,_present) if not exists))
if all(_present):
    from tests import test_rocket_native as support

@unittest.skipUnless(all(_present),'Rocket implementation and fixture absent on this branch; native/combat regressions NOT_TESTED')
class RocketDefeatTests(unittest.TestCase):
    def setUp(self):
        self.f=support.RocketTests();self.f.setUp();self.addCleanup(self.f.tearDown)

    def lose(self):
        f=self.f
        with f.db.transaction() as db:
            for ident in f.parties[0]:
                old=owned(db,f.players[0],ident)
                value=make_pokemon(p,f.game.species[old.pokemon_id],old.pokemon_id,1,[0]*3);value.id=ident
                update_owned(f.game,db,f.players[0],value)
        battle=f.start()
        for _ in range(120):
            f.clock+=2000;value=f.action(battle)
            if value.combat.combat_state in (6,7,8):break
        with f.db.transaction() as db:
            row=db.execute('SELECT * FROM rocket_battles WHERE id=?',(battle.combat_id,)).fetchone();state=json.loads(row['state'])
        self.assertEqual(state['status'],'lost');self.assertEqual([v['hp'] for v in state['party']],[0,0,0])
        self.assertEqual(row['finalized'],1);self.assertEqual(value.combat.combat_state,6)
        return battle,state

    def test_legitimate_final_faint_has_native_loss_projection_and_no_reward(self):
        f=self.f
        with f.db.transaction() as db:
            before=dict(f.game.player(db,f.players[0]));db.execute('INSERT INTO items VALUES(?,1502,1)',(f.players[0],));db.execute('INSERT INTO items VALUES(?,1503,1)',(f.players[0],))
        battle,state=self.lose();self.assertEqual(state['shields'],2)
        req=p.UpdateInvasionBattleProto(incident_lookup=f.lookup,complete_battle=True,update_type=2)
        for _ in range(2):self.assertEqual(f.call(1203,req,p.UpdateInvasionBattleOutProto).status,1)
        self.assertNotEqual(f.call(1203,p.UpdateInvasionBattleProto(incident_lookup=f.lookup,complete_battle=True,update_type=1),p.UpdateInvasionBattleOutProto).status,1)
        with f.db.transaction() as db:
            member=db.execute('SELECT * FROM rocket_players WHERE incident_id=? AND player_id=?',(f.lookup.incident_id,f.players[0])).fetchone()
            self.assertEqual(member['completed_ms'],0);self.assertIsNone(member['receipt']);self.assertIsNone(member['spawn_id'])
            self.assertEqual(db.execute('SELECT SUM(count) FROM items WHERE player_id=? AND item IN (1502,1503)',(f.players[0],)).fetchone()[0],2)
            after=f.game.player(db,f.players[0]);self.assertEqual((after['xp'],after['stardust']),(before['xp'],before['stardust']))
            self.assertTrue(all(owned(db,f.players[0],i).stamina==0 for i in f.parties[0]))

    def test_native_revives_allow_fresh_rematch_and_stale_poll_cannot_reinjure(self):
        f=self.f;old,state=self.lose()
        with f.db.transaction() as db:db.execute('INSERT INTO items VALUES(?,202,3)',(f.players[0],))
        for ident in f.parties[0]:
            self.assertEqual(f.call(116,p.UseItemReviveProto(item=202,pokemon_id=ident),p.UseItemReviveOutProto).result,1)
        self.assertEqual(f.action(old).combat.combat_state,6)
        fresh=f.start();self.assertNotEqual(old.combat_id,fresh.combat_id)
        self.assertEqual(fresh.combat_state,2);self.assertEqual(fresh.player.minigame_defense_chances_left,2)
        self.assertGreater(fresh.player.active_pokemon.stamina,0)

    def test_expired_session_stays_terminal_and_does_not_award_rewards(self):
        f=self.f;b=f.start();f.clock=b.combat_end_ms+1000
        self.assertEqual(f.action(b).combat.combat_state,8)
        result=f.call(1203,p.UpdateInvasionBattleProto(incident_lookup=f.lookup,update_type=2),p.UpdateInvasionBattleOutProto)
        self.assertEqual(result.status,1)
        with f.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM rocket_players WHERE completed_ms>0').fetchone()[0],0)

if __name__=='__main__':unittest.main()
