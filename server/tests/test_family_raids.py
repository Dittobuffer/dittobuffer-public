import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from dittobuffer.gameplay.game import Game,ROOT,p,wire,now_ms,RpcError
from dittobuffer.core.database import Database
from dittobuffer.gameplay.species import make_pokemon
from dittobuffer.core.accounts import digest
from dittobuffer.gameplay import raids, raid_transport, cooperative, gym_battle

class FamilyRaidTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds');self.clock=now_ms();self.counter=0
        self.patches=[patch(name,lambda:self.clock) for name in ('dittobuffer.gameplay.game.now_ms','dittobuffer.gameplay.raids.now_ms','dittobuffer.gameplay.raid_transport.now_ms','dittobuffer.gameplay.gym_battle.now_ms','dittobuffer.gameplay.gyms.now_ms','dittobuffer.gameplay.cooperative.now_ms')]
        for v in self.patches:v.start()
        self.cfg=json.loads((ROOT/'config/development.json').read_text(encoding='utf8'));self.cfg.pop('fixture_player',None)
        self.cfg.update(authentication={'mode':'family'},catch_probability=1,raids={'enabled':True,'multiplayer':True,'participation_limit':3,'epoch_ms':self.clock-2000,'egg_seconds':1,'active_seconds':3600,'lobby_seconds':2,'relobby_seconds':1,
            'pools':[{'species':16,'tier':1,'weight':1,'hp':250,'cp':1000,'xp':1000,'dust':500,'items':{'101':2}}]})
        self.db=Database(Path(self.tmp.name)/'family.sqlite3');self.game=Game(self.cfg,self.db);self.players=[];self.tickets=[];self.credentials=[]
        for name in ['alpha','bravo','charlie']:
            self.game.accounts.register(name,'An isolated password 42',name.title(),self.game.accounts.invite(),peer=name)
            cred=self.game.accounts.login(name,'An isolated password 42',peer=name)['token'];self.credentials.append(cred);self.tickets.append(self.game.accounts.exchange(cred))
            with self.db.transaction() as db:self.players.append(db.execute('SELECT player_id FROM family_accounts WHERE username=?',(name,)).fetchone()[0])
        with self.db.transaction() as db:
            db.execute('UPDATE players SET xp=10000');db.execute("INSERT INTO forts VALUES('gym',0,'Shared Raid Garden',52.37,4.89)")
            for index,player in enumerate(self.players):
                db.execute('INSERT INTO items VALUES(?,1402,2)',(player,))
                for j in range(2):
                    ident=1+index*2+j
                    db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',(100+ident,str(ident),25,52.37,4.89,self.clock+86400000))
                    mon=make_pokemon(p,self.game.species[25],25,35,[15]*3);mon.id=ident
                    db.execute('INSERT INTO pokemon VALUES(?,?,?,?,?)',(ident,player,100+ident,25,mon.SerializeToString()))
            self.seed=raids.schedule(self.game,db,db.execute("SELECT * FROM forts WHERE id='gym'").fetchone())['seed']
    def tearDown(self):
        for v in self.patches:v.stop()
        self.tmp.cleanup()
    def call(self,method,req,typ,index=0):
        self.counter+=1;r=wire.RpcRequest(rpc_id=self.counter);r.requests.add(method=method,body=req.SerializeToString())
        return typ.FromString(self.game.rpc(r,self.tickets[index]).payloads[0].payload)
    def join(self,index=0):return self.call(159,p.JoinLobbyProto(gym_id='gym',raid_seed=self.seed,player_lat_degrees=52.37,player_lng_degrees=4.89),p.JoinLobbyOutProto,index)
    def start(self,index=0,ids=None):return self.call(165,p.StartRaidBattleProto(gym_id='gym',raid_seed=self.seed,attacking_pokemon_id=ids or [1+index*2],player_lat_degrees=52.37,player_lng_degrees=4.89),p.StartRaidBattleOutProto,index)
    def headers(self,index=0):
        with self.db.transaction() as db:
            m=raids.member(db,self.seed,self.players[index]);token=db.execute('SELECT token FROM raid_tokens WHERE seed=? AND player_id=?',(self.seed,self.players[index])).fetchone()[0]
            return {'Authorization':'Bearer '+token,'nbl-battle-id':cooperative.native_id(db,m['battle_id']),'nbl-player-id':self.players[index][:20]}
    def poll(self,index=0):return raid_transport.handle(self.game,'/raid/api/get-state',self.headers(index))
    def attack(self,index=0,sequence=None):
        h=self.headers(index);req=p.SendBattleEventProto(battle_id=h['nbl-battle-id'],player_id=self.players[index][:20],sequence_id=sequence or self.clock,battle_event=p.BattleEventProto(type=4,actor_id=self.players[index][:20],attack=p.RaidPbsAttack(attack_type=1)))
        return raid_transport.handle(self.game,'/raid/api/send-player-event',h,req.SerializeToString()),req
    def setup_battle(self,participants=2):
        for i in range(participants):self.assertEqual(self.join(i).result,1)
        self.clock+=2100
        for i in range(participants):self.assertEqual(self.start(i).result,1)
    def boss_hp(self,frame):return frame.battle_state.pokemon[frame.battle_state.actors['raid-boss'].active_pokemon_id].resources[1].quantity
    def test_shared_lobby_countdown_native_id_and_private_parties(self):
        a=self.join();b=self.join(1);self.assertEqual(a.lobby.raid_battle_id,b.lobby.raid_battle_id)
        self.assertEqual(a.lobby.raid_battle_start_ms,b.lobby.raid_battle_start_ms);self.assertEqual(len(b.lobby.players),2)
        self.clock+=2100;a=self.start();b=self.start(1)
        self.assertEqual(a.battle.battle_id,b.battle.battle_id);self.assertEqual(a.battle.battle_end_ms,b.battle.battle_end_ms)
        self.assertEqual(self.start(2,ids=[1]).result,12)
        f=self.poll();self.assertEqual(f.battle_state.active_actor_count,2)
        self.assertEqual(f.battle_state.actors[self.players[0][:20]].active_pokemon_id,1);self.assertEqual(f.battle_state.actors[self.players[1][:20]].active_pokemon_id,3)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM raid_pass_receipts').fetchone()[0],2)
    def test_concurrent_damage_receipts_and_independent_rewards_restart(self):
        with self.db.transaction() as db:
            db.execute('UPDATE trainer_teams SET team=1 WHERE player_id=?',(self.players[0],))
            db.execute('UPDATE trainer_teams SET team=2 WHERE player_id=?',(self.players[1],))
        self.setup_battle();before=self.boss_hp(self.poll())
        with ThreadPoolExecutor(2) as ex:results=list(ex.map(self.attack,[0,1]))
        self.assertTrue(all(r[0].result==1 for r in results));self.clock+=1100
        a=self.poll();b=self.poll(1);self.assertEqual(self.boss_hp(a),self.boss_hp(b));self.assertLess(self.boss_hp(a),before)
        for i,result in enumerate(results):self.assertEqual(raid_transport.handle(self.game,'/raid/api/send-player-event',self.headers(i),result[1].SerializeToString()),result[0])
        for _ in range(50):
            if self.poll().battle_state.state==5:break
            for i in [0,1]:self.attack(i)
            self.clock+=1100
        self.assertEqual(self.poll().battle_state.events[-1].battle_end.result,1)
        rewards=[]
        for i in [0,1]:
            req=p.CompleteRaidBattleProto(gym_id='gym',battle_id=self.headers(i)['nbl-battle-id'])
            result=self.call(3010,req,p.CompleteRaidBattleOutProto,i);self.assertTrue(result.is_victory);rewards.append(result)
            self.assertEqual(self.call(3010,req,p.CompleteRaidBattleOutProto,i),result)
        a,b=[r.battle_results.post_raid_encounter[0] for r in rewards];self.assertNotEqual(a.encounter_id,b.encounter_id)
        shares=[r.battle_results.participation[0] for r in rewards]
        self.assertEqual(shares[0],shares[1],'Team bars must not depend on the viewing trainer')
        with self.db.transaction() as db:
            damage=[db.execute('SELECT damage FROM raid_room_members WHERE player_id=?',(v,)).fetchone()[0] for v in self.players[:2]]
        self.assertTrue(all(v>0 for v in damage))
        self.assertAlmostEqual(shares[0].blue_percentage,damage[0]/sum(damage),places=6)
        self.assertAlmostEqual(shares[0].red_percentage,damage[1]/sum(damage),places=6)
        self.assertEqual(shares[0].yellow_percentage,0)
        with self.assertRaises(RpcError):self.call(102,p.EncounterProto(encounter_id=a.encounter_id,spawnpoint_id=a.spawnpoint_id,player_lat_degrees=52.37,player_lng_degrees=4.89),p.EncounterOutProto,1)
        catch=p.CatchPokemonProto(encounter_id=a.encounter_id,spawn_point_guid=a.spawnpoint_id,pokeball=5,hit_pokemon=True)
        self.assertEqual(self.call(103,catch,p.CatchPokemonOutProto).status,1)
        self.game=Game(self.cfg,Database(self.db.path))
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pokemon WHERE player_id=?',(self.players[0],)).fetchone()[0],3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pokemon WHERE player_id=?',(self.players[1],)).fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM raid_members WHERE reward IS NOT NULL').fetchone()[0],2)
    def test_friendship_threshold_uses_shared_preclaim_bonus_and_once_only_point(self):
        from dittobuffer.gameplay import friends
        self.cfg['social']={'enabled':True,'lucky_rate':0,'lucky_friend_rate':0}
        friends.install(self.game)
        a,b=sorted(self.players[:2])
        with self.db.transaction() as db:db.execute('INSERT INTO social_pairs(a,b,created_ms,points) VALUES(?,?,?,6)',(a,b,self.clock))
        self.test_concurrent_damage_receipts_and_independent_rewards_restart()
        with self.db.transaction() as db:
            self.assertEqual(friends.pair(db,a,b)['points'],7)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM social_history WHERE kind='raid' AND points=1").fetchone()[0],1)
            states=[json.loads(r[0]) for r in db.execute('SELECT state FROM raid_battles')]
            self.assertEqual([s['friendship_reward_bonus'] for s in states],[[2,0],[2,0]])
            self.assertTrue(all(s['peer_friendship_boost']==1.03 for s in states))
    def test_disconnect_and_relobby_preserve_room_clock_hp_and_other_party(self):
        self.setup_battle();self.attack();self.attack(1);self.clock+=1100;before=self.poll(1)
        # Isolated fixture fainting still passes through the ordinary AI engine.
        with self.db.transaction() as db:
            m=raids.member(db,self.seed,self.players[0]);b=raids.current_battle(db,m);s=json.loads(b['state']);s['party'][0]['hp']=1;raids.persist(db,b['id'],s)
        for _ in range(20):
            self.clock+=1000;self.poll(1)
            with self.db.transaction() as db:s=json.loads(raids.current_battle(db,raids.member(db,self.seed,self.players[0]))['state'])
            if s['status']==3:break
        self.assertEqual(s['status'],3);old_end=s['end'];old_id=self.headers()['nbl-battle-id'];old_headers=self.headers();self.join();self.clock+=1100
        self.assertFalse(any(e.type==18 for e in self.poll(1).battle_state.events),'A peer defeat must not terminate the native director')
        resumed=self.start(ids=[2]);self.assertEqual(resumed.result,1);self.assertEqual(resumed.battle.battle_end_ms,old_end);self.assertEqual(resumed.battle.battle_id,old_id)
        with self.assertRaises(RpcError):raid_transport.handle(self.game,'/raid/api/get-state',old_headers)
        self.assertEqual(self.boss_hp(self.poll()),self.boss_hp(self.poll(1)))
        self.assertEqual(self.poll(1).battle_state.state,3)
        with self.db.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM raid_pass_receipts').fetchone()[0],2)
    def test_spectator_and_revoked_session_cannot_combat_or_get_rewards(self):
        self.setup_battle(3);self.attack();self.attack(1)
        self.game.accounts.revoke(self.credentials[2])
        with self.assertRaises(RpcError):self.poll(2)
        for _ in range(40):
            self.clock+=1100
            if self.poll().battle_state.state==5:break
            self.attack();self.attack(1)
        with self.db.transaction() as db:
            b=raids.current_battle(db,raids.member(db,self.seed,self.players[2]));self.assertNotEqual(json.loads(b['state'])['status'],2)
            self.assertIsNone(raids.member(db,self.seed,self.players[2])['reward'])

    def test_failed_room_rematch_fresh_ids_pass_reuse_and_stale_tokens(self):
        self.setup_battle();old=self.headers();self.clock+=181000;self.assertEqual(self.poll().battle_state.events[-1].battle_end.result,2)
        complete=p.CompleteRaidBattleProto(gym_id='gym',battle_id=old['nbl-battle-id'])
        self.assertFalse(self.call(3010,complete,p.CompleteRaidBattleOutProto).is_victory)
        self.assertEqual(self.join().result,1);self.clock+=2100;fresh=self.start(ids=[2]);self.assertEqual(fresh.result,1)
        self.assertNotEqual(fresh.battle.battle_id,old['nbl-battle-id'])
        with self.assertRaises(RpcError):raid_transport.handle(self.game,'/raid/api/get-state',old)
        self.assertEqual(self.boss_hp(self.poll()),250)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM raid_pass_receipts WHERE player_id=?',(self.players[0],)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM raid_members WHERE reward IS NOT NULL').fetchone()[0],0)

    def test_restart_preserves_pending_damage_order_and_event_receipt(self):
        self.setup_battle();result,request=self.attack();self.attack(1)
        self.game=Game(self.cfg,Database(self.db.path));self.clock+=1100
        one=self.poll(1);two=self.poll();self.assertEqual(self.boss_hp(one),self.boss_hp(two))
        self.assertLess(self.boss_hp(one),250)
        replay=raid_transport.handle(self.game,'/raid/api/send-player-event',self.headers(),request.SerializeToString());self.assertEqual(replay,result)
        self.assertEqual(self.boss_hp(self.poll()),self.boss_hp(one))
        serials=[e.serial for e in two.battle_state.events];self.assertEqual(serials,sorted(set(serials)))

    def test_mega_ally_boost_uses_gm_nonstacking_and_excludes_fainted_self(self):
        from dittobuffer.gameplay import mega
        a={'player_id':'a'};b={'player_id':'b'}
        mon=make_pokemon(p,self.game.species[3],3,25,[15]*3);mon.id=10;mon.pokemon_display.current_temp_evolution=1;mon.pokemon_display.temp_evolution_is_locked=True
        other=make_pokemon(p,self.game.species[25],25,25,[15]*3);other.id=11
        sa=dict(status=1,active=0,party=[gym_battle.entry(mon)]);sb=dict(status=1,active=0,party=[gym_battle.entry(other)])
        cooperative.apply_mega_boosts(self.game,[(a,sa),(b,sb)])
        self.assertEqual(sb['peer_mega_boost']['12'],float(mega._SETTINGS.attack_boost_from_mega_same_type))
        self.assertEqual(sb['peer_mega_boost']['13'],float(mega._SETTINGS.attack_boost_from_mega_different_type))
        self.assertEqual(sa['peer_mega_boost']['12'],1)
        sa['party'][0]['hp']=0;cooperative.apply_mega_boosts(self.game,[(a,sa),(b,sb)]);self.assertEqual(sb['peer_mega_boost']['12'],1)

    def test_lobby_capacity_departure_and_rejoin_do_not_overfill_or_stop_peer(self):
        self.game.raids['participation_limit']=2
        self.assertEqual(self.join().result,1);self.assertEqual(self.join(1).result,1);self.assertEqual(self.join(2).result,13)
        self.call(160,p.LeaveLobbyProto(gym_id='gym',raid_seed=self.seed),p.LeaveLobbyOutProto)
        self.assertEqual(self.join(2).result,1);self.assertEqual(self.join().result,13)
        self.clock+=2100;self.assertEqual(self.start(1).result,1);self.assertEqual(self.start(2).result,1)
        old=self.headers(1);self.attack(1);self.clock+=1100;hp=self.boss_hp(self.poll(2))
        self.call(160,p.LeaveLobbyProto(gym_id='gym',raid_seed=self.seed),p.LeaveLobbyOutProto,1)
        self.assertEqual(self.poll(2).battle_state.state,3);self.assertEqual(self.boss_hp(self.poll(2)),hp)
        self.assertEqual(self.join(1).result,1);self.clock+=2100;self.assertEqual(self.start(1,ids=[4]).result,1)
        self.assertNotEqual(self.headers(1)['nbl-battle-id'],old['nbl-battle-id'])
        with self.assertRaises(RpcError):raid_transport.handle(self.game,'/raid/api/get-state',old)

if __name__=='__main__':unittest.main()
