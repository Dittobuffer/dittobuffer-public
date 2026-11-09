"""Native 0.429.2 research strategies and durable, owned encounter state.

The cinematic identifier is a server configuration key, never a new wire field.
EncounterState.SetupResearchEncounter selects the native strategy from StoryQuest
(4), species and display.shiny. Playback is client-local; an RPC records a request
to open, not proof that an animation finished. Native ResearchEncounter performs
its configured fake escapes locally before submitting the final capture RPC.
"""
import copy
import json
import logging
from pathlib import Path

from dittobuffer.paths import ROOT


def registry():
    document = json.loads(Path(__file__).with_name('special_encounters.json').read_text())
    return document['encounters']


def select(species, shiny, encounter_type):
    for definition in registry().values():
        if encounter_type in definition.get('encounter_types', [4]) and definition['species'] == species and definition['shiny_selector'] in ('either', 'shiny' if shiny else 'normal'):
            return copy.deepcopy(definition)
    return None


def validate_reward(game, reward):
    identifier = reward.get('special_encounter')
    if identifier is None:
        return
    definition = registry().get(identifier)
    if not definition or reward.get('species') != definition['species']:
        raise ValueError('Unknown or mismatched native special encounter')
    # A fixed variant is explicit and cannot silently lose its shiny appearance.
    shiny = reward.get('shiny_rate', 0)
    if definition['shiny_selector'] == 'shiny' and shiny != 1:
        raise ValueError('This native encounter requires a guaranteed shiny reward')
    if definition['shiny_selector'] == 'normal' and shiny != 0:
        raise ValueError('This native encounter requires a normal reward')
    from dittobuffer.gameplay.species import pool_rules
    form = pool_rules(game, reward)['form']
    if shiny and (reward['species'], form) not in game.shiny_eligible:
        raise ValueError('Special encounter shiny resources must be explicitly released')
    if reward.get('max_mode'):
        raise ValueError('Native mythical research cannot be a Max reward')


def snapshot(db, player, spawn_id, pokemon, encounter_type, reward, stamp):
    definition = select(pokemon.pokemon_id, pokemon.pokemon_display.shiny, encounter_type)
    expected = reward.get('special_encounter')
    if expected and (not definition or definition['encounter_id'] != expected):
        raise ValueError('Reward source or appearance cannot select this native encounter')
    if not definition:
        return
    definition.update(form=pokemon.pokemon_display.form,
                      shiny=pokemon.pokemon_display.shiny,
                      source='special_research' if encounter_type==4 else 'research_reward', encounter_type=encounter_type,
                      strict_resources=bool(expected))
    db.execute('INSERT INTO special_research_encounters(player_id,spawn_id,definition,created_ms) VALUES(?,?,?,?)',
               (player, spawn_id, json.dumps(definition, sort_keys=True), stamp))


def owned(db, player, spawn_id):
    row = db.execute('SELECT * FROM special_research_encounters WHERE player_id=? AND spawn_id=?',
                     (player, spawn_id)).fetchone()
    return row, json.loads(row['definition']) if row else None


def capture_rules(db, player, spawn_id):
    row, definition = owned(db, player, spawn_id)
    if not row:
        return None
    # Both canonical research ownership and the frozen strategy are required.
    if not db.execute('SELECT 1 FROM research_encounters WHERE player_id=? AND spawn_id=? AND encounter_type=?',
                      (player, spawn_id, definition['encounter_type'])).fetchone():
        return None
    return definition['capture_rules']


def resource_status(game, definition):
    """Read-only closure preflight; no downloads, catalog edits or cache writes."""
    from dittobuffer.core.asset_catalog import Catalog
    from dittobuffer.core.client_resources import ClientResources
    missing = []
    cfg = game.config.get('client_resources', {})
    if not cfg.get('enabled'):
        return {'ready': False, 'missing': list(definition['required_resources'])}
    try:
        resources = ClientResources(ROOT, cfg)
        catalog = Catalog(json.loads(resources.read('catalog_926.json')[0]))
        for key in definition['required_resources']:
            if key not in catalog.buckets:
                missing.append('address:' + key)
                continue
            for index in catalog.closure(key):
                location = catalog.location(index)
                if not location['type'].endswith('.IAssetBundleResource'):
                    continue
                internal = location['internal_id']
                name = internal.replace('\\', '/').rsplit('/', 1)[-1]
                if internal.startswith('{UnityEngine.AddressableAssets.Addressables.RuntimePath}'):
                    # APK dependencies are independently checked by the operator
                    # audit; the server does not have a portable APK path.
                    continue
                if resources.read(name) is None:
                    missing.append(name)
    except (ValueError, OSError, KeyError, TypeError) as error:
        missing.append('registry:' + type(error).__name__)
    return {'ready': not missing, 'missing': sorted(set(missing))}


def opened(game, db, player, spawn_id, stamp):
    row, definition = owned(db, player, spawn_id)
    if not row:
        # Additive recovery for pre-M23 StoryQuest entitlements: preserve the
        # original Pokemon bytes; only snapshot the native dispatch metadata.
        previous = db.execute('SELECT r.encounter_type,a.data FROM research_encounters r JOIN spawn_attributes a ON a.spawn_id=r.spawn_id WHERE r.player_id=? AND r.spawn_id=?',
                              (player, spawn_id)).fetchone()
        if previous:
            from dittobuffer.gameplay.game import p
            snapshot(db, player, spawn_id, p.PokemonProto.FromString(previous['data']), previous['encounter_type'], {}, stamp)
        row, definition = owned(db, player, spawn_id)
        if not row:
            return True
    if definition.get('strict_resources'):
        status = resource_status(game, definition)
        if not status['ready']:
            db.execute("UPDATE special_research_encounters SET playback_state='RESOURCE_BLOCKED' WHERE player_id=? AND spawn_id=? AND captured_id IS NULL", (player, spawn_id))
            logging.warning('Native research %s remains recoverable; missing resources: %s',
                            definition['encounter_id'], ', '.join(status['missing']))
            return False
    db.execute("UPDATE special_research_encounters SET opened_ms=?,open_count=open_count+1,playback_state='OPEN_REQUESTED' WHERE player_id=? AND spawn_id=? AND captured_id IS NULL",
               (stamp, player, spawn_id))
    return True


def captured(db, player, spawn_id, pokemon_id, stamp):
    db.execute("UPDATE special_research_encounters SET captured_id=?,captured_ms=?,playback_state='CAPTURED' WHERE player_id=? AND spawn_id=? AND captured_id IS NULL",
               (pokemon_id, stamp, player, spawn_id))
