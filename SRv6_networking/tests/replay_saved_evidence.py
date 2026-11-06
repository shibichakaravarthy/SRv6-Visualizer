"""Read-only verification of captured journeys using current reconstruction code."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from services.evidence_service import decode_capture
from services.journey_service import reconstruct
root=Path(__file__).resolve().parents[1]/'backend/data/ping_runs'
run=max(root.iterdir(),key=lambda p:p.stat().st_mtime)
view=json.loads((run/'snapshot.json').read_text()); job=json.loads((run/'result.json').read_text())
rows=[]
for node in view['nodes']:
    decoded,errors=decode_capture(run/(node['id']+'.pcap'),node,job['payload_pattern'])
    rows+=decoded
    assert not errors,errors
journeys=reconstruct(rows,[l for l in view['links'] if l['status']=='observed'],job['source'],job['destination'],job['source_address'],job['destination_address'],view['snapshots']['srv6_policies']['records'])
names={n['id']:n['name'] for n in view['nodes']}
for journey in journeys:
    print(journey['packet_id'],journey['complete'],' → '.join(names[n] for n in journey['observed_path']))
    print([e['description'] for e in journey['events'] if 'observed:' in e['description']])
assert len(journeys)==6
assert all(j['complete'] for j in journeys)
