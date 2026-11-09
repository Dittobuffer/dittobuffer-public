"""Pure schedule tests: no gameplay DB, device, fake participants or clocks."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest

from dittobuffer.gameplay.max_schedule import default_config, select_window, validate_config


def ms(value):
    return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)


POOL = [{'species': 831, 'weight': 1, 'mode': 1, 'tier': 1, 'level': 10}]


def event(ident='max-monday', **values):
    return dict(id=ident, enabled=True, weekdays=[0], start='18:00', end='19:00',
                priority=100) | values


def config(events=None, **values):
    return dict(enabled=True, timezone='Europe/Amsterdam',
                regular={'rotation_seconds': 86400, 'pool': deepcopy(POOL)},
                events=events or []) | values


class MaxScheduleTests(unittest.TestCase):
    def test_default_template_and_missing_flag_are_disabled(self):
        raw = default_config()
        self.assertFalse(raw['enabled'])
        self.assertFalse(raw['events'][0]['enabled'])
        self.assertEqual(('18:00', '19:00'), (raw['events'][0]['start'], raw['events'][0]['end']))
        self.assertIsNone(select_window(raw, ms('2026-10-26T18:30:00+01:00')))
        self.assertIsNone(select_window({}, ms('2026-10-26T18:30:00+01:00')))

    def test_events_require_individual_explicit_enable(self):
        raw = default_config()
        raw['enabled'] = True
        value = select_window(raw, ms('2026-10-26T18:30:00+01:00'), base_pool=POOL)
        self.assertEqual('regular', value.source)

    def test_monday_summer_window_uses_local_time(self):
        value = select_window(config([event()]), ms('2026-07-06T18:30:00+02:00'))
        self.assertEqual('event:max-monday', value.source)
        self.assertEqual(ms('2026-07-06T16:00:00Z'), value.start_ms)
        self.assertEqual(ms('2026-07-06T17:00:00Z'), value.end_ms)

    def test_monday_winter_window_uses_local_time(self):
        value = select_window(config([event()]), ms('2026-10-26T18:30:00+01:00'))
        self.assertEqual(ms('2026-10-26T17:00:00Z'), value.start_ms)
        self.assertEqual(ms('2026-10-26T18:00:00Z'), value.end_ms)

    def test_weekday_and_half_open_endpoints(self):
        raw = config([event()])
        self.assertEqual('regular', select_window(raw, ms('2026-07-05T18:30:00+02:00')).source)
        self.assertEqual('regular', select_window(raw, ms('2026-07-06T17:59:59.999+02:00')).source)
        self.assertEqual('event:max-monday', select_window(raw, ms('2026-07-06T18:00:00+02:00')).source)
        self.assertEqual('regular', select_window(raw, ms('2026-07-06T19:00:00+02:00')).source)

    def test_regular_epoch_rotation_is_deterministic(self):
        raw = config()
        first = select_window(raw, ms('2026-07-06T01:00:00Z'))
        later = select_window(validate_config(raw), ms('2026-07-06T23:59:59.999Z'))
        following = select_window(raw, ms('2026-07-07T00:00:00Z'))
        self.assertEqual(first.key, later.key)
        self.assertEqual(ms('2026-07-06T00:00:00Z'), first.start_ms)
        self.assertEqual(ms('2026-07-07T00:00:00Z'), first.end_ms)
        self.assertNotEqual(first.key, following.key)
        self.assertEqual(first.slot, later.slot)
        self.assertGreater(first.slot, 0)
        self.assertLess(first.slot, 2**63)

    def test_regular_anchor_is_honored(self):
        anchor = ms('2026-07-06T05:00:00Z')
        raw = config(regular={'pool': POOL, 'rotation_seconds': 3600, 'anchor_ms': anchor})
        value = select_window(raw, anchor + 12345)
        self.assertEqual((anchor, anchor + 3600000), (value.start_ms, value.end_ms))

    def test_epoch_zero_is_a_valid_past_timestamp(self):
        value = select_window(config(), 0)
        self.assertEqual((0, 86400000), (value.start_ms, value.end_ms))

    def test_selected_segment_identity_is_stable_inside_every_boundary(self):
        raw = config([event(), event('short', start='18:15', end='18:30', priority=200)])
        for hour, minute in [(17, 30), (18, 5), (18, 20), (18, 40), (19, 10)]:
            stamp = ms(f'2026-07-06T{hour:02}:{minute:02}:00+02:00')
            value = select_window(raw, stamp)
            for point in (value.start_ms, (value.start_ms + value.end_ms) // 2, value.end_ms - 1):
                other = select_window(raw, point)
                self.assertEqual((value.key, value.start_ms, value.end_ms),
                                 (other.key, other.start_ms, other.end_ms))

    def test_priority_splits_regular_and_event_without_duplicate_windows(self):
        raw = config([event(), event('short', start='18:15', end='18:30', priority=200,
                                    pool=[{'species': 1}])])
        values = [select_window(raw, ms('2026-07-06T' + value + '+02:00'))
                  for value in ('17:30:00', '18:05:00', '18:20:00', '18:40:00', '19:10:00')]
        self.assertEqual(['regular', 'event:max-monday', 'event:short', 'event:max-monday', 'regular'],
                         [v.source for v in values])
        self.assertEqual([ms('2026-07-06T' + value + '+02:00')
                          for value in ('18:00:00', '18:15:00', '18:30:00', '19:00:00')],
                         [v.end_ms for v in values[:-1]])
        self.assertEqual([v.end_ms for v in values[:-1]], [v.start_ms for v in values[1:]])
        self.assertEqual(5, len({v.key for v in values}))
        self.assertEqual(1, values[2].pool[0]['species'])

    def test_equal_priorities_are_independent_of_config_order(self):
        alpha, zebra = event('alpha'), event('zebra')
        stamp = ms('2026-07-06T18:30:00+02:00')
        a = select_window(config([alpha, zebra]), stamp)
        b = select_window(config([zebra, alpha]), stamp)
        self.assertEqual('event:alpha', a.source)
        self.assertEqual(a.key, b.key)

    def test_explicit_negative_event_priority_still_overrides_regular(self):
        self.assertEqual('event:max-monday', select_window(config([event(priority=-10)]),
                         ms('2026-07-06T18:30:00+02:00')).source)

    def test_pool_and_custom_multiplier_change_identity(self):
        stamp = ms('2026-07-06T18:30:00+02:00')
        initial = select_window(config([event()]), stamp)
        replacement = select_window(config([event(pool=[{'species': 4}],
                                       difficulty_multipliers={'boss_hp': .25})]), stamp)
        self.assertNotEqual(initial.key, replacement.key)
        self.assertEqual({'boss_hp': .25}, replacement.difficulty_multipliers)
        self.assertEqual(4, replacement.pool[0]['species'])

    def test_event_multiplier_keys_replace_regular_keys(self):
        raw = config([event(difficulty_multipliers={'boss_hp': .25})],
                     regular={'pool': POOL, 'difficulty_multipliers': {'boss_hp': 2, 'player_damage': 1.5}})
        value = select_window(raw, ms('2026-07-06T18:30:00+02:00'))
        self.assertEqual({'boss_hp': .25, 'player_damage': 1.5}, value.difficulty_multipliers)

    def test_overnight_window_belongs_to_local_start_weekday(self):
        raw = config([event(start='23:00', end='01:00')])
        value = select_window(raw, ms('2026-07-07T00:30:00+02:00'))
        self.assertEqual('event:max-monday', value.source)
        self.assertEqual(ms('2026-07-06T23:00:00+02:00'), value.start_ms)
        self.assertEqual(ms('2026-07-07T01:00:00+02:00'), value.end_ms)
        self.assertEqual('regular', select_window(raw, value.end_ms).source)

    def test_midnight_end_is_not_an_extra_day(self):
        value = select_window(config([event(start='23:00', end='24:00')]),
                              ms('2026-07-06T23:30:00+02:00'))
        self.assertEqual(ms('2026-07-07T00:00:00+02:00'), value.end_ms)

    def test_specific_dates_exclude_past_and_future_occurrences(self):
        raw = config([event(dates=['2026-07-06'])])
        for day in ('2026-06-29', '2026-07-13', '2027-07-05'):
            self.assertEqual('regular', select_window(raw, ms(day + 'T18:30:00+02:00')).source)
        self.assertEqual('event:max-monday', select_window(raw, ms('2026-07-06T18:30:00+02:00')).source)

    def test_date_bounds_are_inclusive_local_start_dates(self):
        raw = config([event(start_date='2026-07-06', end_date='2026-07-13', start='23:00', end='01:00')])
        for day in ('2026-07-07', '2026-07-14'):
            self.assertEqual('event:max-monday', select_window(raw, ms(day + 'T00:30:00+02:00')).source)
        self.assertEqual('regular', select_window(raw, ms('2026-07-21T00:30:00+02:00')).source)

    def test_absolute_event_requires_aware_times_and_has_one_identity(self):
        raw = config([{'id': 'one-off', 'enabled': True, 'priority': 20,
                       'starts_at': '2026-10-24T18:00:00+02:00',
                       'ends_at': '2026-10-26T10:00:00+01:00'}])
        first = select_window(raw, ms('2026-10-24T20:00:00+02:00'))
        next_day = select_window(raw, ms('2026-10-25T20:00:00+01:00'))
        self.assertEqual('event:one-off', first.source)
        self.assertEqual(first.key, next_day.key)
        self.assertEqual(41 * 3600000, first.end_ms - first.start_ms)

    def test_stronger_recurring_window_splits_long_absolute_event(self):
        raw = config([{'id': 'one-off', 'enabled': True, 'priority': 10,
                       'starts_at': '2026-07-01T00:00:00Z', 'ends_at': '2026-07-20T00:00:00Z'}, event()])
        before = select_window(raw, ms('2026-07-06T17:00:00+02:00'))
        middle = select_window(raw, ms('2026-07-06T18:30:00+02:00'))
        after = select_window(raw, ms('2026-07-06T20:00:00+02:00'))
        self.assertEqual('event:one-off', before.source)
        self.assertEqual('event:max-monday', middle.source)
        self.assertEqual(before.end_ms, middle.start_ms)
        self.assertEqual(middle.end_ms, after.start_ms)
        self.assertNotEqual(before.key, after.key)

    def test_spring_nonexistent_window_is_skipped_by_default(self):
        raw = config([event(weekdays=[6], start='02:15', end='02:45', dates=['2026-03-29'])])
        self.assertEqual('regular', select_window(raw, ms('2026-03-29T01:30:00Z')).source)

    def test_spring_gap_shift_forward_preserves_minutes(self):
        raw = config([event(weekdays=[6], start='02:15', end='02:45', dates=['2026-03-29'],
                            gap_policy='shift_forward')])
        value = select_window(raw, ms('2026-03-29T01:30:00Z'))
        self.assertEqual('event:max-monday', value.source)
        self.assertEqual(ms('2026-03-29T01:15:00Z'), value.start_ms)
        self.assertEqual(ms('2026-03-29T01:45:00Z'), value.end_ms)

    def test_spring_gap_reject_is_explicit(self):
        raw = config([event(weekdays=[6], start='02:15', end='02:45', dates=['2026-03-29'],
                            gap_policy='reject')])
        with self.assertRaisesRegex(ValueError, 'Nonexistent'):
            select_window(raw, ms('2026-03-29T01:30:00Z'))

    def test_fall_fold_earlier_and_later_choose_real_distinct_intervals(self):
        for policy, hour in [('earlier', '00'), ('later', '01')]:
            with self.subTest(policy=policy):
                raw = config([event(weekdays=[6], start='02:15', end='02:45', dates=['2026-10-25'],
                                    fold_policy=policy)])
                value = select_window(raw, ms('2026-10-25T' + hour + ':30:00Z'))
                self.assertEqual('event:max-monday', value.source)
                self.assertEqual(ms('2026-10-25T' + hour + ':15:00Z'), value.start_ms)
                self.assertEqual(ms('2026-10-25T' + hour + ':45:00Z'), value.end_ms)

    def test_fall_fold_span_covers_both_occurrences(self):
        raw = config([event(weekdays=[6], start='02:15', end='02:45', dates=['2026-10-25'], fold_policy='span')])
        first = select_window(raw, ms('2026-10-25T00:30:00Z'))
        second = select_window(raw, ms('2026-10-25T01:30:00Z'))
        self.assertEqual(first.key, second.key)
        self.assertEqual(90 * 60000, first.end_ms - first.start_ms)

    def test_fall_fold_reject_is_explicit(self):
        raw = config([event(weekdays=[6], start='02:15', end='02:45', dates=['2026-10-25'], fold_policy='reject')])
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            select_window(raw, ms('2026-10-25T00:30:00Z'))

    def test_overnight_dst_interval_uses_real_elapsed_time(self):
        raw = config([event(weekdays=[5], start='23:00', end='04:00', dates=['2026-10-24'])])
        value = select_window(raw, ms('2026-10-25T01:30:00Z'))
        self.assertEqual(6 * 3600000, value.end_ms - value.start_ms)

    def test_past_future_and_other_timezone_are_pure(self):
        raw = config([event()], timezone='UTC')
        before = deepcopy(raw)
        for stamp in ['2025-07-07T18:30:00Z', '2030-07-08T18:30:00Z']:
            value = select_window(raw, ms(stamp))
            self.assertEqual('event:max-monday', value.source)
            self.assertEqual('UTC', value.timezone)
        self.assertEqual(before, raw)

    def test_results_and_default_template_do_not_mutate_configuration(self):
        raw = config([event()]); normalized = validate_config(raw)
        first = select_window(normalized, ms('2026-07-06T18:30:00+02:00'))
        first.pool[0]['species'] = 999
        first.difficulty_multipliers['boss_hp'] = 99
        value = first.as_dict(); value['pool'][0]['species'] = 1
        second = select_window(normalized, ms('2026-07-06T18:30:00+02:00'))
        self.assertEqual(831, second.pool[0]['species'])
        self.assertEqual({}, second.difficulty_multipliers)
        a = default_config(); a['events'][0]['enabled'] = True
        self.assertFalse(default_config()['events'][0]['enabled'])

    def test_enabled_schedule_requires_released_base_pool(self):
        with self.assertRaisesRegex(ValueError, 'pool'):
            select_window({'enabled': True}, ms('2026-07-06T18:30:00Z'))
        value = select_window({'enabled': True}, ms('2026-07-06T18:30:00Z'), base_pool=POOL)
        self.assertEqual(831, value.pool[0]['species'])

    def test_invalid_timezone_flags_bounds_and_multipliers(self):
        bad = [dict(enabled=1), dict(timezone='Mars/Olympus'), dict(gap_policy='guess'),
               dict(fold_policy='both-ish'), dict(regular={'rotation_seconds': 0}),
               dict(regular={'anchor_ms': True}), dict(regular={'pool': []}),
               dict(regular={'pool': [{'species': 1, 'weight': 0}]}),
               dict(regular={'difficulty_multipliers': {'boss_hp': 0}}),
               dict(regular={'difficulty_multipliers': {'boss_hp': float('nan')}}),
               dict(regular={'difficulty_multipliers': {'boss_hp': True}}),
               dict(regular={'difficulty_multipliers': {'Boss HP': 1}})]
        for values in bad:
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_config(config() | values)

    def test_invalid_event_windows_and_duplicate_ids(self):
        bad = [[event(start='24:00')], [event(start='18:00', end='18:00')],
               [event(weekdays=[7])], [event(weekdays=[True])], [event(weekdays=[0, 0])], [event(weekdays=[[]])],
               [event(dates=['2026-02-30'])], [event(dates=[])],
               [event(start_date='2026-07-10', end_date='2026-07-01')],
               [event('same'), event('same')], [event(enabled=1)],
               [{'id': 'abs', 'enabled': True, 'starts_at': '2026-07-01T18:00:00',
                 'ends_at': '2026-07-01T19:00:00Z'}],
               [{'id': 'abs', 'enabled': True, 'starts_at': '2026-07-01T18:00:00Z',
                 'ends_at': '2026-07-01T17:00:00Z'}],
               [event(starts_at='2026-07-01T18:00:00Z', ends_at='2026-07-01T19:00:00Z')]]
        for events in bad:
            with self.subTest(events=events), self.assertRaises(ValueError):
                validate_config(config(events))

    def test_untrusted_timestamps_are_rejected(self):
        for value in (True, None, 'now', float('nan'), float('inf'), -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                select_window(config(), value)


if __name__ == '__main__':
    unittest.main()
