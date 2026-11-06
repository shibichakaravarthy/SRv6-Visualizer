"""Small, offline examples of the thesis acceptance rules."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from collectors.docker_reader import DockerReader, decoded
from collectors.info_collector_improved import collect as collect_declared
from collectors.observation_collector import collect_observations
from collectors.packet_collector import compare_packets
from collectors.srv6_collector import collect_srv6
from collectors.topology_collector import configured_topology, resolve_links
from services.discovery_service import DiscoveryService
from storage.json_store import JsonStore, NAMES
from test_standalone import Reader


def write_run(root, change=None):
    """Build a tiny archive with ingress, egress and downstream observations."""
    observations, frames = [], []
    for number, (node, direction, interface) in enumerate(
            [('R4', 'ingress', 'eth1'), ('R4', 'egress', 'eth2'), ('R7', 'ingress', 'eth1')], 1):
        capture = f'raw/pcaps/{number}.pcap'
        payload = f'fixture frame {number}'.encode()
        path = root / capture
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1) +
                         struct.pack('<IIII', number, 0, len(payload), len(payload)) + payload)
        reference = {'capture_file': capture, 'frame_number': 1,
                     'frame_sha256': hashlib.sha256(payload).hexdigest()}
        point = {'node': node, 'interface': interface, 'capture_file': capture}
        header = {'source': '2001:db8::1', 'destination': 'fcbb:2000:4::1',
                  'hop_limit': 64 - number, 'payload_length': 64, 'next_header': 58}
        state = {'ipv6': header, 'srh_present': False, 'srh': None,
                 'encapsulation': {'layers': []}}
        icmp = {'type': 128, 'identifier': 10, 'sequence': 1, 'payload_sha256': 'probe-hash'}
        frames.append({'run_id': 'run-1', 'observation_point': point,
                       'frame_number': 1, 'frame_sha256': reference['frame_sha256'],
                       'reliability': {'status': 'agree'}, 'icmpv6': icmp, **state})
        observations.append({'run_id': 'run-1', 'packet_id': 'probe-1',
                             'correlation': 'unique', 'traffic_direction': 'forward',
                             'observation_point': point, 'observation_direction': direction,
                             'capture_timestamp_epoch': number, 'evidence_ref': reference,
                             'state': state, **{'icmpv6_' + key: value for key, value in icmp.items()}})
    entry = 'fcbb:2000:4::/64 encap seg6local action End.X nh6 2001:db8::7 dev eth2'
    event = {'run_id': 'run-1', 'event_id': 'event-1', 'packet_correlation_id': 'probe-1',
             'traffic_direction': 'forward', 'node': 'R4', 'declared_behaviour': 'End.X',
             'evidence': [row['evidence_ref'] for row in observations[:2]],
             'forwarding_transition': {'departed_on_link': ['R4', 'R7'],
                 'matched_forwarding_entry': {'entry': entry, 'egress_interface': 'eth2',
                                             'source_file': 'routes.txt'}}}
    if change:
        change(event, observations, frames)
    files = {'run.yaml': 'run_id: run-1\nlab_name: test\n',
             'derived/observations.jsonl': '\n'.join(map(json.dumps, observations)),
             'derived/frames.jsonl': '\n'.join(map(json.dumps, frames)),
             'derived/execution_events.jsonl': json.dumps(event),
             'raw/node_state/pre/R4/routes.txt': entry}
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    manifest = []
    for path in root.rglob('*'):
        if path.is_file():
            manifest.append(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root)}')
    (root / 'manifest.sha256').write_text('\n'.join(manifest))


class MethodologyTests(unittest.TestCase):
    def import_run(self, change=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_run(root, change)
            return collect_observations(root)

    def test_all_five_levels_with_correlated_evidence(self):
        result = self.import_run()
        self.assertEqual(result['status'], 'available')
        self.assertTrue(result['records'][0]['coverage_complete'])
        self.assertEqual(set(result['records'][0]['levels']), {'OCP', 'ON', 'ODP', 'OP', 'ONW'})

    def test_no_run_is_missing_not_execution_success(self):
        self.assertEqual(collect_observations()['status'], 'missing')

    def test_wrong_run_ambiguous_quoted_and_disagreeing_frames(self):
        mutations = [lambda e, o, f: o[0].update(run_id='other'),
                     lambda e, o, f: o[0].update(correlation='ambiguous'),
                     lambda e, o, f: o[0].update(icmpv6_type=1),
                     lambda e, o, f: f[0].update(reliability={'status': 'disagree'}),
                     lambda e, o, f: o[0].update(icmpv6_payload_sha256='other')]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                result = self.import_run(mutation)
                self.assertFalse(result['records'][0]['coverage_complete'])
                self.assertEqual(result['records'][0]['levels']['ON']['status'], 'missing')

    def test_downstream_must_follow_the_same_packet(self):
        result = self.import_run(lambda e, o, f: o[2].update(packet_id='other'))
        self.assertEqual(result['records'][0]['levels']['ONW']['status'], 'missing')

    def test_unrelated_installed_route_does_not_establish_context(self):
        def change(event, observations, frames):
            event['forwarding_transition']['matched_forwarding_entry']['entry'] = '2001:db8::/64 dev eth2'
        result = self.import_run(change)
        self.assertEqual(result['records'][0]['levels']['OCP']['status'], 'missing')

    def test_terminal_exception_requires_explicit_domain_boundary(self):
        def change(event, observations, frames):
            event.update(declared_behaviour='End.DX6', terminal_leaves_observed_domain=True)
            observations.pop()
        result = self.import_run(change)
        self.assertEqual(result['records'][0]['levels']['ONW']['status'], 'not_applicable')

    def test_tampered_capture_fails_import(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_run(root)
            (root / 'raw/pcaps/1.pcap').write_bytes(b'changed')
            result = collect_observations(root)
            self.assertEqual(result['status'], 'failed')
            self.assertIn('hash', result['errors'][0])

    def test_encapsulation_and_absent_srh_are_not_false_deltas(self):
        before = {'depth': 0, 'inner': {'hop_limit': 64}, 'outer': {'hop_limit': 64},
                  'srh_present': False, 'srh': {}}
        after = copy.deepcopy(before)
        after.update(depth=1, outer={'hop_limit': 40})
        fields = compare_packets(before, after)
        self.assertEqual(fields['outer.hop_limit']['status'], 'not_comparable')
        self.assertEqual(fields['inner.hop_limit']['delta'], 0)
        self.assertEqual(fields['srh.segments_left']['status'], 'absent')

    def test_next_csid_can_change_destination_without_segments_left(self):
        before = {'depth': 1, 'inner': {}, 'outer': {'destination': 'fcbb:1:2::'},
                  'srh_present': True, 'srh': {'segments_left': 1}}
        after = copy.deepcopy(before)
        after['outer']['destination'] = 'fcbb:2::'
        fields = compare_packets(before, after)
        self.assertTrue(fields['outer.destination']['changed'])
        self.assertEqual(fields['srh.segments_left']['delta'], 0)


class IntegrationTests(unittest.TestCase):
    def test_stopped_container_is_not_executed(self):
        async def inspect(cid):
            return {'status': 'available', 'data': [{'Config': {'Labels': {'containerlab': 'test'}},
                                                     'State': {'Running': False}}]}
        reader = DockerReader()
        with patch.object(reader, 'inspect', side_effect=inspect), patch('collectors.docker_reader._run') as run:
            result = asyncio.run(reader.observe('a' * 64, 'test', 'routes'))
            self.assertEqual(result['status'], 'missing')
            run.assert_not_called()

    def test_unsupported_json_retains_text_without_fake_records(self):
        async def observe(cid, lab, command):
            if command == 'routes':
                return {'status': 'failed', 'code': 1, 'raw': '', 'error': 'unsupported option'}
            return {'status': 'available', 'code': 0, 'raw': 'default via fe80::1 dev eth0'}
        reader = DockerReader()
        with patch.object(reader, 'observe', side_effect=observe):
            result = asyncio.run(reader.json_observation('a' * 64, 'test', 'routes'))
            self.assertEqual(result['status'], 'unsupported')
            self.assertIsNone(result['data'])
            self.assertIn('default', result['fallback']['raw'])

    def test_old_generation_remains_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cid = 'a' * 32
            generation = root / 'collections' / cid
            generation.mkdir(parents=True)
            (generation / 'manifest.json').write_text(json.dumps({'collection_id': cid, 'collected_at': 'old'}))
            for name in NAMES:
                if name != 'observations':
                    (generation / f'{name}.json').write_text(json.dumps({'collection_id': cid, 'status': 'available'}))
            snapshots = JsonStore(root).read_many()
            self.assertEqual(snapshots['docker'].status, 'available')
            self.assertEqual(snapshots['observations'].status, 'missing')

    def test_admin_up_without_carrier_is_not_operational_up(self):
        reference = {'lab_id': 'test', 'source_name': 'a', 'target_name': 'b',
                     'source_interface': 'eth1', 'target_interface': 'eth1'}
        nodes = [{'lab_id': 'test', 'id': name, 'name': name, 'running': True,
                  'interfaces': [{'ifname': 'eth1', 'ifindex': index, 'link_index': peer, 'flags': ['UP']}]}
                 for name, index, peer in [('a', 10, 20), ('b', 20, 10)]]
        links, _ = resolve_links(nodes, [reference])
        self.assertEqual(links[0]['operational'], 'down')

    def test_full_collection_includes_explicit_missing_packet_evidence(self):
        async def run(root):
            topology = root / 'topology.yml'
            topology.write_text('name: test\ntopology:\n  links: []\n')
            service = DiscoveryService(JsonStore(root / 'snapshots'), topology, Reader())
            service.start()
            await service.task
            snapshots = service.store.read_many()
            self.assertEqual(set(snapshots), set(NAMES))
            self.assertEqual(snapshots['observations'].status, 'partial')
            live = snapshots['observations'].records[0]
            self.assertFalse(live['records'][0]['coverage_complete'])
            self.assertIn('capture', live['records'][0])
            self.assertEqual(service.status['state'], 'partial')
            self.assertEqual(len({snapshot.collection_id for snapshot in snapshots.values()}), 1)
        with tempfile.TemporaryDirectory() as temp:
            asyncio.run(run(Path(temp)))

    def test_all_node_failures_fail_collection(self):
        class Broken(Reader):
            async def json_observation(self, *args, **kwargs):
                raise RuntimeError('fixture node failed')
        async def run(root):
            topology = root / 'lab.yml'
            topology.write_text('name: test\ntopology:\n  links: []\n')
            service = DiscoveryService(JsonStore(root / 'out'), topology, Broken())
            service.start()
            await service.task
            self.assertEqual(service.status['state'], 'failed')
        with tempfile.TemporaryDirectory() as temp:
            asyncio.run(run(Path(temp)))

    def test_static_reader_uses_real_project_topology(self):
        state = collect_declared()
        self.assertFalse(state['provenance']['execution_evidence'])
        self.assertGreater(state['topology']['node_count'], 0)

    def test_malformed_topology_reports_error(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'lab.yml'
            path.write_text('topology: []')
            self.assertIsNotNone(configured_topology(path)['error'])

    def test_invalid_route_json_is_not_empty_success(self):
        result = decoded({'status': 'available', 'raw': '[1]'}, list)
        self.assertEqual(result['status'], 'parse_error')

    def test_srv6_encapsulation_shapes_and_flavors(self):
        result = collect_srv6({'status': 'available', 'data': [
            {'dst': '2001:db8::/64', 'encap': {'type': 'seg6', 'segs': 'fcbb::1,fcbb::2'}},
            {'dst': 'fcbb::/32', 'encap': 'seg6local', 'action': 'End', 'flavors': ['next-csid']},
            {'dst': '::1', 'encap': None}]})
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['policies'][0]['segments'], ['fcbb::1', 'fcbb::2'])
        self.assertEqual(result['local_sids'][0]['flavors'], ['next-csid'])


if __name__ == '__main__':
    unittest.main()
