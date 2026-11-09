"""Source-bound temporary evolution; owned identity/stats remain ordinary."""
import json
import math
import secrets
from pathlib import Path
from dittobuffer.gameplay.game import p, now_ms
from dittobuffer.gameplay.species import apply_stats, multiplier, rules_for
from protocol.opaque import fields, byte_field
from dittobuffer.paths import ROOT

DATA = json.loads(Path(__file__).with_name('mega-rules.json').read_text())
SPECIES = {int(k): v for k, v in DATA['species'].items()}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS mega_energy(
 player_id TEXT NOT NULL REFERENCES players(id),species INTEGER NOT NULL,temp_evo INTEGER NOT NULL,
 amount INTEGER NOT NULL CHECK(amount>=0),PRIMARY KEY(player_id,species,temp_evo));
CREATE TABLE IF NOT EXISTS mega_progress(
 pokemon_id INTEGER NOT NULL REFERENCES pokemon(id) ON DELETE CASCADE,temp_evo INTEGER NOT NULL,
 points INTEGER NOT NULL DEFAULT 0,level INTEGER NOT NULL DEFAULT 0,day INTEGER NOT NULL DEFAULT -1,
 daily_count INTEGER NOT NULL DEFAULT 0,evolutions INTEGER NOT NULL DEFAULT 0,
 finish_ms INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(pokemon_id,temp_evo));
CREATE TABLE IF NOT EXISTS mega_active(
 player_id TEXT PRIMARY KEY REFERENCES players(id),pokemon_id INTEGER UNIQUE NOT NULL REFERENCES pokemon(id) ON DELETE CASCADE,
 temp_evo INTEGER NOT NULL,start_ms INTEGER NOT NULL,end_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS mega_ledger(
 player_id TEXT NOT NULL REFERENCES players(id),source TEXT NOT NULL,kind TEXT NOT NULL,
 pokemon_id INTEGER NOT NULL,species INTEGER NOT NULL,temp_evo INTEGER NOT NULL,
 delta INTEGER NOT NULL,balance INTEGER NOT NULL,at_ms INTEGER NOT NULL,PRIMARY KEY(player_id,source));
CREATE TABLE IF NOT EXISTS mega_catch_xl(
 player_id TEXT NOT NULL REFERENCES players(id),family INTEGER NOT NULL,count INTEGER NOT NULL CHECK(count>=0),PRIMARY KEY(player_id,family));
'''


def enabled(game):
    return game.config.get('mega', {}).get('enabled', True)


def validate(game):
    cfg = game.config.get('mega', {})
    if type(cfg.get('enabled', True)) is not bool:
        raise ValueError('Invalid Mega enabled flag')
    overrides = set(cfg) - {'enabled', 'development_only', 'duration_ms', 'cooldown_ms'}
    if overrides:
        raise ValueError('Unknown Mega rules: ' + str(overrides))
    if any(k in cfg for k in ('duration_ms', 'cooldown_ms')):
        if not cfg.get('development_only') or not game.db.path.resolve().is_relative_to(ROOT / 'builds'):
            raise ValueError('Accelerated Mega timing requires an isolated development fixture')
        for k in ('duration_ms', 'cooldown_ms'):
            if k in cfg and (type(cfg[k]) is not int or cfg[k] < 1000):
                raise ValueError('Invalid accelerated Mega timing')


def branch(species, evo):
    return next((b for b in SPECIES.get(species, {}).get('branches', []) if b['temp_evo'] == evo), None)


def separated(species, evo):
    return any(p.TempEvoPokemonBranch.FromString(t).pokedex_id == species and
               p.TempEvoPokemonBranch.FromString(t).temp_evo_id == evo for t in _SEPARATED)


_SETTINGS = p.GameMasterClientTemplateProto.FromString(bytes.fromhex(next(t['hex'] for t in DATA['templates'] if t['id'] == 'MEGA_EVO_SETTINGS'))).mega_evo_settings
_SEPARATED = [v.SerializeToString() for v in _SETTINGS.separated_temp_evo_branches]


def energy_key(species, evo):
    return evo if separated(species, evo) else 1


def balance(db, player, species, evo):
    row = db.execute('SELECT amount FROM mega_energy WHERE player_id=? AND species=? AND temp_evo=?',
                     (player, species, energy_key(species, evo))).fetchone()
    return row['amount'] if row else 0


def grant(game, db, player, species, evo, amount, source):
    if not enabled(game) or not branch(species, evo) or type(amount) is not int or amount < 0 or not source:
        raise ValueError('Invalid Mega Energy grant')
    old = db.execute('SELECT * FROM mega_ledger WHERE player_id=? AND source=?', (player, source)).fetchone()
    key = energy_key(species, evo)
    if old:
        if (old['kind'], old['species'], old['temp_evo']) != ('grant', species, key):
            raise ValueError('Mega grant source identity changed')
        return old['delta']
    current = balance(db, player, species, evo)
    awarded = min(amount, max(0, _SETTINGS.max_candy_hoard_size - current))
    db.execute('INSERT INTO mega_energy VALUES(?,?,?,?) ON CONFLICT(player_id,species,temp_evo) DO UPDATE SET amount=excluded.amount',
               (player, species, key, current + awarded))
    db.execute('INSERT INTO mega_ledger VALUES(?,?,?,?,?,?,?,?,?)',
               (player, source, 'grant', 0, species, key, awarded, current + awarded, now_ms()))
    game.touch(db, player)
    return awarded


def level_settings(species, evo, level):
    matches = [r for r in DATA['levels'] if r['level'] == level and r['species'] in (0, species) and r['temp_evo'] in (0, evo)]
    if not matches:
        return None
    selected = max(matches, key=lambda r: (r['species'] == species, r['temp_evo'] == evo))
    return p.MegaEvolutionLevelSettingsProto.FromString(bytes.fromhex(selected['hex']))


def progress(db, pokemon, evo):
    row = db.execute('SELECT * FROM mega_progress WHERE pokemon_id=? AND temp_evo=?', (pokemon, evo)).fetchone()
    return dict(row) if row else dict(pokemon_id=pokemon, temp_evo=evo, points=0, level=0, day=-1, daily_count=0, evolutions=0, finish_ms=0)


def active(db, pokemon_id):
    return db.execute('SELECT * FROM mega_active WHERE pokemon_id=? AND end_ms>?', (pokemon_id, now_ms())).fetchone()


def duration(game, species, evo, level):
    settings = level_settings(species, evo, level)
    return game.config.get('mega', {}).get('duration_ms', settings.evolution_length_ms or _SETTINGS.evolution_length_ms)


def cooldown(game, species, evo, level):
    return game.config.get('mega', {}).get('cooldown_ms', level_settings(species, evo, level).cooldown.duration_ms)


def end_active(game, db, row, stamp):
    db.execute('UPDATE mega_progress SET finish_ms=? WHERE pokemon_id=? AND temp_evo=?',
               (stamp, row['pokemon_id'], row['temp_evo']))
    db.execute('DELETE FROM mega_active WHERE player_id=?', (row['player_id'],))
    game.touch(db, row['player_id'])


def expire(game, db):
    for row in db.execute('SELECT * FROM mega_active WHERE end_ms<=?', (now_ms(),)).fetchall():
        end_active(game, db, row, row['end_ms'])


def cost(game, db, pokemon, evo):
    b = branch(pokemon.pokemon_id, evo)
    saved = progress(db, pokemon.id, evo)
    if not saved['evolutions']:
        return b['first_cost']
    setting = level_settings(pokemon.pokemon_id, evo, saved['level']).cooldown
    # Native MsSinceLastMegaEvo is server-now minus temporary finish time.
    # Cost interpolation uses duration + the supplied 2s client buffer, but
    # becomes zero exactly at the authoritative cooldown boundary.
    finish = db.execute('SELECT MAX(finish_ms) FROM mega_progress WHERE pokemon_id=?', (pokemon.id,)).fetchone()[0] or 0
    elapsed = max(0, now_ms() - finish)
    total = cooldown(game, pokemon.pokemon_id, evo, saved['level'])
    if elapsed >= total:
        return 0
    fraction = elapsed / (total + _SETTINGS.client_mega_cooldown_buffer_ms)
    value = math.ceil(setting.bypass_cost_initial * (1 - fraction) + setting.bypass_cost_final * fraction)
    step = setting.bypass_cost_rounding_value or 1
    return int(math.floor(value / step + .5) * step)


def project(game, db, pokemon, combat=False):
    out = p.PokemonProto();out.CopyFrom(pokemon)
    # ``pokemon.data`` can retain a temporary evolution that has already
    # expired.  Only mega_active below may advertise a live evolution; stale
    # fields otherwise surface in the client as negative hours remaining.
    out.pokemon_display.current_temp_evolution = 0
    out.pokemon_display.temporary_evolution_finish_ms = 0
    out.pokemon_display.temp_evolution_is_locked = False
    out.pokemon_display.locked_temp_evolution = 0
    out.pokemon_display.ClearField('mega_evolution_level')
    out.temp_evo_cp = 0
    out.temp_evo_cp_multiplier = 0
    out.temp_evo_stamina_modifier = 0
    # Older databases stored CP multipliers from the former level formula.
    # The client derives the power-up level from this value, so reconcile it
    # with the authoritative stored level before serialising the Pokemon.
    saved = db.execute('SELECT level FROM pokemon_levels WHERE pokemon_id=?',
                       (pokemon.id,)).fetchone()
    if saved:
        try:
            rules = rules_for(game, out)
            expected = multiplier(saved['level'], rules.get('cp_multipliers'))
            if abs(out.cp_multiplier - expected) > .0005:
                apply_stats(out, rules, saved['level'], heal=False)
        except (KeyError, ValueError):
            pass
    rows = db.execute('SELECT * FROM mega_progress WHERE pokemon_id=? ORDER BY temp_evo', (pokemon.id,)).fetchall()
    if not rows:
        return out
    out.has_mega_evolved = True
    del out.mega_evolved_forms[:]
    del out.pokemon_display.branched_mega_evolution_levels[:]
    for row in rows:
        if row['evolutions']:
            out.mega_evolved_forms.append(row['temp_evo'])
        target = out.pokemon_display.branched_mega_evolution_levels.add(temp_evolution_id=row['temp_evo']).mega_evolution_level
        target.points = row['points'];target.level = row['level']
        target.mega_point_daily_counters.mega_evo.CopyFrom(p.DailyCounterProto(window=row['day'], count=row['daily_count'], buckets_per_day=1))
    selected = max(rows, key=lambda r: r['finish_ms'])
    out.pokemon_display.mega_evolution_level.CopyFrom(next(v.mega_evolution_level for v in out.pokemon_display.branched_mega_evolution_levels if v.temp_evolution_id == selected['temp_evo']))
    out.pokemon_display.temporary_evolution_finish_ms = selected['finish_ms']
    current = active(db, pokemon.id) if enabled(game) else None
    if current:
        evo = current['temp_evo']
        out.pokemon_display.current_temp_evolution = evo
        out.pokemon_display.temporary_evolution_finish_ms = current['end_ms']
        out.pokemon_display.mega_evolution_level.CopyFrom(next(v.mega_evolution_level for v in out.pokemon_display.branched_mega_evolution_levels if v.temp_evolution_id == evo))
        boosted = p.PokemonProto();boosted.CopyFrom(pokemon)
        from dittobuffer.core.management import level_of
        extra=level_settings(pokemon.pokemon_id,evo,progress(db,pokemon.id,evo)['level']).effects.self_cp_boost_additional_level
        apply_stats(boosted, temporary_rules(game, pokemon, evo), min(40,level_of(db, pokemon)+extra))
        out.temp_evo_cp = boosted.cp
        out.temp_evo_stamina_modifier = boosted.max_stamina / pokemon.max_stamina
        out.temp_evo_cp_multiplier = boosted.cp_multiplier
        if combat:
            out.cp = boosted.cp;out.max_stamina = boosted.max_stamina
            out.cp_multiplier=boosted.cp_multiplier
            out.stamina = min(out.max_stamina, math.ceil(pokemon.stamina * out.temp_evo_stamina_modifier))
            out.pokemon_display.temp_evolution_is_locked = True
            out.pokemon_display.locked_temp_evolution = evo
    return out


def temporary_rules(game, pokemon, evo):
    b = branch(pokemon.pokemon_id, evo)
    if not b:
        raise ValueError('Unsupported Mega combat identity')
    result = dict(game.forms[pokemon.pokemon_id, SPECIES[pokemon.pokemon_id]['form']])
    override = p.TempEvoOverrideProto.FromString(bytes.fromhex(b['override_hex']))
    result.update(stamina=override.stats.base_stamina, attack=override.stats.base_attack,
                  defense=override.stats.base_defense, types=[override.type_override1, override.type_override2])
    return result


def evolve(game, db, player, body):
    from dittobuffer.core.management import owned
    request = p.MegaEvolvePokemonProto.FromString(body)
    pokemon = owned(db, player, request.pokemon_id)
    fail = lambda n: p.MegaEvolvePokemonOutProto(result=n)
    if not pokemon:
        return fail(2)
    b = branch(pokemon.pokemon_id, request.temp_evo_id)
    if not enabled(game) or not b or pokemon.is_egg or pokemon.pokemon_display.form not in (0, SPECIES[pokemon.pokemon_id]['form']) or pokemon.pokemon_display.costume or pokemon.pokemon_display.alignment == 1 or not pokemon.stamina:
        return fail(4)
    if pokemon.deployed_fort_id or pokemon.deployed_station_id:
        return fail(5)
    from dittobuffer.gameplay.gyms import battle_busy
    previous_active=db.execute('SELECT pokemon_id FROM mega_active WHERE player_id=?',(player,)).fetchone()
    if battle_busy(db,player,[pokemon.id]+([previous_active['pokemon_id']] if previous_active else [])):
        return fail(5)
    if b['move_requirement'] and b['move_requirement'] not in (pokemon.move1, pokemon.move2, pokemon.move3):
        return fail(6)
    current = active(db, pokemon.id)
    if current and current['temp_evo'] == request.temp_evo_id:
        return fail(7)
    charge = cost(game, db, pokemon, request.temp_evo_id)
    if balance(db, player, pokemon.pokemon_id, request.temp_evo_id) < charge:
        return fail(3)
    if request.preview:
        from dittobuffer.core.management import level_of
        preview = p.PokemonProto();preview.CopyFrom(pokemon)
        apply_stats(preview, temporary_rules(game, pokemon, request.temp_evo_id), level_of(db, pokemon))
        shown=p.PokemonProto();shown.CopyFrom(pokemon)
        shown.pokemon_display.current_temp_evolution=request.temp_evo_id
        shown.pokemon_display.temporary_evolution_finish_ms=now_ms()+duration(game,pokemon.pokemon_id,request.temp_evo_id,progress(db,pokemon.id,request.temp_evo_id)['level'])
        shown.temp_evo_cp=preview.cp;shown.temp_evo_stamina_modifier=preview.max_stamina/pokemon.max_stamina
        shown.temp_evo_cp_multiplier=preview.cp_multiplier
        out=p.MegaEvolvePokemonOutProto(result=1,evolved_pokemon=shown)
        out.preview.default_cp_range.CopyFrom(p.CpRange(min_cp=preview.cp,max_cp=preview.cp))
        out.preview.buddy_boosted_cp_range.CopyFrom(out.preview.default_cp_range)
        out.preview.evolving_pokemon_default_cp=pokemon.cp
        out.preview.evolving_pokemon_buddy_boosted_cp=pokemon.cp
        return out
    stamp = now_ms();key = energy_key(pokemon.pokemon_id, request.temp_evo_id)
    if charge:
        db.execute('UPDATE mega_energy SET amount=amount-? WHERE player_id=? AND species=? AND temp_evo=?', (charge, player, pokemon.pokemon_id, key))
    old = db.execute('SELECT * FROM mega_active WHERE player_id=?', (player,)).fetchone()
    if old:
        end_active(game, db, old, stamp)
    saved = progress(db, pokemon.id, request.temp_evo_id)
    current_settings = level_settings(pokemon.pokemon_id, request.temp_evo_id, saved['level'])
    day = stamp // 86400000
    count = saved['daily_count'] if saved['day'] == day else 0
    points = saved['points']
    if count < current_settings.progression.points_limit_per_period:
        points += current_settings.progression.points_per_mega_evo_action;count += 1
    level = saved['level']
    for target in range(level + 1, 5):
        target_settings = level_settings(pokemon.pokemon_id, request.temp_evo_id, target)
        if target_settings and not target_settings.mega_energy_cost_to_unlock and target_settings.progression.points_required <= points:
            level = target
    db.execute('INSERT INTO mega_progress VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(pokemon_id,temp_evo) DO UPDATE SET points=excluded.points,level=excluded.level,day=excluded.day,daily_count=excluded.daily_count,evolutions=excluded.evolutions',
               (pokemon.id, request.temp_evo_id, points, level, day, count, saved['evolutions'] + 1, saved['finish_ms']))
    end = stamp + duration(game, pokemon.pokemon_id, request.temp_evo_id, level)
    db.execute('INSERT INTO mega_active VALUES(?,?,?,?,?)', (player, pokemon.id, request.temp_evo_id, stamp, end))
    db.execute('INSERT INTO mega_ledger VALUES(?,?,?,?,?,?,?,?,?)', (player, 'evolve:'+str(pokemon.id)+':'+str(request.temp_evo_id)+':'+str(saved['evolutions']+1),
               'evolve', pokemon.id, pokemon.pokemon_id, key, -charge, balance(db, player, pokemon.pokemon_id, request.temp_evo_id), stamp))
    game.touch(db, player)
    from dittobuffer.gameplay.trainer import event
    event(game,db,player,'mega:'+str(pokemon.id)+':'+str(request.temp_evo_id)+':'+str(saved['evolutions']+1),'mega_evolve',{'species':pokemon.pokemon_id,'pokemon':pokemon.SerializeToString().hex()})
    return p.MegaEvolvePokemonOutProto(result=1, evolved_pokemon=project(game, db, pokemon))


def templates(config):
    if not config.get('mega', {}).get('enabled', True):
        return []
    values = [p.GameMasterClientTemplateProto.FromString(bytes.fromhex(v['hex'])) for v in DATA['templates']]
    for t in values:
        if t.template_id == 'MEGA_EVO_SETTINGS' and 'duration_ms' in config.get('mega', {}):
            t.mega_evo_settings.evolution_length_ms = config['mega']['duration_ms']
        if t.HasField('mega_evo_level_settings') and 'cooldown_ms' in config.get('mega', {}):
            t.mega_evo_level_settings.cooldown.duration_ms = config['mega']['cooldown_ms']
    return values


def augment_template(raw, config):
    if not config.get('mega', {}).get('enabled', True):
        return raw
    outer = list(fields(raw));data = next((v for n, k, _, v in outer if n == 2 and k == 2), None)
    if data is None:
        return raw
    values = {n: v for n, k, _, v in fields(data)}
    entry = SPECIES.get(values.get(1))
    if not entry or values.get(28, 0) not in (0, entry['form']):
        return raw
    existing_branches = {dict((a,b) for a,c,_,b in fields(v)).get(14) for n,k,_,v in fields(data) if n==26 and k==2}
    existing_overrides = {dict((a,b) for a,c,_,b in fields(v)).get(1) for n,k,_,v in fields(data) if n==51 and k==2}
    for b in entry['branches']:
        if b['temp_evo'] not in existing_branches:
            data += byte_field(26, bytes.fromhex(b['branch_hex']))
        if b['temp_evo'] not in existing_overrides:
            data += byte_field(51, bytes.fromhex(b['override_hex']))
    return b''.join(byte_field(2, data) if n == 2 and k == 2 else wire
                    for n, k, wire, value in outer)


def inventory_resources(game, db, player, families):
    for row in db.execute('SELECT * FROM mega_catch_xl WHERE player_id=?',(player,)):
        if row['family'] not in families:families[row['family']]=p.PokemonFamilyProto(family_id=row['family'])
        families[row['family']].xl_candy=row['count']
    for row in db.execute('SELECT * FROM mega_energy WHERE player_id=? ORDER BY species,temp_evo', (player,)):
        if not separated(row['species'],row['temp_evo']):
            legacy=p.HoloInventoryItemProto()
            legacy.mega_evolve_species.CopyFrom(p.MegaEvolvePokemonSpeciesProto(energy_count=row['amount'],pokemon_species_id=row['species']))
            yield legacy
        item = p.HoloInventoryItemProto()
        item.pokemon_temp_evo_resource.energy_count = row['amount']
        item.pokemon_temp_evo_resource.temp_evo_pokemon_branch.CopyFrom(p.TempEvoPokemonBranch(pokedex_id=row['species'], temp_evo_id=row['temp_evo']))
        yield item
        family = SPECIES[row['species']]['family']
        if family not in families:
            families[family] = p.PokemonFamilyProto(family_id=family)
        families[family].mega_evolution_resources.add(temporary_evolution_id=row['temp_evo'], energy_count=row['amount'], max_energy_count=_SETTINGS.max_candy_hoard_size)


def level_cost(pokemon, evo, saved, target):
    settings = level_settings(pokemon.pokemon_id, evo, target)
    if not saved['evolutions'] or target <= saved['level'] or settings is None:
        return None
    if settings.mega_energy_cost_to_unlock:
        return settings.mega_energy_cost_to_unlock if saved['level'] == target-1 and saved['points'] >= settings.progression.points_required else None
    if settings.mega_energy_cost_per_point:
        return max(0, settings.progression.points_required-saved['points']) * settings.mega_energy_cost_per_point
    return None


def level_preview(game, db, player, body):
    from dittobuffer.core.management import owned
    req=p.GetMegaLevelUpPreviewProto.FromString(body)
    pokemon=owned(db,player,req.pokemon_id)
    if pokemon is None:return p.GetMegaLevelUpPreviewOutProto(result=2)
    if not enabled(game) or not branch(pokemon.pokemon_id,req.temp_evo_id):return p.GetMegaLevelUpPreviewOutProto(result=3)
    saved=progress(db,pokemon.id,req.temp_evo_id)
    out=p.GetMegaLevelUpPreviewOutProto(result=1)
    for target in range(saved['level']+1,5):
        charge=level_cost(pokemon,req.temp_evo_id,saved,target)
        if charge is not None:
            out.bulk_level_up_cost_table.add(number_of_level_up=target-saved['level'],target_mega_level=target,total_mega_energy_cost=charge)
    return out


def level_up(game, db, player, body):
    from dittobuffer.core.management import owned
    req=p.MegaLevelUpProto.FromString(body);pokemon=owned(db,player,req.pokemon_id)
    fail=lambda n:p.MegaLevelUpOutProto(result=n)
    if pokemon is None:return fail(2)
    if not enabled(game) or not branch(pokemon.pokemon_id,req.temp_evo_id):return fail(3)
    from dittobuffer.gameplay.gyms import battle_busy
    if battle_busy(db,player,[pokemon.id]):return fail(6)
    saved=progress(db,pokemon.id,req.temp_evo_id)
    if req.current_mega_level!=saved['level']:return fail(5)
    charge=level_cost(pokemon,req.temp_evo_id,saved,req.target_mega_level)
    if charge is None:return fail(4)
    if balance(db,player,pokemon.pokemon_id,req.temp_evo_id)<charge:return fail(7)
    key=energy_key(pokemon.pokemon_id,req.temp_evo_id)
    db.execute('UPDATE mega_energy SET amount=amount-? WHERE player_id=? AND species=? AND temp_evo=?',(charge,player,pokemon.pokemon_id,key))
    target=level_settings(pokemon.pokemon_id,req.temp_evo_id,req.target_mega_level)
    db.execute('UPDATE mega_progress SET points=MAX(points,?),level=? WHERE pokemon_id=? AND temp_evo=?',
               (target.progression.points_required,req.target_mega_level,pokemon.id,req.temp_evo_id))
    remaining=balance(db,player,pokemon.pokemon_id,req.temp_evo_id)
    db.execute('INSERT INTO mega_ledger VALUES(?,?,?,?,?,?,?,?,?)',(player,'level:'+str(pokemon.id)+':'+str(req.temp_evo_id)+':'+str(req.target_mega_level),
               'level',pokemon.id,pokemon.pokemon_id,key,-charge,remaining,now_ms()))
    game.touch(db,player)
    return p.MegaLevelUpOutProto(result=1,mega_level_after=req.target_mega_level,mega_energy_balance_after=remaining,
        total_mega_energy_cost=charge,updated_pokemon=project(game,db,pokemon))


def unlock_level(game, db, player, body):
    request=p.UnlockTemporaryEvolutionLevelProto.FromString(body)
    from dittobuffer.core.management import owned
    pokemon=owned(db,player,request.pokemon_id)
    if not enabled(game):return p.UnlockTemporaryEvolutionLevelOutProto(result=7)
    if pokemon is None:return p.UnlockTemporaryEvolutionLevelOutProto(result=2)
    if not branch(pokemon.pokemon_id,request.temp_evo_id):return p.UnlockTemporaryEvolutionLevelOutProto(result=4)
    settings=level_settings(pokemon.pokemon_id,request.temp_evo_id,request.temp_evo_level)
    if not settings or not settings.mega_energy_cost_to_unlock:return p.UnlockTemporaryEvolutionLevelOutProto(result=5)
    saved=progress(db,pokemon.id,request.temp_evo_id)
    upgraded=level_up(game,db,player,p.MegaLevelUpProto(pokemon_id=pokemon.id,temp_evo_id=request.temp_evo_id,
        current_mega_level=saved['level'],target_mega_level=request.temp_evo_level).SerializeToString())
    result={1:1,2:2,3:4,4:5,5:5,7:3}.get(upgraded.result,6)
    out=p.UnlockTemporaryEvolutionLevelOutProto(result=result)
    if result==1:out.updated_pokemon.CopyFrom(upgraded.updated_pokemon)
    return out


def combat_rules(pokemon, ordinary):
    """A battle snapshot locks the admitted Mega until that battle concludes."""
    display=pokemon.pokemon_display;evo=display.current_temp_evolution
    if not evo or not (display.temp_evolution_is_locked or display.temporary_evolution_finish_ms>now_ms()):return ordinary
    b=branch(pokemon.pokemon_id,evo)
    if not b:raise ValueError('Invalid temporary combat form')
    override=p.TempEvoOverrideProto.FromString(bytes.fromhex(b['override_hex']))
    return dict(ordinary,stamina=override.stats.base_stamina,attack=override.stats.base_attack,
                defense=override.stats.base_defense,types=[override.type_override1,override.type_override2])


def ordinary_hp(combat_pokemon, hp):
    ratio=combat_pokemon.temp_evo_stamina_modifier
    return max(0,math.ceil(hp/ratio)) if ratio>0 and combat_pokemon.pokemon_display.current_temp_evolution else hp


def catch_bonus(game, db, player, pokemon, raid=False):
    if not enabled(game):return (0,0,0)
    row=db.execute('SELECT a.*,p.data FROM mega_active a JOIN pokemon p ON p.id=a.pokemon_id WHERE a.player_id=? AND a.end_ms>?',(player,now_ms())).fetchone()
    if not row:return (0,0,0)
    current=p.PokemonProto.FromString(row['data']);saved=progress(db,current.id,row['temp_evo'])
    effects=level_settings(current.pokemon_id,row['temp_evo'],saved['level']).effects
    bonus_types=DATA.get('bonus_types',{}).get(str(current.pokemon_id),temporary_rules(game,current,row['temp_evo'])['types'])
    matching=bool((set(bonus_types)-{0}) & (set(rules_for(game,pokemon)['types'])-{0}))
    candy=effects.same_type_extra_catch_candy if matching else _SETTINGS.active_mega_bonus_catch_candy if raid else 0
    xp=effects.same_type_extra_catch_xp if matching else 0
    from dittobuffer.gameplay.progression import bounds
    chance=effects.same_type_extra_catch_candy_xl_chance if matching and bounds(game.config,game.player(db,player)['xp'])[0]>=DATA['xl_min_player_level'] else 0
    xl=int(chance>0 and secrets.randbelow(1000000)<int(chance*1000000))
    if xl:
        family=rules_for(game,pokemon)['family']
        db.execute('INSERT INTO mega_catch_xl VALUES(?,?,?) ON CONFLICT(player_id,family) DO UPDATE SET count=count+excluded.count',(player,family,xl))
    return candy,xp,xl
