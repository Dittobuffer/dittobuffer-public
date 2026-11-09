"""One configurable level schedule shared by inventory and Game Master."""
from bisect import bisect_right
from itertools import accumulate
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def pinned():
    return json.loads(Path(__file__).with_name('trainer-reference.json').read_text())['levels']['playerLevel']


def cap(config):
    if config.get('progression_schedule','local')=='pinned-modern-gated':return int(config.get('_earned_level_cap',70))
    if config.get('progression_schedule','local')=='pinned-modern':return int(pinned()['defaultLevelCap'])
    return len(requirements(config))

def requirements(config):
    mode=config.get('progression_schedule','local')
    if mode in ('pinned-modern','pinned-modern-gated'):
        cumulative=pinned()['requiredExperience']
        if not 2<=len(cumulative)<=80 or cumulative[0]!=0 or any(type(v) is not int for v in cumulative) or any(b<=a for a,b in zip(cumulative,cumulative[1:])):
            raise ValueError('Invalid pinned cumulative level schedule')
        if not 1<=int(pinned()['defaultLevelCap'])<=len(cumulative):raise ValueError('Invalid pinned challenge cap')
        return [0]+[b-a for a,b in zip(cumulative,cumulative[1:])]
    if mode!='local':raise ValueError('Unknown progression schedule')
    values=config.get('level_requirements',[0]+[1000]*39)
    if not 2<=len(values)<=50 or values[0]!=0 or any(type(v) is not int or v<=0 for v in values[1:]):
        raise ValueError('Level requirements need 0 followed by positive integer XP costs (2-50 levels)')
    return values

def bounds(config,xp):
    thresholds=list(accumulate(requirements(config)))
    limit=cap(config);level=min(limit,bisect_right(thresholds,xp))
    return level,thresholds[level-1],thresholds[level] if level<limit else thresholds[level-1]
