import asyncio
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from fastapi.testclient import TestClient
from collectors.docker_reader import DockerReader
from main import create_app
from models.schemas import Snapshot
from services.evidence_service import decode_capture
from services.journey_service import reconstruct
from services.ping_service import ping_statistics
from services.workspace_service import addresses
from storage.json_store import JsonStore, NAMES


def state(depth=0, sl=None):
    ipv6 = {'source':'fc00::1','destination':'fc00::2','hop_limit':64,'payload_length':64,'next_header':58}
    return {'ipv6':ipv6,'encapsulation':{'layers':[ipv6]*depth},'srh_present':sl is not None,'srh':{'segments_left':sl} if sl is not None else {}}


def link(a,b):
    return {'id':a+b,'source':a,'target':b,'source_interface':'to'+b,'target_interface':'to'+a,'status':'observed'}


def fixture(reply=False):
    sequence = list('dcba' if reply else 'abcd'); rows=[]
    for i,(a,b) in enumerate(zip(sequence,sequence[1:])):
        for node,interface,direction,time in ((a,'to'+b,'egress',10+i),(b,'to'+a,'ingress',10+i+.1)):
            rows.append({'node':node,'name':node,'interface':interface,'direction':direction,'timestamp':time,'wire_sha256':str(i),
                         'packet_key':[129 if reply else 128,42,1,'payload',*(['fc00::2','fc00::1'] if reply else ['fc00::1','fc00::2'])], 'state':state()})
    return rows


class ReconstructionTests(unittest.TestCase):
    def journey(self,rows):
        return reconstruct(rows,[link('a','b'),link('b','c'),link('c','d')],'a','d','fc00::1','fc00::2',[])
    def test_every_transit_hop_and_reply(self):
        for reply in (False,True):
            result=self.journey(fixture(reply))[0]
            self.assertTrue(result['complete'])
            self.assertEqual(len([e for e in result['events'] if e['kind']=='transfer']),3)
            self.assertEqual(result['observed_path'],list('dcba' if reply else 'abcd'))
    def test_normal_ipv6_forwarding_uses_the_same_physical_reconstruction(self):
        result = self.journey(fixture())[0]
        self.assertTrue(result['complete'])
        processing = [e for e in result['events'] if e['kind'] == 'processing']
        self.assertTrue(processing)
        self.assertTrue(all('Forwarding observed' in e['description'] for e in processing))
        self.assertTrue(all(not e['before']['state']['srh_present'] for e in result['events']))

    def test_gap_never_filled(self):
        result=self.journey(fixture()[:2]+fixture()[4:])[0]
        self.assertFalse(result['complete'])
        self.assertNotIn('bc',[e.get('link_id') for e in result['events']])
    def test_duplicate_capture_refuses_ambiguous_link(self):
        result=self.journey(fixture()+[fixture()[1]])[0]
        self.assertFalse(result['complete'])
        self.assertNotIn('ab',[e.get('link_id') for e in result['events']])
    def test_mismatched_wire_bytes_do_not_animate(self):
        rows=fixture(); rows[1]['wire_sha256']='different'
        self.assertNotIn('ab',[e.get('link_id') for e in self.journey(rows)[0]['events']])
    def test_other_ping_addresses_excluded(self):
        rows=fixture()
        for row in rows: row['packet_key'][-1]='fc00::99'
        self.assertEqual(self.journey(rows),[])
    def test_encapsulation_keeps_observed_and_inferred_separate(self):
        rows=fixture(); rows[2]['state']=state(depth=1,sl=2)
        event=next(e for e in self.journey(rows)[0]['events'] if e.get('node')=='b')
        self.assertIn('Encapsulation observed',event['description'])
        self.assertIn('inferred',event['behavior_certainty'])


class DecoderTests(unittest.TestCase):
    def test_real_sll2_interface_provenance_dual_decode(self):
        from scapy.layers.inet6 import IPv6, ICMPv6EchoRequest
        pattern='abcdef0123456789'*2
        ipv6=bytes(IPv6(src='fc00::1',dst='fc00::2')/ICMPv6EchoRequest(id=42,seq=1,data=bytes.fromhex(pattern)*3))
        cooked=struct.pack('!HHIHBB8s',0x86dd,0,7,1,3,6,bytes(8))+ipv6
        pcap=struct.pack('<IHHIIII',0xa1b2c3d4,2,4,0,0,65535,276)+struct.pack('<IIII',100,500,len(cooked),len(cooked))+cooked
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'node.pcap'; path.write_bytes(pcap)
            rows,errors=decode_capture(path,{'id':'a','name':'a','interfaces':[{'ifindex':7,'ifname':'eth1'}]},pattern)
            self.assertFalse(errors); self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['direction'],'ingress')
            self.assertEqual(rows[0]['frame_sha256'],hashlib.sha256(cooked).hexdigest())
            self.assertEqual(rows[0]['reliability']['status'],'agree')
            self.assertEqual(decode_capture(path,{'id':'a','name':'a','interfaces':[]},pattern)[0],[])


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name); topology=root/'lab.yml'
        topology.write_text('name: lab\ntopology:\n  nodes:\n    a: {}\n    b: {}\n')
        self.store=JsonStore(root/'snapshots')
        nodes=[{'id':c*64,'name':c,'running':True,'lab_id':'lab','state':'running','interfaces':[],
                'ipv6_addresses':{'data':[{'ifname':'eth1','addr_info':[{'local':f'fc00::{i}'}]}]}} for i,c in enumerate('ab',1)]
        for name in NAMES:
            self.store.write(name,Snapshot(collection_id='c'*32,collected_at=datetime.now(timezone.utc).isoformat(),records=nodes if name=='topology' else []))
        self.app=create_app(self.store,DockerReader(),topology,root/'runs')
    def test_workspace_and_device_404(self):
        with TestClient(self.app) as client:
            result=client.get('/api/workspace').json()
            self.assertEqual(len(result['nodes']),2); self.assertEqual(result['nodes'][0]['role'],'host')
            self.assertEqual(client.get('/api/devices/unknown').status_code,404)
    def test_invalid_commands_and_foreign_origin_rejected(self):
        with TestClient(self.app) as client:
            self.assertEqual(client.post('/api/pings',json={'source':'x;touch /tmp/no'}).status_code,422)
            self.assertEqual(client.post('/api/refresh',json={},headers={'Origin':'https://evil.example'}).status_code,403)
            body={'source':'a'*64,'destination':'b'*64,'source_address':'fc00::1','destination_address':'fc00::99'}
            self.assertEqual(client.post('/api/pings',json=body).status_code,422)
    def test_passive_refresh(self):
        async def collect(service):
            self.assertFalse(service.active_observation); service.status['state']='available'
        with patch('services.discovery_service.DiscoveryService.collect',collect):
            with TestClient(self.app) as client:
                self.assertEqual(client.post('/api/refresh',json={}).status_code,202)
    def test_stale_or_missing_timestamp_refreshes_then_starts_ping(self):
        for timestamp in ('2000-01-01T00:00:00+00:00', None, 'invalid', '2026-01-01T00:00:00'):
            with self.subTest(timestamp=timestamp):
                snapshot = self.store.read('topology')
                self.store.update_latest('topology', snapshot.model_copy(update={'collected_at': timestamp}))
                async def collect(service):
                    self.assertFalse(service.active_observation)
                    self.store.update_latest('topology', snapshot.model_copy(update={
                        'collected_at': datetime.now(timezone.utc).isoformat()}))
                    service.status['state'] = 'available'
                async def run(*args):
                    self.app.state.ping.status['state'] = 'complete'
                with patch('services.discovery_service.DiscoveryService.collect', collect), patch.object(self.app.state.ping, 'run', run):
                    with TestClient(self.app) as client:
                        response = client.post('/api/pings', json={
                            'source':'a'*64, 'destination':'b'*64,
                            'source_address':'fc00::1', 'destination_address':'fc00::2'})
                        self.assertEqual(response.status_code, 202, response.text)
                        self.assertNotEqual(client.get('/api/pings/current/workspace').json()['collected_at'], timestamp)

    def test_failed_automatic_refresh_does_not_start_ping(self):
        snapshot = self.store.read('topology')
        self.store.update_latest('topology', snapshot.model_copy(update={'collected_at': None}))
        async def collect(service):
            service.status.update(state='failed', errors=['Docker unavailable'])
        with patch('services.discovery_service.DiscoveryService.collect', collect):
            with TestClient(self.app) as client:
                response = client.post('/api/pings', json={'source':'a'*64, 'destination':'b'*64,
                    'source_address':'fc00::1', 'destination_address':'fc00::2'})
                self.assertEqual(response.status_code, 503)
                self.assertIn('Docker unavailable', response.json()['detail'])
                self.assertIsNone(self.app.state.ping.task)

    def test_management_address_not_selectable(self):
        self.assertEqual(addresses({'ipv6_addresses':{'data':[{'ifname':'eth0','addr_info':[{'local':'fc00::1'}]}]}}),[])
    def test_statistics(self):
        result=ping_statistics('3 packets transmitted, 3 received, 0% packet loss\nrtt min/avg/max/mdev = 1.0/2.0/3.0/0.1 ms')
        self.assertEqual(result['received'],3); self.assertEqual(result['rtt_ms']['avg'],2)
    def test_probe_pattern_rejects_injection(self):
        class Reader(DockerReader):
            async def inspect(self,cid):
                return {'status':'available','data':[{'Config':{'Labels':{'containerlab':'lab'}},'State':{'Running':True}}]}
        with self.assertRaises(ValueError):
            asyncio.run(Reader().probe('a'*64,'lab','fc00::2','fc00::1','; bad'))
