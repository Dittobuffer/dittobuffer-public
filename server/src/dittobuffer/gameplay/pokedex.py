"""Persistent registration independent of transfers and current storage."""
from dittobuffer.gameplay.game import p

def record(game,db,player,key,pokemon,caught=False):
    display=pokemon.pokemon_display
    changed=db.execute('INSERT INTO pokedex_observations VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(player_id,source_key) DO UPDATE SET caught=MAX(caught,excluded.caught),data=excluded.data WHERE caught<excluded.caught',
        (player,key,pokemon.pokemon_id,display.form,display.costume,display.gender,display.shiny,int(caught),pokemon.SerializeToString())).rowcount
    if changed:game.touch(db,player)

def ensure(game,db,player):
    if db.execute('SELECT 1 FROM pokedex_imports WHERE player_id=?',(player,)).fetchone():return
    # Only reconstruct facts retained in authoritative encounter/owned blobs.
    # Historical unrecorded sightings are unknowable; do not invent them.
    for row in db.execute('SELECT e.spawn_id,e.state,a.data FROM encounters e JOIN spawn_attributes a ON a.spawn_id=e.spawn_id WHERE e.player_id=?',(player,)).fetchall():
        record(game,db,player,'spawn:'+str(row['spawn_id']),p.PokemonProto.FromString(row['data']),row['state']=='caught')
    for row in db.execute('SELECT o.id,o.spawn_id,o.data,a.data original FROM pokemon o LEFT JOIN spawn_attributes a ON a.spawn_id=o.spawn_id WHERE o.player_id=?',(player,)).fetchall():
        value=p.PokemonProto.FromString(row['data'])
        if row['original']:
            original=p.PokemonProto.FromString(row['original'])
            if (value.pokemon_id,value.pokemon_display.form)==(original.pokemon_id,original.pokemon_display.form):
                record(game,db,player,'spawn:'+str(row['spawn_id']),value,True);continue
        record(game,db,player,'owned:'+str(row['id'])+':'+str(value.pokemon_id)+':'+str(value.pokemon_display.form),value,True)
    db.execute('INSERT INTO pokedex_imports VALUES(?)',(player,))

def inventory(game,db,player):
    grouped={}
    for row in db.execute('SELECT * FROM pokedex_observations WHERE player_id=? ORDER BY species,source_key',(player,)):
        grouped.setdefault(row['species'],[]).append(row)
    result=[]
    for species,rows in grouped.items():
        caught=[r for r in rows if r['caught']]
        item=p.HoloInventoryItemProto();entry=item.pokedex_entry
        entry.pokedex_entry_number=species;entry.times_encountered=len(rows);entry.times_captured=len(caught)
        for prefix,values in [('encountered',rows),('captured',caught)]:
            for suffix,column in [('costumes','costume'),('forms','form'),('genders','gender')]:
                getattr(entry,prefix+'_'+suffix).extend(sorted({r[column] for r in values}))
            setattr(entry,prefix+'_shiny',any(r['shiny'] for r in values))
        entry.captured_shiny_forms.extend(sorted({r['form'] for r in caught if r['shiny']}))
        entry.times_lucky_received=sum(p.PokemonProto.FromString(r['data']).is_lucky for r in caught)
        entry.location_cards.extend(sorted({p.PokemonProto.FromString(r['data']).pokemon_display.location_card.location_card for r in caught}-{0}))
        # The current Android Dex counters query category_status, independently
        # of the older CapturedShiny flag used by the model picker.
        categories={}
        category_values=[(p.PokemonProto.FromString(row['data']),bool(row['caught'])) for row in rows]
        if getattr(game,'rocket_reference',None):
            for row in db.execute('SELECT after FROM rocket_purifications WHERE player_id=?',(player,)):
                mon=p.PokemonProto.FromString(row['after'])
                if mon.pokemon_id==species:category_values.append((mon,True))
        for mon,was_caught in category_values:
            display=mon.pokemon_display
            kinds={1}
            if display.shiny:kinds.add(11)
            if mon.is_lucky:kinds.add(12)
            iv=mon.individual_attack+mon.individual_defense+mon.individual_stamina
            if iv>=37:kinds.add(13)
            if iv==45:kinds.add(14)
            if display.shiny and iv>=37:kinds.add(101)
            if display.shiny and iv==45:kinds.add(102)
            if display.bread_mode_enum==1:kinds.add(18)
            if display.bread_mode_enum in (2,3):kinds.add(19)
            if display.costume or game.forms.get((species,display.form),{}).get('is_costume'):kinds.add(17)
            if display.alignment==1:kinds.add(15)
            if display.alignment==2:kinds.add(16)
            for kind in kinds:
                state=categories.setdefault(kind,{'encountered':True,'acquired':False,'last_capture_timestamp_ms':0})
                if was_caught:
                    state['acquired']=True
                    state['last_capture_timestamp_ms']=max(state['last_capture_timestamp_ms'],mon.creation_time_ms)
        # Temporary evolution facts survive transfer and expiry in the existing ledger.
        for evo in db.execute("SELECT temp_evo,COUNT(*) total,MAX(at_ms) stamp FROM mega_ledger WHERE player_id=? AND species=? AND kind='evolve' GROUP BY temp_evo",(player,species)):
            entry.temp_evo_data.add(temp_evo_id=evo['temp_evo'],times_encountered=evo['total'],times_obtained=evo['total'],last_obtained_timestamp_ms=evo['stamp'])
            categories[2]=dict(encountered=True,acquired=True,last_capture_timestamp_ms=evo['stamp'])
        for mode in sorted({mon.pokemon_display.bread_mode_enum for mon,_ in category_values}-{0}):
            mons=[(mon,done) for mon,done in category_values if mon.pokemon_display.bread_mode_enum==mode]
            data=entry.bread_dex_data.add(modifier_id=mode,times_encountered=len(mons),times_obtained=sum(done for _,done in mons))
            data.genders_encountered.extend(sorted({mon.pokemon_display.gender for mon,_ in mons}));data.genders_obtained.extend(sorted({mon.pokemon_display.gender for mon,done in mons if done}))
            data.encountered_shiny_gender.extend(sorted({mon.pokemon_display.gender for mon,_ in mons if mon.pokemon_display.shiny}));data.obtained_shiny_gender.extend(sorted({mon.pokemon_display.gender for mon,done in mons if done and mon.pokemon_display.shiny}))
        for kind,state in sorted(categories.items()):
            entry.category_status.add(key=str(kind)).value.CopyFrom(p.PokedexEntryProto.PokedexCategoryStatus(pokedex_category=kind,**state))
        entry.last_capture_timestamp_ms=max((p.PokemonProto.FromString(r['data']).creation_time_ms for r in caught),default=0)
        result.append(item)
    return result
