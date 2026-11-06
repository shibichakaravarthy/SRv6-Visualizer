import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from collectors._cli import collect_one
from storage.json_store import JsonStore, NAMES
from models.schemas import Snapshot

class Reader:
    def __init__(self): self.commands = []
    async def discover(self):
        return {'status': 'available', 'data': [{'Id':'a'*64,'Name':'/r1',
                'Config':{'Labels':{'containerlab':'test','clab-node-name':'r1'}},
                'State':{'Running':True}}]}
    async def observe(self, cid, lab, command):
        self.commands.append(command)
        return {'status':'available','raw':'configuration'}
    async def json_observation(self, cid, lab, command, expected=list):
        self.commands.append(command)
        return {'status':'available','data':[],'raw':'[]'}

class Collectors(unittest.TestCase):
    def test_independent_collectors_and_repeated_update(self):
        async def check(root):
            topology=root/'lab.yml';topology.write_text('name: test\ntopology:\n  links: []\n')
            expected={'configurations':{'running_config','frr_config','frr_daemons','forwarding','seg6_enabled'},
                      'docker':set(),'ipv6_routes':{'interfaces','routes','neighbors','rules'},
                      'isis':{'isis','isis_routes'},'srv6_policies':{'routes'},'topology':{'links','interfaces'}}
            for name in expected:
                directory=root/name;reader=Reader()
                first,path=await collect_one(name,directory,topology,reader)
                before=path.read_bytes()
                second,_=await collect_one(name,directory,topology,reader)
                self.assertEqual(set(reader.commands),expected[name])
                self.assertNotEqual(first.collection_id,second.collection_id)
                self.assertEqual(json.loads(path.read_text())['collection_id'],second.collection_id)
                self.assertEqual(list(directory.glob('*.json')),[path])
                history=list((directory/'history'/name).glob('*.json'))
                self.assertEqual(len(history),2)
                self.assertIn(before,[p.read_bytes() for p in history])
        with tempfile.TemporaryDirectory() as temp: asyncio.run(check(Path(temp)))

    def test_failed_replace_keeps_previous_json(self):
        with tempfile.TemporaryDirectory() as temp:
            store=JsonStore(Path(temp));path=store.update_latest('docker',Snapshot(collection_id='old'))
            before=path.read_bytes()
            with patch('storage.json_store.os.replace',side_effect=OSError('disk full')):
                with self.assertRaises(OSError): store.update_latest('docker',Snapshot(collection_id='new'))
            self.assertEqual(before,path.read_bytes())

    def test_full_collection_refreshes_all_latest_files(self):
        with tempfile.TemporaryDirectory() as temp:
            store=JsonStore(Path(temp))
            for cid in ('a'*32,'b'*32):
                batch={name:Snapshot(collection_id=cid,collected_at='2026-11-02T10:00:00Z') for name in NAMES}
                generation=store.publish(batch)
                for name in NAMES:
                    self.assertEqual((generation/f'{name}.json').read_bytes(),(Path(temp)/f'{name}.json').read_bytes())
            self.assertEqual(len(store.history()),2)

    def test_failed_discovery_is_explicit(self):
        class Failed(Reader):
            async def discover(self):return {'status':'failed','data':[],'error':'Docker unavailable'}
        with tempfile.TemporaryDirectory() as temp:
            snapshot,path=asyncio.run(collect_one('docker',Path(temp),Path(temp)/'none',Failed()))
            self.assertEqual(snapshot.status,'failed')
            self.assertIn('Docker unavailable',json.loads(path.read_text())['errors'])

if __name__=='__main__':unittest.main()
