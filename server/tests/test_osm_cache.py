"""On-the-fly street cells: small OSM bboxes, a gzip cache and a changing epoch."""
import gzip
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dittobuffer.world import osm_cache, world_map

ROOT = Path(__file__).resolve().parents[1]


class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *values):
        return False

    def read(self):
        return self.body


class OsmCacheTests(unittest.TestCase):
    def test_cells_are_small_and_keep_the_requested_point_inside(self):
        key = osm_cache._key(52.3702, 4.8952)
        bottom, left, top, right = osm_cache._bounds(key)
        self.assertAlmostEqual(top - bottom, osm_cache.CELL_DEG)
        self.assertAlmostEqual(right - left, osm_cache.CELL_DEG)
        self.assertTrue(bottom <= 52.3702 <= top and left <= 4.8952 <= right)
        self.assertEqual(osm_cache._filename(key).name, f'{key[0]}_{key[1]}.osm.gz')

    def test_fetch_writes_a_gzip_cell_and_status_reports_it(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'builds') as folder:
            areas = Path(folder) / 'areas'
            body = b'<?xml version="1.0" encoding="UTF-8"?><osm version="0.6"></osm>'
            with patch.object(osm_cache, 'AREA_DIR', areas), patch(
                    'dittobuffer.world.osm_cache.urllib.request.urlopen',
                    return_value=_Response(body)):
                key = osm_cache._key(52.3702, 4.8952)
                osm_cache._fetch(key)
                target = areas / osm_cache._filename(key).name
                # A worker thread left running by another test can add its own
                # cells to this directory, so assert on the requested cell.
                self.assertTrue(target.is_file())
                self.assertIn(b'<osm', gzip.open(target, 'rb').read())
                status = osm_cache.status({'osm_download': True, 'osm_download_radius_m': 750})
                self.assertEqual((status['enabled'], status['radius_m']), (True, 750))
                self.assertGreaterEqual(status['cells'], 1)
                self.assertGreater(status['bytes'], 0)
                self.assertEqual(status['directory'], 'data/maps/areas')

    def test_non_xml_replies_are_not_cached(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'builds') as folder:
            areas = Path(folder) / 'areas'
            with patch.object(osm_cache, 'AREA_DIR', areas), patch(
                    'dittobuffer.world.osm_cache.urllib.request.urlopen',
                    return_value=_Response(b'<html>rate limited</html>')):
                key = osm_cache._key(1.0, 1.0)
                with self.assertRaises(ValueError):
                    osm_cache._fetch(key)
            # A pre-existing downloader can finish an unrelated valid cell
            # while AREA_DIR is patched. This response must not cache its own.
            self.assertFalse((areas / osm_cache._filename(key).name).exists())

    def test_disabled_downloads_never_start_the_worker(self):
        with patch.object(osm_cache, '_start') as start:
            osm_cache.ensure(52.37, 4.89, {'world': {'osm_download': False}})
            start.assert_not_called()

    def test_null_island_poll_never_queues_a_download(self):
        with patch.object(osm_cache, '_start') as start, \
                patch.object(osm_cache, '_queue', queue.PriorityQueue()):
            osm_cache.ensure(0.0, 0.0, {'world': {'osm_download': True}})
            start.assert_not_called()
            self.assertTrue(osm_cache._queue.empty())
        with self.assertRaises(ValueError):
            osm_cache.set_focus(0.0, 0.0)

    def test_expansion_walks_outward_ring_by_ring_and_remembers_progress(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'builds') as folder:
            areas = Path(folder) / 'areas'
            areas.mkdir(parents=True)
            with patch.object(osm_cache, 'AREA_DIR', areas), \
                    patch.object(osm_cache, '_queue', queue.PriorityQueue()), \
                    patch.object(osm_cache, '_queued', set()), \
                    patch.object(osm_cache, '_events', {}), \
                    patch.object(osm_cache, '_failed', {}), \
                    patch.object(osm_cache, '_start'):
                osm_cache._apply({'osm_download': True, 'osm_expand': True, 'osm_expand_ring_max': 2})
                osm_cache._hub, osm_cache._ring_index = None, 0
                self.assertEqual(osm_cache.set_focus(52.37, 4.89)['ring'], 0)
                steps = 0
                while osm_cache._expansion_step():
                    steps += 1
                    while not osm_cache._queue.empty():
                        _, _, key = osm_cache._queue.get()
                        osm_cache._queued.discard(key)
                        osm_cache._events.pop(key, None)
                # Rings 0, 1 and 2 were queued, then the configured cap stopped it.
                self.assertEqual((steps, osm_cache._ring_index), (3, 3))
                self.assertFalse(osm_cache._expansion_step())
                # A restarted server resumes where the previous one stopped.
                osm_cache._hub, osm_cache._ring_index = None, 0
                osm_cache._load_state()
                self.assertEqual((osm_cache._hub, osm_cache._ring_index), ((52.37, 4.89), 3))
                self.assertEqual(osm_cache.status({})['hub'], [52.37, 4.89])

    def test_expansion_has_a_hard_safety_cap_when_no_ring_cap_is_set(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'builds') as folder:
            areas = Path(folder) / 'areas'
            areas.mkdir(parents=True)
            with patch.object(osm_cache, 'AREA_DIR', areas), \
                    patch.object(osm_cache, '_queue', queue.PriorityQueue()), \
                    patch.object(osm_cache, '_queued', set()), \
                    patch.object(osm_cache, '_events', {}), \
                    patch.object(osm_cache, '_failed', {}), \
                    patch.object(osm_cache, '_start'):
                osm_cache._apply({'osm_download': True, 'osm_expand': True, 'osm_expand_ring_max': 0})
                osm_cache._hub, osm_cache._ring_index = None, 0
                osm_cache.set_focus(52.37, 4.89)
                for _ in range(20):
                    while not osm_cache._queue.empty():
                        _, _, key = osm_cache._queue.get()
                        osm_cache._queued.discard(key)
                    osm_cache._expansion_step()
                # A zero admin cap means no configured limit, not unlimited
                # growth; the safety cap stops the frontier at MAX_RING.
                self.assertEqual(osm_cache.status({})['ring'], osm_cache.MAX_RING + 1)
                self.assertFalse(osm_cache._expansion_step())
                osm_cache._apply({'osm_download': True, 'osm_expand': False})
                self.assertFalse(osm_cache._expansion_step())

    def test_admin_switches_apply_without_a_restart(self):
        value = osm_cache.status({'osm_download': True, 'osm_download_radius_m': 50000,
                                  'osm_expand': False, 'osm_request_interval_s': 3.5, 'osm_expand_ring_max': 4})
        self.assertEqual((value['radius_m'], value['expand'], value['interval_s'], value['ring_max']),
                         (osm_cache.RADIUS_MAX_M, False, 3.5, 4))
        self.assertIn('queued', value)
        # Even an admin asking for a burst stays polite to the public API.
        self.assertEqual(osm_cache.status({'osm_request_interval_s': 0.01})['interval_s'], osm_cache.MIN_INTERVAL_S)

    def test_new_cell_changes_the_client_map_epoch(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'builds') as folder:
            root = Path(folder)
            areas = root / 'data/maps/areas'
            areas.mkdir(parents=True)
            world_map.epoch.cache_clear()
            with patch('dittobuffer.world.world_map.ROOT', root):
                self.assertEqual(world_map.epoch(), world_map.epoch())
                first = world_map.epoch()
                (areas / '1_1.osm.gz').write_bytes(gzip.compress(b'<osm version="0.6"></osm>'))
                world_map.epoch.cache_clear()
                self.assertNotEqual(first, world_map.epoch())
            world_map.epoch.cache_clear()


if __name__ == '__main__':
    unittest.main()
