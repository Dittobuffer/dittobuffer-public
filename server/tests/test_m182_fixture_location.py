"""Declared debug coordinates allow proximity, never earned walking credit."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dittobuffer.gameplay.game import ROOT
from dittobuffer.gameplay import friends, max_system
from tests import test_max_system as resource_support


class FixtureLocationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=resource_support.MaxTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.game=self.fixture.game
        self.private=tempfile.TemporaryDirectory(prefix='m182-locations-',dir=ROOT/'.private')
        self.addCleanup(self.private.cleanup)
        # Only the path policy is redirected; the existing isolated SQLite
        # connection remains open at its original temporary fixture path.
        self.scoped=Path(self.private.name)/'fixture-one'/'game.sqlite3'
        self.game.config.update(profile='m182-power-spots',
            geographic_fixture_locations=[{'lat':52.08,'lng':5.12}])
        self.game.config['social']['allow_fixture_location']=True
        self.clock=patch('dittobuffer.gameplay.friends.now_ms',lambda:self.fixture.clock)
        self.clock.start();self.addCleanup(self.clock.stop)

    def fix(self,**changes):
        return dict(lat=52.08,lng=5.12,accuracy=5,provider=3,
                    time=self.fixture.clock/1000,source='debug')|changes

    def observe(self,fix,path=None):
        with self.fixture.db.transaction() as db:
            db.execute('DELETE FROM social_locations')
            before=[tuple(row) for row in db.execute('SELECT * FROM walking')]
            with patch.object(self.game.db,'path',path or self.scoped):
                friends.observe(self.game,db,self.fixture.players[0],fix)
            accepted=db.execute('SELECT COUNT(*) FROM social_locations').fetchone()[0]
            self.assertEqual(before,[tuple(row) for row in db.execute('SELECT * FROM walking')])
            return accepted

    def test_declared_fix_allows_real_range_and_collection_without_walking(self):
        self.assertEqual(self.observe(self.fix()),1)
        with self.fixture.db.transaction() as db:
            self.assertTrue(max_system.near(self.game,db,self.fixture.players[0],self.fixture.stations[0]))
        self.assertEqual(self.fixture.loot().status,1)
        self.assertEqual(self.fixture.loot().status,1)
        with self.fixture.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM max_ledger').fetchone()[0],1)

    def test_rejects_undeclared_stale_bad_provider_and_inaccurate_fixes(self):
        for changes in ({'lat':52.081},{'time':self.fixture.clock/1000-31},
                        {'time':self.fixture.clock/1000+3},{'provider':1},
                        {'accuracy':30},{'source':'mock'}):
            with self.subTest(changes=changes):self.assertEqual(self.observe(self.fix(**changes)),0)

    def test_rejects_nonprivate_wrong_profile_and_disabled_flag(self):
        self.assertEqual(self.observe(self.fix(),ROOT/'builds'/'game.sqlite3'),0)
        self.game.config['profile']='family'
        self.assertEqual(self.observe(self.fix()),0)
        self.game.config['profile']='m182-power-spots'
        self.game.config['social']['allow_fixture_location']=False
        self.assertEqual(self.observe(self.fix()),0)

    def test_replay_does_not_replace_latest_presence(self):
        self.assertEqual(self.observe(self.fix()),1)
        with self.fixture.db.transaction() as db,patch.object(self.game.db,'path',self.scoped):
            original=dict(db.execute('SELECT * FROM social_locations').fetchone())
            friends.observe(self.game,db,self.fixture.players[0],self.fix(time=self.fixture.clock/1000-1))
            self.assertEqual(original,dict(db.execute('SELECT * FROM social_locations').fetchone()))

    def test_legacy_m16_scope_remains_accepted(self):
        self.game.config['profile']='m16-unit'
        self.assertEqual(self.observe(self.fix(lat=52.081),ROOT/'builds'/'fixture'/'game.sqlite3'),1)
