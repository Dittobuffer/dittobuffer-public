"""Touch-driver guard tests; these do not certify Android gameplay."""
import importlib.util
from pathlib import Path
import unittest


class NativeTouchGuardTests(unittest.TestCase):
    def test_only_foreground_development_game_can_receive_battle_touches(self):
        spec=importlib.util.spec_from_file_location('pvp_native_touch',Path(__file__).resolve().parents[1]/'scripts/m19-native-touch.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for state in ('mResumedActivity','topResumedActivity'):
            self.assertTrue(module.game_activity(state+': ActivityRecord{ org.librenlp.pokemongo.dev/com.nianticlabs.pokemongo.UnityMainActivity }'))
        for activity in (
            'Hist #0: ActivityRecord{ org.librenlp.pokemongo.dev/com.nianticlabs.pokemongo.UnityMainActivity }',
            'topResumedActivity: ActivityRecord{ com.nianticlabs.pokemongo/com.nianticlabs.pokemongo.UnityMainActivity }',
            'mResumedActivity: ActivityRecord{ org.librenlp.pokemongo.dev/org.librenlp.launcher.FamilyActivity }',
            'mResumedActivity: null'):
            self.assertFalse(module.game_activity(activity))
