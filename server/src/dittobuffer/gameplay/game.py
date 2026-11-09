"""Authoritative transactions. Wire messages always come from the supplied schema."""
import hashlib
import gzip
import json
import math
import re
import random
from pathlib import Path
import secrets
import sys
import time

from s2sphere import CellId, LatLng
from dittobuffer.core.database import Database
from protocol.opaque import merge

from dittobuffer.paths import ROOT
import game_pb2 as p
import devrpc_pb2 as wire

def now_ms():
    return time.time_ns() // 1_000_000

def distance(a, b, c, d):
    lat = math.radians(c - a)
    lng = math.radians(d - b)
    x = math.sin(lat/2)**2 + math.cos(math.radians(a))*math.cos(math.radians(c))*math.sin(lng/2)**2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(x)))

def foreign_max_reward(db, player, encounter_id):
    # Max tables are optional, but disabling the feature does not make existing
    # private rewards public. Keep both the entry and catch paths authoritative.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='max_members'").fetchone():
        return False
    return bool(db.execute('SELECT 1 FROM max_members WHERE spawn_id=? AND player_id!=?',
                           (encounter_id, player)).fetchone())

class RpcError(Exception):
    pass

class Game:
    def __init__(self, config=None, database=None):
        self.config = config or json.loads((ROOT / 'config' / 'family.json').read_text())
        dex_xp=self.config.get('pokedex_entry_xp',1000)
        if type(dex_xp) is not int or not 0<=dex_xp<=100000:raise ValueError('Invalid first Pokedex registration XP')
        self.db = database or Database(ROOT / self.config['database'])
        if self.config.get('world',{}).get('json'):
            from dittobuffer.world.world_json import JsonWorld
            self.world=JsonWorld(self.config.get('world'))
        else:
            from dittobuffer.world.world_store import WorldStore
            self.world=WorldStore(self.config.get('world'))
        self.weather=None
        if self.world.path and self.config.get('weather',{}).get('enabled',True):
            from dittobuffer.world.world_weather import WeatherService
            self.weather=WeatherService(self.config.get('weather'),fixture_context=self.config)
        from dittobuffer.gameplay.progression import requirements,cap
        requirements(self.config)
        from dittobuffer.gameplay.species import catalog,form_catalog,REFERENCE
        mode=self.config.get('pokemon_stats_mode','legacy-development')
        if mode not in ('legacy-development','reference'):raise ValueError('Unknown Pokemon stats mode')
        self.forms={key:dict(rules) for key,rules in form_catalog(ROOT/'data/static/gmts.binpb.gz').items()}
        if mode=='reference':
            for rules in self.forms.values():rules['cp_multipliers']=REFERENCE['cp_multipliers'][:50]
        self.species={ident:self.forms[ident,rules['form']] for ident,rules in catalog(ROOT/'data/static/gmts.binpb.gz').items()}
        self.pokemon_genders=REFERENCE['genders']
        from dittobuffer.gameplay.pokemon_sizes import validate as validate_sizes
        validate_sizes(self.config)
        if self.config.get('pokemon_sizes',{}).get('enabled'):
            for (species,form),rules in self.forms.items():
                entry=REFERENCE.get('sizes',{}).get(f'{species}:{form}')
                if entry:rules['size_settings']=entry['bounds']
        from dittobuffer.gameplay.costumes import registry as costume_registry
        self.costumes=costume_registry(ROOT,self.config)
        # This default is the existing ordinary pool, not every imported form.
        self.shiny_eligible={(s,self.species[s]['form']) for s in (1,4,7,10,16,25)}
        for value in self.config.get('shiny_eligible_forms',[]):
            key=value['species'],value.get('form',self.species[value['species']]['form'])
            if key not in self.forms:raise ValueError('Unknown shiny eligibility form')
            self.shiny_eligible.add(key)
        from dittobuffer.gameplay.eggs import validate
        validate(self)
        from dittobuffer.gameplay.research import validate as validate_research
        validate_research(self)
        from dittobuffer.gameplay.gyms import rules
        rules(self)
        from dittobuffer.gameplay.battle_master import settings
        settings(self.config)
        from dittobuffer.gameplay.shop import catalog
        catalog(self.config)
        from dittobuffer.gameplay.trainer_rewards import validate as validate_trainer_rewards
        validate_trainer_rewards(self.config)
        from dittobuffer.gameplay.buddy_perks import rules as buddy_perk_rules
        buddy_perk_rules(self.config)
        from dittobuffer.gameplay.mega import validate as validate_mega
        validate_mega(self)
        from dittobuffer.gameplay.raids import validate as validate_raids
        validate_raids(self)
        from dittobuffer.gameplay.backgrounds import definitions as background_definitions
        background_ids={c['id'] for c in background_definitions(self.config)}
        self.spawn_pools=self.config.get('spawn_pools') or [{'species':[{'id':s,'weight':1} for s in self.config['spawn_species']]}]
        for pool in self.spawn_pools:
            entries=pool['species']
            if not entries or any(e['id'] not in self.species or not math.isfinite(e['weight']) or e['weight']<=0 for e in entries):
                raise ValueError('Spawn pools require supported species and positive weights')
            for entry in entries:
                from dittobuffer.gameplay.species import pool_rules
                pool_rules(self,entry)
                if (entry['id'],entry.get('form',self.species[entry['id']]['form'])) not in self.forms:
                    raise ValueError('Unknown spawn species/form')
                if entry.get('gender',1) not in (1,2,3):raise ValueError('Unknown spawn gender')
                if entry.get('background',0) and entry['background'] not in background_ids:raise ValueError('Unknown collectible background')
            if not 0<=pool.get('shiny_rate',self.config.get('shiny_rate',0))<=1:raise ValueError('Invalid pool shiny rate')
        low,high=self.config.get('spawn_level_range',[5,10])
        if not all(isinstance(v,int) for v in (low,high)) or not 1<=low<=high<=40:raise ValueError('Invalid spawn level range')
        low,high=self.config.get('spawn_iv_range',[0,15])
        if not all(isinstance(v,int) for v in (low,high)) or not 0<=low<=high<=15:raise ValueError('Invalid spawn IV range')
        count=self.config.get('spawn_points_per_tile',1)
        if not isinstance(count,int) or not 1<=count<=30:raise ValueError('Spawn point count must be 1-30')
        ring=self.config.get('spawn_neighbor_tiles',0)
        if type(ring) is not int or not 0<=ring<=1:raise ValueError('Spawn neighbor ring must be 0 or 1')
        if type(self.config.get('spawn_generation',1)) is not int:raise ValueError('Spawn generation must be an integer')
        if not isinstance(self.config['spawn_duration_seconds'],int) or self.config['spawn_duration_seconds']<=0:
            raise ValueError('Spawn duration must be a positive integer')
        if not 0<=self.config.get('shiny_rate',0)<=1:raise ValueError('Invalid shiny rate')
        self.spawn_seconds=self.config['spawn_duration_seconds']
        self.spawn_points_per_tile=count
        self.spawn_level_range=list(self.config.get('spawn_level_range',[5,10]))
        self.spawn_iv_range=list(self.config.get('spawn_iv_range',[0,15]))
        self.spawn_shiny_rate=self.config.get('shiny_rate',0)
        self.random_spawns={'enabled':False}
        from dittobuffer.gameplay.random_spawns import install as install_random_spawns
        install_random_spawns(self)
        from dittobuffer.gameplay.liveops import install as install_liveops
        install_liveops(self)
        for name in ('power_up_candy_cost','power_up_stardust_cost','evolution_xp'):
            value=self.config.get(name,{'power_up_candy_cost':1,'power_up_stardust_cost':100,'evolution_xp':500}[name])
            if not isinstance(value,int) or value<0:raise ValueError('Invalid resource rule: '+name)
        for level,items in self.config.get('level_up_rewards',{}).items():
            if not 2<=int(level)<=cap(self.config) or any(int(item) not in p.Item.values() or not isinstance(count,int) or count<=0 for item,count in items.items()):
                raise ValueError('Invalid level rewards')
        self.methods = {v.number: v.name.removeprefix('Method_') for v in p.DESCRIPTOR.enum_types_by_name['Method'].values}
        for enum in ('PlatformClientAction','GameAccountRegistryActions','GameAnticheatAction','GameBackgroundModeAction','GameFitnessAction','GameIapAction','PlayerSubmissionAction','GameAdventureSyncAction'):
            for value in p.DESCRIPTOR.enum_types_by_name[enum].values:
                if value.number:
                    part=value.name.split('_',1)[1]
                    self.methods[value.number]=part if part.isupper() else re.sub(r'(?<!^)(?=[A-Z])','_',part).upper()
        self.accounts=None
        from dittobuffer.gameplay.routes import install as install_routes
        install_routes(self)
        from dittobuffer.gameplay.friends import install as install_social
        install_social(self)
        from dittobuffer.gameplay.rocket_native import install as install_native_rocket
        install_native_rocket(self)
        from dittobuffer.gameplay.vivillon import install as install_vivillon
        install_vivillon(self)
        from dittobuffer.gameplay.max_system import install as install_max
        install_max(self)
        mode=self.config.get('authentication',{}).get('mode','local-development')
        if mode not in ('local-development','family'):raise ValueError('Unknown authentication mode')
        if mode=='family':
            if self.config.get('fixture_player'):raise ValueError('Family accounts cannot use fixed fixture identity routing')
            self.db.enable_fair_transactions()
            from dittobuffer.core.accounts import Accounts
            self.accounts=Accounts(self)
            if self.raids['participation_limit']>1:
                from dittobuffer.gameplay.cooperative import SCHEMA
                from contextlib import closing
                with closing(self.db.connect()) as db:db.executescript(SCHEMA)
        from dittobuffer.gameplay.pvp import install as install_pvp
        install_pvp(self)
        from dittobuffer.gameplay.reward_calendar import install as install_rewards
        install_rewards(self)
        from dittobuffer.gameplay.onboarding import install as install_onboarding
        install_onboarding(self)

    def create_player(self,db,identity,name):
        stamp=now_ms()
        db.execute('INSERT INTO players(id,name,created_ms,revision) VALUES(?,?,?,?)',(identity,name,stamp,stamp))
        db.executemany('INSERT INTO items VALUES(?,?,?)',[(identity,int(k),v) for k,v in self.config['initial_items'].items()])
        db.execute('INSERT INTO trainer_teams(player_id,team) VALUES(?,0)',(identity,))
        from dittobuffer.gameplay.onboarding import created
        created(self,db,identity)

    def capacity(self,db,player,name):
        base=self.accounts.capacity(db,player,name) if self.accounts else self.config[name]
        row=db.execute('SELECT pokemon,items FROM trainer_storage_rewards WHERE player_id=?',(player,)).fetchone()
        return base+(row[0 if name=='pokemon_capacity' else 1] if row else 0)

    def authenticate(self, request):
        if self.accounts:
            from dittobuffer.core.accounts import AccountError
            try:return wire.AuthResponse(ticket=self.accounts.exchange(request.token.decode('utf8')))
            except (AccountError,UnicodeError):raise RpcError('Family credentials required') from None
        if request.auth_id.startswith('family:') or request.token.startswith(b'lf1_'):raise RpcError('Family credentials cannot enter the legacy development profile')
        # Local development identity only. Never send upstream tokens elsewhere or log them.
        identity = hashlib.sha256(('local:' + request.auth_id).encode()).hexdigest()
        fixture=self.config.get('fixture_player')
        if fixture:
            # Explicit test-profile routing; never usable with the live trainer DB.
            if not self.config.get('profile','').startswith(('m7-','m8-','m9-','m10-','m14-')) or not self.db.path.resolve().is_relative_to((ROOT/'builds').resolve()):
                raise RpcError('Fixture identity requires an isolated builds database')
            identity=fixture
        with self.db.transaction() as db:
            player = db.execute('SELECT id FROM players WHERE id=?', (identity,)).fetchone()
            if not player:
                if request.prevent_account_creation:
                    raise RpcError('Account does not exist')
                self.create_player(db,identity,self.config['player_name'])
            ticket = 'local-' + secrets.token_hex(24)
            # Force a fresh authoritative snapshot after login/profile switches.
            self.touch(db,identity)
            db.execute('INSERT INTO sessions VALUES(?,?)', (ticket,identity))
        return wire.AuthResponse(ticket=ticket)

    def rpc(self, request, ticket, run='',location=None):
        body_hash = hashlib.sha256(request.SerializeToString()).hexdigest()
        with self.db.transaction() as db:
            if self.accounts:
                from dittobuffer.core.accounts import AccountError
                try:identity=self.accounts.authorize(db,ticket)['player_id']
                except AccountError:raise RpcError('Family session invalid or expired') from None
                ticket='family:'+hashlib.sha256(ticket.removeprefix('Bearer ').encode()).hexdigest()
            session = db.execute('SELECT player_id FROM sessions WHERE ticket=?', (ticket,)).fetchone()
            if not session:
                raise RpcError('A local session is required')
            old = db.execute('SELECT request_hash,response FROM rpc_receipts WHERE ticket=? AND session_run=? AND rpc_id=?',
                             (ticket,run,request.rpc_id)).fetchone()
            if old:
                if old['request_hash'] != body_hash:
                    raise RpcError('RPC identifier reused with a different body')
                return wire.RpcResponse.FromString(old['response'])
            from dittobuffer.gameplay.liveops.runtime import project as live_projection
            from dittobuffer.gameplay.level_research import project as level_projection
            self=level_projection(self,db,session['player_id'])
            self=live_projection(self,db,session['player_id'],location)
            from dittobuffer.gameplay.mega import expire as expire_mega
            expire_mega(self,db)
            from dittobuffer.gameplay.gym_battle import expire
            expire(self,db)
            from dittobuffer.gameplay.raids import expire as expire_raids
            expire_raids(self,db)
            self.world.reconcile_retired(self,db,session['player_id'])
            response = wire.RpcResponse(rpc_id=request.rpc_id,status=1)
            for item in request.requests:
                name = self.methods.get(item.method, f'UNKNOWN_{item.method}')
                # 0.429 probes this optional, empty-body method during startup.
                # A 400 makes the native client retry on a five-minute backoff
                # and leaves the loading screen at ~90%; an empty reply lets it
                # continue. Nothing is persisted for this probe.
                if item.method == 610001:
                    response.payloads.add(method=item.method, payload=b'')
                    continue
                if item.method in self.config.get('rpc_trace_methods',[]) and self.db.path.resolve().is_relative_to(ROOT/'builds'):
                    import logging
                    if item.method in (5012,5017):
                        # Social envelopes can carry device push tokens. The
                        # action number and length suffice for settings tracing.
                        try:action=p.ProxyRequestProto.FromString(item.body).action
                        except Exception:action=-1
                        logging.info('Fixture social method=%d action=%d bytes=%d',item.method,action,len(item.body))
                    else:logging.info('Fixture RPC wire method=%d body=%s',item.method,item.body.hex())
                handler = getattr(self, 'rpc_' + name.lower(), None)
                if item.method in {950,951,952,955,956,958,970,971,972,973,974,1909,1910,1911,1913,*range(2600,2609)}:
                    from dittobuffer.gameplay.friends import enabled as social_enabled
                    if not social_enabled(self):raise RpcError('Social gameplay is disabled in this profile')
                if handler is None:
                    raise RpcError(f'Unsupported method {item.method} {name}')
                before=self.player(db,session['player_id']);before_stats=self.player_stats(before)
                if location is not None:
                    from dittobuffer.gameplay.friends import observe
                    observe(self,db,session['player_id'],location)
                    from dittobuffer.gameplay.eggs import sample
                    sample(self,db,session['player_id'],location)
                    from dittobuffer.gameplay.routes import sample as sample_route
                    sample_route(self,db,session['player_id'],location)
                    location=None
                if self.accounts:
                    from dittobuffer.core.accounts import principal_session
                    marker=principal_session.set(ticket.removeprefix('family:'))
                    try:payload = handler(db, session['player_id'], item.body)
                    finally:principal_session.reset(marker)
                else:payload = handler(db, session['player_id'], item.body)
                after=self.player(db,session['player_id'])
                if after['stardust']>before['stardust']:
                    from dittobuffer.gameplay.research import progress as research_progress
                    research_progress(self,db,session['player_id'],'stardust',after['stardust']-before['stardust'])
                from dittobuffer.gameplay.progression import bounds
                if self.player_stats(after).level>before_stats.level:
                    self.record_stats(db,session['player_id'],before,2,before_stats)
                    self.record_stats(db,session['player_id'],after,1)
                response.payloads.add(method=item.method, payload=payload.SerializeToString())
            db.execute('INSERT INTO rpc_receipts VALUES(?,?,?,?,?)', (ticket,run,request.rpc_id,body_hash,response.SerializeToString()))
            return response

    def player(self, db, player):
        return db.execute('SELECT p.*,COALESCE(w.total_m,0)/1000 km_walked,COALESCE(w.hatches,0) eggs_hatched FROM players p LEFT JOIN walking w ON w.player_id=p.id WHERE p.id=?', (player,)).fetchone()

    def touch(self, db, player):
        db.execute('UPDATE players SET revision=MAX(revision+1,?) WHERE id=?', (now_ms(),player))

    def player_stats(self,row):
        from dittobuffer.gameplay.progression import bounds,cap
        level,previous,following=bounds(self.config,row['xp'])
        return p.PlayerStatsProto(level=level,experience=row['xp'],prev_level_exp=previous,
            next_level_exp=following,level_cap=cap(self.config),num_pokemon_captured=row['captures'],
            number_of_pokeball_thrown=row['throws'],poke_stop_visits=row['stops'],km_walked=row['km_walked'],num_eggs_hatched=row['eggs_hatched'])

    def record_stats(self,db,player,row,reason,stats=None):
        stats=self.player_stats(row) if stats is None else stats
        changed=db.execute('INSERT OR IGNORE INTO player_stats_history VALUES(?,?,?,?)',
            (player,stats.level,reason,stats.SerializeToString())).rowcount
        if changed:self.touch(db,player)

    def appearance(self, db, player):
        from dittobuffer.gameplay.avatar import default_avatar
        row=db.execute('SELECT data FROM player_appearance WHERE player_id=?',(player,)).fetchone()
        if row:
            return p.PlayerNeutralAvatarProto.FromString(row['data'])
        avatar=default_avatar(p)
        db.execute('INSERT INTO player_appearance VALUES(?,?)',(player,avatar.SerializeToString()))
        self.touch(db,player)
        return avatar

    def rpc_get_player(self, db, player, body):
        row = self.player(db,player)
        out = p.GetPlayerOutProto(success=True)
        out.player.name = row['name']
        out.player.creation_time_ms = row['created_ms']
        out.player.max_pokemon_storage = self.capacity(db,player,'pokemon_capacity')
        out.player.max_item_storage = self.capacity(db,player,'item_capacity')
        from dittobuffer.gameplay.friends import enabled as social_enabled
        if social_enabled(self):
            out.player.max_postcard_storage=self.config.get('social',{}).get('postcard_capacity',350)
            out.player.max_giftbox_storage=self.config.get('social',{}).get('gift_capacity',20)
        out.player.obfuscated_player_id = player[:20]
        from dittobuffer.gameplay.gyms import team
        wallet=team(db,player)
        out.player.team = wallet['team']
        out.player.team_change_info.SetInParent()
        out.player.player_avatar_proto.SetInParent()
        # GeneralSettings.HandleDismiss (0x7919CD4) dereferences the social
        # settings record before saving any toggles. Absent records leave the
        # native close action throwing before SAVE_PLAYER_PREFERENCES.
        from dittobuffer.core.preferences import load_group
        for kind,field in [('social','social_player_settings'),('combat','combat_player_preferences'),('contact','contact_settings_proto')]:
            getattr(out.player,field).CopyFrom(load_group(db,player,kind))
        # The known initial onboarding values from this client's enum.
        from dittobuffer.gameplay.onboarding import completed_flags
        done=completed_flags(self,db,player)
        out.player.tutorial_complete.extend(sorted(done))
        for currency, quantity in [('STARDUST',row['stardust']),('POKECOIN',wallet['coins'])]:
            out.player.currency_balance.add(currency_type=currency,quantity=quantity)
        from dittobuffer.gameplay.buddy import player_data
        player_data(self,db,player,out.player)
        from dittobuffer.core.preferences import load
        out.player.player_preferences.CopyFrom(load(db,player))
        return out

    def _rocket_location(self, db, player):
        row = db.execute('SELECT lat,lng FROM admin_locations WHERE player_id=?', (player,)).fetchone()
        if not row:
            row = db.execute('SELECT lat,lng FROM walking WHERE player_id=? AND lat IS NOT NULL',
                             (player,)).fetchone()
        return (row['lat'], row['lng']) if row else (None, None)

    def rpc_get_rocket_balloon(self, db, player, body):
        if self.rocket_native_enabled:
            from dittobuffer.gameplay.rocket_progression import balloon
            return balloon(self,db,player,body)
        p.GetRocketBalloonProto.FromString(body)
        from dittobuffer.gameplay import rocket
        if not rocket.config().get('enabled'):
            return p.GetRocketBalloonOutProto(status=4)
        lat, lng = self._rocket_location(db, player)
        balloon = rocket.nearest(db, lat, lng, player)
        if not balloon:
            balloon = rocket.maybe_spawn(self, db, player, lat, lng)
        if not balloon:
            return p.GetRocketBalloonOutProto(status=3)
        display = p.RocketBalloonDisplayProto(
            type=p.RocketBalloonDisplayProto.BalloonType.Value('BalloonType_Rocket'))
        display.incident_display.incident_id = balloon['incident_id']
        display.incident_display.incident_display_type = {
            'grunt': p.IncidentDisplayType_INVASION_GRUNT,
            'leader': p.IncidentDisplayType_INVASION_LEADER,
            'giovanni': p.IncidentDisplayType_INVASION_GIOVANNI,
        }[balloon['kind']]
        return p.GetRocketBalloonOutProto(status=1, display=display)

    def rpc_start_rocket_balloon_incident(self, db, player, body):
        if self.rocket_native_enabled:
            from dittobuffer.gameplay.rocket_native import start
            return start(self,db,player,body)
        request = p.StartRocketBalloonIncidentProto.FromString(body)
        return self._start_rocket_incident(db, player, request.incident_lookup.incident_id,
                                           request.incident_lookup.context)

    def rpc_get_map_forts(self, db, player, body):
        if self.rocket_native_enabled:
            from dittobuffer.gameplay.rocket_progression import map_forts
            return map_forts(self,db,player,body)
        p.GetMapFortsProto.FromString(body)
        return p.GetMapFortsOutProto(status=1)

    def rpc_start_incident(self, db, player, body):
        if self.rocket_native_enabled:
            from dittobuffer.gameplay.rocket_native import start
            return start(self,db,player,body)
        request = p.StartIncidentProto.FromString(body)
        return self._start_rocket_incident(db, player, request.incident_lookup.incident_id,
                                           request.incident_lookup.context)

    def _start_rocket_incident(self, db, player, incident_id, context=1):
        from dittobuffer.gameplay import rocket
        balloon = next((row for row in rocket.active(db, player)
                        if row['incident_id'] == incident_id), None)
        if not balloon:
            return p.StartIncidentOutProto(status=4)
        incident = p.ClientIncidentProto(
            incident_id=balloon['incident_id'],
            fort_id=balloon['incident_id'],
            fort_name=balloon['character_name'], current_step=0)
        # The native incident screen walks one recognized step at a time.  An
        # empty step (the old implementation) is a valid protobuf but leaves the
        # panel with nothing to render, so tapping a balloon looked inert.
        incident.context = p.EnumWrapper.InvasionContext.Value('InvasionContext_ROCKET_BALLOON')
        incident.start_phase = p.EnumWrapper.IncidentStartPhase.Value(
            'IncidentStartPhase_INCIDENT_START_ON_SPIN_OR_EXIT')
        # Local profiles do not run the native PvP/RVN Rocket battle.  Start
        # with the encounter step so tapping the balloon opens the rescued
        # shadow instead of asking the client for a battle that does not exist
        # (which surfaced natively as "battle not found").
        incident.step.add().invasion_encounter.SetInParent()
        incident.completion_display.incident_id = balloon['incident_id']
        incident.completion_display.incident_start_ms = balloon['spawned_ms']
        incident.completion_display.incident_expiration_ms = balloon['expires_ms']
        incident.completion_display.incident_completed = True
        incident.completion_display.incident_display_type = p.IncidentDisplayType_INVASION_GENERIC
        incident.completion_display.invasion_finished.style = p.EnumWrapper.PokestopStyle.Value(
            'PokestopStyle_POKESTOP_ROCKET_VICTORY')
        return p.StartIncidentOutProto(status=1, incident=incident)

    def _rocket_encounter(self, db, player, incident_id):
        from dittobuffer.gameplay import rocket
        balloon = rocket.find(db, incident_id, player)
        if not balloon or balloon['caught_ms']:
            return None
        existing = rocket.encounter(db, player, incident_id)
        if existing:
            spawn = db.execute('SELECT s.*,a.data pokemon_data FROM spawns s '
                               'LEFT JOIN spawn_attributes a ON a.spawn_id=s.id '
                               'WHERE s.id=?', (existing['spawn_id'],)).fetchone()
            if spawn and spawn['expires_ms'] > now_ms() and not existing['caught_ms']:
                return spawn
            if existing['caught_ms']:
                return None
        source = db.execute('SELECT s.*,a.data pokemon_data,a.level FROM spawns s '
                            'LEFT JOIN spawn_attributes a ON a.spawn_id=s.id '
                            'WHERE s.id=?', (balloon['spawn_id'],)).fetchone()
        if not source or not source['pokemon_data']:
            return None
        stamp = now_ms()
        duration = max(0, int(self.config.get('encounter_duration_seconds', 0))) * 1000
        end = max(balloon['expires_ms'], stamp + duration, stamp + 5 * 60 * 1000)
        # Dedicated reward identity exactly like a raid bonus encounter.  The
        # client never has to guess a balloon's map-spawn id when throwing.
        spawn_id = secrets.randbits(61) | (1 << 60)
        point = 'rocket-reward:' + incident_id.rsplit(':', 1)[-1]
        level = float(source['level'] or 25)
        db.execute('INSERT INTO spawns VALUES(?,?,?,?,?,?)',
                   (spawn_id, point, source['species'], source['lat'], source['lng'], end))
        db.execute('INSERT INTO spawn_attributes VALUES(?,?,?)',
                   (spawn_id, source['pokemon_data'], level))
        db.execute('INSERT INTO encounters(player_id,spawn_id) VALUES(?,?)',
                   (player, spawn_id))
        balls = max(1, min(100, int(rocket.config().get('balloon_encounter_balls', 8))))
        db.execute('INSERT INTO rocket_encounters(player_id,incident_id,spawn_id,balls,created_ms) '
                   'VALUES(?,?,?,?,?) ON CONFLICT(player_id,incident_id) DO UPDATE SET '
                   'spawn_id=excluded.spawn_id,balls=excluded.balls,created_ms=excluded.created_ms',
                   (player, incident_id, spawn_id, balls, stamp))
        db.execute('UPDATE rocket_balloons SET encounter_spawn_id=? WHERE incident_id=?',
                   (spawn_id, incident_id))
        if duration:
            db.execute('INSERT INTO encounter_deadlines VALUES(?,?,?) '
                       'ON CONFLICT(player_id,spawn_id) DO UPDATE SET deadline_ms=excluded.deadline_ms',
                       (player, spawn_id, end))
        return db.execute('SELECT s.*,a.data pokemon_data FROM spawns s '
                          'LEFT JOIN spawn_attributes a ON a.spawn_id=s.id '
                          'WHERE s.id=?', (spawn_id,)).fetchone()

    def rpc_invasion_encounter(self, db, player, body):
        if self.rocket_native_enabled:
            from dittobuffer.gameplay.rocket_native import encounter
            return encounter(self,db,player,body)
        request = p.InvasionEncounterProto.FromString(body)
        spawn = self._rocket_encounter(db, player, request.incident_lookup.incident_id)
        if not spawn:
            return p.InvasionEncounterOutProto()
        from dittobuffer.gameplay.raids import probabilities
        from dittobuffer.gameplay import rocket
        record = rocket.encounter_for_spawn(db, player, spawn['id'])
        pokemon = self.pokemon(spawn)
        return p.InvasionEncounterOutProto(
            status=p.InvasionStatus.Status.Status_Success, encounter_pokemon=pokemon,
            capture_probability=probabilities(self),
            throws_remaining=record['balls'] if record else 8,
            encounter_id=spawn['id'], spawn_point_guid=spawn['spawnpoint'],
            invasion_ball=5, appraisal_star=3)

    def rpc_invasion_battle_update(self, db, player, body):
        if self.rocket_native_enabled:
            from dittobuffer.gameplay.rocket_native import battle_update
            return battle_update(self,db,player,body)
        p.UpdateInvasionBattleProto.FromString(body)
        return p.UpdateInvasionBattleOutProto(status=p.InvasionStatus.Status.Status_Success)

    def rpc_invasion_complete_dialogue(self, db, player, body):
        p.CompleteInvasionDialogueProto.FromString(body)
        return p.CompleteInvasionDialogueOutProto(status=p.InvasionStatus.Status.Status_Success)

    def rpc_invasion_open_combat_session(self, db, player, body):
        if self.rocket_native_enabled:
            from dittobuffer.gameplay.rocket_battle import open_invasion
            return open_invasion(self,db,player,body)
        p.OpenInvasionCombatSessionProto.FromString(body)
        return p.OpenInvasionCombatSessionOutProto(status=p.InvasionStatus.Status.Status_Success)

    def rpc_open_combat_session(self, db, player, body):
        if self.rocket_native_enabled:
            from dittobuffer.gameplay.rocket_battle import open_session
            return open_session(self,db,player,body)
        p.OpenCombatSessionProto.FromString(body)
        # Non-PvP combat ids stay on the local invasion flow, which reports the
        # same plain success as rpc_invasion_open_combat_session. The PvP layer
        # replaces this handler for its own 'pvp-' battles.
        return p.OpenCombatSessionOutProto(result=1)

    def rpc_update_combat(self, db, player, body):
        if self.rocket_native_enabled:
            from dittobuffer.gameplay.rocket_battle import update
            return update(self,db,player,body)
        p.UpdateCombatProto.FromString(body)
        return p.UpdateCombatOutProto(result=1)

    def rpc_quit_combat(self, db, player, body):
        if self.rocket_native_enabled:
            from dittobuffer.gameplay.rocket_battle import quit
            return quit(self,db,player,body)
        p.QuitCombatProto.FromString(body)
        return p.QuitCombatOutProto(result=1)

    def rpc_get_vs_seeker_status(self, db, player, body):
        p.GetVsSeekerStatusProto.FromString(body)
        return p.GetVsSeekerStatusOutProto(result=3)

    def rpc_get_holoholo_inventory(self, db, player, body):
        request = p.GetHoloholoInventoryProto.FromString(body)
        from dittobuffer.gameplay.eggs import ensure,inventory
        ensure(self,db,player)
        from dittobuffer.gameplay.research import ensure as ensure_research
        ensure_research(self,db,player)
        from dittobuffer.gameplay.max_station import inventory as settle_stations
        settle_stations(self,db,player)
        from dittobuffer.gameplay.pokedex import ensure as ensure_dex
        ensure_dex(self,db,player)
        avatar=self.appearance(db,player)
        from dittobuffer.gameplay.wardrobe import ensure as ensure_wardrobe,inventory as wardrobe_inventory
        ensure_wardrobe(self,db,player)
        from dittobuffer.gameplay.medals import refresh as refresh_medals,apply_stats
        medal_counters=refresh_medals(self,db,player)
        if not db.execute('SELECT 1 FROM player_stats_history WHERE player_id=?',(player,)).fetchone():
            self.record_stats(db,player,self.player(db,player),2)
        row = self.player(db,player)
        out = p.GetHoloholoInventoryOutProto(success=True)
        out.inventory_delta.original_timestamp = request.timestamp_millis
        out.inventory_delta.new_timestamp = row['revision']
        if request.timestamp_millis==row['revision']:return out
        # Full snapshots on change; no-change polls carry an empty delta.
        values = []
        stats = p.HoloInventoryItemProto()
        stats.player_stats.CopyFrom(self.player_stats(row))
        stats.player_stats.num_total_mega_evolutions=db.execute("SELECT COUNT(*) FROM mega_ledger WHERE player_id=? AND kind='evolve'",(player,)).fetchone()[0]
        stats.player_stats.num_unique_mega_evolutions=db.execute("SELECT COUNT(DISTINCT species) FROM mega_ledger WHERE player_id=? AND kind='evolve'",(player,)).fetchone()[0]
        apply_stats(self,db,player,stats.player_stats,medal_counters)
        from dittobuffer.gameplay.max_stats import apply as apply_max_stats
        apply_max_stats(self,db,player,stats.player_stats)
        values.append(stats)
        history=p.HoloInventoryItemProto()
        for saved in db.execute('SELECT reason,data FROM player_stats_history WHERE player_id=? ORDER BY level',(player,)):
            history.player_stats_snapshots.snap_shot.add(reason=saved['reason'],stats=p.PlayerStatsProto.FromString(saved['data']))
        values.append(history)
        appearance=p.HoloInventoryItemProto()
        appearance.neutral_avatar.CopyFrom(avatar)
        values.append(appearance)
        values.extend(wardrobe_inventory(self,db,player))
        from dittobuffer.gameplay.gifts import inventory as gift_inventory
        values.extend(gift_inventory(self,db,player))
        from dittobuffer.gameplay.routes import inventory as route_inventory
        values.extend(route_inventory(self,db,player))
        from dittobuffer.gameplay.vivillon import inventory as butterfly_inventory
        values.extend(butterfly_inventory(self,db,player))
        from dittobuffer.gameplay.medals import inventory as medal_inventory
        values.extend(medal_inventory(self,db,player))
        values.extend(inventory(self,db,player))
        from dittobuffer.gameplay.pokedex import inventory as dex_inventory
        values.extend(dex_inventory(self,db,player))
        from dittobuffer.gameplay.research import inventory as research_inventory
        values.append(research_inventory(self,db,player))
        from dittobuffer.gameplay.max_system import inventory as max_inventory
        values.extend(max_inventory(self,db,player))
        for field in ('applied_items','inventory_upgrades','player_camera'):
            value=p.HoloInventoryItemProto()
            getattr(value,field).SetInParent()
            if field=='player_camera':value.player_camera.default_camera=True
            values.append(value)
        for item in db.execute('SELECT * FROM items WHERE player_id=? ORDER BY item',(player,)):
            value=p.HoloInventoryItemProto()
            value.item.item=item['item'];value.item.count=item['count'];values.append(value)
        for pokemon in db.execute('SELECT data FROM pokemon WHERE player_id=? ORDER BY id',(player,)):
            from dittobuffer.gameplay.mega import project
            value=p.HoloInventoryItemProto();value.pokemon.CopyFrom(project(self,db,p.PokemonProto.FromString(pokemon['data'])));values.append(value)
        families={r['family']:p.PokemonFamilyProto(family_id=r['family'],candy=r['count']) for r in db.execute('SELECT * FROM candy WHERE player_id=?',(player,))}
        from dittobuffer.gameplay.mega import inventory_resources
        values.extend(inventory_resources(self,db,player,families))
        for family in families.values():
            value=p.HoloInventoryItemProto();value.pokemon_family.CopyFrom(family);values.append(value)
        for value in values:
            out.inventory_delta.inventory_item.add(modified_timestamp=row['revision'],item=value.SerializeToString())
        for deleted in db.execute('SELECT pokemon_id,revision FROM pokemon_tombstones WHERE player_id=? AND revision>?',(player,request.timestamp_millis)):
            key=p.HoloInventoryKeyProto(pokemon_id=deleted['pokemon_id'])
            out.inventory_delta.inventory_item.add(modified_timestamp=deleted['revision'],deleted_item_key=key.SerializeToString())
        if self.routes['enabled']:
            from dittobuffer.gameplay.routes import active
            latest=db.execute('SELECT state FROM route_attempts WHERE player=? ORDER BY start_ms DESC LIMIT 1',(player,)).fetchone()
            if not active(db,player) and (not latest or latest['state'] in ('QUIT','INVALIDATED','COMPLETED')):
                out.inventory_delta.inventory_item.add(modified_timestamp=row['revision'],deleted_item_key=p.HoloInventoryKeyProto(route_play=True).SerializeToString())
        return out

    def settings_bytes(self):
        settings=p.GlobalSettingsProto(minimum_client_version='0.429.2',max_pokemon_types=1025)
        settings.map_settings.pokemon_visible_range=250
        settings.map_settings.pokemon_despawn_range=300
        settings.map_settings.poke_nav_range_meters=1000
        settings.map_settings.encounter_range_meters=self.config['interaction_range_meters']
        settings.map_settings.get_map_objects_min_refresh_seconds=5
        settings.map_settings.get_map_objects_max_refresh_seconds=15
        settings.map_settings.get_map_objects_min_distance_meters=10
        settings.fort_settings.interaction_range_meters=self.config['interaction_range_meters']
        settings.fort_settings.far_interaction_range_meters=1000
        if self.reward_bonuses['weekly_enabled']:settings.bgmode_global_settings.min_player_level_fitness=5
        settings.level_settings.trainer_cp_modifier=1
        settings.quest_global_settings.enable_quests=self.research['enabled']
        settings.quest_global_settings.max_challenge_quests=self.research['capacity']
        if self.rocket_native_enabled:
            settings.incident_settings.min_player_level=self.rocket['minimum_level']
            settings.incident_settings.min_player_level_for_v2=self.rocket['minimum_level']
            if self.rocket.get('balloons_enabled'):settings.rocket_balloon_settings.min_player_level=self.rocket['minimum_level']
            settings.combat_global_settings.CopyFrom(p.CombatGlobalSettingsProto(enable_combat=True,enable_combat_stat_stages=True,minimum_player_level=self.rocket['minimum_level'],active_combat_update_interval_ms=500,waiting_for_player_update_interval_ms=500,ready_for_battle_update_interval_ms=500,pre_move_submit_window_ms=500,post_move_submit_window_ms=1000,enable_sockets=False))
        if self.routes['enabled']:
            settings.route_settings.CopyFrom(p.RouteGlobalSettingsProto(enable_routes=True,enable_route_play=True,enable_poi_detail_caching=False,distance_to_resume_route_meters=35,max_client_nearby_map_panning_distance_meters=self.routes['discovery_m'],minimum_client_version='0.429.2',minimum_client_version_to_play='0.429.2',minimum_client_version_to_create='0.429.2'))
        from dittobuffer.gameplay.mega import enabled as mega_enabled
        settings.mega_evo_settings.enable_mega_level=mega_enabled(self)
        from dittobuffer.gameplay.gyms import rules
        gym_rules=rules(self)
        settings.fort_settings.max_total_deployed_pokemon=gym_rules['capacity']
        settings.fort_settings.max_player_deployed_pokemon=1
        settings.fort_settings.max_same_pokemon_at_fort=1
        settings.fort_settings.max_player_total_deployed_pokemon=gym_rules['max_deployed']
        settings.fort_settings.deploy_stamina_multiplier=2
        settings.fort_settings.deploy_attack_multiplier=1
        settings.lobby_client_settings.lobby_refresh_interval_ms=2000
        if self.config.get('pvp',{}).get('enabled'):
            settings.combat_global_settings.CopyFrom(p.CombatGlobalSettingsProto(enable_combat=True,enable_combat_stat_stages=True,minimum_player_level=self.pvp['minimum_level'],active_combat_update_interval_ms=500,waiting_for_player_update_interval_ms=500,ready_for_battle_update_interval_ms=500,pre_move_submit_window_ms=500,post_move_submit_window_ms=1000,enable_sockets=False))
            settings.combat_global_settings.vs_seeker_player_min_level=self.pvp['minimum_level']
            settings.combat_global_settings.matchmaking_timeout_duration_ms=self.pvp['queue_seconds']*1000
            settings.combat_global_settings.maximum_daily_rewarded_battles=self.pvp['friend_rewards_per_day']
        from dittobuffer.gameplay.friends import enabled as social_enabled
        if social_enabled(self):
            settings.social_settings.enable_social=True
            settings.social_settings.max_friend_details=self.config.get('social',{}).get('friend_capacity',550)
            settings.social_settings.player_level_gate=1
            settings.social_settings.max_friend_nickname_length=20
            settings.social_settings.friend_details_page_size=100
            settings.social_settings.disable_facebook_friends_opening_prompt=True
            settings.trading_global_settings.enable_trading=True
            settings.trading_global_settings.min_player_level=10
        # Stored/evolved model availability is independent of the spawn pool.
        enabled=list(self.species)
        settings.enabled_pokemon.enabled_pokemon_range.add(start=min(enabled),end=max(enabled))
        raw=settings.SerializeToString()
        baseline=ROOT/'data/static/settings.binpb.gz'
        if baseline.exists():
            raw=merge(gzip.decompress(baseline.read_bytes()),raw)
        return raw

    def rpc_download_settings(self, db, player, body):
        raw=self.settings_bytes()
        return p.DownloadSettingsResponseProto(sha1=hashlib.sha1(raw).hexdigest(),values=raw)

    def rpc_download_remote_config_version(self, db, player, body):
        from dittobuffer.core.master import version
        return p.GetRemoteConfigVersionsOutProto(result=1,game_master_timestamp=version(self.config),asset_digest_timestamp=1)

    def rpc_download_game_config_version(self, db, player, body):
        from dittobuffer.core.master import version
        return p.GetGameConfigVersionsOutProto(result=1,game_master_timestamp=version(self.config),asset_digest_timestamp=1)

    def rpc_get_server_time(self, db, player, body):
        return p.GetServerTimeOutProto(status=1,server_time_ms=now_ms())

    def rpc_get_player_day(self, db, player, body):
        return p.GetPlayerDayOutProto(result=1,day=int(time.time()//86400))

    def rpc_get_player_profile(self, db, player, body):
        from dittobuffer.gameplay.gyms import list_badges
        out=p.PlayerProfileOutProto(result=1,start_time=self.player(db,player)['created_ms'])
        from dittobuffer.gameplay.medals import profile
        out.badges.extend(profile(self,db,player))
        from dittobuffer.gameplay.vivillon import profile as butterfly_profile
        out.badges.extend(butterfly_profile(self,db,player))
        badges=list_badges(self,db,player,b'').gym_badge
        out.gym_badges.total=len(badges)
        out.gym_badges.gym_badge.extend(badges[:10])
        return out

    def rpc_sfida_action_log(self,db,player,body):
        from dittobuffer.gameplay.journal import read
        return read(self,db,player,body)

    def rpc_process_player_inbox(self, db, player, body):
        # There are no local inbox messages yet; this response is empty by design.
        p.ProcessPlayerInboxProto.FromString(body)
        return p.ProcessPlayerInboxOutProto()

    def rpc_get_new_quests(self, db, player, body):
        from dittobuffer.gameplay.research import get_new
        return get_new(self,db,player,body)

    def rpc_get_quest_details(self,db,player,body):
        from dittobuffer.gameplay.research import details
        return details(self,db,player,body)

    def rpc_complete_quest(self,db,player,body):
        from dittobuffer.gameplay.research import complete
        return complete(self,db,player,body)

    def rpc_remove_quest(self,db,player,body):
        from dittobuffer.gameplay.research import remove
        return remove(self,db,player,body)

    def rpc_quest_encounter(self,db,player,body):
        from dittobuffer.gameplay.research import encounter
        return encounter(self,db,player,body)

    def rpc_complete_quest_stamp_card(self,db,player,body):
        from dittobuffer.gameplay.research import complete_card
        return complete_card(self,db,player,body)

    def rpc_get_quest_ui(self, db, player, body):
        from dittobuffer.gameplay.research import ui
        return ui(self,db,player,body)

    def rpc_read_quest_dialog(self,db,player,body):
        from dittobuffer.gameplay.stories import read_dialog
        return read_dialog(self,db,player,body)

    def rpc_check_awarded_badges(self, db, player, body):
        from dittobuffer.gameplay.medals import awarded
        return awarded(self,db,player,body)

    def rpc_list_avatar_store_items(self,db,player,body):
        from dittobuffer.gameplay.wardrobe import listings
        return listings(self,db,player,body)

    def rpc_list_avatar_appearance_items(self,db,player,body):
        from dittobuffer.gameplay.wardrobe import listings
        return listings(self,db,player,body,appearance=True)

    def rpc_set_neutral_avatar(self,db,player,body):
        from dittobuffer.gameplay.wardrobe import save
        out=save(self,db,player,body)
        if out.status==1:
            from dittobuffer.gameplay.onboarding import avatar_saved
            avatar_saved(self,db,player)
            out.player.CopyFrom(self.rpc_get_player(db,player,b'').player)
        return out

    def rpc_neutral_avatar_badge_reward(self,db,player,body):
        from dittobuffer.gameplay.medals import cosmetic_rewards
        return cosmetic_rewards(self,db,player,body)

    def rpc_get_contests_unclaimed_rewards(self, db, player, body):
        return p.GetContestsUnclaimedRewardsOutProto(status=2)

    def rpc_get_party(self, db, player, body):
        return p.GetPartyOutProto(result=5)

    def rpc_get_daily_encounter(self, db, player, body):
        return p.GetDailyEncounterOutProto(result=5)

    def rpc_get_photobomb(self,db,player,body):
        p.GetPhotobombProto.FromString(body)
        return p.GetPhotobombOutProto(status=2)

    def rpc_remote_gift_ping(self, db, player, body):
        # The local account has no remote gifts; keep this optional poll from
        # rolling back an otherwise valid inventory/settings batch.
        p.RemoteGiftPingRequestProto.FromString(body)
        return p.RemoteGiftPingResponseProto(result=1)

    def rpc_get_image_gallery_settings(self, db, player, body):
        # One local stop illustration; user photo uploads/voting are disabled.
        return p.GetImageGallerySettingsOutProto(is_image_gallery_enabled=False)

    def rpc_get_weekly_challenge_info(self, db, player, body):
        return p.GetWeeklyChallengeInfoOutProto(status=2,matchmaking_status=3)

    def rpc_get_pokemon_tags(self, db, player, body):
        from dittobuffer.gameplay.pokemon_tags import listing
        return listing(self,db,player,body)

    def rpc_create_pokemon_tag(self,db,player,body):
        from dittobuffer.gameplay.pokemon_tags import create
        return create(self,db,player,body)

    def rpc_edit_pokemon_tag(self,db,player,body):
        from dittobuffer.gameplay.pokemon_tags import edit
        return edit(self,db,player,body)

    def rpc_delete_pokemon_tag(self,db,player,body):
        from dittobuffer.gameplay.pokemon_tags import delete
        return delete(self,db,player,body)

    def rpc_set_pokemon_tags_for_pokemon(self,db,player,body):
        from dittobuffer.gameplay.pokemon_tags import assign
        return assign(self,db,player,body)

    def rpc_get_fitness_report(self, db, player, body):
        from dittobuffer.gameplay.reward_calendar import fitness_report
        return fitness_report(self,db,player,body)

    def rpc_get_fitness_rewards(self, db, player, body):
        p.GetFitnessRewardsProto.FromString(body)
        from dittobuffer.gameplay.reward_calendar import fitness_rewards
        return fitness_rewards(self,db,player)

    def rpc_get_web_token_action(self, db, player, body):
        return p.GetWebTokenActionOutProto(status=2)

    def rpc_get_available_skus_and_balances(self, db, player, body):
        from dittobuffer.gameplay.shop import available
        return available(self,db,player,body)

    def rpc_purchase_sku(self,db,player,body):
        from dittobuffer.gameplay.shop import purchase
        return purchase(self,db,player,body)

    def rpc_is_sku_available(self,db,player,body):
        from dittobuffer.gameplay.shop import is_available
        return is_available(self,db,player,body)

    def rpc_fetch_newsfeed_action(self, db, player, body):
        return p.PlatformFetchNewsfeedResponse(status=4)

    def rpc_get_incense_recap(self, db, player, body):
        return p.GetIncenseRecapOutProto(result=4)

    def rpc_get_buddy_walked(self, db, player, body):
        from dittobuffer.gameplay.buddy import walked
        return walked(self,db,player,body)

    def rpc_set_buddy_pokemon(self,db,player,body):
        from dittobuffer.gameplay.buddy import select
        return select(self,db,player,body)

    def rpc_get_buddy_stats(self,db,player,body):
        from dittobuffer.gameplay.buddy import stats
        return stats(self,db,player,body)

    def rpc_get_buddy_map(self,db,player,body):
        from dittobuffer.gameplay.buddy import map_data
        return map_data(self,db,player,body)

    def rpc_feed_buddy(self,db,player,body):
        from dittobuffer.gameplay.buddy import feed
        return feed(self,db,player,body)

    def rpc_pet_buddy(self,db,player,body):
        from dittobuffer.gameplay.buddy import pet
        return pet(self,db,player,body)

    def rpc_open_buddy_gift(self,db,player,body):
        from dittobuffer.gameplay.buddy_perks import open_gift
        return open_gift(self,db,player,body)

    def rpc_get_buddy_history(self,db,player,body):
        from dittobuffer.gameplay.buddy import history
        return history(self,db,player,body)

    def rpc_complete_snapshot_session(self,db,player,body):
        from dittobuffer.gameplay.buddy import snapshot
        return snapshot(self,db,player,body)

    def rpc_save_player_preferences(self,db,player,body):
        from dittobuffer.core.preferences import save
        return save(self,db,player,body)

    def rpc_save_social_player_settings(self,db,player,body):
        from dittobuffer.core.preferences import save_group
        return save_group(self,db,player,body,'social')

    def rpc_save_combat_player_preferences(self,db,player,body):
        from dittobuffer.core.preferences import save_group
        return save_group(self,db,player,body,'combat')

    def rpc_set_contact_settings(self,db,player,body):
        from dittobuffer.core.preferences import save_group
        return save_group(self,db,player,body,'contact')

    def rpc_claim_codename(self,db,player,body):
        from dittobuffer.gameplay.onboarding import codename
        return codename(self,db,player,body)

    def rpc_encounter_tutorial_complete(self,db,player,body):
        from dittobuffer.gameplay.onboarding import starter
        return starter(self,db,player,body)

    def rpc_mark_tutorial_complete(self, db, player, body):
        request=p.MarkTutorialCompleteProto.FromString(body)
        from dittobuffer.gameplay.onboarding import mark
        out=p.MarkTutorialCompleteOutProto(success=mark(self,db,player,request.tutorial_complete))
        out.player.CopyFrom(self.rpc_get_player(db,player,b'').player)
        # Transient acknowledgement avoids resetting the native selection screen
        # when its two HTTP callbacks arrive out of order. Durable flags and
        # starter entitlement still require SetNeutralAvatar to succeed.
        if out.success and 1 in request.tutorial_complete and 1 not in out.player.tutorial_complete:
            out.player.tutorial_complete.append(1)
        return out

    def rpc_download_game_master_templates(self, db, player, body):
        from dittobuffer.core.master import template_bytes,version
        from protocol.opaque import fields
        request=p.DownloadGmTemplatesRequestProto.FromString(body)
        import logging
        logging.info('Game Master basis=%d batch=%d page=%d',request.basis_batch_id,request.batch_id,request.page_offset)
        epoch=version(self.config)
        out=p.DownloadGmTemplatesResponseProto(result=1,batch_id=epoch,gms_batch_id=f'dittobuffer-{epoch}'.encode())
        out.deleted_template.extend([
            'V0001_POKEMON_V0001POKEMONBULBASAUR','V0004_POKEMON_V0004POKEMONCHARMANDER',
            'V0007_POKEMON_V0007POKEMONSQUIRTLE','V0025_POKEMON_V0025POKEMONPIKACHU',
            'ITEM_POKEBALL','ITEM_GREATBALL','ITEM_ULTRABALL','ITEM_RAZZBERRY'])
        for raw in template_bytes(self.config):
            name=next(value.decode() for n,k,_,value in fields(raw) if n==1 and k==2)
            out.template.add(template_id=name,data=raw)
        return out

    def rpc_get_outstanding_warnings(self, db, player, body):
        return p.GetOutstandingWarningsResponseProto()

    def rpc_mark_remote_tradable(self,db,player,body):
        from dittobuffer.gameplay.remote_trading import mark
        return mark(self,db,player,body)

    def rpc_get_remote_tradable_pokemon_from_other_player(self,db,player,body):
        from dittobuffer.gameplay.remote_trading import list_other
        return list_other(self,db,player,body)

    def rpc_propose_remote_trade(self,db,player,body):
        from dittobuffer.gameplay.remote_trading import propose
        return propose(self,db,player,body)

    def rpc_get_pending_remote_trade(self,db,player,body):
        from dittobuffer.gameplay.remote_trading import pending
        return pending(self,db,player,body)

    def rpc_cancel_remote_trade(self,db,player,body):
        from dittobuffer.gameplay.remote_trading import cancel
        return cancel(self,db,player,body)

    def rpc_respond_remote_trade(self,db,player,body):
        from dittobuffer.gameplay.remote_trading import respond
        return respond(self,db,player,body)

    def rpc_open_trading(self,db,player,body):
        from dittobuffer.gameplay.trading import handle
        return handle(self,db,player,body,'open')

    def rpc_get_trading(self,db,player,body):
        from dittobuffer.gameplay.trading import handle
        return handle(self,db,player,body,'get')

    def rpc_update_trading(self,db,player,body):
        from dittobuffer.gameplay.trading import handle
        return handle(self,db,player,body,'update')

    def rpc_confirm_trading(self,db,player,body):
        from dittobuffer.gameplay.trading import handle
        return handle(self,db,player,body,'confirm')

    def rpc_cancel_trading(self,db,player,body):
        from dittobuffer.gameplay.trading import handle
        return handle(self,db,player,body,'cancel')

    def rpc_send_gift(self,db,player,body):
        from dittobuffer.gameplay.gifts import send
        return send(self,db,player,body)

    def rpc_save_player_snapshot(self,db,player,body):
        # Public profiles are derived from owned state by friends.summary;
        # the client cannot replace level, appearance or public statistics.
        p.SavePlayerSnapshotProto.FromString(body)
        return p.SavePlayerSnapshotOutProto(result=1)

    def rpc_get_friendship_milestone_rewards(self,db,player,body):
        from dittobuffer.gameplay.friends import claim
        return claim(self,db,player,body)

    def rpc_open_gift(self,db,player,body):
        from dittobuffer.gameplay.gifts import open_gift
        return open_gift(self,db,player,body)

    def rpc_gift_details(self,db,player,body):
        from dittobuffer.gameplay.gifts import get_details
        return get_details(self,db,player,body)

    def rpc_check_send_gift(self,db,player,body):
        from dittobuffer.gameplay.gifts import can_send
        return p.CheckSendGiftOutProto(result=can_send(self,db,player,p.CheckSendGiftProto.FromString(body).player_id))

    def rpc_delete_gift_from_inventory(self,db,player,body):
        from dittobuffer.gameplay.gifts import delete
        return delete(self,db,player,body)

    def rpc_create_postcard(self,db,player,body):
        from dittobuffer.gameplay.gifts import postcard
        return postcard(self,db,player,body)

    def rpc_get_memento_list(self,db,player,body):
        from dittobuffer.gameplay.gifts import mementos
        return mementos(self,db,player,body)

    def rpc_update_postcard(self,db,player,body):
        from dittobuffer.gameplay.gifts import update_postcard
        return update_postcard(self,db,player,body)

    def rpc_delete_postcards(self,db,player,body):
        from dittobuffer.gameplay.gifts import delete_postcards
        return delete_postcards(self,db,player,body)

    def rpc_proxy_social_side_channel_action(self, db, player, body):
        return self.rpc_proxy_social_action(db,player,body)

    def rpc_proxy_social_action(self, db, player, body):
        request=p.ProxyRequestProto.FromString(body)
        from dittobuffer.gameplay.gym_notifications import inbox,update
        if request.action in (10103,10105):
            if request.action==10105:
                from dittobuffer.gameplay.friends import pending_notifications
                pending_notifications(self,db,player)
            out=(update if request.action==10103 else inbox)(db,player,request.payload)
            return p.ProxyResponseProto(status=1,payload=out.SerializeToString())
        from dittobuffer.gameplay.friends import proxy
        return proxy(self,db,player,request)

    def rpc_get_inbox(self,db,player,body):
        from dittobuffer.gameplay.gym_notifications import inbox
        return inbox(db,player,body)

    def rpc_gar_proxy_action(self, db, player, body):
        p.GarProxyRequestProto.FromString(body)
        return p.GarProxyResponseProto(status=14,error_message='Local account registry service is not implemented')

    def rpc_fort_details(self, db, player, body):
        request=p.FortDetailsProto.FromString(body)
        fort=db.execute('SELECT * FROM forts WHERE id=?',(request.id,)).fetchone()
        if not fort:
            raise RpcError('Unknown fort')
        place=self.world.details(fort['id'])
        return p.FortDetailsOutProto(id=fort['id'],name=fort['name'],fort_type=fort['kind'],
            latitude=fort['lat'],longitude=fort['lng'],description=place['description'] if place else 'PokéStop',
            image_url=[place['url'] if place else 'http://127.0.0.1:8080/assets/dittobuffer-stop.png'],poi_images_count=1)

    def seed(self, db, lat, lng,context=None):
        # Persistent deterministic tiles; moving generates neighboring local world data.
        tile=f'{lat:.3f},{lng:.3f}'
        base_lat=round(lat,3);base_lng=round(lng,3)
        stamp=now_ms();bucket=stamp//(self.spawn_seconds*1000)
        ids=[]
        count=self.spawn_points_per_tile
        for index in range(count):
            key=f'pool-v1:{self.config.get("spawn_generation",1)}:{tile}:{bucket}:{index}'
            digest=hashlib.sha256(key.encode()).digest()
            rng=random.Random(int.from_bytes(digest,'big'))
            pool_config=self.spawn_pools[index%len(self.spawn_pools)]
            location=None
            if self.world.path:
                location=self.world.spawn_point(base_lat,base_lng,rng)
                if location is None:
                    # The published world covers the Netherlands; players and
                    # development fixtures elsewhere must still get wild spawns.
                    # Keep the same deterministic tile point and fall back to a
                    # small offset instead of silently producing an empty map.
                    location=(base_lat+(rng.random()-.5)*.0004,
                              base_lng+(rng.random()-.5)*.0004)
                habitat=self.world.habitat(*location)
                local=context or {}
                eligible=[v for v in self.spawn_pools if (not v.get('biomes') or habitat in v['biomes']) and (not v.get('time') or v['time']==('day' if local.get('day',True) else 'night')) and (not v.get('weather') or local.get('condition') in v['weather'])]
                if eligible:pool_config=eligible[index%len(eligible)]
            from dittobuffer.gameplay.liveops.runtime import spawn_selection
            selected=spawn_selection(self,eligible if self.world.path and 'eligible' in locals() and eligible else self.spawn_pools,rng,context)
            if selected is not None:pool_config=selected
            from dittobuffer.gameplay import liveops
            if selected is not None:
                pool=pool_config['species']
                entry=rng.choices(pool,weights=[e['weight'] for e in pool])[0]
            else:
                pool=[value for value in pool_config['species'] if liveops.allowed(self,value['id'])]
                if not pool:pool=pool_config['species']
                day=liveops.community_day(self)
                if day and int(day['species']) in self.species:
                    species=int(day['species']);entry={'id':species,'form':day.get('form') or self.species[species]['form']}
                    if not liveops.allowed(self,species):entry={'id':pool[0]['id'],'form':pool[0].get('form')}
                else:
                    entry=rng.choices(pool,weights=[max(1e-9,float(e['weight'])*liveops.spawn_weight(self,e['id'],e.get('form',self.species[e['id']]['form']))) for e in pool])[0]
            species=entry['id'];rules=self.forms[species,entry.get('form',self.species[species]['form'])]
            spawn_id=int.from_bytes(hashlib.sha256(key.encode()).digest()[:7],'big')
            ids.append(spawn_id)
            angle=2*math.pi*index/count
            spawn_lat=base_lat+30*math.cos(angle)/111320
            spawn_lng=base_lng+30*math.sin(angle)/(111320*max(0.01,math.cos(math.radians(base_lat))))
            if location:spawn_lat,spawn_lng=location
            spawnpoint=CellId.from_lat_lng(LatLng.from_degrees(spawn_lat,spawn_lng)).parent(20).to_token()
            db.execute('INSERT OR IGNORE INTO spawns VALUES(?,?,?,?,?,?)',
                (spawn_id,spawnpoint,species,spawn_lat,spawn_lng,(bucket+1)*self.spawn_seconds*1000))
            level=rng.randint(*pool_config.get('level_range',self.spawn_level_range))
            ivs=[rng.randint(*self.spawn_iv_range) for _ in range(3)]
            from dittobuffer.gameplay.species import make_pokemon
            gender=entry.get('gender')
            if gender is None:
                from dittobuffer.gameplay.species import choose_gender
                gender=choose_gender(self,species,rng)
            shiny=rng.random()<liveops.shiny_rate(self,pool_config.get('shiny_rate',self.spawn_shiny_rate),species,rules['form'])
            shiny=shiny and (species,rules['form']) in self.shiny_eligible
            pokemon=make_pokemon(p,rules,species,level,ivs,shiny,gender,costume=entry.get('costume',0))
            from dittobuffer.gameplay.pokemon_sizes import assign as assign_size
            assign_size(self,pokemon,entry,rng)
            from dittobuffer.gameplay.backgrounds import assign
            assign(self,pokemon,rng,'wild',entry.get('background',0))
            if self.reward_bonuses['enabled'] and context:
                weather=context.get('condition',0)
                boosts={1:{10,12,5},2:{11,13,7},3:{1,6},4:{18,2,4},5:{16,3,14},6:{15,9},7:{17,8}}
                if set(rules['types']) & boosts.get(weather,set()):pokemon.pokemon_display.weather_boosted_condition=weather
            pokemon.creation_time_ms=bucket*self.spawn_seconds*1000
            db.execute('INSERT OR IGNORE INTO spawn_attributes VALUES(?,?,?)',(spawn_id,pokemon.SerializeToString(),level))
            db.execute('INSERT OR IGNORE INTO spawn_generations VALUES(?,?)',(spawn_id,self.config.get('spawn_generation',1)))
        return ids

    def pokemon(self, spawn, pokemon_id=0, ball=1):
        from dittobuffer.gameplay.species import make_pokemon
        out=p.PokemonProto.FromString(spawn['pokemon_data']) if spawn['pokemon_data'] else make_pokemon(p,self.species[spawn['species']],spawn['species'],5,[10,10,10])
        out.id=pokemon_id;out.pokeball=ball
        return out

    def wild(self, spawn):
        pokemon=self.pokemon(spawn)
        # Modification time identifies this immutable encounter, not a poll.
        # Android reinitializes changed records (and requests the model again).
        stamp=now_ms()
        out=p.WildPokemonProto(encounter_id=spawn['id'],spawn_point_id=spawn['spawnpoint'],latitude=spawn['lat'],
                longitude=spawn['lng'],last_modified_ms=pokemon.creation_time_ms,
                time_till_hidden_ms=max(0,min(2147483647,spawn['expires_ms']-stamp)))
        out.pokemon.CopyFrom(pokemon)
        return out

    def rpc_get_map_objects(self, db, player, body):
        request=p.GetMapObjectsProto.FromString(body)
        if not (-90<=request.player_lat<=90 and -180<=request.player_lng<=180):
            raise RpcError('Invalid coordinates')
        if any(not CellId(value).is_valid() for value in request.cell_id):raise RpcError('Invalid map cell')
        if len(request.cell_id)>100:raise RpcError('Too many map cells')
        # The admin panel reads this authoritative client fix. Walking credit is
        # deliberately untouched; that remains owned by the walking sampler.
        # An empty/default poll before the phone has a fix must not move the
        # authoritative location to Null Island.
        if abs(request.player_lat) >= 1e-6 or abs(request.player_lng) >= 1e-6:
            db.execute('INSERT INTO admin_locations(player_id,lat,lng,seen_ms) VALUES(?,?,?,?) '
                       'ON CONFLICT(player_id) DO UPDATE SET lat=excluded.lat,lng=excluded.lng,seen_ms=excluded.seen_ms',
                       (player,request.player_lat,request.player_lng,now_ms()))
        # Fetch the trainer's current OSM cell before serving map tiles, then
        # let the throttled worker fill the surrounding cells in the background.
        from dittobuffer.world import osm_cache
        osm_cache.ensure(request.player_lat,request.player_lng,self.config,wait=4.0)
        spatial='';spatial_args=[]
        if (self.world.path or self.config.get('max_system',{}).get('enabled')) and request.cell_id:
            from s2sphere import Cell
            if any(CellId(value).level()<12 for value in request.cell_id):raise RpcError('World map cells must be local')
            bounds=[Cell(CellId(value)).get_rect_bound() for value in request.cell_id]
            spatial_args=[min(r.lat_lo().degrees for r in bounds),max(r.lat_hi().degrees for r in bounds),min(r.lng_lo().degrees for r in bounds),max(r.lng_hi().degrees for r in bounds)]
            spatial=' AND s.lat BETWEEN ? AND ? AND s.lng BETWEEN ? AND ?'
        # Always sync: on a fresh install there is no snapshot, but admin and
        # OSM-imported POIs still need forts before map objects are built.
        self.world.materialize(db,request.player_lat,request.player_lng)
        from dittobuffer.gameplay import rocket
        # Balloon arrival is player-scoped and persisted; map polling is the one
        # call every client makes even when the dedicated balloon RPC is idle.
        rocket.maybe_spawn(self, db, player, request.player_lat, request.player_lng)
        context=self.weather.snapshot(request.player_lat,request.player_lng) if self.weather else None
        active_ids=self.seed(db,request.player_lat,request.player_lng,context=context)
        ring=self.config.get('spawn_neighbor_tiles',0)
        for north in range(-ring,ring+1):
            for east in range(-ring,ring+1):
                if north==east==0:continue
                latitude=round(request.player_lat,3)+north*0.001
                longitude=(round(request.player_lng,3)+east*0.001+180)%360-180
                if -89.999<=latitude<=89.999:
                    active_ids.extend(self.seed(db,latitude,longitude,context=context))
        active_points={row['spawnpoint'] for row in db.execute(
            'SELECT spawnpoint FROM spawns WHERE id IN ('+','.join('?' for _ in active_ids)+')',active_ids)}
        out=p.GetMapObjectsOutProto(status=1)
        if self.weather:self.weather.apply(out,p,request.player_lat,request.player_lng)
        cells={i:out.map_cell.add(s2_cell_id=i,as_of_time_ms=now_ms()) for i in dict.fromkeys(request.cell_id)}
        def cell(lat,lng):
            value=CellId.from_lat_lng(LatLng.from_degrees(lat,lng))
            return next((c for i,c in cells.items() if CellId(i).contains(value)),None)
        for spawn in db.execute("SELECT s.*,a.data pokemon_data FROM spawns s JOIN spawn_generations g ON g.spawn_id=s.id LEFT JOIN spawn_attributes a ON a.spawn_id=s.id WHERE expires_ms>? AND g.generation=?"+spatial+" AND NOT EXISTS(SELECT 1 FROM encounters e WHERE e.spawn_id=s.id AND e.player_id=? AND e.state!='active')",[now_ms(),self.config.get('spawn_generation',1),*spatial_args,player]):
            if not spawn['pokemon_data']:continue
            # Keep still-visible neighboring tiles when GPS crosses a tile edge.
            # A configuration epoch may replace a point's encounter; expose only
            # its current generation at that same point.
            if spawn['id'] not in active_ids and spawn['spawnpoint'] in active_points:continue
            # Each returned cell is a full snapshot. Clipping its contents by
            # the latest GPS fix makes a tiny boundary crossing delete an
            # unexpired record from that same cell. Requested S2 cells bound
            # delivery; the client's spawn/despawn ranges bound rendering.
            c=cell(spawn['lat'],spawn['lng'])
            if c is not None:
                c.wild_pokemon.add().CopyFrom(self.wild(spawn))
                visible=c.catchable_pokemon.add(spawnpoint_id=spawn['spawnpoint'],encounter_id=spawn['id'],pokedex_type_id=spawn['species'],
                        expiration_time_ms=spawn['expires_ms'],latitude=spawn['lat'],longitude=spawn['lng'])
                visible.pokemon_display.CopyFrom(self.pokemon(spawn).pokemon_display)
        if self.config.get('map_trace',False) and not self.accounts:
            import logging
            logging.info('Map snapshot %s',json.dumps({'at':now_ms(),'player':player[:12],
                'lat':request.player_lat,'lng':request.player_lng,'since':list(request.since_time_ms),
                'cells':[{'id':str(c.s2_cell_id),'as_of':c.as_of_time_ms,
                    'spawns':[{'id':str(v.encounter_id),'point':v.spawnpoint_id,'expires':v.expiration_time_ms,
                        'species':v.pokedex_type_id} for v in c.catchable_pokemon]} for c in out.map_cell]},separators=(',',':')))
        for fort in db.execute('SELECT * FROM forts s'+(' WHERE 1=1'+spatial if spatial else ''),spatial_args):
            if self.world.path:
                place=self.world.poi(fort['id'])
                if not place or place['status']!='APPROVED':continue
            c=cell(fort['lat'],fort['lng'])
            if c is not None:
                if fort['kind']==0:
                    from dittobuffer.gameplay.gyms import map_fort
                    c.fort.add().CopyFrom(map_fort(self,db,fort))
                else:
                    value=c.fort.add(fort_id=fort['id'],fort_type=fort['kind'],latitude=fort['lat'],longitude=fort['lng'],enabled=True,last_modified_ms=now_ms())
                    if self.rocket_native_enabled:
                        from dittobuffer.gameplay.rocket_native import decorate
                        decorate(self,db,player,fort,value)
        from dittobuffer.gameplay.max_system import enabled as max_enabled,materialize as materialize_max,station as max_station
        if max_enabled(self) and spatial_args:
            materialize_max(self,db,spatial_args)
            for row in db.execute('SELECT * FROM max_stations WHERE end_ms>? AND start_ms<=? AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?', [now_ms(),now_ms(),*spatial_args]):
                target=cell(row['lat'],row['lng'])
                if target is not None:target.stations.add().CopyFrom(max_station(self,db,player,row))
        from dittobuffer.gameplay.routes import map_cells as route_map_cells
        route_map_cells(self,db,player,out.map_cell)
        signature=tuple((v.pokedex_type_id,v.pokemon_display.form) for c in out.map_cell for v in c.catchable_pokemon)
        if signature!=getattr(self,'_last_map_signature',None):
            import logging
            logging.info('Map visible species/forms=%s cells=%d',signature,len(out.map_cell))
            self._last_map_signature=signature
            (ROOT/'logs').mkdir(exist_ok=True)
            (ROOT/'logs/last-map-response.binpb').write_bytes(out.SerializeToString())
            (ROOT/'logs/last-map-request.binpb').write_bytes(body)
        return out

    def rpc_encounter(self, db, player, body):
        request=p.EncounterProto.FromString(body)
        if self.has_rocket_state and db.execute('SELECT 1 FROM rocket_players WHERE spawn_id=?',(request.encounter_id,)).fetchone():
            raise RpcError('Rocket rewards require the authenticated invasion encounter')
        # Public wild identities are shared; earned reward encounters are private.
        if db.execute('SELECT 1 FROM raid_members WHERE spawn_id=? AND player_id!=? UNION ALL SELECT 1 FROM research_encounters WHERE spawn_id=? AND player_id!=?',(request.encounter_id,player,request.encounter_id,player)).fetchone() or foreign_max_reward(db,player,request.encounter_id):
            raise RpcError('Encounter does not belong to this trainer')
        if db.execute('SELECT 1 FROM special_research_encounters WHERE player_id=? AND spawn_id=?',(player,request.encounter_id)).fetchone():
            raise RpcError('Native research must be opened through its owned Quest encounter')
        spawn=db.execute('SELECT s.*,a.data pokemon_data FROM spawns s LEFT JOIN spawn_attributes a ON a.spawn_id=s.id WHERE s.id=? AND spawnpoint=?',(request.encounter_id,request.spawnpoint_id)).fetchone()
        if not spawn or spawn['expires_ms']<=now_ms():
            raise RpcError('Encounter does not exist or expired')
        if distance(request.player_lat_degrees,request.player_lng_degrees,spawn['lat'],spawn['lng'])>self.config['interaction_range_meters']:
            raise RpcError('Encounter out of range')
        old=db.execute('SELECT state FROM encounters WHERE player_id=? AND spawn_id=?',(player,spawn['id'])).fetchone()
        if old and old['state']!='active':
            raise RpcError('Encounter already finished')
        db.execute('INSERT OR IGNORE INTO encounters(player_id,spawn_id) VALUES(?,?)',(player,spawn['id']))
        duration=self.config.get('encounter_duration_seconds',0)
        if duration:
            db.execute('INSERT OR IGNORE INTO encounter_deadlines VALUES(?,?,?)',
                (player,spawn['id'],max(spawn['expires_ms'],now_ms()+duration*1000)))
        out=p.EncounterOutProto(status=1)
        out.pokemon.CopyFrom(self.wild(spawn))
        deadline=db.execute('SELECT deadline_ms FROM encounter_deadlines WHERE player_id=? AND spawn_id=?',(player,spawn['id'])).fetchone()
        if deadline:out.pokemon.time_till_hidden_ms=max(0,min(2147483647,deadline['deadline_ms']-now_ms()))
        from dittobuffer.gameplay.pokedex import record
        record(self,db,player,'spawn:'+str(spawn['id']),out.pokemon.pokemon)
        from dittobuffer.gameplay.items import active_berry,probabilities
        out.capture_probability.CopyFrom(probabilities(self,active_berry(db,player,spawn['id'])))
        return out

    def rpc_catch_pokemon(self, db, player, body):
        request=p.CatchPokemonProto.FromString(body)
        # A few client paths emit CatchPokemonProto.encounter_id with a wire
        # type other than the supplied schema's fixed64. Recover the ID from the
        # raw field before looking up ownership.
        if not request.encounter_id:
            from protocol.opaque import fields
            for tag,kind,_,value in fields(body):
                if tag==1 and kind in (0,1):
                    request.encounter_id=value if kind==0 else int.from_bytes(value,'little')
                    break
        encounter_id=request.encounter_id
        if self.config.get('map_trace',False):
            import logging
            logging.info('Catch throw encounter=%d ball=%d hit=%s reticle=%s spin=%s',encounter_id,request.pokeball,request.hit_pokemon,request.normalized_reticle_size,request.spin_modifier)
        from dittobuffer.gameplay import rocket
        rocket.ensure(db)
        # The reward record is authoritative.  Native 0.429.2 usually echoes
        # the invitation encounter id, but restart/deferred paths can send the
        # spawn-point token, zero, or a stale map id.  Recover the trainer's
        # open Rocket reward before declaring ownership missing.
        native_rocket = db.execute('SELECT * FROM rocket_players WHERE player_id=? AND spawn_id=? AND receipt IS NOT NULL',(player,encounter_id)).fetchone() if self.has_rocket_state else None
        rocket_record = None if native_rocket else rocket.resolve_catch(db, player, request)
        if rocket_record:
            if not rocket.encounter(db, player, rocket_record['incident_id']):
                self._rocket_encounter(db, player, rocket_record['incident_id'])
            rocket_record = rocket.encounter(db, player, rocket_record['incident_id'])
            if rocket_record and not rocket_record['caught_ms']:
                encounter_id = rocket_record['spawn_id']
                request.encounter_id = encounter_id
            else:
                rocket_record = None
        if native_rocket:rocket_record=native_rocket
        rocket_spawn = rocket_record is not None
        if foreign_max_reward(db,player,encounter_id):
            raise RpcError('Encounter does not belong to this trainer')
        max_reward=None
        if self.config.get('max_system',{}).get('enabled'):
            from dittobuffer.gameplay.max_battle import catch_member
            max_reward=catch_member(self,db,player,encounter_id)
        encounter=db.execute('SELECT * FROM encounters WHERE player_id=? AND spawn_id=?',(player,encounter_id)).fetchone()
        # Invasion encounters should always have inserted this row in
        # rpc_invasion_encounter. Older/deferred client flows occasionally reach
        # the catch without it; a live Rocket reward record is sufficient proof
        # of ownership. Never apply this fallback to ordinary wild spawns.
        if not encounter and rocket_spawn:
            db.execute('INSERT OR IGNORE INTO encounters(player_id,spawn_id) VALUES(?,?)',(player,encounter_id))
            encounter=db.execute('SELECT * FROM encounters WHERE player_id=? AND spawn_id=?',(player,encounter_id)).fetchone()
        if not encounter:
            raise RpcError('Encounter ownership missing')
        if encounter['state']=='caught':
            return p.CatchPokemonOutProto.FromString(encounter['result'])
        reward=db.execute('SELECT 1 FROM research_encounters WHERE player_id=? AND spawn_id=?',(player,encounter_id)).fetchone()
        from dittobuffer.gameplay.special_encounters import capture_rules as special_rules
        scripted=special_rules(db,player,encounter_id) if reward else None
        raid=db.execute('SELECT * FROM raid_members WHERE player_id=? AND spawn_id=? AND reward IS NOT NULL',(player,encounter_id)).fetchone()
        spawn=db.execute('SELECT s.*,a.data pokemon_data FROM spawns s LEFT JOIN spawn_attributes a ON a.spawn_id=s.id WHERE s.id=?',(encounter_id,)).fetchone()
        if spawn and not reward and not rocket_spawn and not max_reward and spawn['spawnpoint']!=request.spawn_point_guid:
            spawn=None
        deadline=db.execute('SELECT deadline_ms FROM encounter_deadlines WHERE player_id=? AND spawn_id=?',(player,encounter_id)).fetchone()
        expiry=max(spawn['expires_ms'],deadline['deadline_ms'] if deadline else 0) if spawn else 0
        if not spawn or expiry<=now_ms() or encounter['state']!='active':
            raise RpcError('Encounter unavailable')
        allowed={scripted['ball']} if scripted else ({5} if raid or rocket_spawn or max_reward else {1,2,3,4})
        if request.pokeball not in allowed:
            raise RpcError('Unsupported ball')
        from dittobuffer.gameplay.eggs import count,storage_id
        if db.execute('SELECT COUNT(*) FROM pokemon WHERE player_id=?',(player,)).fetchone()[0]+count(db,player)>=self.capacity(db,player,'pokemon_capacity'):
            raise RpcError('Pokemon storage full')
        if scripted:
            changed=1  # Native UsesInventory=false; no bag item is consumed.
        elif native_rocket:
            changed=db.execute('UPDATE rocket_players SET balls=balls-1 WHERE player_id=? AND incident_id=? AND balls>0',(player,native_rocket['incident_id'])).rowcount
        elif rocket_spawn:
            changed=db.execute('UPDATE rocket_encounters SET balls=balls-1 '
                               'WHERE player_id=? AND incident_id=? AND balls>0',
                               (player,rocket_record['incident_id'])).rowcount
            rocket_record=rocket.encounter(db,player,rocket_record['incident_id'])
        elif raid:
            changed=db.execute('UPDATE raid_members SET balls=balls-1 WHERE seed=? AND player_id=? AND balls>0',(raid['seed'],player)).rowcount
        elif max_reward:
            changed=db.execute('UPDATE max_members SET balls=balls-1 WHERE room_id=? AND player_id=? AND balls>0',(max_reward['room_id'],player)).rowcount
        else:
            changed=db.execute('UPDATE items SET count=count-1 WHERE player_id=? AND item=? AND count>0',(player,request.pokeball)).rowcount
        if not changed:
            raise RpcError('No balls available')
        from dittobuffer.gameplay.reward_calendar import attempted
        reward_attempts=attempted(self,db,player,encounter_id,request.pokeball)
        db.execute('UPDATE players SET throws=throws+1 WHERE id=?',(player,))
        self.touch(db,player)
        exhausted = bool((raid and raid['balls']==1) or (rocket_spawn and rocket_record and rocket_record['balls']==1) or (max_reward and max_reward['balls']==1))
        if exhausted:
            # Persist the exhausted bonus encounter even when the final throw
            # misses or breaks out. A later restart cannot refill Premier Balls.
            db.execute("UPDATE encounters SET state='fled' WHERE player_id=? AND spawn_id=?",(player,spawn['id']))
        if not request.hit_pokemon:
            return p.CatchPokemonOutProto(status=4)
        from dittobuffer.gameplay.research import progress
        thrown=self.pokemon(spawn)
        progress(self,db,player,'throw',context={'species':thrown.pokemon_id,'types':self.species[thrown.pokemon_id]['types'],'shiny':thrown.pokemon_display.shiny,'weather_boosted':bool(thrown.pokemon_display.weather_boosted_condition),'quality':request.normalized_reticle_size,'curveball':request.spin_modifier>=.5,'ball':request.pokeball})
        from dittobuffer.gameplay.items import active_berry,probabilities
        ball_index=0 if raid or rocket_spawn or max_reward or request.pokeball not in (1,2,3,4) else request.pokeball-1
        probability=probabilities(self,active_berry(db,player,spawn['id'])).capture_probability[ball_index]
        if scripted:probability=1  # Client-local scripted escapes precede this RPC.
        db.execute('UPDATE encounter_items SET item=0 WHERE player_id=? AND spawn_id=?',(player,spawn['id']))
        if secrets.randbelow(1_000_000)>=int(probability*1_000_000):
            if exhausted and rocket_spawn and not native_rocket:
                rocket.mark_caught(db, player, rocket_record['incident_id'], spawn['id'])
            return p.CatchPokemonOutProto(status=3 if exhausted else 2)
        from dittobuffer.gameplay.pokedex import ensure as ensure_pokedex
        ensure_pokedex(self,db,player)
        first_registration=not db.execute('SELECT 1 FROM pokedex_observations WHERE player_id=? AND species=? AND caught=1 LIMIT 1',(player,spawn['species'])).fetchone()
        cursor=db.execute('INSERT INTO pokemon(id,player_id,spawn_id,species,data) VALUES(?,?,?,?,?)',(storage_id(db),player,spawn['id'],spawn['species'],b''))
        pokemon=self.pokemon(spawn,cursor.lastrowid,request.pokeball)
        pokemon.creation_time_ms=now_ms()
        captured_cell=CellId.from_lat_lng(LatLng.from_degrees(spawn['lat'],spawn['lng'])).parent(10).id()
        pokemon.captured_s2_cell_id=captured_cell if captured_cell<2**63 else captured_cell-2**64
        if native_rocket:
            incident=db.execute('SELECT definition FROM rocket_incidents WHERE id=?',(native_rocket['incident_id'],)).fetchone()
            pokemon.origin_detail.invasion_detail.origin=json.loads(incident['definition'])['character']
        elif rocket_spawn:
            # Legacy balloon imports do not carry the native incident metadata.
            balloon=rocket.find(db, rocket_record['incident_id'], player)
            if balloon:
                pokemon.origin_detail.invasion_detail.SetInParent()
        elif raid:
            raid_row=db.execute('SELECT definition FROM raids WHERE seed=?',(raid['seed'],)).fetchone()
            if raid_row:pokemon.origin_detail.raid_detail.raid_level=json.loads(raid_row['definition'])['tier']
        elif max_reward:
            size=db.execute('SELECT COUNT(*) FROM max_victories WHERE room_id=?',(max_reward['room_id'],)).fetchone()[0]
            pokemon.origin_detail.bread_battle_detail.lobby_size=max(1,size)
            # Native origin metadata uses nonzero field 6 for a REMOTE catch;
            # this Max entry path is local, so keep the canonical location.
            pokemon.origin_detail.bread_battle_detail.ClearField('player_captured_s2_cell_id')
        elif not reward:pokemon.origin_detail.wild_detail.caught_in_wild=True
        from dittobuffer.gameplay.pokedex import record
        record(self,db,player,'spawn:'+str(spawn['id']),pokemon,True)
        db.execute('UPDATE pokemon SET data=? WHERE id=?',(pokemon.SerializeToString(),cursor.lastrowid))
        level=db.execute('SELECT level FROM spawn_attributes WHERE spawn_id=?',(spawn['id'],)).fetchone()
        if level:db.execute('INSERT INTO pokemon_levels VALUES(?,?)',(cursor.lastrowid,level['level']))
        if rocket_spawn and not native_rocket:
            # The balloon's one shadow has been caught; retire both the map
            # balloon and the dedicated reward spawn.
            rocket.mark_caught(db, player, rocket_record['incident_id'], spawn['id'])
        from dittobuffer.gameplay.mega import catch_bonus
        mega_candy,mega_xp,mega_xl=catch_bonus(self,db,player,pokemon,bool(raid))
        # XL Candy unlocks at trainer level 31 and scales with the wild Pokémon's level.
        from dittobuffer.core.management import add_xl_candy,xl_catch_chance
        from dittobuffer.gameplay.progression import bounds as level_bounds
        base_xl=0
        wild_level=level['level'] if level else 20
        if level_bounds(self.config,self.player(db,player)['xp'])[0]>=self.config.get('xl_candy_min_player_level',31):
            if secrets.randbelow(1_000_000)<int(xl_catch_chance(self.config,wild_level)*1_000_000):base_xl=1
        if base_xl:add_xl_candy(db,player,self.species[spawn['species']]['family'],base_xl)
        from dittobuffer.gameplay.reward_calendar import catch as catch_rewards
        score_rows=catch_rewards(self,db,player,request,pokemon,reward_attempts)
        if first_registration and self.config.get('pokedex_entry_xp',1000):
            score_rows.append([p.HoloActivityType_ActivityPokedexEntryNew,self.config.get('pokedex_entry_xp',1000),0,0])
        from dittobuffer.gameplay.liveops.runtime import score as live_score
        score_rows=live_score(self,score_rows)
        catch_xp=sum(v[1] for v in score_rows);catch_candy=sum(v[2] for v in score_rows);catch_dust=sum(v[3] for v in score_rows)
        db.execute('UPDATE players SET xp=xp+?,stardust=stardust+?,captures=captures+1 WHERE id=?',
                   (catch_xp+mega_xp,catch_dust,player))
        db.execute('INSERT INTO candy VALUES(?,?,?) ON CONFLICT(player_id,family) DO UPDATE SET count=count+excluded.count',
                   (player,self.species[spawn['species']]['family'],catch_candy+mega_candy))
        out=p.CatchPokemonOutProto(status=1,captured_pokemon_id=cursor.lastrowid,display_pokedex_id=spawn['species'],capture_reason=1)
        out.pokemon_display.CopyFrom(pokemon.pokemon_display)
        out.display_pokemon_display.CopyFrom(pokemon.pokemon_display)
        for activity,xp,candy,dust in score_rows:
            out.scores.activity_type.append(activity);out.scores.exp.append(xp);out.scores.candy.append(candy);out.scores.stardust.append(dust)
        # 0.429.2 EncounterSummary.SetScores (0x86CBEB4) reads the nested
        # temporary-evolution breakdown. Legacy Mega scalar fields are ignored.
        # Base lists stay unchanged; native rollup adds this bonus exactly once.
        if mega_candy or mega_xp:
            active_mega=db.execute('SELECT temp_evo FROM mega_active WHERE player_id=?',(player,)).fetchone()
            out.scores.temp_evo_score_info.CopyFrom(p.TempEvoScoreInfo(active_temp_evo_id=active_mega['temp_evo'],
                candy_from_active_temp_evo=mega_candy,experience_from_active_temp_evo=mega_xp))
        if mega_xl+base_xl:out.scores.xl_candy.append(mega_xl+base_xl)
        db.execute("UPDATE encounters SET state='caught',result=? WHERE player_id=? AND spawn_id=?",(out.SerializeToString(),player,spawn['id']))
        if scripted:
            from dittobuffer.gameplay.special_encounters import captured
            captured(db,player,spawn['id'],pokemon.id,now_ms())
        from dittobuffer.gameplay.vivillon import caught as butterfly_caught
        butterfly_caught(self,db,player,spawn['id'],pokemon.id)
        progress(self,db,player,'catch',context={'form':pokemon.pokemon_display.form,'gender':pokemon.pokemon_display.gender,'alignment':pokemon.pokemon_display.alignment,'max_mode':pokemon.pokemon_display.bread_mode_enum,'costume':pokemon.pokemon_display.costume,'source':'raid' if raid else 'research' if reward else 'rocket' if rocket_spawn else 'max' if max_reward else 'wild','species':pokemon.pokemon_id,'types':self.species[pokemon.pokemon_id]['types'],'shiny':pokemon.pokemon_display.shiny,'weather_boosted':bool(pokemon.pokemon_display.weather_boosted_condition),'quality':request.normalized_reticle_size,'curveball':request.spin_modifier>=.5,'ball':request.pokeball})
        if native_rocket:progress(self,db,player,'shadow_catch')
        from dittobuffer.gameplay.trainer import event
        event(self,db,player,'catch:'+str(pokemon.id),'catch',dict(pokemon=pokemon.SerializeToString().hex(),xp=catch_xp+mega_xp,dust=catch_dust,candy=catch_candy+mega_candy))
        return out

    def rpc_release_pokemon(self, db, player, body):
        from dittobuffer.core.management import release
        return release(self,db,player,body)

    def rpc_use_item_move_reroll(self,db,player,body):
        from dittobuffer.gameplay.pokemon_moves import reroll
        return reroll(self,db,player,body)

    def rpc_unlock_special_move(self,db,player,body):
        from dittobuffer.gameplay.pokemon_moves import unlock
        return unlock(self,db,player,body)

    def rpc_set_favorite_pokemon(self,db,player,body):
        from dittobuffer.core.management import favorite
        return favorite(self,db,player,body)

    def rpc_nickname_pokemon(self,db,player,body):
        from dittobuffer.core.management import nickname
        return nickname(self,db,player,body)

    def rpc_recycle_inventory_item(self,db,player,body):
        from dittobuffer.gameplay.items import recycle
        return recycle(self,db,player,body)

    def rpc_use_item_encounter(self,db,player,body):
        from dittobuffer.gameplay.items import berry
        return berry(self,db,player,body)

    def rpc_use_item_potion(self,db,player,body):
        from dittobuffer.gameplay.items import potion
        return potion(self,db,player,body)

    def rpc_use_item_egg_incubator(self,db,player,body):
        from dittobuffer.gameplay.eggs import assign
        return assign(self,db,player,body)

    def rpc_get_hatched_eggs(self,db,player,body):
        from dittobuffer.gameplay.eggs import hatched
        return hatched(self,db,player,body)

    def rpc_use_item_revive(self,db,player,body):
        from dittobuffer.gameplay.items import revive
        return revive(self,db,player,body)

    def rpc_use_item_bulk_heal(self,db,player,body):
        from dittobuffer.gameplay.items import bulk_heal
        return bulk_heal(self,db,player,body)

    def rpc_upgrade_pokemon(self, db, player, body):
        from dittobuffer.core.management import upgrade
        return upgrade(self,db,player,body)

    def rpc_mega_evolve_pokemon(self,db,player,body):
        from dittobuffer.gameplay.mega import evolve
        return evolve(self,db,player,body)

    def rpc_get_mega_level_up_preview(self,db,player,body):
        from dittobuffer.gameplay.mega import level_preview
        return level_preview(self,db,player,body)

    def rpc_mega_level_up(self,db,player,body):
        from dittobuffer.gameplay.mega import level_up
        return level_up(self,db,player,body)

    def rpc_unlock_temporary_evolution_level(self,db,player,body):
        from dittobuffer.gameplay.mega import unlock_level
        return unlock_level(self,db,player,body)

    def rpc_purify_pokemon(self, db, player, body):
        if self.rocket_native_enabled:
            from dittobuffer.gameplay.rocket_shadow import purify
            return purify(self,db,player,body)
        from dittobuffer.core.management import purify
        return purify(self,db,player,body)

    def rpc_evolve_pokemon(self, db, player, body):
        from dittobuffer.core.management import evolve
        return evolve(self,db,player,body)

    def rpc_level_up_rewards(self, db, player, body):
        requested=p.LevelUpRewardsProto.FromString(body).level
        from dittobuffer.gameplay.progression import bounds
        current=bounds(self.config,self.player(db,player)['xp'])[0]
        if not 2<=requested<=current:raise RpcError('Level reward requires an earned level')
        pending=[level for level in range(2,requested+1) if not db.execute(
            'SELECT 1 FROM level_reward_claims WHERE player_id=? AND level=?',(player,level)).fetchone()]
        if not pending:return p.LevelUpRewardsOutProto(result=2)
        out=p.LevelUpRewardsOutProto(result=3 if len(pending)>1 else 1)
        awarded={}
        for level in pending:
            from dittobuffer.gameplay.level_research import reward as modern_reward
            modern=modern_reward(self,db,player,level,out)
            for item,count in (modern if modern is not None else self.config.get('level_up_rewards',{}).get(str(level),{})).items():
                awarded[int(item)]=awarded.get(int(item),0)+count
            db.execute('INSERT INTO level_reward_claims VALUES(?,?)',(player,level))
            from dittobuffer.gameplay.trainer_rewards import trigger
            for article in trigger(self,db,player,'level',level,str(level)):
                out.neutral_avatar_item_templates.add(item_template_id=article,display_template_id='N_DISPLAY_'+article)
        for item,count in awarded.items():
            # Earned level rewards may exceed capacity, like catch rewards.
            if item in (901,902):
                from dittobuffer.gameplay.eggs import grant
                grant(db,player,item,count)
            else:
                db.execute('INSERT INTO items VALUES(?,?,?) ON CONFLICT(player_id,item) DO UPDATE SET count=count+excluded.count',
                           (player,item,count))
            out.items.add(item=item,item_count=count)
        self.touch(db,player)
        return out

    def rpc_fort_search(self, db, player, body):
        request=p.FortSearchProto.FromString(body)
        fort=db.execute('SELECT * FROM forts WHERE id=?',(request.id,)).fetchone()
        if not fort or fort['kind'] not in (0,1):
            raise RpcError('Unknown Gym or Pokestop')
        place=self.world.poi(fort['id'])
        if self.world.path and (not place or place['status']!='APPROVED'):raise RpcError('Pokestop is disabled')
        if distance(request.player_lat_degrees,request.player_lng_degrees,fort['lat'],fort['lng'])>self.config['interaction_range_meters']:
            return p.FortSearchOutProto(result=2)
        old=db.execute('SELECT * FROM fort_visits WHERE player_id=? AND fort_id=?',(player,fort['id'])).fetchone()
        if old and old['cooldown_ms']>now_ms():
            return p.FortSearchOutProto(result=3,cooldown_complete=old['cooldown_ms'])
        total=self.bag_count(db,player)
        from dittobuffer.gameplay.progression import bounds
        trainer_level=bounds(self.config,self.player(db,player)['xp'])[0]
        from dittobuffer.world.world_rewards import max_awards
        from dittobuffer.gameplay.reward_calendar import spin as spin_bonus
        bonus_xp,bonus_items,streak_day=spin_bonus(self,db,player)
        if total+max_awards(self.config,trainer_level)+sum(bonus_items.values())>self.capacity(db,player,'item_capacity'):
            return p.FortSearchOutProto(result=4)
        cooldown=now_ms()+self.config['fort_cooldown_seconds']*1000
        bonus_xp,bonus_items,streak_day=spin_bonus(self,db,player,True)
        from dittobuffer.gameplay.gyms import rules as gym_rules
        xp=(gym_rules(self)['spin_xp'] if fort['kind']==0 else self.config.get('fort_xp',50))+bonus_xp
        out=p.FortSearchOutProto(result=1,xp_awarded=xp,cooldown_complete=cooldown,fort_id=fort['id'])
        rewards={1:3}
        if place:
            from dittobuffer.world.world_rewards import awards
            rewards=awards(self,player,fort['id'],cooldown,trainer_level)
        if streak_day:
            for item,count in bonus_items.items():
                out.bonus_loot.loot_item.add(item=item,count=count)
                rewards[item]=rewards.get(item,0)+count
        for item,count in rewards.items():
            out.items.add(item=item,item_count=count)
            db.execute('INSERT INTO items VALUES(?,?,?) ON CONFLICT(player_id,item) DO UPDATE SET count=count+excluded.count',(player,item,count))
        db.execute('UPDATE players SET xp=xp+?,stops=stops+1 WHERE id=?',(xp,player))
        self.touch(db,player)
        from dittobuffer.gameplay.eggs import award
        egg=award(self,db,player,fort)
        if egg is not None:out.egg_pokemon.CopyFrom(egg)
        if fort['kind']==0:
            from dittobuffer.gameplay.gyms import badge
            out.awarded_gym_badge.CopyFrom(badge(self,db,player,fort))
        from dittobuffer.gameplay.gifts import award as award_gift
        gift=award_gift(self,db,player,fort)
        if gift is not None:out.gift_box.CopyFrom(gift)
        from dittobuffer.gameplay.research import progress,award_field
        progress(self,db,player,'spin')
        task=award_field(self,db,player,fort) if fort['kind']==1 else None
        if task is not None:out.challenge_quest.CopyFrom(task)
        db.execute('INSERT INTO fort_visits VALUES(?,?,?,?) ON CONFLICT(player_id,fort_id) DO UPDATE SET cooldown_ms=excluded.cooldown_ms,result=excluded.result',
                   (player,fort['id'],cooldown,out.SerializeToString()))
        from dittobuffer.gameplay.trainer import event
        event(self,db,player,'spin:'+fort['id']+':'+str(self.player(db,player)['stops']),'spin',dict(fort_id=fort['id'],xp=xp,items=[dict(item=i,count=n) for i,n in rewards.items()],egg=egg.SerializeToString().hex() if egg is not None else None))
        if old is None:
            from dittobuffer.gameplay.buddy import activity
            activity(self,db,player,'explore',fort['id'])
        return out

    def rpc_set_player_team(self,db,player,body):
        from dittobuffer.gameplay.gyms import select_team
        return select_team(self,db,player,body)

    def rpc_gym_get_info(self,db,player,body):
        from dittobuffer.gameplay.gyms import info
        return info(self,db,player,body)

    def rpc_gym_deploy(self,db,player,body):
        from dittobuffer.gameplay.gyms import deploy
        return deploy(self,db,player,body)

    def rpc_gym_feed_pokemon(self,db,player,body):
        from dittobuffer.gameplay.gyms import feed
        return feed(self,db,player,body)

    def rpc_list_gym_badges(self,db,player,body):
        from dittobuffer.gameplay.gyms import list_badges
        return list_badges(self,db,player,body)

    def rpc_get_gym_badge_details(self,db,player,body):
        from dittobuffer.gameplay.gyms import badge_details
        return badge_details(self,db,player,body)

    def rpc_gym_start_session(self,db,player,body):
        from dittobuffer.gameplay.gym_battle import start
        return start(self,db,player,body)

    def rpc_gym_battle_attack(self,db,player,body):
        from dittobuffer.gameplay.gym_battle import attack
        return attack(self,db,player,body)

    def rpc_get_raid_details(self,db,player,body):
        from dittobuffer.gameplay.raids import details
        return details(self,db,player,body)

    def rpc_get_simple_raid_details(self,db,player,body):
        from dittobuffer.gameplay.raids import simple_details
        return simple_details(self,db,player,body)

    def rpc_join_lobby(self,db,player,body):
        from dittobuffer.gameplay.raids import join
        return join(self,db,player,body)

    def rpc_leave_lobby(self,db,player,body):
        from dittobuffer.gameplay.raids import leave
        return leave(self,db,player,body)

    def rpc_get_raid_lobby_details(self,db,player,body):
        from dittobuffer.gameplay.raids import lobby_details
        return lobby_details(self,db,player,body)

    def rpc_award_free_raid_ticket(self,db,player,body):
        from dittobuffer.gameplay.raids import award_pass
        return award_pass(self,db,player,body)

    def rpc_start_raid_battle(self,db,player,body):
        from dittobuffer.gameplay.raids import start
        return start(self,db,player,body)

    def rpc_attack_raid(self,db,player,body):
        from dittobuffer.gameplay.raids import attack
        return attack(self,db,player,body)

    def rpc_complete_raid_battle(self,db,player,body):
        from dittobuffer.gameplay.raids import complete
        return complete(self,db,player,body)

    def rpc_sync_battle_inventory(self,db,player,body):
        from dittobuffer.gameplay.raids import sync_inventory
        return sync_inventory(self,db,player,body)

    def rpc_get_raid_encounter_details(self,db,player,body):
        from dittobuffer.gameplay.raids import encounter_details
        return encounter_details(self,db,player,body)

    def rpc_get_combat_player_profile(self,db,player,body):
        p.GetCombatPlayerProfileProto.FromString(body)
        from dittobuffer.gameplay.gyms import public
        out=p.GetCombatPlayerProfileOutProto(result=1)
        out.profile.CopyFrom(p.CombatPlayerProfileProto(player_id=player,player_nia_id=player,public_profile=public(self,db,player),combat_league_template_id=['COMBAT_LEAGUE_DEFAULT_MASTER_NPC']))
        return out

    def rpc_get_mp_summary(self,db,player,body):
        from dittobuffer.gameplay.max_system import summary
        return summary(self,db,player,body)

    def rpc_start_mp_walk_quest(self,db,player,body):
        from dittobuffer.gameplay.max_system import start_walk
        return start_walk(self,db,player,body)

    def rpc_enhance_bread_move(self,db,player,body):
        from dittobuffer.gameplay.max_system import enhance
        return enhance(self,db,player,body)

    def rpc_loot_station(self,db,player,body):
        from dittobuffer.gameplay.max_system import loot
        return loot(self,db,player,body)

    def rpc_get_station_info(self,db,player,body):
        from dittobuffer.gameplay.max_system import info
        return info(self,db,player,body)

    def rpc_get_station_details(self,db,player,body):
        from dittobuffer.gameplay.max_station import details
        return details(self,db,player,body)

    def rpc_station_pokemon(self,db,player,body):
        from dittobuffer.gameplay.max_station import assign
        return assign(self,db,player,body)

    def rpc_release_stationed_pokemon(self,db,player,body):
        from dittobuffer.gameplay.max_station import release
        return release(self,db,player,body)

    def rpc_get_num_station_assists(self,db,player,body):
        from dittobuffer.gameplay.max_station import statistics
        return statistics(self,db,player,body)

    def rpc_get_bread_lobby_details(self,db,player,body):
        from dittobuffer.gameplay.max_system import lobby_details
        return lobby_details(self,db,player,body)

    def rpc_join_bread_lobby(self,db,player,body):
        from dittobuffer.gameplay.max_battle import join
        return join(self,db,player,body)

    def rpc_prepare_bread_lobby(self,db,player,body):
        from dittobuffer.gameplay.max_battle import prepare
        return prepare(self,db,player,body)

    def rpc_leave_bread_lobby(self,db,player,body):
        from dittobuffer.gameplay.max_battle import leave
        return leave(self,db,player,body)

    def rpc_start_bread_battle(self,db,player,body):
        from dittobuffer.gameplay.max_battle import start
        return start(self,db,player,body)

    def rpc_complete_bread_battle(self,db,player,body):
        from dittobuffer.gameplay.max_battle import complete
        return complete(self,db,player,body)

    def rpc_rejoin_battle_check(self,db,player,body):
        from dittobuffer.gameplay.max_battle import rejoin_status
        return rejoin_status(self,db,player,body)

    def bag_count(self,db,player):
        # Zygarde Cells and their non-discardable Cube use separate storage.
        return db.execute('SELECT COALESCE(SUM(count),0) FROM items WHERE player_id=? AND item NOT IN (650,651)',(player,)).fetchone()[0]


    def rpc_get_routes(self,db,player,body):
        from dittobuffer.gameplay.routes import nearby
        return nearby(self,db,player,body)


    def rpc_change_pokemon_form(self,db,player,body):
        from dittobuffer.gameplay.route_cells import change_form
        return change_form(self,db,player,body)


    def rpc_get_change_pokemon_form_preview(self,db,player,body):
        from dittobuffer.gameplay.route_cells import change_form
        return change_form(self,db,player,body,preview=True)


    def rpc_butterfly_collector_reward_encounter(self,db,player,body):
        from dittobuffer.gameplay.vivillon import encounter
        return encounter(self,db,player,body)


    def rpc_start_route(self,db,player,body):
        from dittobuffer.gameplay.routes import start
        return start(self,db,player,body)


    def rpc_progress_route(self,db,player,body):
        from dittobuffer.gameplay.routes import progress
        return progress(self,db,player,body)


    def rpc_cancel_route(self,db,player,body):
        from dittobuffer.gameplay.routes import cancel
        return cancel(self,db,player,body)


    def rpc_list_route_badges(self,db,player,body):
        from dittobuffer.gameplay.routes import list_badges
        return list_badges(self,db,player,body)


    def rpc_list_route_stamps(self,db,player,body):
        p.ListRouteStampsProto.FromString(body)
        return p.ListRouteStampsOutProto()


    def rpc_get_published_routes(self,db,player,body):
        p.GetPublishedRoutesProto.FromString(body)
        out=p.GetPublishedRoutesOutProto(result=1)
        if self.routes['enabled']:
            for row in db.execute("SELECT proto FROM routes WHERE creator=? AND status='PUBLISHED' ORDER BY id LIMIT 100",(player,)):out.routes.add().ParseFromString(row[0])
        return out


    def rpc_get_route_creations(self,db,player,body):
        from dittobuffer.gameplay.route_creator import inventory
        return inventory(self,db,player)


    def rpc_create_route_draft(self,db,player,body):
        from dittobuffer.gameplay.route_creator import create
        return create(self,db,player,body)


    def rpc_update_route_draft(self,db,player,body):
        from dittobuffer.gameplay.route_creator import update
        return update(self,db,player,body)


    def rpc_submit_route_draft(self,db,player,body):
        from dittobuffer.gameplay.route_creator import submit
        return submit(self,db,player,body)


    def rpc_delete_route_draft(self,db,player,body):
        from dittobuffer.gameplay.route_creator import delete
        return delete(self,db,player,body)


    def rpc_get_route_draft(self,db,player,body):
        from dittobuffer.gameplay.route_creator import get
        return get(self,db,player,body)


    def rpc_npc_route_gift(self,db,player,body):
        from dittobuffer.gameplay.route_mateo import route_gifts
        return route_gifts(self,db,player,body)


    def rpc_npc_update_state(self,db,player,body):
        from dittobuffer.gameplay.route_mateo import update
        return update(self,db,player,body)


    def rpc_npc_send_gift(self,db,player,body):
        from dittobuffer.gameplay.route_mateo import send
        return send(self,db,player,body)


    def rpc_npc_open_gift(self,db,player,body):
        from dittobuffer.gameplay.route_mateo import open_gift
        return open_gift(self,db,player,body)


    def rpc_get_bonus_attracted_pokemon(self,db,player,body):
        # Native Route tracking batches this poll with pause/progress. There
        # are no configured bonus spawns yet; an empty success is authoritative.
        p.GetBonusAttractedPokemonProto.FromString(body)
        return p.GetBonusAttractedPokemonOutProto(result=1)


    def rpc_update_breadcrumb_history(self,db,player,body):
        # Foreground Route clients also flush this ancillary history. Distance
        # is already validated from the current native transport fix; accepting
        # this bounded duplicate history must never credit it a second time.
        # Background/Fitness history is explicitly unsupported here.
        req=p.UpdateBreadcrumbHistoryRequestProto.FromString(body)
        valid=self.routes['enabled'] and len(req.breadcrumb_history)<=512
        previous=0
        for fix in req.breadcrumb_history:
            # Accuracy is absent (=0) in the native manual breadcrumb path.
            # Queued older records may be flushed after reconnect. Neither
            # is suitable to grant distance, but both can be acknowledged.
            valid=valid and fix.app_is_foregrounded and previous<fix.timestamp_ms<=now_ms()+2000 and all(math.isfinite(v) for v in (fix.latitude_deg,fix.longitude_deg,fix.accuracy_m)) and -85<=fix.latitude_deg<=85 and -180<=fix.longitude_deg<=180 and 0<=fix.accuracy_m<=self.routes['max_accuracy_m']
            previous=fix.timestamp_ms
        if not valid and self.routes['allow_debug_fixture']:
            import logging
            logging.info('Routes isolated history rejected: count=%d foreground=%s age_ms=%s accuracy=%s',len(req.breadcrumb_history),[v.app_is_foregrounded for v in req.breadcrumb_history[:3]],[now_ms()-v.timestamp_ms for v in req.breadcrumb_history[:3]],[v.accuracy_m for v in req.breadcrumb_history[:3]])
        return p.UpdateBreadcrumbHistoryResponseProto(status=1 if valid else 2)
