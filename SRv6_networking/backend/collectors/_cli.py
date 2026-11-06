"""Shared command-line runner for individual read-only collectors."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from collectors.docker_reader import DockerReader
from collectors.observation_collector import collect_observations
from collectors.configuration_collector import collect_configuration, collect_configured_files
from collectors.ipv6_collector import collect_ipv6
from collectors.isis_collector import collect_isis, collect_isis_routes
from collectors.srv6_collector import collect_srv6
from collectors.topology_collector import configured_topology, link_interfaces, resolve_links
from models.schemas import Snapshot
from storage.json_store import JsonStore


async def collect_one(name, output, topology, reader=None, evidence_run=None):
    if name == 'observations' and evidence_run is None:
        from services.discovery_service import DiscoveryService
        service = DiscoveryService(JsonStore(output), topology, reader=reader)
        service.start()
        await service.task
        snapshot = service.store.read_many(collection_id=service.status['collection_id'])['observations']
        return snapshot, output / 'observations.json'
    if name == 'observations':
        report = await asyncio.to_thread(collect_observations, evidence_run)
        snapshot = Snapshot(collection_id=uuid.uuid4().hex,
                            collected_at=datetime.now(timezone.utc).isoformat(),
                            status=report['status'], records=[report], errors=report['errors'],
                            source='archived packet experiment; original run identity retained in records')
        return snapshot, JsonStore(output).update_latest(name, snapshot)
    if name not in ('docker', 'configurations', 'ipv6_routes', 'isis', 'srv6_policies', 'topology'):
        raise ValueError(f'Unknown collector: {name}')
    reader = reader or DockerReader()
    inventory = await reader.discover()
    errors = list(inventory.get('errors', []))
    if inventory['status'] != 'available':
        errors.append(inventory.get('error') or f'Docker discovery: {inventory["status"]}')
    rows = inventory.get('data', [])
    semaphore = asyncio.Semaphore(4)

    async def node(row):
        async with semaphore:
            cid = row['Id']
            lab = row['Config']['Labels']['containerlab']
            base = {'id': cid, 'lab_id': lab,
                    'name': row['Config']['Labels'].get('clab-node-name') or row['Name'].lstrip('/')}
            if name == 'docker':
                return {**base, 'container_name': row.get('Name', '').lstrip('/'),
                        'image': row['Config'].get('Image'), 'labels': row['Config']['Labels'],
                        'state': row.get('State', {}), 'network_settings': row.get('NetworkSettings', {}),
                        'mounts': row.get('Mounts', [])}
            if name == 'configurations':
                observations = await collect_configuration(reader, cid, lab)
                result = {**base, 'configuration': observations}
            elif name == 'ipv6_routes':
                observations = await collect_ipv6(reader, cid, lab)
                result = {**base, **observations}
            elif name == 'isis':
                observations = {'isis': await collect_isis(reader, cid, lab),
                                'isis_routes': await collect_isis_routes(reader, cid, lab)}
                result = {**base, **observations}
            elif name == 'srv6_policies':
                routes = await reader.json_observation(cid, lab, 'routes')
                srv6 = collect_srv6(routes)
                observations = {'routes': routes, 'srv6': srv6}
                result = {**base, **srv6, 'route_observation': routes}
            elif name == 'topology':
                links = await reader.json_observation(cid, lab, 'links')
                addresses = await reader.json_observation(cid, lab, 'interfaces')
                observations = {'links': links, 'interfaces': addresses}
                result = {**base, 'container_name': row.get('Name', '').lstrip('/'),
                          'running': bool(row.get('State', {}).get('Running')),
                          'state': row.get('State', {}).get('Status', 'unknown'),
                          'interfaces': link_interfaces(links), 'link_observation': links,
                          'ipv6_addresses': addresses}
            else:
                raise ValueError('Unknown collector')
            for key, value in observations.items():
                if value['status'] != 'available':
                    errors.append(f'{base["name"]}: {key}: {value["status"]}')
            return result

    results = await asyncio.gather(*(node(row) for row in rows), return_exceptions=True)
    records = []
    for result in results:
        if isinstance(result, BaseException):
            errors.append(f'Node collection failed: {result}')
        else:
            records.append(result)
    links, configured = [], []
    if name == 'topology':
        reference = configured_topology(topology)
        if reference['error']:
            errors.append(reference['error'])
        links, configured = resolve_links(records, reference['links'])
        if any(link['status'] != 'observed' for link in configured):
            errors.append('Some configured links lack live endpoint evidence')
    status = ('failed' if inventory['status'] == 'failed' or (rows and not records)
              else 'partial' if errors else 'available' if records else 'missing')
    snapshot = Snapshot(collection_id=uuid.uuid4().hex,
                        collected_at=datetime.now(timezone.utc).isoformat(),
                        lab_ids=sorted({r['Config']['Labels']['containerlab'] for r in rows}),
                        status=status, records=records, errors=errors,
                        links=links, configured_links=configured)
    if name == 'configurations':
        snapshot.configured_files = await asyncio.to_thread(collect_configured_files, topology)
        snapshot.errors.extend(snapshot.configured_files['errors'])
        if snapshot.errors and snapshot.status == 'available':
            snapshot.status = 'partial'
    path = JsonStore(output).update_latest(name, snapshot)
    return snapshot, path


def main(name):
    parser = argparse.ArgumentParser(description=f'Collect live {name} data and update {name}.json.')
    parser.add_argument('--output', type=Path, default=ROOT / 'backend/data/snapshots')
    parser.add_argument('--topology', type=Path, default=ROOT / 'topology/SRv6_lab.yml')
    parser.add_argument('--evidence-run', type=Path, help='Archived experiment directory for observations')
    args = parser.parse_args()
    snapshot, path = asyncio.run(collect_one(name, args.output.resolve(), args.topology.resolve(),
                                           evidence_run=args.evidence_run))
    print(json.dumps({'file': str(path), 'collection_id': snapshot.collection_id,
                      'status': snapshot.status, 'records': len(snapshot.records),
                      'errors': snapshot.errors}, indent=2))
    return 0 if snapshot.records and snapshot.status in ('available', 'partial') else 1
