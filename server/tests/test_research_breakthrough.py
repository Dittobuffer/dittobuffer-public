from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo
import json
from unittest.mock import patch
from dittobuffer.gameplay import research
from tests.test_research_lifecycle import fx
from tests import test_research as fixtures
from dittobuffer.gameplay.game import p,Game,Database

def test_breakthrough_freezes_pool_at_seventh_stamp_and_concurrent_claims():
 t=fixtures.ResearchTests();t.setUp()
 try:
  t.game.research['field_pool']=[{'task':'local-stop','weight':1}]
  t.game.research['breakthrough_pool']=[{'type':'encounter','species':16,'level':10,'weight':1}]
  for index in range(7):
   with patch('dittobuffer.gameplay.research.now_ms',return_value=1800000000000+index*86400000):
    identifier=t.task();t.spin();assert t.complete(identifier).status==1
  t.game.research['breakthrough_rewards']=[{'type':'xp','amount':99999}];t.game.research['breakthrough_pool']=[{'type':'encounter','species':1,'level':10,'weight':1}]
  def claim(i):return t.call('COMPLETE_QUEST_STAMP_CARD',p.CompleteQuestStampCardProto(),p.CompleteQuestStampCardOutProto,rpc_id=8000+i).status
  with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(claim,range(2)))==[1,2]
  with t.db.transaction() as db:
   frozen=db.execute('SELECT * FROM research_breakthroughs').fetchone();assert frozen['claimed_ms'];assert json.loads(frozen['definition'])[-1]['species']==16
   assert db.execute('SELECT COUNT(*) FROM research_encounters WHERE encounter_type=5').fetchone()[0]==1
   assert db.execute('SELECT xp FROM players WHERE id=?',(t.player,)).fetchone()[0]<99999
 finally:t.tearDown()

def test_named_timezone_repeated_dst_hour_and_midnight():
 t=fixtures.ResearchTests();t.setUp()
 try:
  t.game.research['timezone']='Europe/Amsterdam'
  stamps=[int(datetime.fromisoformat(v).timestamp()*1000) for v in ('2026-10-25T02:30:00+02:00','2026-10-25T02:30:00+01:00','2026-10-25T23:59:59+01:00','2026-10-26T00:00:00+01:00')]
  days=[]
  for at in stamps:
   with patch.object(research,'now_ms',return_value=at):days.append(research.day(t.game))
  assert days==['2026-10-25']*3+['2026-10-26']
 finally:t.tearDown()
