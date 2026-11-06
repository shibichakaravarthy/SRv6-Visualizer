import asyncio
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from collectors.configuration_collector import collect_configured_files
from collectors.docker_reader import _run
from collectors.live_observation_collector import collect_live_observations


class LiveCollectionTests(unittest.TestCase):
    def test_subprocess_timeout_keeps_partial_output(self):
        with patch('collectors.docker_reader.subprocess.run', side_effect=
                   subprocess.TimeoutExpired(['ip'], 1, output=b'partial')):
            result = asyncio.run(_run(['ip'], 1))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['raw'], 'partial')

    def test_config_sources_keep_exact_text_and_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('topology', 'configs', 'deployment'):
                (root / name).mkdir()
            topology = root / 'topology/lab.yml'
            topology.write_text('name: lab\n')
            (root / 'deployment/R1.sh').write_text('ip -6 route add "$PREFIX" dev eth1\n')
            result = collect_configured_files(topology)
            self.assertEqual(result['status'], 'available')
            self.assertEqual(len(result['files']), 2)
            script = next(r for r in result['files'] if r['path'].endswith('.sh'))
            self.assertIn('$PREFIX', script['content'])
            self.assertEqual(len(script['sha256']), 64)

    def test_capture_and_probes_do_not_fabricate_execution(self):
        class Reader:
            async def observe(self, cid, lab, command):
                return {'status': 'missing', 'error': 'tcpdump unavailable', 'raw': ''}
            async def json_observation(self, *args):
                return {'status': 'available', 'data': [], 'raw': '[]'}
            async def probe(self, cid, lab, destination):
                return {'status': 'available', 'raw': '3 packets received'}
        nodes = [{'id': str(i), 'name': f'R{i}', 'lab_id': 'lab', 'running': True,
                  'ipv6_addresses': {'data': [{'addr_info': [{'local': f'2001:db8::{i}'}]}]},
                  'configuration': {}, 'isis': {}, 'isis_routes': {}, 'link_observation': {}}
                 for i in (1, 2)]
        report = asyncio.run(collect_live_observations(Reader(), nodes, [{'id': n['id']} for n in nodes]))
        self.assertEqual(len(report['probes']), 2)
        self.assertEqual(report['status'], 'partial')
        for row in report['records']:
            self.assertFalse(row['coverage_complete'])
            self.assertEqual(set(row['levels']), {'OCP', 'ON', 'ODP', 'OP', 'ONW'})
            self.assertTrue(all(v['status'] == 'missing' for v in row['levels'].values()))
