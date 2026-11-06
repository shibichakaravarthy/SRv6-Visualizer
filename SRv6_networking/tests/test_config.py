import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from services.config_service import Change, ConfigService, frr_commands, route_command, matches_change, frr_errors

CID='a'*64
VIEW={'nodes':[{'id':CID,'name':'R1','role':'router','running':True,'lab_id':'lab'}]}

class Reader:
    def __init__(self):
        self.routes=[];self.executed=[];self.fail=False
    async def json_observation(self,cid,lab,key):
        data=[{'ifname':'eth1','ifindex':2},{'ifname':'eth0','ifindex':1}] if key=='links' else copy.deepcopy(self.routes)
        return {'status':'available','data':data,'raw':json.dumps(data)}
    async def configure(self,cid,lab,command):
        self.executed.append(command)
        return {'status':'failed','error':'Rejected','raw':''} if self.fail else {'status':'available','raw':''}

class ConfigurationTests(unittest.TestCase):
    def test_policy_order_and_no_shell(self):
        c=Change(device=CID,prefix='fc00::9',interface='eth1',segments=['fcbb::1','fcbb::2'])
        cmd=route_command(c)
        self.assertEqual(cmd[4],'fc00::9/128')
        self.assertIn('fcbb::1,fcbb::2',cmd)
        for value in ('fc00::1; reboot','$(id)','--help'):
            with self.assertRaises(ValueError):Change(device=CID,prefix=value,interface='eth1')
    def test_sid_validation(self):
        with self.assertRaises(ValueError):Change(device=CID,kind='sid',prefix='fcbb::1',interface='eth1',action='End.DX6')
        with self.assertRaises(ValueError):Change(device=CID,kind='sid',prefix='fcbb::1',interface='eth1',next_csid=True,lblen=33)
        c=Change(device=CID,kind='sid',prefix='fcbb::1',interface='eth1',next_csid=True)
        self.assertIn('next-csid',route_command(c))
    def test_next_csid_live_json_geometry(self):
        c=Change(device=CID,kind='sid',prefix='fcbb::1',interface='eth1',next_csid=True,lblen=48)
        for encap in ({'flavors':['next-csid'],'lblen':48,'nflen':16}, {'flavors':{'operation':['next-csid'],'lblen':48,'nflen':16}}):
            route={'dev':'eth1','encap':{'encap_type':'seg6local','action':'End',**encap}}
            self.assertTrue(matches_change(route,c))
            self.assertFalse(matches_change(route,c.model_copy(update={'lblen':32})))

    def test_plain_ipv6_route_has_no_encapsulation(self):
        c=Change(device=CID,kind='ipv6',prefix='fc00::/64',interface='eth1',gateway='fe80::1')
        self.assertNotIn('encap',route_command(c))
        self.assertTrue(matches_change({'dev':'eth1','gateway':'fe80::1'},c))
        self.assertFalse(matches_change({'dev':'eth1','gateway':'fe80::1','encap':{'encap_type':'seg6'}},c))
    def test_frr_startup_warning_does_not_hide_actual_command_errors(self):
        warning="% Can't open configuration file /etc/frr/vtysh.conf"
        before={'error':warning}
        self.assertFalse(frr_errors(before,{'error':warning,'raw':'% Configuration applied with notes:\nNo changes found to be committed!'}))
        self.assertTrue(frr_errors(before,{'error':warning,'raw':'% Unknown command: bad'}))
        self.assertTrue(frr_errors(before,{'error':warning+'\ncommit failed','raw':''}))

    def test_frr_subset_and_context(self):
        self.assertIn('isis metric 20',frr_commands('interface eth1\nisis metric 20\nexit',['eth1']))
        for commands in ('do shell touch /tmp/x','interface eth0\nshutdown','interface eth1\nisis metric 20; reboot','router isis CORE\ninterface eth1','interface eth1\nipv6 address bad'):
            with self.assertRaises(ValueError):frr_commands(commands,['eth0','eth1'])
    def test_stale_preview_refuses_execution(self):
        async def check(root):
            r=Reader();s=ConfigService(r,root)
            c=Change(device=CID,kind='ipv6',prefix='fc00::9',interface='eth1')
            plan=await s.preview(c,VIEW);c.expected=plan['expected']
            r.routes=[{'dst':'fc00::9','dev':'eth1','metric':1024,'protocol':'boot'}]
            with self.assertRaises(RuntimeError):await s.apply(c,VIEW)
            self.assertFalse(r.executed)
        with tempfile.TemporaryDirectory() as t:asyncio.run(check(Path(t)))
    def test_dynamic_routes_and_management_interfaces_protected(self):
        async def check(root):
            r=Reader();s=ConfigService(r,root)
            r.routes=[{'dst':'fc00::9','dev':'eth1','metric':1024,'protocol':'isis'}]
            c=Change(device=CID,kind='ipv6',prefix='fc00::9',interface='eth1')
            with self.assertRaises(ValueError):await s.preview(c,VIEW)
            with self.assertRaises(ValueError):Change(device=CID,kind='ipv6',prefix='::/0',interface='eth0')
        with tempfile.TemporaryDirectory() as t:asyncio.run(check(Path(t)))
    def test_failed_command_preserves_audit_and_reports_failure(self):
        async def check(root):
            r=Reader();r.fail=True;s=ConfigService(r,root)
            c=Change(device=CID,kind='ipv6',prefix='fc00::9',interface='eth1')
            c.expected=(await s.preview(c,VIEW))['expected']
            result=await s.apply(c,VIEW)
            self.assertEqual(result['status'],'failed')
            audit=json.loads(next(root.glob('*.json')).read_text())
            self.assertIn('before',audit);self.assertIn('after',audit)
        with tempfile.TemporaryDirectory() as t:asyncio.run(check(Path(t)))
    def test_missing_interface_and_delete_without_entry_rejected(self):
        async def check(root):
            s=ConfigService(Reader(),root)
            for fields in ({'interface':'eth9'}, {'interface':'eth1','operation':'delete'}):
                with self.assertRaises(ValueError):await s.preview(Change(device=CID,kind='ipv6',prefix='fc00::9',**fields),VIEW)
        with tempfile.TemporaryDirectory() as t:asyncio.run(check(Path(t)))

class ConfigurationApiTests(unittest.TestCase):
    def test_preview_api_and_apply_require_matching_live_state(self):
        from fastapi.testclient import TestClient
        from main import create_app
        from models.schemas import Snapshot
        from storage.json_store import JsonStore,NAMES
        from unittest.mock import patch
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);topology=root/'lab.yml'
            topology.write_text('name: lab\ntopology:\n  nodes:\n    R1:\n      labels: {role: router}\n')
            store=JsonStore(root/'snapshots');reader=Reader()
            for name in NAMES:
                store.write(name,Snapshot(collection_id='b'*32,collected_at=datetime.now(timezone.utc).isoformat(),records=VIEW['nodes'] if name=='topology' else []))
            app=create_app(store,reader,topology,root/'runs')
            with TestClient(app) as client:
                body={'device':CID,'kind':'ipv6','prefix':'fc00::9','interface':'eth1'}
                response=client.post('/api/config/preview',json=body)
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(client.post('/api/config/apply',json=body).status_code,409)
                body['expected']=response.json()['expected']
                body['prefix']='fc00::10'
                self.assertEqual(client.post('/api/config/apply',json=body).status_code,409)
                self.assertEqual(client.post('/api/config/preview',json=body,headers={'Origin':'https://untrusted.example'}).status_code,403)
                self.assertFalse(reader.executed)
