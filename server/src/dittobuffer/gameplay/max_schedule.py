"""Opt-in Max rotations, independent of gameplay writes and season systems.

Integration API::

    schedule = validate_config(config.get('schedule', {}))
    window = select_window(schedule, stamp_ms, base_pool=max_config['pools'])

Disabled schedules return None so the existing materializer can retain its
current behavior. An enabled schedule returns exactly ONE winning segment.
Derive the candidate's identity from (candidate_id, window.key), and materialize
only that segment; do not add event spots alongside regular spots. Persist its
pool and difficulty multipliers in the new station/attempt snapshot. Never
change an already started battle when a schedule changes.

Weekdays use Monday=0. Date filters apply to the local START date, including
overnight windows. Intervals are [start, end). Priority wins descending; equal
priorities use the event ID ascending. Multipliers are explicit custom tuning,
not official game balance; event keys replace regular multiplier keys.

No clocks, databases, device APIs, assets, family configuration or events are
changed by this module. The supplied Monday18:00-19:00 example is disabled.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
DAY_MS = 86_400_000
DEFAULT_TEMPLATE = {
    'enabled': False,
    'timezone': 'Europe/Amsterdam',
    'fold_policy': 'earlier',
    'gap_policy': 'skip',
    'regular': {'rotation_seconds': 86400, 'anchor_ms': 0},
    'events': [{'id': 'max-monday', 'enabled': False, 'weekdays': [0],
                'start': '18:00', 'end': '19:00', 'priority': 100}],
}


@dataclass(frozen=True)
class Selection:
    key: str
    start_ms: int
    end_ms: int
    pool: tuple
    difficulty_multipliers: dict
    source: str
    timezone: str

    @property
    def slot(self):
        """Stable positive SQLite-sized segment identity, not a time counter."""
        return int(hashlib.sha256(self.key.encode()).hexdigest()[:15], 16)

    def as_dict(self):
        return deepcopy(dict(key=self.key, start_ms=self.start_ms,
                             end_ms=self.end_ms, pool=list(self.pool),
                             difficulty_multipliers=self.difficulty_multipliers,
                             source=self.source, timezone=self.timezone))


@dataclass(frozen=True)
class _Event:
    id: str
    enabled: bool
    priority: int
    start_minute: int | None
    end_minute: int | None
    weekdays: frozenset
    dates: frozenset | None
    start_date: date | None
    end_date: date | None
    absolute_start: int | None
    absolute_end: int | None
    pool: tuple | None
    multipliers: dict
    fold_policy: str
    gap_policy: str


@dataclass(frozen=True)
class Schedule:
    enabled: bool
    timezone: str
    zone: ZoneInfo
    rotation_ms: int
    anchor_ms: int
    pool: tuple | None
    multipliers: dict
    events: tuple


@dataclass(frozen=True)
class _Window:
    id: str
    source: str
    start_ms: int
    end_ms: int
    priority: int
    pool: tuple
    multipliers: dict

    @property
    def rank(self):
        return (-self.priority, self.id, self.start_ms, self.end_ms)


def default_config():
    return deepcopy(DEFAULT_TEMPLATE)


def _keys(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError('Invalid Max schedule ' + label + ' fields')


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Invalid Max schedule ' + label)
    return value


def _boolean(value, label):
    if type(value) is not bool:
        raise ValueError('Invalid Max schedule ' + label)
    return value


def _date(value):
    if not isinstance(value, str):
        raise ValueError('Max schedule date requires YYYY-MM-DD')
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise ValueError('Max schedule date requires YYYY-MM-DD') from None
    if result.isoformat() != value:
        raise ValueError('Max schedule date requires YYYY-MM-DD')
    return result


def _minute(value, *, end=False):
    if end and value == '24:00':
        return 1440
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
        raise ValueError('Max schedule local time requires HH:MM')
    hour, minute = map(int, value.split(':'))
    return hour * 60 + minute


def _absolute(value):
    if not isinstance(value, str):
        raise ValueError('Max event timestamp requires an explicit UTC offset')
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.utcoffset() is None:
            raise ValueError()
        return int(stamp.timestamp() * 1000)
    except (ValueError, OverflowError):
        raise ValueError('Max event timestamp requires an explicit UTC offset') from None


def _pool(value):
    if value is None:
        return None
    if not isinstance(value, list) or not value or len(value) > 1024:
        raise ValueError('Max schedule pool requires explicit species entries')
    result = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError('Invalid Max schedule pool entry')
        _integer(entry.get('species'), 1, 100000, 'pool species')
        _integer(entry.get('weight', 1), 1, 1000000, 'pool weight')
        try:
            result.append(json.loads(json.dumps(entry, allow_nan=False)))
        except (TypeError, ValueError):
            raise ValueError('Max schedule pool must contain finite JSON values') from None
    # The existing Max species/form/eligibility validator remains authoritative.
    return tuple(result)


def _multipliers(value):
    if not isinstance(value, dict):
        raise ValueError('Max schedule multipliers require a mapping')
    for key, factor in value.items():
        if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', key):
            raise ValueError('Invalid Max schedule multiplier name')
        if type(factor) not in (int, float) or not math.isfinite(factor) or not 0 < factor <= 1000:
            raise ValueError('Invalid Max schedule custom multiplier')
    return deepcopy(value)


def _policies(value, default_fold, default_gap):
    fold = value.get('fold_policy', default_fold)
    gap = value.get('gap_policy', default_gap)
    if fold not in ('earlier', 'later', 'span', 'reject') or gap not in ('skip', 'shift_forward', 'reject'):
        raise ValueError('Invalid Max schedule DST policy')
    return fold, gap


def validate_config(config):
    """Validate an independent schedule; never enable one by default.

    `regular.pool` may be omitted and supplied as select_window(base_pool=...).
    Events require enabled=True individually. `starts_at`/`ends_at` are absolute
    aware ISO timestamps; otherwise local start/end and optional date/weekday
    filters apply. Date bounds are inclusive local start dates.
    """
    _keys(config, ('enabled', 'timezone', 'fold_policy', 'gap_policy', 'regular', 'events'), 'root')
    enabled = _boolean(config.get('enabled', False), 'enabled')
    name = config.get('timezone', 'Europe/Amsterdam')
    try:
        if not isinstance(name, str):
            raise ValueError()
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('Invalid Max schedule timezone') from None
    fold, gap = _policies(config, 'earlier', 'skip')
    regular = config.get('regular', {})
    _keys(regular, ('rotation_seconds', 'anchor_ms', 'pool', 'difficulty_multipliers'), 'regular')
    rotation = _integer(regular.get('rotation_seconds', 86400), 60, 604800, 'rotation_seconds') * 1000
    anchor = _integer(regular.get('anchor_ms', 0), 0, 253370764800000, 'anchor_ms')
    pool = _pool(regular.get('pool'))
    multipliers = _multipliers(regular.get('difficulty_multipliers', {}))
    raw = config.get('events', [])
    if not isinstance(raw, list) or len(raw) > 128:
        raise ValueError('Invalid Max schedule events')
    events, ids = [], set()
    for item in raw:
        _keys(item, ('id', 'enabled', 'priority', 'weekdays', 'dates', 'start_date', 'end_date',
                     'start', 'end', 'starts_at', 'ends_at', 'pool', 'difficulty_multipliers',
                     'fold_policy', 'gap_policy'), 'event')
        ident = item.get('id')
        if not isinstance(ident, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}', ident) or ident in ids:
            raise ValueError('Max schedule event IDs must be unique and stable')
        ids.add(ident)
        active = _boolean(item.get('enabled', False), 'event enabled')
        priority = _integer(item.get('priority', 0), -1000000, 1000000, 'priority')
        efold, egap = _policies(item, fold, gap)
        absolute = 'starts_at' in item or 'ends_at' in item
        if absolute:
            if any(k in item for k in ('start', 'end', 'weekdays', 'dates', 'start_date', 'end_date')):
                raise ValueError('Max event cannot mix absolute and local windows')
            first, last = _absolute(item.get('starts_at')), _absolute(item.get('ends_at'))
            if not 0 < last - first <= 366 * DAY_MS:
                raise ValueError('Invalid Max absolute event interval')
            start = end = start_date = end_date = dates = None
            weekdays = frozenset()
        else:
            start, end = _minute(item.get('start')), _minute(item.get('end'), end=True)
            if start == end:
                raise ValueError('Max event window must not have zero wall duration')
            days = item.get('weekdays', list(range(7)))
            if not isinstance(days, list) or not days or any(type(v) is not int for v in days) or len(days) != len(set(days)):
                raise ValueError('Invalid Max schedule weekdays')
            weekdays = frozenset(_integer(v, 0, 6, 'weekday') for v in days)
            raw_dates = item.get('dates')
            if raw_dates is not None and (not isinstance(raw_dates, list) or not raw_dates):
                raise ValueError('Max schedule dates must be a nonempty list')
            dates = frozenset(_date(v) for v in raw_dates) if raw_dates is not None else None
            if dates is not None and len(dates) != len(raw_dates):
                raise ValueError('Duplicate Max schedule date')
            start_date = _date(item['start_date']) if 'start_date' in item else None
            end_date = _date(item['end_date']) if 'end_date' in item else None
            if start_date and end_date and start_date > end_date:
                raise ValueError('Invalid Max schedule date range')
            first = last = None
        events.append(_Event(ident, active, priority, start, end, weekdays, dates,
                             start_date, end_date, first, last, _pool(item.get('pool')),
                             _multipliers(item.get('difficulty_multipliers', {})), efold, egap))
    return Schedule(enabled, name, zone, rotation, anchor, pool, multipliers, tuple(events))


def _local_stamp(naive, zone, fold_policy, gap_policy, *, end=False):
    """Resolve wall time by UTC round trip, rejecting fabricated DST offsets."""
    valid, forward = set(), []
    for fold in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=fold)
        utc = aware.astimezone(UTC)
        back = utc.astimezone(zone).replace(tzinfo=None)
        if back == naive:
            valid.add(int(utc.timestamp() * 1000))
        elif back > naive:
            forward.append((back, int(utc.timestamp() * 1000)))
    if not valid:
        if gap_policy == 'skip':
            return None
        if gap_policy == 'shift_forward' and forward:
            return min(forward)[1]
        raise ValueError('Nonexistent Max event local time: ' + naive.isoformat())
    if len(valid) > 1 and fold_policy == 'reject':
        raise ValueError('Ambiguous Max event local time: ' + naive.isoformat())
    return max(valid) if fold_policy == 'later' or fold_policy == 'span' and end else min(valid)


def _instances(schedule, base_pool, first_ms, last_ms):
    first = datetime.fromtimestamp(first_ms / 1000, schedule.zone).date() - timedelta(days=1)
    last = datetime.fromtimestamp(last_ms / 1000, schedule.zone).date() + timedelta(days=1)
    for event in schedule.events:
        if not event.enabled:
            continue
        pool = event.pool if event.pool is not None else base_pool
        factors = schedule.multipliers | event.multipliers
        if event.absolute_start is not None:
            yield _Window(event.id, 'event:' + event.id, event.absolute_start,
                          event.absolute_end, event.priority, pool, factors)
            continue
        owner = first
        while owner <= last:
            if (owner.weekday() in event.weekdays and
                    (event.dates is None or owner in event.dates) and
                    (event.start_date is None or owner >= event.start_date) and
                    (event.end_date is None or owner <= event.end_date)):
                midnight = datetime.combine(owner, time())
                begin = midnight + timedelta(minutes=event.start_minute)
                finish = midnight + timedelta(minutes=event.end_minute)
                if event.end_minute <= event.start_minute:
                    finish += timedelta(days=1)
                start = _local_stamp(begin, schedule.zone, event.fold_policy, event.gap_policy)
                end = _local_stamp(finish, schedule.zone, event.fold_policy, event.gap_policy, end=True)
                if start is not None and end is not None and start < end:
                    yield _Window(event.id, 'event:' + event.id, start, end,
                                  event.priority, pool, factors)
            owner += timedelta(days=1)


def select_window(config, stamp_ms, *, base_pool=None):
    """Return one stable winning segment, or None for a disabled schedule.

    Regular rotations use UTC epoch slots; local event windows use the supplied
    IANA timezone/DST rules. A higher-priority event splits the regular segment
    at its boundaries, so callers cannot materialize both at one location.
    Changing the pool/multipliers changes the segment key. Returned collections
    are detached copies; modifying them does not change the validated schedule.
    """
    schedule = config if isinstance(config, Schedule) else validate_config(config)
    stamp = _integer(stamp_ms, 0, 253370764800000 - 366 * DAY_MS, 'stamp_ms')
    if not schedule.enabled:
        return None
    pool = schedule.pool if schedule.pool is not None else _pool(base_pool)
    if pool is None:
        raise ValueError('Enabled Max schedule requires a regular/base pool')
    slot = (stamp - schedule.anchor_ms) // schedule.rotation_ms
    start = schedule.anchor_ms + slot * schedule.rotation_ms
    regular = _Window('regular', 'regular', start, start + schedule.rotation_ms,
                      -2147483648, pool, schedule.multipliers)
    windows = [regular, *_instances(schedule, pool, regular.start_ms, regular.end_ms)]
    winner = min((v for v in windows if v.start_ms <= stamp < v.end_ms), key=lambda v: v.rank)
    # An absolute event may outlive this regular slot. Include every stronger
    # recurring window across its interval before deciding the stable segment.
    if winner.source != 'regular':
        windows = [regular, *_instances(schedule, pool, winner.start_ms, winner.end_ms)]
    start, end = winner.start_ms, winner.end_ms
    for other in windows:
        if other.rank >= winner.rank:
            continue
        if other.end_ms <= stamp:
            start = max(start, other.end_ms)
        elif other.start_ms > stamp:
            end = min(end, other.start_ms)
    signature = dict(source=winner.source, start_ms=start, end_ms=end,
                     pool=list(winner.pool), difficulty_multipliers=winner.multipliers,
                     timezone=schedule.timezone)
    digest = hashlib.sha256(json.dumps(signature, sort_keys=True, separators=(',', ':'),
                                      allow_nan=False).encode()).hexdigest()[:24]
    return Selection(winner.source + ':' + digest, start, end, deepcopy(winner.pool),
                     deepcopy(winner.multipliers), winner.source, schedule.timezone)
