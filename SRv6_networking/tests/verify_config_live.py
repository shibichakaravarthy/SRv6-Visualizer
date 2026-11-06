"""Explicit reversible lab check: temporary routes in an unused table + plain IPv6 ping."""
import json
from pathlib import Path
import time
import urllib.request

BASE='http://127.0.0.1:8000/api/'
def api(path, body=None):
    request=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=90) as response:return json.load(response)

def wait_for(operation):
    for _ in range(90):
        state=api('status')[operation]['state']
        if state!='running':return state
        time.sleep(1)
    raise RuntimeError('Lab operation timed out')

def apply(change):
    preview=api('config/preview',change)
    result=api('config/apply',{**change,'expected':preview['expected']})
    assert result['status'] in ('verified','applied'), {k:result[k] for k in ('status','message','result','audit_file')}
    print(change['kind'], change.get('operation','replace'), result['status'], flush=True)
    return result

if __name__=='__main__':
    api('refresh',{});wait_for('collection');view=api('workspace')
    edge=next(n for n in view['nodes'] if n['name']=='edge1')
    table=42420
    for row in view['snapshots']['ipv6_routes']['records']:
        assert not any(str(r.get('table'))==str(table) for r in row['routes'].get('data') or []),'Test table already contains routes'
        assert not any(str(r.get('table'))==str(table) for r in row['rules'].get('data') or []),'Test table is selected by a policy rule'
    interface=next(i['ifname'] for i in edge['interfaces'] if i['ifname'] not in ('eth0','lo'))
    base={'device':edge['id'],'table':table,'interface':interface,'prefix':'2001:db8:ffff::123/128'}
    installed=None;checks=[]
    try:
        installed={**base,'kind':'policy','segments':['fcbb:2000:7::','fcbb:2000:8::']}
        apply(installed);checks.append('Add policy verified')
        installed={**installed,'segments':['fcbb:2000:8::','fcbb:2000:7::']}
        apply(installed);checks.append('Edit segment order verified')
        # Ping without manual refresh; its saved installed state must include the latest policy.
        source=next(n for n in view['nodes'] if n['name']=='client1')
        target=next(n for n in view['nodes'] if n['name']=='backup-server')
        api('pings',{'source':source['id'],'destination':target['id'],
                     'source_address':source['addresses'][0]['address'],'destination_address':target['addresses'][0]['address']})
        wait_for('ping');job=api('pings/current');saved=api('pings/current/workspace')
        routes=next(r for r in saved['snapshots']['ipv6_routes']['records'] if r['id']==edge['id'])['routes']['data']
        assert any(str(r.get('table'))==str(table) and r.get('encap',{}).get('segs')==installed['segments'] for r in routes)
        assert job['statistics']['received']==3,job.get('ping')
        assert len(job['journeys'])==6
        events=[e for j in job['journeys'] for e in j['events']]
        assert events
        assert all(not e['before']['state'].get('srh_present') and not e['before']['state'].get('encapsulation',{}).get('layers') for e in events)
        checks.append('Fresh ping snapshot adopts changed policy')
        checks.append('Plain IPv6 ping: 3/3 replies, six observed journeys, no SRv6 encapsulation')
        apply({**installed,'operation':'delete'});installed=None;checks.append('Delete policy verified')
        installed={**base,'kind':'sid','action':'End'};apply(installed);checks.append('Add local SID verified')
        installed={**installed,'next_csid':True};apply(installed);checks.append('Edit local SID to NEXT-CSID verified')
        apply({**installed,'operation':'delete'});installed=None;checks.append('Delete SID verified')
        installed={**base,'kind':'ipv6'};apply(installed);checks.append('Add normal IPv6 route verified')
        apply({**installed,'operation':'delete'});installed=None;checks.append('Delete normal IPv6 route verified')
    finally:
        if installed:
            apply({**installed,'operation':'delete'})
    report={'checks':checks,'ping_run_id':job['id'],'statistics':job['statistics'],
            'journeys':[{'direction':j['direction'],'complete':j['complete'],'events':len(j['events'])} for j in job['journeys']],
            'test_table':table,'temporary_routes_removed':True}
    output=Path(__file__).resolve().parents[1]/'docs/config-verification.json'
    output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
