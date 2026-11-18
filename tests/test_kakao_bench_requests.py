# -*- coding: utf-8 -*-
"""서버 요청 처리 시간 측정 스크립트 (scripts/bench_server_requests.py) 가 계속 돌아가는지 - 작은 규모로.
수치(100ms 목표)는 PC 마다 달라 여기서는 보지 않는다. 운영 규모 수치는 스크립트를 직접 돌려 본다"""

import shutil
import tempfile
import unittest

from scripts import bench_server_requests as bench


class BenchRequestsTests(unittest.TestCase):
    def test_small_scale_runs_every_screen_request(self):
        cache = tempfile.mkdtemp(prefix='kcs_bench_test_')
        self.addCleanup(shutil.rmtree, cache, True)
        result = bench.measure('small', repeat=1, cache_dir=cache)
        names = [row['name'] for row in result['requests']]
        for expected in ('join (자동 로그인)', 'history', 'drawer_query:image', 'mention_list', 'search', 'admin_users',
                         'event_list', 'call_history', 'room_create', 'read'):
            self.assertIn(expected, names)
        self.assertEqual([(row['name'], row['error']) for row in result['requests'] if row['error']], [])
        self.assertTrue(all(row['bytes'] > 0 for row in result['requests']))
        storm = result['storm']
        self.assertEqual(storm['ok'], storm['count'])               # 몰려도 모두 welcome 을 받는다
        self.assertGreater(storm['sends'], 0)
        text = bench.render(result)
        self.assertIn('| drawer_query:image |', text)
        self.assertIn('로그인 몰림', text)


if __name__ == '__main__':
    unittest.main()
