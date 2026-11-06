"""Coordinate a bounded background collection; API reads never run Docker."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import uuid
from collectors.docker_reader import DockerReader
from collectors.observation_collector import collect_observations
from collectors.ipv6_collector import collect_ipv6
from collectors.isis_collector import collect_isis, collect_isis_routes
from collectors.srv6_collector import collect_srv6
from collectors.topology_collector import configured_topology, link_interfaces, resolve_links
from collectors.configuration_collector import collect_configuration, collect_configured_files
from collectors.live_observation_collector import collect_live_observations
from models.schemas import Snapshot
from storage.json_store import JsonStore


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DiscoveryService:
    def __init__(self, store: JsonStore, topology_path: Path, reader=None, evidence_run: Path | None = None, active_observation: bool = True):
        self.store = store
        self.topology_path = topology_path
        self.reader = reader or DockerReader()
        self.evidence_run = evidence_run
        self.active_observation = active_observation
        self.task: asyncio.Task | None = None
        self.status = {'state': 'idle', 'collection_id': None, 'started_at': None,
                       'finished_at': None, 'errors': [], 'completed_nodes': 0, 'total_nodes': 0}

    def start(self) -> dict:
        if self.task and not self.task.done():
            return self.status
        self.status = {'state': 'running', 'collection_id': uuid.uuid4().hex,
                       'started_at': now(), 'finished_at': None, 'errors': [],
                       'completed_nodes': 0, 'total_nodes': 0}
        self.task = asyncio.create_task(self.collect())
        return self.status

    async def collect_node(self, row: dict, semaphore: asyncio.Semaphore) -> tuple:
        async with semaphore:
            cid = row['Id']
            labels = row['Config']['Labels']
            lab = labels['containerlab']
            name = labels.get('clab-node-name') or row.get('Name', '').lstrip('/')
            base = {'id': cid, 'name': name, 'lab_id': lab}
            ipv6 = await collect_ipv6(self.reader, cid, lab)
            isis = await collect_isis(self.reader, cid, lab)
            isis_routes = await collect_isis_routes(self.reader, cid, lab)
            configuration = await collect_configuration(self.reader, cid, lab)
            links = await self.reader.json_observation(cid, lab, 'links')
            node = {**base, 'container_name': row.get('Name', '').lstrip('/'),
                    'running': bool(row.get('State', {}).get('Running')),
                    'state': row.get('State', {}).get('Status', 'unknown'),
                    'interfaces': link_interfaces(links), 'link_observation': links,
                    'ipv6_addresses': ipv6['interfaces'], 'isis': isis,
                    'isis_routes': isis_routes, 'configuration': configuration}
            self.status['completed_nodes'] += 1
            return node, {**base, **ipv6}, {**base, **collect_srv6(ipv6['routes'])}

    async def collect(self) -> None:
        try:
            configured_files = await asyncio.to_thread(collect_configured_files, self.topology_path)
            inventory = await self.reader.discover()
            reference = await asyncio.to_thread(configured_topology, self.topology_path)
            errors = list(inventory.get('errors', []))
            if inventory['status'] != 'available':
                errors.append(inventory.get('error') or inventory.get('raw') or 'Docker unavailable')
            rows = [row for row in inventory.get('data', [])
                    if row['Config']['Labels']['containerlab'] == reference['lab_id']]
            self.status['total_nodes'] = len(rows)
            # Limit simultaneous containers so a large lab cannot flood Docker.
            semaphore = asyncio.Semaphore(4)
            results = await asyncio.gather(*(self.collect_node(row, semaphore) for row in rows),
                                           return_exceptions=True)
            nodes, routes, policies = [], [], []
            for result in results:
                if isinstance(result, BaseException):
                    errors.append(f'Node collection failed: {result}')
                    continue
                node, route, policy = result
                nodes.append(node); routes.append(route); policies.append(policy)
            links, configured = resolve_links(nodes, reference['links'])
            labs = sorted({row['Config']['Labels']['containerlab'] for row in rows})
            common = {'collection_id': self.status['collection_id'], 'collected_at': now(),
                      'lab_ids': labs}
            state = 'failed' if inventory['status'] == 'failed' or (rows and not nodes) else 'partial' if errors else 'available' if nodes else 'missing'
            topology_errors = errors + ([reference['error']] if reference['error'] else [])
            topology_errors += [f'{n["name"]}: {key}: {n[key]["status"]}' for n in nodes
                                for key in ('link_observation', 'ipv6_addresses', 'isis', 'isis_routes')
                                if n[key]['status'] != 'available']
            if any(link['status'] != 'observed' for link in configured):
                topology_errors.append('Some configured links lack live endpoint evidence')
            route_errors = errors + [f'{n["name"]}: {key}: {n[key]["status"]}' for n in routes
                                    for key in ('interfaces', 'routes', 'neighbors', 'rules') if n[key]['status'] != 'available']
            policy_errors = errors + [f'{n["name"]}: {n["status"]}: {"; ".join(n["errors"])}'
                                     for n in policies if n['status'] != 'available']
            snapshots = {
                'topology': Snapshot(**common, status='partial' if topology_errors and state == 'available' else state,
                                     records=nodes, links=links, configured_links=configured, errors=topology_errors),
                'ipv6_routes': Snapshot(**common, status='partial' if route_errors and state == 'available' else state,
                                        records=routes, errors=route_errors),
                'srv6_policies': Snapshot(**common, status='partial' if policy_errors and state == 'available' else state,
                                          records=policies, errors=policy_errors),
            }
            configurations = [{key: node[key] for key in ('id', 'name', 'lab_id', 'configuration')} for node in nodes]
            isis_records = [{key: node[key] for key in ('id', 'name', 'lab_id', 'isis', 'isis_routes')} for node in nodes]
            docker_records = [{
                'id': row['Id'], 'container_name': row.get('Name', '').lstrip('/'),
                'lab_id': row['Config']['Labels']['containerlab'],
                'name': row['Config']['Labels'].get('clab-node-name'),
                'image': row['Config'].get('Image'), 'labels': row['Config']['Labels'],
                'state': row.get('State', {}), 'network_settings': row.get('NetworkSettings', {}),
                'mounts': row.get('Mounts', []),
            } for row in rows]
            config_errors = errors + [f'{n["name"]}: {key}: {value["status"]}' for n in nodes
                                      for key, value in n['configuration'].items() if value['status'] != 'available']
            isis_errors = errors + [f'{n["name"]}: {key}: {n[key]["status"]}' for n in nodes
                                    for key in ('isis', 'isis_routes') if n[key]['status'] != 'available']
            for name, records, issues in (('docker', docker_records, errors),
                                           ('isis', isis_records, isis_errors),
                                           ('configurations', configurations, config_errors)):
                snapshots[name] = Snapshot(**common, records=records, errors=issues,
                    status='partial' if issues and state == 'available' else state)
            snapshots['configurations'].configured_files = configured_files
            snapshots['configurations'].errors.extend(configured_files['errors'])
            if configured_files['errors'] and snapshots['configurations'].status == 'available':
                snapshots['configurations'].status = 'partial'
            live_report = (await collect_live_observations(self.reader, nodes, routes)
                           if self.active_observation else {
                               'status': 'missing', 'records': [], 'errors': [],
                               'source': 'Passive collection; start an explicit ping job for packet evidence'})
            report = (await asyncio.to_thread(collect_observations, self.evidence_run)
                      if self.evidence_run else live_report)
            snapshots['observations'] = Snapshot(
                **common, status='failed' if report['status'] == 'failed' else live_report['status'], records=([live_report, report] if self.evidence_run else [report]),
                errors=list(dict.fromkeys(live_report['errors'] + report['errors'])),
                source='live lab evidence; optional archived experiment is separately labelled')
            await asyncio.to_thread(self.store.publish, snapshots)
            self.status.update(state='failed' if state == 'failed' or report['status'] == 'failed' else 'partial' if any(
                s.status in ('partial', 'failed', 'missing') for s in snapshots.values()) else state,
                errors=list(dict.fromkeys(topology_errors + route_errors + policy_errors + config_errors + isis_errors + report['errors'])))
        except asyncio.CancelledError:
            self.status.update(state='failed', errors=['Collection interrupted by application shutdown'])
            raise
        except Exception as exc:
            self.status.update(state='failed', errors=[str(exc)])
        finally:
            self.status['finished_at'] = now()
