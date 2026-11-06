"""Explicit live verification: passive refresh, then one 3-echo client ping."""
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from main import create_app
from services.workspace_service import workspace

async def main():
    app = create_app()
    discovery, ping = app.state.discovery, app.state.ping
    discovery.start()
    await discovery.task
    view = workspace(discovery.store, discovery.topology_path)
    print(json.dumps({'collection': discovery.status, 'devices': len(view['nodes']), 'observed_links': len([l for l in view['links'] if l['status'] == 'observed'])}), flush=True)
    source = next(n for n in view['nodes'] if n['name'] == 'client1')
    destination = next(n for n in view['nodes'] if n['name'] == 'webserver')
    ping.start(view, source['id'], destination['id'], source['addresses'][0]['address'], destination['addresses'][0]['address'])
    await ping.task
    print(json.dumps({'job': {k:v for k,v in ping.status.items() if k not in ('journeys', 'captures')},
                      'captures': [{k:v for k,v in c.items() if k not in ('command', 'log')} for c in ping.status.get('captures', [])],
                      'journeys': [{k:v for k,v in j.items() if k != 'events'} for j in ping.status['journeys']]}), flush=True)

if __name__ == '__main__':
    asyncio.run(main())
