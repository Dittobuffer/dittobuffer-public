"""Local gameplay rules with losslessly preserved model and form definitions."""
import gzip
from protocol.opaque import fields,byte_field,varint
from dittobuffer.gameplay.game import p,ROOT

VERSION=86

def version(config):
    # Keep opt-in Trainer templates out of a normal profile's native GM cache.
    # Both profiles can share a clone, but must never share a cache epoch.
    route=config.get('routes',{})
    # Distinct optional-feature bits retain the PvP cache epoch while making
    # every Routes/Cells/Mateo/Vivillon template combination invalidate it.
    base=VERSION+3000017*int(config.get('progression_schedule')=='pinned-modern-gated')+2000003*int(bool(config.get('reward_bonuses',{}).get('weekly_enabled')))
    optional=0
    if route.get('enabled'):
        optional=1+2*int(bool(route.get('cells',{}).get('enabled')))+4*int(bool(route.get('mateo',{}).get('enabled')))
    optional+=8*int(bool(config.get('vivillon',{}).get('enabled')))
    if config.get('pvp',{}).get('enabled'):
        settings=config['pvp']
        base+=1000003+settings.get('season',1)*10000+settings.get('template_revision',0)
    # Reserve four low bits: a PvP revision must never collide with enabling
    # an optional Routes/Cells/Mateo/Vivillon template group.
    return base*32+optional+16*int(bool(config.get('rocket',{}).get('enabled') and config.get('rocket',{}).get('server_combat')))

def template_bytes(config):
    path=ROOT/'data/static/gmts.binpb.gz'
    generated=templates(config)
    native_shadow={}
    if config.get('rocket',{}).get('enabled') and config.get('rocket',{}).get('server_combat'):
        from dittobuffer.gameplay.rocket_native import reference
        native_shadow={value['template_id']:value for value in reference(config)['shadow'].values() if value.get('template_id')}
    overrides={t.template_id:t.SerializeToString() for t in generated}
    if len(overrides)!=len(generated):raise ValueError('Duplicate local Game Master template ID')
    output=[]
    if path.exists():
        for number,kind,_,payload in fields(gzip.decompress(path.read_bytes())):
            if number!=2 or kind!=2:continue
            name=next((value.decode('utf-8') for n,k,_,value in fields(payload) if n==1 and k==2),'')
            replacement=overrides.pop(name,payload)
            # These contain model/camera/form data whose exporter field numbers
            # are incomplete. Preserve the original wire representation.
            if name.startswith('V') and '_POKEMON_' in name:
                from dittobuffer.gameplay.mega import augment_template
                from dittobuffer.gameplay.shadow import augment_template as augment_shadow
                # Mega branches and shadow purification metadata are both
                # omitted by the compact exporter but are required by the
                # native client.
                from dittobuffer.gameplay.rocket_shadow import augment_template as augment_native_shadow
                output.append(augment_native_shadow(augment_shadow(augment_template(payload,config),config),native_shadow.get(name)))
            else:output.append(replacement)
    output.extend(overrides.values())
    return output

def prelogin(config):
    # GetGameMasterClientTemplatesOutProto: result=1, repeated item=2, timestamp=3.
    raw=varint(8)+varint(1)+b''.join(byte_field(2,t) for t in template_bytes(config))+varint(24)+varint(version(config))
    return gzip.compress(raw,mtime=0)

def templates(config):
    from dittobuffer.gameplay.mega import templates as mega_templates
    from dittobuffer.gameplay.avatar import templates as avatar_templates
    values=avatar_templates(p)+mega_templates(config)
    from dittobuffer.gameplay.level_research import templates as level_templates
    values.extend(level_templates(config))
    from dittobuffer.gameplay.vivillon import templates as butterfly_templates
    values.extend(butterfly_templates(config))
    from dittobuffer.gameplay.route_npc_resources import templates as route_npc_templates
    values.extend(route_npc_templates(config))
    from dittobuffer.gameplay.friends import templates as social_templates
    values.extend(social_templates(config))
    from dittobuffer.gameplay.wardrobe import templates as wardrobe_templates
    values.extend(wardrobe_templates(config))
    from dittobuffer.gameplay.medals import templates as medal_templates
    values.extend(medal_templates(config))
    from dittobuffer.gameplay.buddy import templates as buddy_templates
    values.extend(buddy_templates(config))
    from dittobuffer.gameplay.pokemon_tags import templates as tag_templates
    values.extend(tag_templates(config))
    from dittobuffer.gameplay.battle_master import templates as battle_templates
    values.extend(battle_templates(config))
    from dittobuffer.gameplay.shop import templates as shop_templates
    values.extend(shop_templates(config))
    from dittobuffer.gameplay.raids import templates as raid_templates
    values.extend(raid_templates(config))
    from dittobuffer.gameplay.max_system import templates as max_templates
    values.extend(max_templates(config))
    from dittobuffer.gameplay.pvp import templates as pvp_templates
    values.extend(pvp_templates(config))
    from dittobuffer.gameplay.max_system import merge_client_settings
    merge_client_settings(config,values)
    from dittobuffer.gameplay.backgrounds import templates as background_templates
    values.extend(background_templates(config))
    from dittobuffer.gameplay.species import REFERENCE
    if config.get('pokemon_sizes',{}).get('enabled'):
        from dittobuffer.gameplay.pokemon_sizes import bounds
        from dittobuffer.gameplay.species import form_catalog
        known=form_catalog(ROOT/'data/static/gmts.binpb.gz')
        import re
        for key,entry in REFERENCE.get('sizes',{}).items():
            species,form=map(int,key.split(':'))
            if (species,form) not in known:continue
            bounds(entry['bounds'])
            item=p.GameMasterClientTemplateProto(template_id=entry['template_id'])
            item.pokemon_extended_settings.unique_id=species;item.pokemon_extended_settings.form=form
            for original,value in entry['bounds'].items():
                field=re.sub(r'(?<!^)(?=[A-Z])','_',original).lower()
                if field in p.PokemonSizeSettingsProto.DESCRIPTOR.fields_by_name:
                    setattr(item.pokemon_extended_settings.size_settings,field,value)
            values.append(item)
    # The supplied compact snapshot omits SPAWN gender templates. Supply the
    # already pinned numeric distributions to the real client as well as the
    # server; a PokemonDisplay gender symbol alone is not model validation.
    for ident,gender in REFERENCE.get('genders',{}).items():
        item=p.GameMasterClientTemplateProto(template_id='LIBRE_GENDER_'+ident)
        item.gender_settings.pokemon=int(ident)
        item.gender_settings.gender.CopyFrom(p.ClientGenderProto(male_percent=gender.get('malePercent',0),
            female_percent=gender.get('femalePercent',0),genderless_percent=gender.get('genderlessPercent',0)))
        values.append(item)
    # Master Ball is not in the supplied snapshot's item templates but the
    # protocol defines it (Item_MasterBall=4); supply its inventory data.
    master=p.GameMasterClientTemplateProto(template_id='ITEM_MASTER_BALL')
    master.item.unique_id=4;master.item.item_type=1;master.item.category=1
    master.item.drop_trainer_level=1;master.item.pokeball.capture_multi=255
    values.append(master)
    for ident,move in REFERENCE.get('pvp_moves',{}).items():
        item=p.GameMasterClientTemplateProto(template_id=move['template_id'])
        item.combat_move.CopyFrom(p.CombatMoveSettingsProto(unique_id=int(ident),type=move['type'],power=move['power'],
            energy_delta=move['energy_delta'],duration_turns=move['duration_turns'],vfx_name=move['vfx_name']))
        values.append(item)
    # The level-up GUI reads these independently of the reward RPC response.
    from dittobuffer.gameplay.level_research import MODE,reward_templates
    values.extend(reward_templates(config))
    for number,items in ({} if config.get('progression_schedule')==MODE else config.get('level_up_rewards',{})).items():
        reward=p.GameMasterClientTemplateProto(template_id=f'LEVEL_UP_REWARDS_LEVEL_{number}')
        reward.level_up_rewards.level=int(number)
        for item,count in items.items():
            reward.level_up_rewards.items.append(int(item))
            reward.level_up_rewards.items_count.append(count)
        values.append(reward)
    upgrade=p.GameMasterClientTemplateProto(template_id='POKEMON_UPGRADE_SETTINGS')
    upgrade.pokemon_upgrades.upgrades_per_level=2
    upgrade.pokemon_upgrades.allowed_levels_above_player=config.get('power_up_levels_above_player',10)
    upgrade.pokemon_upgrades.max_normal_upgrade_level=50
    # Levels 41-50 consume XL candy; the regular candy column must be zero
    # there or the client can refuse to render the XL power-up action.
    upgrade.pokemon_upgrades.candy_cost.extend([config.get('power_up_candy_cost',1)]*40+[0]*10)
    upgrade.pokemon_upgrades.stardust_cost.extend([config.get('power_up_stardust_cost',100)]*50)
    upgrade.pokemon_upgrades.default_cp_boost_additional_level=1
    upgrade.pokemon_upgrades.xl_candy_min_player_level=config.get('xl_candy_min_player_level',31)
    upgrade.pokemon_upgrades.xl_candy_min_pokemon_level=40
    upgrade.pokemon_upgrades.xl_candy_cost.extend([config.get('power_up_xl_candy_cost',10)]*20)
    upgrade.pokemon_upgrades.shadow_candy_multiplier=1
    upgrade.pokemon_upgrades.shadow_stardust_multiplier=1
    upgrade.pokemon_upgrades.purified_candy_multiplier=1
    upgrade.pokemon_upgrades.purified_stardust_multiplier=1
    values.append(upgrade)
    from dittobuffer.gameplay.shadow import shared_move_multipliers
    shadow_dust,shadow_candy,purified_dust,purified_candy=shared_move_multipliers(config)
    shared=p.GameMasterClientTemplateProto(template_id='SHARED_MOVE_SETTINGS')
    shared.shared_move_settings.shadow_third_move_unlock_stardust_multiplier=shadow_dust
    shared.shared_move_settings.shadow_third_move_unlock_candy_multiplier=shadow_candy
    shared.shared_move_settings.purified_third_move_unlock_stardust_multiplier=purified_dust
    shared.shared_move_settings.purified_third_move_unlock_candy_multiplier=purified_candy
    shared.shared_move_settings.reroll_move_update_fusion_details_enabled=True
    values.append(shared)
    level=p.GameMasterClientTemplateProto(template_id='PLAYER_LEVEL_SETTINGS')
    from dittobuffer.gameplay.progression import requirements,cap,pinned
    costs=requirements(config)
    # Android reads this list as cumulative thresholds, while configuration
    # contains the individual costs of reaching each successive level.
    from itertools import accumulate
    level.player_level.required_exp.extend(accumulate(costs))
    from dittobuffer.gameplay.species import REFERENCE
    # Ship the exact reference CPM table through level 50.  Regenerating
    # levels 41-50 from a formula made the client disagree with the server's
    # stored cp_multiplier and hid the XL-candy power-up action.
    level.player_level.cp_multiplier.extend(REFERENCE['cp_multipliers'][:50])
    level.player_level.rank_num.extend(pinned()['rankNum'] if config.get('progression_schedule') in ('pinned-modern','pinned-modern-gated') else range(1,len(costs)+1))
    level.player_level.default_level_cap=cap(config)
    if config.get('progression_schedule') in ('pinned-modern','pinned-modern-gated'):level.player_level.milestone_levels.extend(pinned()['milestoneLevels'])
    level.player_level.max_encounter_player_level=30
    level.player_level.max_egg_player_level=20
    level.player_level.max_raid_encounter_player_level=20
    values.append(level)
    encounter=p.GameMasterClientTemplateProto(template_id='ENCOUNTER_SETTINGS')
    encounter.encounter_settings.spin_bonus_threshold=0.5
    encounter.encounter_settings.nice_throw_threshold=1.3
    encounter.encounter_settings.great_throw_threshold=1.7
    encounter.encounter_settings.excellent_throw_threshold=1.85
    encounter.encounter_settings.global_stardust_multiplier=1
    encounter.encounter_settings.global_candy_multiplier=1
    values.append(encounter)
    # 0.429 falls back to a three-slot raid/gym party when the Game Master
    # omits this template, which cut the native lobby party from six to three.
    party=p.GameMasterClientTemplateProto(template_id='BATTLE_PARTY_SETTINGS')
    party.battle_party_settings.enable_battle_party_saving=True
    party.battle_party_settings.max_battle_parties=20
    party.battle_party_settings.overall_parties_cap=100
    party.battle_party_settings.gym_and_raid_battle_party_size=6
    party.battle_party_settings.max_party_name_length=24
    values.append(party)
    if config.get('reward_bonuses',{}).get('weekly_enabled'):
        fitness=p.GameMasterClientTemplateProto(template_id='BACKGROUND_MODE_SETTINGS')
        for i,km in enumerate((5,25,50,100),1):setattr(fitness.background_mode_settings,'weekly_fitness_goal_level'+str(i)+'_distance_km',km)
        values.append(fitness)
    inventory=p.GameMasterClientTemplateProto(template_id='INVENTORY_SETTINGS')
    inventory.inventory_settings.max_pokemon=config['pokemon_capacity']
    inventory.inventory_settings.base_pokemon=config['pokemon_capacity']
    inventory.inventory_settings.max_bag_items=config['item_capacity']
    inventory.inventory_settings.base_bag_items=config['item_capacity']
    inventory.inventory_settings.base_eggs=config.get('eggs',{}).get('capacity',9)
    values.append(inventory)
    categories=p.GameMasterClientTemplateProto(template_id='ITEM_INVENTORY_UPDATE_SETTINGS')
    for order,(category,name) in enumerate([(1,'Poké Balls'),(2,'Berries'),(3,'Medicine'),(6,'Tools'),(12,'Evolution Items'),(13,'TMs'),(14,'Candy'),(15,'Raid Passes')]):
        group=[category]
        if category==6 and config.get('routes',{}).get('enabled') and config.get('routes',{}).get('cells',{}).get('enabled'):group.append(5)
        categories.item_inventory_update_settings.category_proto.add(category=group,category_name=name,sort_order=order)
    if config.get('rocket',{}).get('enabled') and config.get('rocket',{}).get('server_combat'):
        categories.item_inventory_update_settings.category_proto.add(category=[21],category_name='Team GO Rocket',sort_order=8)
    values.append(categories)
    interaction=p.GameMasterClientTemplateProto(template_id='CLIENT_MAP_OBJECTS_INTERACTION_RANGE_SETTINGS')
    interaction.map_objects_interaction_range_settings.interaction_range_meters=config['interaction_range_meters']
    interaction.map_objects_interaction_range_settings.far_interaction_range_meters=1000
    interaction.map_objects_interaction_range_settings.remote_interaction_range_meters=1000
    interaction.map_objects_interaction_range_settings.white_pulse_radius_meters=80
    values.append(interaction)
    camera=p.GameMasterClientTemplateProto(template_id='ITEM_SPECIAL_CAMERA')
    camera.item.unique_id=801;camera.item.item_type=7;camera.item.category=6
    camera.item.ignore_inventory_space=True
    values.append(camera)
    for ident,entry in REFERENCE.get('evolution_items',{}).items():
        stone=p.GameMasterClientTemplateProto(template_id=entry['template_id'])
        stone.item.unique_id=int(ident);stone.item.item_type=13;stone.item.category=12
        stone.item.drop_trainer_level=entry['drop_trainer_level']
        values.append(stone)
    for item,name in [(1201,'FAST_ATTACK'),(1202,'SPECIAL_ATTACK'),(1203,'ELITE_FAST_ATTACK'),(1204,'ELITE_SPECIAL_ATTACK')]:
        tm=p.GameMasterClientTemplateProto(template_id='ITEM_MOVE_REROLL_'+name)
        tm.item.unique_id=item;tm.item.item_type=14;tm.item.category=13;tm.item.drop_trainer_level=1
        values.append(tm)
    for item,uses,name in [(901,0,'INCUBATOR_BASIC_UNLIMITED'),(902,3,'INCUBATOR_BASIC')]:
        incubator=p.GameMasterClientTemplateProto(template_id='ITEM_'+name)
        incubator.item.unique_id=item;incubator.item.item_type=9;incubator.item.category=6
        incubator.item.ignore_inventory_space=True
        incubator.item.egg_incubator.incubator_type=1;incubator.item.egg_incubator.uses=uses
        incubator.item.egg_incubator.distance_multiplier=1
        values.append(incubator)
    # PokemonSettingsProto's exported field numbers are not trustworthy. Model,
    # camera, collision and form templates come only from the static snapshot.
    from dittobuffer.gameplay.items import POTIONS,REVIVES
    for item in [1,2,3,*POTIONS,*REVIVES,701]:
        name={1:'POKE_BALL',2:'GREAT_BALL',3:'ULTRA_BALL',101:'POTION',102:'SUPER_POTION',103:'HYPER_POTION',
              104:'MAX_POTION',201:'REVIVE',202:'MAX_REVIVE',701:'RAZZ_BERRY'}[item]
        template=p.GameMasterClientTemplateProto(template_id=f'ITEM_{name}')
        template.item.unique_id=item;template.item.drop_trainer_level=1
        if item in (1,2,3):
            template.item.item_type=1;template.item.category=1
            template.item.pokeball.capture_multi={1:1,2:1.5,3:2}[item]
        else:
            template.item.item_type=2 if item in POTIONS else 3 if item in REVIVES else 6
            template.item.category=2 if item==701 else 3
            if item in POTIONS:
                template.item.potion.sta_amount,template.item.potion.sta_percent=POTIONS[item]
            if item in REVIVES:template.item.revive.sta_percent=REVIVES[item]
            if item==701:
                template.item.food.num_buddy_affection_points=1
                template.item.food.num_buddy_hunger_points=2
                template.item.food.map_duration_ms=3600000
                template.item.food.active_duration_ms=3600000
                template.item.food.berry_multiplier=1.5
                template.item.food.remote_berry_multiplier=0.25
                template.item.food.item_effect.append(1014)
                template.item.food.item_effect_percent.append(1.5)
        values.append(template)
    from dittobuffer.gameplay.routes import templates as route_templates
    values.extend(route_templates(config))
    from dittobuffer.gameplay.route_cells import templates as cell_templates
    cells=cell_templates(config)
    # Cell/Cube definitions override only their exact generic item templates.
    cell_ids={v.template_id for v in cells}
    values=[v for v in values if v.template_id not in cell_ids]+cells
    from dittobuffer.world.world_rewards import augment_templates
    from dittobuffer.gameplay.rocket_native import templates as rocket_templates,reference as rocket_reference
    native=rocket_templates(config)
    if native:
        from dittobuffer.gameplay.rocket_shadow import client_cost_multiplier
        for name,value in rocket_reference(config)['upgrade'].items():setattr(upgrade.pokemon_upgrades,name,client_cost_multiplier(value))
        replacements={value.template_id:value for value in native}
        values=[value for value in values if value.template_id not in replacements]+native
    # The PvP reference snapshot contributes its own BATTLE_PARTY_SETTINGS with
    # a three-slot gym/raid party. Keep exactly one copy and keep it at six so
    # the native lobby never falls back to three.
    unique={}
    for value in values:
        if value.template_id=='BATTLE_PARTY_SETTINGS' and value is not party:
            continue
        unique[value.template_id]=value
    return augment_templates(list(unique.values()),config)
