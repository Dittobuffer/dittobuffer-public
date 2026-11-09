"""Studio publications coexist with the already-shipped Platform JSON settings."""
from types import SimpleNamespace
from unittest.mock import patch

from dittobuffer.gameplay import liveops


def game():
    return SimpleNamespace(
        liveops=liveops.normalise({
            'shiny_rates': {'1': 0.5},
            'spawn_percent': {'1': 20},
            'shadow_overrides': {'1': {'dust': 1200, 'candy': 3}},
            'community_days': [{'species': 1, 'shiny_rate': 1.0,
                                'starts_ms': 100, 'ends_ms': 200}],
        }),
        raids={'pools': [{'species': 1, 'tier': 5, 'weight': 2}]},
    )


def test_platform_settings_and_snapshot_survive_studio_package_conversion():
    value = game()
    with patch('dittobuffer.gameplay.liveops.legacy.now_ms', return_value=150):
        assert liveops.community_day(value)['species'] == 1
        assert liveops.shiny_rate(value, 0.01, 1, 0) == 1.0
        assert liveops.snapshot(value)['liveops'] == value.liveops
    assert liveops.spawn_percent(value) == {'1': 20.0}
    assert liveops.shadow_rule(value, 1, 0)['dust'] == 1200
    assert liveops.raid_pool(value)[0]['weight'] == 40


def test_studio_override_is_specific_to_its_published_subsystem():
    value = game()
    value.liveops_effective = {'spawns': [{'species': 7}]}
    with patch('dittobuffer.gameplay.liveops.legacy.now_ms', return_value=150):
        assert liveops.shiny_rate(value, 0.02, 1, 0) == 0.02
        assert liveops.shiny_rate(value, 0.02, 1, 0, source='raids') == 1.0
    assert liveops.raid_pool(value)[0]['weight'] == 40
    value.liveops_effective = {'raids': [{'species': 7}]}
    value.raids['pools'] = [{'species': 7, 'tier': 5, 'weight': 3}]
    with patch('dittobuffer.gameplay.liveops.legacy.now_ms', return_value=150):
        assert liveops.shiny_rate(value, 0.02, 1, 0) == 1.0
        assert liveops.shiny_rate(value, 0.02, 1, 0, source='raids') == 0.02
    assert liveops.raid_pool(value) == value.raids['pools']
