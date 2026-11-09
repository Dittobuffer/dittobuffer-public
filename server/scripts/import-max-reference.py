"""Extract exact cached Max/Bread templates from the authorized Game Master cache.

The compact exported cache wraps each template as:
    outer field 2 -> [field 1 name, field 2 -> [field 1 name, field N payload]]
where N is exporter-specific (235 in the full cache).  The payload is the
settings message itself, not a GameMasterClientTemplateProto wrapper.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from protocol.opaque import fields
from dittobuffer.gameplay.game import p

TYPES = {
    'MP_SHARED_SETTINGS': p.MpSharedSettingsProto,
    'BREAD_FEATURE_FLAGS': p.BreadFeatureFlagsProto,
    'BREAD_SHARED_SETTINGS': p.BreadSharedSettingsProto,
    'BREAD_BATTLE_CLIENT_SETTINGS': p.BreadBattleClientSettingsProto,
    'BREAD_MOVE_LEVEL_SETTINGS_GROUP_1': p.BreadMoveLevelSettingsProto,
    'BREAD_MOVE_LEVEL_SETTINGS_GROUP_2': p.BreadMoveLevelSettingsProto,
    'BREAD_MOVE_LEVEL_SETTINGS_GROUP_3': p.BreadMoveLevelSettingsProto,
    'BREAD_MOVE_LEVEL_SETTINGS_GROUP_4': p.BreadMoveLevelSettingsProto,
    'BREAD_MOVE_LEVEL_SETTINGS_GROUP_7': p.BreadMoveLevelSettingsProto,
    'BREAD_MOVE_LEVEL_SETTINGS_GROUP_8': p.BreadMoveLevelSettingsProto,
    'BREAD_MOVE_LEVEL_SETTINGS_GROUP_Z': p.BreadMoveLevelSettingsProto,
    'BREAD_MOVE_MAPPING_SETTINGS': p.BreadMoveMappingSettingsProto,
    'SOURDOUGH_MOVE_MAPPING_SETTINGS': p.SourdoughMoveMappingSettingsProto,
    'STATIONED_POKEMON_POWER_BOOST_TABLE_SETTINGS': p.StationedPokemonTableSettingsProto,
    'STATION_REWARD_SETTINGS': p.StationRewardSettingsProto,
    'VNEXT_BATTLE_CLIENT_SETTINGS': p.VNextBattleClientSettingsProto,
    'BREAD_LOBBY_COUNTER_SETTINGS': p.BreadLobbyCounterSettingsProto,
    'BREAD_POKEMON_SCALING_SETTINGS': p.BreadPokemonScalingSettingsProto,
    'BREAD_LOBBY_UPDATE_SETTINGS': p.BreadLobbyUpdateSettingsProto,
}


def payload_of(wire):
    entries = list(fields(wire))
    if entries and entries[0][0] == 1:
        return max((value for number, kind, _, value in entries
                    if number != 1 and isinstance(value, bytes)),
                   key=len, default=b'')
    return wire


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='../LibreNLP/builds/m161-source/game-master.cache')
    parser.add_argument('--output', default='data/static/max-reference.json')
    args = parser.parse_args()
    source = (ROOT / args.source).resolve()
    target = (ROOT / args.output).resolve()
    if not source.is_file():
        raise SystemExit(f'Game Master cache not found: {source}')
    if not target.is_relative_to(ROOT):
        raise SystemExit('Output must stay inside the project')
    raw = source.read_bytes()
    templates = {}
    groups = {}
    for number, kind, _, entry in fields(raw):
        if number != 2 or kind != 2:
            continue
        inner = list(fields(entry))
        name = next((value.decode('utf8') for n, k, _, value in inner
                     if n == 1 and isinstance(value, bytes)), '')
        wire = payload_of(next((value for n, k, _, value in inner
                                if n != 1 and isinstance(value, bytes)), b''))
        if name in TYPES:
            try:
                TYPES[name].FromString(wire)
            except Exception:
                continue
            templates[name] = wire.hex()
        if name.startswith('V') and '_POKEMON_' in name:
            try:
                values = {n: value for n, k, _, value in fields(wire)}
            except Exception:
                continue
            if isinstance(values.get(1), int) and isinstance(values.get(88), int):
                groups[f'{values[1]}:0'] = values[88]
    required = ('BREAD_FEATURE_FLAGS', 'BREAD_SHARED_SETTINGS', 'MP_SHARED_SETTINGS',
                'BREAD_MOVE_MAPPING_SETTINGS', 'BREAD_MOVE_LEVEL_SETTINGS_GROUP_1')
    missing = [name for name in required if name not in templates]
    if missing:
        raise SystemExit('Incomplete Max reference; missing ' + ', '.join(missing))
    result = {
        'source': str(source),
        'source_sha256': hashlib.sha256(raw).hexdigest(),
        'templates': templates,
        'species_groups': groups,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    print(json.dumps(dict(templates=len(templates), species_groups=len(groups),
                          output=str(target.relative_to(ROOT)))))


if __name__ == '__main__':
    main()
