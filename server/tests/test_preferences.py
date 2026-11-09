import unittest
from tests import test_game as base
from dittobuffer.gameplay.game import Game,p
from dittobuffer.core.database import Database


class PreferencesTests(unittest.TestCase):
    setUp=base.GameTests.setUp
    tearDown=base.GameTests.tearDown
    call=base.GameTests.call

    def test_native_preferences_survive_restart_and_identical_save_does_not_touch(self):
        value=p.PlayerPreferencesProto(opt_out_of_sponsored_gifts=True,postcard_trainer_info_sharing_preference=2)
        req=p.SavePlayerPreferencesProto(player_preferences_proto=value)
        self.assertEqual(self.call(1652,req,p.SavePlayerPreferencesOutProto).result,1)
        with self.database.transaction() as db:revision=db.execute('SELECT revision FROM players').fetchone()[0]
        self.assertEqual(self.call(1652,req,p.SavePlayerPreferencesOutProto).result,1)
        with self.database.transaction() as db:self.assertEqual(db.execute('SELECT revision FROM players').fetchone()[0],revision)
        self.game=Game(self.config,Database(self.database.path))
        returned=self.call(2,p.GetPlayerProto(),p.GetPlayerOutProto).player
        self.assertEqual(returned.player_preferences.postcard_trainer_info_sharing_preference,value.postcard_trainer_info_sharing_preference)
        self.assertEqual(returned.player_preferences.opt_out_of_sponsored_gifts,value.opt_out_of_sponsored_gifts)
        self.assertTrue(returned.HasField('social_player_settings'))
        self.assertTrue(returned.HasField('combat_player_preferences'))
        self.assertTrue(returned.HasField('contact_settings_proto'))
        self.assertTrue(returned.player_preferences.HasField('activity_sharing_preference'))
        self.assertEqual(self.call(1652,p.SavePlayerPreferencesProto(),p.SavePlayerPreferencesOutProto).result,2)

    def test_native_settings_groups_persist_without_resetting_other_groups(self):
        requests=[(959,p.SaveSocialPlayerSettingsProto(settings=p.SocialPlayerSettingsProto(disable_last_pokemon_caught=True,disable_location_card_background=True)),p.SaveSocialPlayerSettingsOutProto,'social_player_settings','settings'),
            (999,p.SaveCombatPlayerPreferencesProto(preferences=p.CombatPlayerPreferencesProto(friends_combat_opt_out=True)),p.SaveCombatPlayerPreferencesOutProto,'combat_player_preferences','preferences'),
            (151,p.SetContactSettingsProto(contact_settings_proto=p.ContactSettingsProto(send_push_notifications=True)),p.SetContactSettingsOutProto,'contact_settings_proto','contact_settings_proto')]
        for method,req,response,_,_ in requests:
            result=self.call(method,req,response)
            self.assertEqual(getattr(result,'result' if method!=151 else 'status'),1)
            with self.database.transaction() as db:revision=db.execute('SELECT revision FROM players').fetchone()[0]
            self.call(method,req,response)
            with self.database.transaction() as db:self.assertEqual(revision,db.execute('SELECT revision FROM players').fetchone()[0])
        self.game=Game(self.config,Database(self.database.path));player=self.call(2,p.GetPlayerProto(),p.GetPlayerOutProto).player
        for method,req,response,destination,source in requests:
            self.assertEqual(getattr(player,destination),getattr(req,source))
            result=self.call(method,type(req)(),response)
            self.assertEqual(getattr(result,'result' if method!=151 else 'status'),2)
