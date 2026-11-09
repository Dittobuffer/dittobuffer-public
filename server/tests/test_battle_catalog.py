import unittest
from dittobuffer.gameplay.battle_master import MOVES,PINNED,effectiveness,settings,templates
from dittobuffer.gameplay.game import p

class BattleCatalogTests(unittest.TestCase):
    def test_reviewed_regular_catalog_and_explicit_exclusions(self):
        self.assertEqual(len(MOVES),309)
        self.assertEqual({m[0] for m in MOVES.values()},set(range(1,19)))
        self.assertEqual(PINNED['sha256'],'5c947ac64d1de8859bea1b3bf044609d74b6e8429d53f8cb1aa30a72a46dce84')
        self.assertEqual(MOVES[231][1],0) # Splash is non-damaging, not minimum 1.
        self.assertNotIn(242,MOVES) # Transform needs a separate mechanic.
        self.assertNotIn(77,MOVES) # Pinned Thunder Punch window ends before it starts.
        for ident,m in PINNED['moves'].items():
            self.assertIn(int(ident),p.HoloPokemonMove.values())
            self.assertGreater(m['durationMs'],0)
            self.assertGreaterEqual(m['damageWindowStartMs'],0)
            self.assertGreaterEqual(m['damageWindowEndMs'],m['damageWindowStartMs'])
            self.assertGreater(m['energyDelta'],0) if '_FAST' in m['template_id'] else self.assertLessEqual(m['energyDelta'],0)
        for sequence in PINNED['sequences'].values():self.assertIn('sys complete',sequence)

    def test_type_chart_including_dual_weakness_and_immunity(self):
        self.assertEqual(effectiveness(13,[3,1]),1.6) # Electric vs Pidgey
        self.assertEqual(effectiveness(11,[10]),1.6)
        self.assertEqual(effectiveness(11,[12]),.625)
        self.assertAlmostEqual(effectiveness(15,[3,16]),2.56)
        self.assertAlmostEqual(effectiveness(13,[5]),.390625)
        self.assertEqual(effectiveness(1,[8]),.390625)
        self.assertEqual(effectiveness(10,[12,12]),1.6)

    def test_shared_damage_non_damage_and_configured_stab(self):
        from dittobuffer.gameplay.combat import damage_value
        attacker=p.PokemonProto(pokemon_id=1,cp_multiplier=1)
        defender=p.PokemonProto(pokemon_id=2,cp_multiplier=1)
        species={1:{'attack':100,'defense':100,'types':[10]},2:{'attack':100,'defense':100,'types':[12]}}
        self.assertEqual(damage_value(attacker,defender,species,209),10) # Ember: 5 * STAB1.2 * grass1.6, floor + 1.
        self.assertEqual(damage_value(attacker,defender,species,231),0)
        self.assertEqual(damage_value(attacker,defender,species,209,{'combat_balance':{'same_type_attack_bonus_multiplier':2}}),17)
        for bad in ({'maximum_energy':1},{'round_duration_seconds':0},{'dodge_damage_reduction_percent':float('nan')}):
            with self.assertRaises(ValueError):settings({'combat_balance':bad})
        records=templates({'combat_balance':{'round_duration_seconds':120}})
        self.assertEqual(records[0].battle_settings.round_duration_seconds,120)
        for record in records:
            if record.HasField('type_effective'):
                chart=record.type_effective
                self.assertEqual(len(chart.attack_scalar),18)
                for index,value in enumerate(chart.attack_scalar):self.assertAlmostEqual(value,effectiveness(chart.attack_type,[index+1]))
