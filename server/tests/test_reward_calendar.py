"""Calendar/transaction integration tests; physical walking is not simulated as proof."""
from datetime import datetime
from zoneinfo import ZoneInfo
import json, unittest
from tests import test_game as fixture
from dittobuffer.gameplay.game import Game, p
from dittobuffer.gameplay import reward_calendar as rewards

class RewardTests(unittest.TestCase):
    call=fixture.GameTests.call
    spawn=fixture.GameTests.spawn
    encounter=fixture.GameTests.encounter
    inventory=fixture.GameTests.inventory
    tearDown=fixture.GameTests.tearDown

    def setUp(self):
        fixture.GameTests.setUp(self)
        self.config['reward_bonuses']={'enabled':True,'weekly_enabled':True,
            'weekly_rewards':[{'km':5,'items':{'1':20},'dust':100}],
            'weekly_provenance':'Explicit deterministic unit-test rewards'}
        self.game=Game(self.config,self.database)
        self.day('2026-10-24T23:59:00')
        with self.database.transaction() as db:self.player=db.execute('SELECT id FROM players').fetchone()[0]

    def day(self,value):
        self.clock=int(datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('Europe/Amsterdam')).timestamp()*1000)
        self.game.reward_calendar_clock=lambda:self.clock

    def catch(self,quality=1,spin=0,hit=1,miss=False):
        self.game.config['spawn_generation']=self.game.config.get('spawn_generation',1)+1
        spawn=self.encounter()
        req=p.CatchPokemonProto(encounter_id=spawn.encounter_id,spawn_point_guid=spawn.spawnpoint_id,pokeball=1,
            hit_pokemon=not miss,normalized_reticle_size=quality,normalized_hit_position=hit,spin_modifier=spin)
        return spawn,req,self.call(103,req,p.CatchPokemonOutProto)

    def test_catch_seven_days_dst_reset_and_replay(self):
        xp=[]
        for day in range(24,31):
            self.day(f'2026-10-{day:02}T12:00:00')
            _,req,out=self.catch(1.9,1)
            xp.append(sum(out.scores.exp))
            self.assertEqual(self.call(103,req,p.CatchPokemonOutProto),out)
        self.assertEqual(xp,[3670]+[2670]*5+[7170])
        self.day('2026-10-31T12:00:00');self.assertEqual(sum(self.catch()[2].scores.exp),1650)
        self.day('2026-11-02T12:00:00');self.catch()
        with self.database.transaction() as db:
            self.assertEqual(db.execute("SELECT streak FROM reward_streaks WHERE kind='catch'").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reward_days WHERE kind='catch'").fetchone()[0],9)

    def test_first_throw_counts_misses_and_restart(self):
        spawn,req,out=self.catch(miss=True);self.assertEqual(out.status,4)
        self.game=Game(self.config,self.database);self.game.reward_calendar_clock=lambda:self.clock
        req.hit_pokemon=True;out=self.call(103,req,p.CatchPokemonOutProto)
        self.assertNotIn(9,out.scores.activity_type)
        other=self.catch()[2];self.assertIn(9,other.scores.activity_type);self.assertNotIn(14,other.scores.activity_type)

    def test_throw_thresholds_match_served_gm(self):
        self.assertEqual(p.PokemonDisplayProto(weather_boosted_condition=1).SerializeToString(),b'\x28\x01')
        from dittobuffer.core.master import templates
        gm=next(v.encounter_settings for v in templates(self.config) if v.template_id=='ENCOUNTER_SETTINGS')
        for quality,typ in [(gm.nice_throw_threshold+.001,10),(gm.great_throw_threshold+.001,11),(gm.excellent_throw_threshold+.001,12)]:
            out=self.catch(quality,.6)[2];self.assertIn(typ,out.scores.activity_type);self.assertIn(13,out.scores.activity_type)
        self.assertNotIn(12,self.catch(float('nan'))[2].scores.activity_type)

    def test_midnight_spring_fall_and_clock_rollback(self):
        for first,nextday in [('2026-03-28T23:59:59','2026-03-29T00:00:00'),('2026-10-24T23:59:59','2026-10-25T00:00:00')]:
            self.day(first)
            with self.database.transaction() as db:
                db.execute('DELETE FROM reward_streaks');db.execute('DELETE FROM reward_days')
                self.assertEqual(rewards.streak(self.game,db,self.player,'spin',True),1)
            self.day(nextday)
            with self.database.transaction() as db:self.assertEqual(rewards.streak(self.game,db,self.player,'spin',True),2)
            self.day(first)
            with self.database.transaction() as db:self.assertEqual(rewards.streak(self.game,db,self.player,'spin',True),0)

    def test_weekly_distance_separate_from_streaks_and_once_after_restart(self):
        self.day('2026-10-25T12:00:00')
        with self.database.transaction() as db:
            db.execute('UPDATE players SET xp=10000 WHERE id=?',(self.player,))
            rewards.walked(self.game,db,self.player,5000)
            rewards.streak(self.game,db,self.player,'catch',True)
        self.day('2026-10-26T08:59:59')
        self.assertEqual(self.call(980,p.GetFitnessRewardsProto(),p.GetFitnessRewardsOutProto).result,2)
        self.day('2026-10-26T09:00:00')
        out=self.call(980,p.GetFitnessRewardsProto(),p.GetFitnessRewardsOutProto);self.assertEqual(out.result,1)
        self.game=Game(self.config,self.database);self.game.reward_calendar_clock=lambda:self.clock
        self.assertEqual(self.call(980,p.GetFitnessRewardsProto(),p.GetFitnessRewardsOutProto).result,2)
        with self.database.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reward_fitness_claims').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reward_days').fetchone()[0],1)

    def test_species_weather_and_event_rewards(self):
        self.game.reward_bonuses.update(species_rewards={'1':{'candy':5,'dust':200}},events=[dict(start_ms=self.clock-1,end_ms=self.clock+1,xp=2,dust=3,candy=2)])
        with self.database.transaction() as db:
            mon=p.PokemonProto(pokemon_id=1);mon.pokemon_display.weather_boosted_condition=1
            rows=rewards.catch(self.game,db,self.player,p.CatchPokemonProto(pokeball=1),mon,1)
            self.assertEqual(sum(v[1] for v in rows),3300)
            self.assertEqual(sum(v[2] for v in rows),10)
            self.assertEqual(sum(v[3] for v in rows),2550)

    def test_spin_capacity_cooldown_and_bonus_ledger(self):
        self.spawn()
        # No production synthetic stops: this isolated test owns its fixture.
        with self.database.transaction() as db:
            db.execute("INSERT INTO forts VALUES('reward-stop',1,'Reward Fixture',52.08,5.12)")
            fort=db.execute("SELECT * FROM forts WHERE id='reward-stop'").fetchone()
        req=p.FortSearchProto(id=fort['id'],player_lat_degrees=fort['lat'],player_lng_degrees=fort['lng'])
        self.game.config['item_capacity']=1
        self.assertEqual(self.call(101,req,p.FortSearchOutProto).result,4)
        with self.database.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM reward_days').fetchone()[0],0)
        self.game.config['item_capacity']=10000
        out=self.call(101,req,p.FortSearchOutProto)
        self.assertEqual(out.result,1);self.assertEqual(out.xp_awarded,1550);self.assertEqual(out.chain_hack_sequence_number,0)
        quest=next(v.quests for v in self.inventory() if v.HasField('quests'))
        self.assertEqual(next(v.daily_quest.current_streak_count for v in quest.quest if v.quest_type==2),1)
        self.assertEqual(sum(v.item_count for v in out.items),6)
        self.assertEqual(self.call(101,req,p.FortSearchOutProto).result,3)
        with self.database.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM reward_days').fetchone()[0],1)

if __name__=='__main__':unittest.main()
