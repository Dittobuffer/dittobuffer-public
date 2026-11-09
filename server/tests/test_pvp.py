"""Trainer battle transaction tests. These are not Android acceptance tests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dittobuffer.gameplay.game import Game,ROOT,p,wire,now_ms
from dittobuffer.core.database import Database
from dittobuffer.gameplay.species import make_pokemon
from dittobuffer.gameplay import pvp,pvp_engine as e,pvp_rules as r


class PvpTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds')
        self.stamp=now_ms();self.counter=0
        self.patches=[patch(name,lambda:self.stamp) for name in
                      ('dittobuffer.gameplay.pvp.now_ms','dittobuffer.gameplay.pvp_engine.now_ms','dittobuffer.gameplay.pvp_matchmaking.now_ms','dittobuffer.gameplay.pvp_challenges.now_ms','dittobuffer.gameplay.pvp_rewards.now_ms')]
        for value in self.patches:value.start()
        cfg=json.loads((ROOT/'config/family.json').read_text())
        cfg.update(profile='m19-unit',authentication={'mode':'local-development'},
                   world={'enabled':False},weather={'enabled':False},social={'enabled':False},
                   rocket={'enabled':False},max_system={'enabled':False})
        # This profile runs the single-player identity path; cooperative raid
        # membership is validated separately in the family profile tests.
        cfg['raids']={**cfg.get('raids',{}),'participation_limit':1,
                      'multiplayer':False,'development_only':False}
        settings=p.CombatSettingsProto(round_duration_seconds=270,turn_duration_seconds=.5,
            minigame_duration_seconds=7,minigame_submit_score_duration_seconds=1,
            change_pokemon_duration_seconds=10,quick_swap_cooldown_duration_seconds=45,
            same_type_attack_bonus_multiplier=1.2,fast_attack_bonus_multiplier=1.3,
            charge_attack_bonus_multiplier=1.3,max_energy=100,minigame_bonus_base_multiplier=.25,
            minigame_bonus_variable_multiplier=.75,shadow_pokemon_attack_bonus_multiplier=1.2,
            shadow_pokemon_defense_bonus_multiplier=5/6)
        path=Path(self.tmp.name)/'ref.json'
        path.write_text(json.dumps(dict(templates={
            name:p.GameMasterClientTemplateProto(template_id=name,**({'combat_settings':settings} if name=='COMBAT_SETTINGS' else {})).SerializeToString().hex()
            for name in ('COMBAT_SETTINGS','COMBAT_STAT_STAGE_SETTINGS','BATTLE_PARTY_SETTINGS')})))
        cfg['pvp']=dict(enabled=True,reference=str(path),ai_backfill_seconds=15)
        self.game=Game(cfg,Database(Path(self.tmp.name)/'pvp.sqlite3'))
        self.players=[];self.parties=[];self.tickets=[]
        for number in range(3):
            ticket=self.game.authenticate(wire.AuthRequest(auth_id='pvp-unit-'+str(number))).ticket
            self.tickets.append(ticket)
            with self.game.db.transaction() as db:
                player=db.execute('SELECT player_id FROM sessions WHERE ticket=?',(ticket,)).fetchone()[0]
                self.players.append(player);db.execute('UPDATE players SET xp=2000000 WHERE id=?',(player,))
                party=[]
                for index,species in enumerate((3,6,9)):
                    spawn=190000+number*10+index
                    db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',(spawn,str(spawn),species,52.08,5.12,0))
                    mon=make_pokemon(p,self.game.species[species],species,18,[12]*3)
                    mon.id=db.execute('INSERT INTO pokemon(player_id,spawn_id,species,data) VALUES(?,?,?,?)',(player,spawn,species,b'')).lastrowid
                    db.execute('UPDATE pokemon SET data=? WHERE id=?',(mon.SerializeToString(),mon.id))
                    party.append(mon)
                self.parties.append(party)
        self.league='COMBAT_LEAGUE_DEFAULT_GREAT'

    def tearDown(self):
        for value in self.patches:value.stop()
        self.tmp.cleanup()

    def call(self,method,request,cls,side=0):
        self.counter+=1
        req=wire.RpcRequest(rpc_id=self.counter)
        req.requests.add(method=method,body=request.SerializeToString())
        return cls.FromString(self.game.rpc(req,self.tickets[side],run='pvp-unit').payloads[0].payload)

    def state(self,bot=False):
        with self.game.db.transaction() as db:
            state=e.create(self.game,db,[self.players[0],None if bot else self.players[1]],
                self.parties[:2],self.league,'league' if bot else 'friend',
                dict(id='test',name='Libre AI (normal)',difficulty='normal') if bot else None)
            e.join(self.game,state,0);e.join(self.game,state,1)
            e.ready(self.game,state,0);e.ready(self.game,state,1);e.save(db,state)
            return state

    def queue(self,side=0):
        self.call(1308,p.ActivateVsSeekerProto(reward_track=0),p.ActivateVsSeekerOutProto,side)
        return self.call(1300,p.VsSeekerStartMatchmakingProto(combat_league_template_id=self.league,
            attacking_pokemon_id=[v.id for v in self.parties[side]]),p.VsSeekerStartMatchmakingOutProto,side)

    def test_owned_distinct_eligible_party(self):
        with self.game.db.transaction() as db:
            ids=[v.id for v in self.parties[0]]
            self.assertIsNotNone(pvp.party(self.game,db,self.players[0],ids,self.league))
            self.assertIsNone(pvp.party(self.game,db,self.players[1],ids,self.league))
            self.assertIsNone(pvp.party(self.game,db,self.players[0],[ids[0]]*3,self.league))
            self.assertNotIn('COMBAT_LEAGUE_DEFAULT_MASTER',pvp.eligible(self.game,db,self.players[0]))

    def test_queue_matches_independent_authenticated_players(self):
        a=self.queue();self.assertEqual(a.result,2)
        b=self.queue(1);self.assertEqual(b.result,1)
        a=self.call(1302,p.GetMatchmakingStatusProto(queue_id=a.queue_id),p.GetMatchmakingStatusOutProto)
        self.assertEqual(a.challenge.combat_id,b.challenge.combat_id)
        self.assertEqual({a.challenge.challenger.player_id,a.challenge.opponent.player_id},{v[:20] for v in self.players[:2]})

    def test_queue_retry_cancel_and_no_cross_account_access(self):
        a=self.queue();b=self.queue();self.assertEqual(a.queue_id,b.queue_id)
        self.assertEqual(self.call(1302,p.GetMatchmakingStatusProto(queue_id=a.queue_id),p.GetMatchmakingStatusOutProto,1).result,5)
        self.assertEqual(self.call(1301,p.CancelMatchmakingProto(queue_id=a.queue_id),p.CancelMatchmakingOutProto).result,1)
        self.stamp+=20000
        self.assertEqual(self.call(1302,p.GetMatchmakingStatusProto(queue_id=a.queue_id),p.GetMatchmakingStatusOutProto).result,3)

    def test_bot_only_after_delay_and_explicit_label(self):
        a=self.queue()
        self.assertEqual(self.call(1302,p.GetMatchmakingStatusProto(queue_id=a.queue_id),p.GetMatchmakingStatusOutProto).result,2)
        self.stamp+=16000
        b=self.call(1302,p.GetMatchmakingStatusProto(queue_id=a.queue_id),p.GetMatchmakingStatusOutProto)
        self.assertEqual(b.result,1)
        self.assertEqual(len(b.challenge.opponent.player_id),20)
        self.assertIn('Libre AI',b.challenge.opponent.public_profile.name)

    def test_fast_damage_and_energy_at_end_of_move(self):
        state=self.state();self.stamp=state['start'];e.advance(self.game,state)
        move=r.MOVES[self.parties[0][0].move1]
        before=r.active(state,1)['hp']
        self.assertEqual(e.submit(self.game,state,0,p.CombatActionProto(type=1)),1)
        self.assertEqual(r.active(state,0)['energy'],0)
        self.stamp+=500*max(1,move['duration_turns']);e.advance(self.game,state)
        self.assertLess(r.active(state,1)['hp'],before)
        self.assertEqual(r.active(state,0)['energy'],move['energy_delta'])

    def test_simultaneous_fast_ko_is_draw(self):
        state=self.state();self.stamp=state['start'];e.advance(self.game,state)
        for side in (0,1):
            for mon in state['members'][side]['party']:mon['hp']=0
            r.active(state,side)['hp']=1;e.fast(state,side,0)
        due=max(v['turn'] for v in state['pending'])
        for value in state['pending']:value['turn']=due
        self.stamp+=due*500;e.advance(self.game,state)
        self.assertEqual(state['phase'],6);self.assertIsNone(state['winner'])

    def test_shield_is_chosen_at_finish_and_consumed_once(self):
        state=self.state();self.stamp=state['start'];e.advance(self.game,state)
        r.active(state,1)['energy']=100;e.begin_charge(self.game,state,1,2,0)
        before=r.active(state,0)['hp']
        self.assertEqual(e.submit(self.game,state,0,p.CombatActionProto(type=5)),1)
        self.assertEqual(state['members'][0]['shields'],2)
        self.assertEqual(e.submit(self.game,state,0,p.CombatActionProto(type=6,minigame_score=1)),1)
        self.assertEqual(state['members'][0]['shields'],1)
        self.assertEqual(e.submit(self.game,state,0,p.CombatActionProto(type=6,minigame_score=1)),13)
        self.assertEqual(e.submit(self.game,state,1,p.CombatActionProto(type=4,minigame_score=1)),1)
        e.advance(self.game,state)
        self.assertEqual(r.active(state,0)['hp'],before-1)

    def test_switch_cooldown_and_unowned_target(self):
        state=self.state();self.stamp=state['start'];e.advance(self.game,state)
        value=p.CombatActionProto(type=9,target_pokemon_id=self.parties[0][1].id)
        self.assertEqual(e.submit(self.game,state,0,value),1)
        value.target_pokemon_id=self.parties[0][2].id
        self.assertEqual(e.submit(self.game,state,0,value),14)
        self.stamp+=46000;e.advance(self.game,state);value.action_start_turn=92
        value.target_pokemon_id=self.parties[1][1].id
        self.assertEqual(e.submit(self.game,state,0,value),14)

    def test_private_projection_and_nonparticipant_denied(self):
        state=self.state()
        with self.game.db.transaction() as db:
            a=e.projection(self.game,db,state,0);b=e.projection(self.game,db,state,1)
        self.assertEqual(a.player.active_pokemon.pokemon_id,b.opponent.active_pokemon.pokemon_id)
        denied=self.call(1001,p.UpdateCombatProto(combat_id=state['id']),p.UpdateCombatOutProto,2)
        self.assertEqual(denied.result,3)

    def test_duplicate_action_receipt_and_hp_not_injured(self):
        state=self.state();self.stamp=state['start']
        request=p.UpdateCombatProto(combat_id=state['id'],combat_request_counter=1,action=p.CombatActionProto(type=1))
        a=self.call(1001,request,p.UpdateCombatOutProto);b=self.call(1001,request,p.UpdateCombatOutProto)
        self.assertEqual(a.SerializeToString(),b.SerializeToString())
        request.action.type=2
        self.assertEqual(self.call(1001,request,p.UpdateCombatOutProto).result,5)
        self.stamp=state['end']+1
        self.call(1001,p.UpdateCombatProto(combat_id=state['id'],combat_request_counter=2),p.UpdateCombatOutProto)
        with self.game.db.transaction() as db:
            self.assertEqual(db.execute('SELECT data FROM pokemon WHERE id=?',(self.parties[0][0].id,)).fetchone()[0],self.parties[0][0].SerializeToString())

    def test_native_wire_types(self):
        self.assertEqual(p.AcceptCombatChallengeProto.DESCRIPTOR.fields_by_name['attacking_pokemon_id'].number,6)
        self.assertEqual(p.VsSeekerStartMatchmakingProto.DESCRIPTOR.fields_by_name['attacking_pokemon_id'].type,6)
        self.assertEqual(p.OpenCombatChallengeProto.DESCRIPTOR.fields_by_name['attacking_pokemon_id'].type,6)
        self.assertEqual(p.CombatChallengeProto.DESCRIPTOR.fields_by_name['challenger'].number,5)
        self.assertEqual(p.CombatChallengeProto.DESCRIPTOR.fields_by_name['expiration_timestamp_ms'].number,19)
        self.assertEqual(p.CombatNpcTrainerProto.DESCRIPTOR.fields_by_name['avatar'].number,7)
        self.assertEqual(p.CombatNpcTrainerProto.DESCRIPTOR.fields_by_name['available_pokemon'].number,8)
        self.assertEqual(p.CombatNpcTrainerProto.DESCRIPTOR.fields_by_name['backdrop_image_bundle'].number,12)

    def test_status_and_generated_league_cp_conditions(self):
        value=self.call(1304,p.GetVsSeekerStatusProto(),p.GetVsSeekerStatusOutProto)
        self.assertEqual(value.vs_seeker.reward_track,0)
        self.assertEqual(value.vs_seeker.max_battles_in_set,5)
        great=next(t.combat_league for t in pvp.templates(self.game.config) if t.template_id==self.league)
        self.assertEqual(great.pokemon_condition[0].with_pokemon_cp_limit.max_cp,1500)
        from dittobuffer.core.master import version
        self.assertNotEqual(version(self.game.config),version({}), 'returning to a normal profile must invalidate the PvP Game Master cache')

    def completed_set(self):
        from dittobuffer.gameplay.pvp_rewards import settle
        for winner in (1,1,0,1,1):
            state=self.state()
            state['kind']='league'
            e.finish(state,winner,'fainted')
            with self.game.db.transaction() as db:
                e.save(db,state);settle(self.game,db,state);settle(self.game,db,state)

    def test_loss_before_win_does_not_claim_reward(self):
        self.completed_set()
        out=self.call(1304,p.GetVsSeekerStatusProto(),p.GetVsSeekerStatusOutProto)
        wins=[v for v in out.combat_log.current_vs_seeker_set_results if v.battle_result==0]
        self.assertEqual(len(wins),1);self.assertFalse(wins[0].rewards_claimed)
        reward=next(v for v in pvp.templates(self.game.config) if v.template_id=='VS_SEEKER_LOOT_FREE_1')
        self.assertEqual(reward.vs_seeker_loot_proto.reward[0].item.WhichOneof('Type'),'item')
        self.assertEqual(reward.vs_seeker_loot_proto.reward[0].item.count,5)

    def test_reward_set_and_retry_are_atomic(self):
        self.completed_set()
        with self.game.db.transaction() as db:
            balls=db.execute('SELECT count FROM items WHERE player_id=? AND item=1',(self.players[0],)).fetchone()[0]
            dust=self.game.player(db,self.players[0])['stardust']
        blocked=self.call(1303,p.CompleteVsSeekerAndRestartChargingProto(),p.CompleteVsSeekerAndRestartChargingOutProto)
        self.assertEqual(blocked.result,7)
        for expected in (1,4):
            claim=self.call(1306,p.ClaimVsSeekerRewardsProto(win_index=0),p.ClaimVsSeekerRewardsOutProto)
            self.assertEqual(claim.result,expected)
        for expected in (1,3):
            done=self.call(1303,p.CompleteVsSeekerAndRestartChargingProto(),p.CompleteVsSeekerAndRestartChargingOutProto)
            self.assertEqual(done.result,expected)
        with self.game.db.transaction() as db:
            self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1',(self.players[0],)).fetchone()[0],balls+5)
            self.assertEqual(self.game.player(db,self.players[0])['stardust'],dust+500)
            self.assertEqual(pvp.profile(self.game,db,self.players[0])['set_id'],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pvp_results').fetchone()[0],10)

    def test_bot_backfill_can_be_disabled(self):
        self.game.pvp['ai_backfill_enabled']=False
        queued=self.queue();self.stamp+=20000
        value=self.call(1302,p.GetMatchmakingStatusProto(queue_id=queued.queue_id),p.GetMatchmakingStatusOutProto)
        self.assertEqual(value.result,2)

    def test_public_profile_alias_preserves_canonical_identity(self):
        self.friend_fixture()
        value=self.call(990,p.GetCombatPlayerProfileProto(player_id=self.players[1][:20]),p.GetCombatPlayerProfileOutProto)
        self.assertEqual(value.result,1)
        self.assertEqual(value.profile.player_nia_id,self.players[1])
        self.assertEqual(value.profile.player_id,self.players[1][:20])

    def friend_fixture(self):
        from dittobuffer.gameplay import friends
        with self.game.db.transaction() as db:
            db.executescript(friends.SCHEMA)
            a,b=sorted(self.players[:2])
            db.execute('INSERT OR IGNORE INTO social_pairs(a,b,created_ms) VALUES(?,?,?)',(a,b,self.stamp))
            for player in self.players[:2]:
                db.execute("INSERT INTO trainer_settings VALUES(?,'combat',?) ON CONFLICT(player_id,kind) DO UPDATE SET data=excluded.data",(player,b''))

    def invitation(self,side=0):
        generated=self.call(991,p.GenerateCombatChallengeIdProto(),p.GenerateCombatChallengeIdOutProto,side)
        return generated.challenge_id,self.call(993,p.OpenCombatChallengeProto(type=3,challenge_id=generated.challenge_id,
            combat_league_template_id=self.league,opponent_player_id=self.players[1-side][:20],
            attacking_pokemon_id=[v.id for v in self.parties[side]]),p.OpenCombatChallengeOutProto,side)

    def test_friend_preferences_are_projected_and_enforced(self):
        self.friend_fixture()
        with self.game.db.transaction() as db:
            db.execute("DELETE FROM trainer_settings WHERE player_id=? AND kind='combat'",(self.players[1],))
        value=self.call(990,p.GetCombatPlayerProfileProto(player_id=self.players[1][:20]),p.GetCombatPlayerProfileOutProto)
        self.assertTrue(value.profile.combat_player_preferences.friends_combat_opt_out)
        self.assertTrue(all(not v.endswith('_NPC') for v in value.profile.combat_league_template_id))
        self.assertNotEqual(self.invitation()[1].result,1)
        self.assertEqual(self.call(990,p.GetCombatPlayerProfileProto(player_id=self.players[2][:20]),p.GetCombatPlayerProfileOutProto).result,3)

    def test_mutual_invites_and_duplicate_accept_cannot_create_two_battles(self):
        self.friend_fixture();ident,opened=self.invitation();self.assertEqual(opened.result,1)
        self.assertNotEqual(self.invitation(1)[1].result,1)
        accepted=self.call(995,p.AcceptCombatChallengeProto(challenge_id=ident,attacking_pokemon_id=[v.id for v in self.parties[1]]),p.AcceptCombatChallengeOutProto,1)
        duplicate=self.call(995,p.AcceptCombatChallengeProto(challenge_id=ident),p.AcceptCombatChallengeOutProto,1)
        self.assertEqual(accepted.challenge.combat_id,duplicate.challenge.combat_id)
        with self.game.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM pvp_battles').fetchone()[0],1)

    def test_result_rematch_is_stable_owned_and_requires_consent(self):
        self.friend_fixture();state=self.state()
        with self.game.db.transaction() as db:e.finish(state,0,'fainted');e.save(db,state)
        req=p.GetCombatResultsProto(combat_id=state['id'])
        a=self.call(1003,req,p.GetCombatResultsOutProto);b=self.call(1003,req,p.GetCombatResultsOutProto)
        self.assertTrue(a.HasField('combat_rematch'));self.assertTrue(a.HasField('friend_level_up'))
        self.assertTrue(a.combat_rematch.combat_rematch_id)
        self.assertEqual(a.combat_rematch,b.combat_rematch)
        ident=a.combat_rematch.combat_rematch_id
        self.assertEqual(self.call(992,p.CreateCombatChallengeProto(challenge_id=ident),p.CreateCombatChallengeOutProto,2).result,4)
        first=self.call(1000,p.OpenCombatSessionProto(combat_id=ident,combat_league_template_id=self.league,attacking_pokemon_id=[v.id for v in self.parties[0]]),p.OpenCombatSessionOutProto)
        self.assertEqual(first.result,1);self.assertEqual(first.combat.combat_state,1)
        with self.game.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM pvp_battles').fetchone()[0],1)
        other=self.call(1003,req,p.GetCombatResultsOutProto,1)
        self.assertEqual(other.combat_rematch.combat_rematch_id,ident)
        second=self.call(1000,p.OpenCombatSessionProto(combat_id=ident,combat_league_template_id=self.league,attacking_pokemon_id=[v.id for v in self.parties[1]]),p.OpenCombatSessionOutProto,1)
        self.assertEqual(second.result,1);self.assertEqual(second.combat.combat_state,2)
        with self.game.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pvp_battles').fetchone()[0],2)
            fresh=json.loads(e.row(db,ident,self.players[0])['state'])
            self.assertEqual([m['shields'] for m in fresh['members']],[2,2])

    def test_removed_friend_invalidates_pending_invitation(self):
        self.friend_fixture();ident,_=self.invitation()
        with self.game.db.transaction() as db:db.execute('UPDATE social_pairs SET active=0')
        self.assertNotEqual(self.call(995,p.AcceptCombatChallengeProto(challenge_id=ident,attacking_pokemon_id=[v.id for v in self.parties[1]]),p.AcceptCombatChallengeOutProto,1).result,1)
        self.assertEqual(self.call(994,p.GetCombatChallengeProto(challenge_id=ident),p.GetCombatChallengeOutProto).challenge.state,7)

    def test_premium_activation_consumes_one_local_pass_and_locks_track(self):
        self.game.pvp['premium_enabled']=True
        request=p.ActivateVsSeekerProto(reward_track=1)
        self.assertEqual(self.call(1308,request,p.ActivateVsSeekerOutProto).result,2)
        with self.game.db.transaction() as db:
            db.execute('INSERT INTO items VALUES(?,1402,2) ON CONFLICT(player_id,item) DO UPDATE SET count=2',(self.players[0],))
        for _ in range(2):
            value=self.call(1308,request,p.ActivateVsSeekerOutProto)
            self.assertEqual(value.result,1);self.assertEqual(value.vs_seeker.reward_track,1)
        self.assertEqual(self.call(1308,p.ActivateVsSeekerProto(reward_track=0),p.ActivateVsSeekerOutProto).result,4)
        with self.game.db.transaction() as db:self.assertEqual(db.execute('SELECT count FROM items WHERE player_id=? AND item=1402',(self.players[0],)).fetchone()[0],1)

    def test_earned_encounter_uses_canonical_owned_replay_safe_catch(self):
        self.game.pvp['reward_items']=dict(self.game.pvp['reward_items'])
        self.game.pvp['reward_items']['1']=dict(species=1,level=20)
        self.completed_set()
        a=self.call(1307,p.VsSeekerRewardEncounterProto(win_index=0),p.VsSeekerRewardEncounterOutProto)
        b=self.call(1307,p.VsSeekerRewardEncounterProto(win_index=0),p.VsSeekerRewardEncounterOutProto)
        self.assertEqual(a.result,1);self.assertEqual(a.encounter_id,b.encounter_id)
        self.assertEqual(self.call(1307,p.VsSeekerRewardEncounterProto(win_index=0),p.VsSeekerRewardEncounterOutProto,2).result,3)
        from dittobuffer.gameplay.game import RpcError
        request=p.CatchPokemonProto(encounter_id=a.encounter_id,spawn_point_guid='native-reward-sentinel',pokeball=1,hit_pokemon=True)
        with self.assertRaises(RpcError):self.call(103,request,p.CatchPokemonOutProto,2)
        self.game.config['catch_probability']=1
        captured=self.call(103,request,p.CatchPokemonOutProto)
        self.assertEqual(captured.status,1)
        self.assertEqual(captured,self.call(103,request,p.CatchPokemonOutProto))
        self.assertEqual(self.call(1307,p.VsSeekerRewardEncounterProto(win_index=0),p.VsSeekerRewardEncounterOutProto).result,2)
        with self.game.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pokemon WHERE spawn_id=?',(a.encounter_id,)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pvp_claims WHERE slot=0').fetchone()[0],1)
            owned=p.PokemonProto.FromString(db.execute('SELECT data FROM pokemon WHERE spawn_id=?',(a.encounter_id,)).fetchone()[0])
            self.assertEqual(owned.origin_detail.vs_seeker_detail.season,1)
            self.assertEqual(owned.origin_detail.vs_seeker_detail.league,self.league)

    def test_cancel_pending_rematch_invalidates_other_consent(self):
        self.friend_fixture();state=self.state()
        with self.game.db.transaction() as db:e.finish(state,1,'fainted');e.save(db,state)
        result=self.call(1003,p.GetCombatResultsProto(combat_id=state['id']),p.GetCombatResultsOutProto)
        ident=result.combat_rematch.combat_rematch_id
        request=p.OpenCombatSessionProto(combat_id=ident,combat_league_template_id=self.league,attacking_pokemon_id=[v.id for v in self.parties[0]])
        self.assertEqual(self.call(1000,request,p.OpenCombatSessionOutProto).result,1)
        self.assertNotEqual(self.call(1002,p.QuitCombatProto(combat_id=ident),p.QuitCombatOutProto,2).result,1)
        self.assertEqual(self.call(1002,p.QuitCombatProto(combat_id=ident),p.QuitCombatOutProto).result,1)
        request.attacking_pokemon_id[:]=[v.id for v in self.parties[1]]
        self.assertEqual(self.call(1000,request,p.OpenCombatSessionOutProto,1).result,11)
        with self.game.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM pvp_battles').fetchone()[0],1)

    def test_cancel_accepted_but_unstarted_invitation_releases_both_players(self):
        from dittobuffer.gameplay.pvp_challenges import occupied
        self.friend_fixture();ident,_=self.invitation()
        accepted=self.call(995,p.AcceptCombatChallengeProto(challenge_id=ident,attacking_pokemon_id=[v.id for v in self.parties[1]]),p.AcceptCombatChallengeOutProto,1)
        self.assertTrue(accepted.challenge.combat_id)
        self.assertEqual(self.call(997,p.CancelCombatChallengeProto(challenge_id=ident),p.CancelCombatChallengeOutProto).result,1)
        with self.game.db.transaction() as db:
            self.assertFalse(occupied(self.game,db,self.players[0]))
            self.assertFalse(occupied(self.game,db,self.players[1]))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pvp_results').fetchone()[0],0)
        self.assertNotEqual(self.call(995,p.AcceptCombatChallengeProto(challenge_id=ident),p.AcceptCombatChallengeOutProto,1).result,1)

    def test_read_pending_invitation_redelivers_once_per_authenticated_login(self):
        from dittobuffer.core.accounts import principal_session
        from dittobuffer.gameplay.pvp_challenges import login_notifications
        self.friend_fixture();ident,_=self.invitation();note='trainer-challenge:'+ident
        with self.game.db.transaction() as db:
            db.execute('UPDATE gym_notifications SET state=1 WHERE id=?',(note,))
            for session,expected in (('login-a',0),('login-a',1),('login-b',0)):
                marker=principal_session.set(session)
                try:login_notifications(self.game,db,self.players[1])
                finally:principal_session.reset(marker)
                self.assertEqual(db.execute('SELECT state FROM gym_notifications WHERE id=?',(note,)).fetchone()[0],expected)
                db.execute('UPDATE gym_notifications SET state=1 WHERE id=?',(note,))
            db.execute('UPDATE pvp_challenges SET state=4 WHERE id=?',(ident,))
            marker=principal_session.set('login-selection')
            try:login_notifications(self.game,db,self.players[1])
            finally:principal_session.reset(marker)
            self.assertEqual(db.execute('SELECT state FROM pvp_challenges WHERE id=?',(ident,)).fetchone()[0],2)
            db.execute('UPDATE gym_notifications SET state=1 WHERE id=?',(note,))
            db.execute('UPDATE pvp_challenges SET state=5 WHERE id=?',(ident,))
            marker=principal_session.set('login-c')
            try:login_notifications(self.game,db,self.players[1])
            finally:principal_session.reset(marker)
            self.assertEqual(db.execute('SELECT state FROM gym_notifications WHERE id=?',(note,)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pvp_battles').fetchone()[0],0)

    def test_season_reward_only_after_end_and_exactly_once(self):
        self.completed_set();self.game.pvp['season_stardust']=321
        method=next(k for k,v in self.game.methods.items() if v=='COMPLETE_COMBAT_COMPETITIVE_SEASON_ACTION')
        request=p.CompleteCompetitiveSeasonProto()
        self.assertEqual(self.call(method,request,p.CompleteCompetitiveSeasonOutProto).result,2)
        self.game.pvp['season']=2
        with self.game.db.transaction() as db:before=self.game.player(db,self.players[0])['stardust']
        self.assertEqual(self.call(method,request,p.CompleteCompetitiveSeasonOutProto).result,1)
        self.assertEqual(self.call(method,request,p.CompleteCompetitiveSeasonOutProto).result,3)
        with self.game.db.transaction() as db:
            self.assertEqual(self.game.player(db,self.players[0])['stardust'],before+321)
            self.assertEqual(pvp.profile(self.game,db,self.players[0])['battles'],0)

    def test_readiness_polls_do_not_reset_action_replay_protection(self):
        state=self.state();self.stamp=state['start']
        opening=p.OpenCombatSessionProto(combat_id=state['id'],lobby_join_time_ms=self.stamp,
            attacking_pokemon_id=[v.id for v in self.parties[0]])
        self.assertEqual(self.call(1000,opening,p.OpenCombatSessionOutProto).result,1)
        action=p.UpdateCombatProto(combat_id=state['id'],combat_request_counter=8,action=p.CombatActionProto(type=1))
        original=self.call(1001,action,p.UpdateCombatOutProto)
        self.call(1000,opening,p.OpenCombatSessionOutProto)
        self.assertEqual(original,self.call(1001,action,p.UpdateCombatOutProto))
        action.combat_request_counter=7
        self.assertEqual(self.call(1001,action,p.UpdateCombatOutProto).result,5)
        opening.lobby_join_time_ms-=1
        self.assertEqual(self.call(1000,opening,p.OpenCombatSessionOutProto).result,11)

    def test_queued_old_season_expires_and_late_battle_keeps_original_season(self):
        queued=self.queue();self.game.pvp['season']=2
        self.assertEqual(self.call(1302,p.GetMatchmakingStatusProto(queue_id=queued.queue_id),p.GetMatchmakingStatusOutProto).result,3)
        state=self.state(bot=True);self.game.pvp['season']=3
        from dittobuffer.gameplay.pvp_rewards import settle
        with self.game.db.transaction() as db:
            e.finish(state,0,'fainted');e.save(db,state);settle(self.game,db,state)
            self.assertEqual(pvp.profile(self.game,db,self.players[0],2)['battles'],1)
            self.assertEqual(pvp.profile(self.game,db,self.players[0],3)['battles'],0)

    def test_additional_rank_requirements_start_at_attained_rank(self):
        self.game.pvp['rank_requirements']='additional'
        self.game.pvp['ranks']=[dict(battles=0,wins=0),dict(battles=1,wins=0),dict(battles=1,wins=1)]
        from dittobuffer.gameplay.pvp_rewards import settle
        for winner,expected in [(0,2),(1,2),(0,3)]:
            state=self.state(bot=True)
            with self.game.db.transaction() as db:
                e.finish(state,winner,'fainted');e.save(db,state);settle(self.game,db,state)
                self.assertEqual(pvp.profile(self.game,db,self.players[0])['rank'],expected)

    def test_npc_expired_or_quit_reward_is_not_granted(self):
        state=self.state(bot=True);state['kind']='npc';state['bot']['id']='TRAINER_BLANCHE_GREAT'
        with self.game.db.transaction() as db:e.finish(state,0,'fainted');e.save(db,state)
        self.stamp+=601000
        req=p.GetNpcCombatRewardsProto(combat_id=state['id'],combat_npc_trainer_template_id=state['bot']['id'],attacking_pokemon_id=[v.id for v in self.parties[0]])
        self.assertEqual(self.call(1005,req,p.GetNpcCombatRewardsOutProto).result,3)

    def test_all_ai_difficulties_follow_energy_shield_and_turn_limits(self):
        for difficulty in ('beginner','normal','advanced','expert'):
            state=self.state(bot=True);state['bot']['difficulty']=difficulty
            self.stamp=state['start'];e.advance(self.game,state)
            for _ in range(25):self.stamp+=500;e.advance(self.game,state)
            self.assertTrue(all(0<=m['shields']<=2 for m in state['members']))
            self.assertTrue(all(0<=v['energy']<=100 and 0<=v['hp']<=r.source(v).max_stamina for m in state['members'] for v in m['party']))

    def test_season_rollover_preserves_lifetime(self):
        self.completed_set();self.game.pvp['season']=2
        value=self.call(1304,p.GetVsSeekerStatusProto(),p.GetVsSeekerStatusOutProto)
        self.assertEqual(value.combat_log.current_season_results.total_battles,0)
        self.assertEqual(value.combat_log.lifetime_results.total_battles,5)
        self.assertEqual(value.combat_log.previous_season_results.total_wins,1)

    def test_lobby_wait_and_expiry_do_not_consume_set(self):
        from dittobuffer.gameplay.pvp_rewards import settle
        with self.game.db.transaction() as db:
            state=e.create(self.game,db,self.players[:2],self.parties[:2],self.league,'league')
            e.join(self.game,state,0)
            self.stamp+=121000;e.advance(self.game,state);e.save(db,state);settle(self.game,db,state)
            self.assertEqual(state.get('reason'),'lobby_timeout')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pvp_results').fetchone()[0],0)

    def test_charged_priority_uses_attack_not_arrival_order(self):
        for arrival in ((0,1),(1,0)):
            state=self.state();self.stamp=state['start'];e.advance(self.game,state)
            # Use naturally different owned species with verified source stats.
            state['members'][1]['active']=1
            for side in (0,1):r.active(state,side)['energy']=100
            expected=max((0,1),key=lambda side:r.attack_stat(self.game,r.active(state,side)))
            for side in arrival:
                self.assertEqual(e.submit(self.game,state,side,p.CombatActionProto(type=2)),1)
            self.assertIsNone(state['charge'])
            self.stamp+=500;e.advance(self.game,state)
            self.assertEqual(state['charge']['side'],expected)
            self.assertEqual(len(state['charge_intents']),1)

    def test_timer_ends_even_while_minigame_score_is_missing(self):
        state=self.state();self.stamp=state['end']-100;e.advance(self.game,state)
        r.active(state,0)['energy']=100;e.begin_charge(self.game,state,0,2,state['clock'])
        self.stamp=state['end']+1;e.advance(self.game,state)
        self.assertEqual(state['phase'],6);self.assertEqual(state['reason'],'timer')
        self.assertIsNone(state['winner'])

    def test_management_cannot_release_active_party(self):
        state=self.state()
        request=p.ReleasePokemonProto(pokemon_id=self.parties[0][0].id)
        out=self.call(112,request,p.ReleasePokemonOutProto)
        self.assertEqual(out.status,2)
        with self.game.db.transaction() as db:
            self.assertIsNotNone(db.execute('SELECT id FROM pokemon WHERE id=?',(self.parties[0][0].id,)).fetchone())

    def test_expired_session_is_settled_and_cannot_reopen(self):
        state=self.state();state['kind']='league'
        with self.game.db.transaction() as db:e.save(db,state)
        self.stamp=state['end']+1
        request=p.OpenCombatSessionProto(combat_id=state['id'],attacking_pokemon_id=[v.id for v in self.parties[0]])
        for _ in range(2):self.assertEqual(self.call(1000,request,p.OpenCombatSessionOutProto).result,2)
        with self.game.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pvp_results').fetchone()[0],2)

    def test_league_bot_has_native_avatar_without_npc_transport(self):
        state=self.state(bot=True)
        with self.game.db.transaction() as db:value=e.projection(self.game,db,state,0)
        self.assertEqual(value.opponent.combat_npc_personality_id,'')
        self.assertTrue(value.opponent.public_profile.HasField('neutral_avatar'))
        self.assertEqual(len(value.opponent.public_profile.player_id),20)

    def test_friend_challenge_consent_ownership_and_expiration(self):
        from dittobuffer.gameplay import friends
        with self.game.db.transaction() as db:
            db.executescript(friends.SCHEMA)
            a,b=sorted(self.players[:2]);db.execute('INSERT INTO social_pairs(a,b,created_ms) VALUES(?,?,?)',(a,b,self.stamp))
            for player in self.players[:2]:db.execute("INSERT INTO trainer_settings VALUES(?,'combat',?)",(player,p.CombatPlayerPreferencesProto().SerializeToString()))
        method=next(k for k,v in self.game.methods.items() if v=='GET_ELIGIBLE_COMBAT_LEAGUES')
        eligible=self.call(method,p.GetEligibleCombatLeaguesProto(player_ids=[self.players[1],self.players[2]]),p.GetEligibleCombatLeaguesOutProto)
        self.assertEqual([v.player_id for v in eligible.other_players_eligible_leagues],[self.players[1][:20]])
        self.assertEqual(list(eligible.skipped_player_ids),[self.players[2]])
        generated=self.call(991,p.GenerateCombatChallengeIdProto(),p.GenerateCombatChallengeIdOutProto)
        opened=self.call(993,p.OpenCombatChallengeProto(type=3,challenge_id=generated.challenge_id,
            combat_league_template_id=self.league,opponent_player_id=self.players[1][:20],
            attacking_pokemon_id=[v.id for v in self.parties[0]]),p.OpenCombatChallengeOutProto)
        self.assertEqual(opened.result,1)
        self.assertEqual(opened.challenge.challenger.player_id,self.players[0][:20])
        denied=self.call(995,p.AcceptCombatChallengeProto(challenge_id=generated.challenge_id),p.AcceptCombatChallengeOutProto,2)
        self.assertEqual(denied.result,3)
        accepted=self.call(995,p.AcceptCombatChallengeProto(challenge_id=generated.challenge_id,
            attacking_pokemon_id=[v.id for v in self.parties[1]]),p.AcceptCombatChallengeOutProto,1)
        self.assertEqual(accepted.result,1);self.assertTrue(accepted.challenge.combat_id)
        again=self.call(995,p.AcceptCombatChallengeProto(challenge_id=generated.challenge_id),p.AcceptCombatChallengeOutProto,1)
        self.assertEqual(again.challenge.combat_id,accepted.challenge.combat_id)
        expired=self.call(991,p.GenerateCombatChallengeIdProto(),p.GenerateCombatChallengeIdOutProto)
        self.stamp+=301000
        invalid=self.call(993,p.OpenCombatChallengeProto(type=3,challenge_id=expired.challenge_id),p.OpenCombatChallengeOutProto)
        self.assertEqual(invalid.result,2)

    def test_four_ai_policies_use_authoritative_fast_and_charged_actions(self):
        for difficulty in ('beginner','normal','advanced','expert'):
            state=self.state(bot=True);state['bot']['difficulty']=difficulty
            self.stamp=state['start'];e.advance(self.game,state)
            self.assertTrue(any(v['side']==1 for v in state['pending']) or
                            any(v['type']=='switch' and v['side']==1 for v in state['history']))
            self.stamp+=3000;e.advance(self.game,state)
            self.assertGreater(r.active(state,1)['energy'],0)
            r.active(state,1)['energy']=100
            for _ in range(40):
                self.stamp+=500;e.advance(self.game,state)
                if state['charge']:break
            self.assertIsNotNone(state['charge'],difficulty)
            self.assertEqual(state['charge']['side'],1)
            self.assertEqual(state['charge']['score'],self.game.pvp['ai'][difficulty]['score'])

    def test_arena_loading_cannot_spend_clock_or_apply_ai_damage(self):
        with self.game.db.transaction() as db:
            state=e.create(self.game,db,[self.players[0],None],self.parties[:2],self.league,'league',dict(id='loading',name='Libre AI',difficulty='expert'))
            e.join(self.game,state,0)
            self.stamp+=20000;e.advance(self.game,state)
            self.assertEqual(r.active(state,0)['hp'],self.parties[0][0].max_stamina)
            self.assertEqual(state['phase'],2);self.assertFalse(state['started'])
            e.ready(self.game,state,0)
            self.assertEqual(state['start'],self.stamp+3000)
            self.assertEqual(state['end']-state['start'],270000)

    def test_classic_npc_claim_without_id_uses_owned_authority_once(self):
        with self.game.db.transaction() as db:
            state=e.create(self.game,db,[self.players[0],None],self.parties[:2],self.league,'npc',dict(id='practice-test',name='Libre practice',difficulty='normal'))
            e.ready(self.game,state,0);e.finish(state,1,'fainted');e.save(db,state)
            dust=self.game.player(db,self.players[0])['stardust']
        request=p.GetNpcCombatRewardsProto(combat_npc_trainer_template_id='practice-test',finish_state=0,attacking_pokemon_id=[v.id for v in self.parties[0]])
        for _ in range(2):self.assertEqual(self.call(1005,request,p.GetNpcCombatRewardsOutProto).result,1)
        self.assertEqual(self.call(1005,request,p.GetNpcCombatRewardsOutProto,1).result,3)
        with self.game.db.transaction() as db:
            self.assertEqual(self.game.player(db,self.players[0])['stardust'],dust+300)
            self.assertEqual(db.execute('SELECT finish FROM pvp_results WHERE battle_id=?',(state['id'],)).fetchone()[0],1)
