"""Acquire live evidence without treating raw captures as verified execution."""
import asyncio
import ipaddress
from datetime import datetime, timezone

from collectors.observation_collector import LEVELS, level


async def collect_live_observations(reader, nodes, routes):
    started = datetime.now(timezone.utc).isoformat()
    running = [node for node in nodes if node['running']]
    captures = [asyncio.create_task(reader.observe(n['id'], n['lab_id'], 'packets'))
                for n in running]
    probes = []
    try:
        # Give tcpdump time to attach. Readiness is not asserted: packet
        # correlation must later prove that a probe was actually captured.
        if captures:
            await asyncio.sleep(1)
        for lab in sorted({n['lab_id'] for n in running}):
            members = [n for n in running if n['lab_id'] == lab]
            for source in members:
                destination = None
                for target in members:
                    if target['id'] == source['id']:
                        continue
                    for interface in target['ipv6_addresses'].get('data') or []:
                        if interface.get('ifname') == 'eth0':
                            continue  # Containerlab management traffic bypasses data links.
                        for address in interface.get('addr_info', []):
                            try:
                                candidate = ipaddress.IPv6Address(address.get('local', ''))
                            except ValueError:
                                continue
                            if not (candidate.is_link_local or candidate.is_loopback or
                                    candidate.is_multicast or candidate.is_unspecified):
                                destination = str(candidate)
                                break
                        if destination:
                            break
                    if destination:
                        break
                if destination and hasattr(reader, 'probe'):
                    probes.append((source, destination, asyncio.create_task(
                        reader.probe(source['id'], lab, destination))))
        probe_results = await asyncio.gather(*(p[2] for p in probes), return_exceptions=True)
        capture_results = await asyncio.gather(*captures, return_exceptions=True)
    finally:
        tasks = captures + [p[2] for p in probes]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    records, errors = [], []
    for node, capture in zip(running, capture_results):
        if isinstance(capture, BaseException):
            capture = {'status': 'failed', 'raw': '', 'error': str(capture)}
        if capture['status'] != 'available':
            errors.append(f'{node["name"]}: capture: {capture.get("error")}')
        route = next(r for r in routes if r['id'] == node['id'])
        post = await reader.json_observation(node['id'], node['lab_id'], 'routes')
        levels = {key: level(reason=description + '; packet correlation required')
                  for key, description in LEVELS.items()}
        for key, references in {
            'OCP': ['control_plane', 'forwarding_pre.routes', 'routes_post'],
            'ON': ['capture', 'interfaces'],
            'ODP': ['forwarding_pre', 'routes_post', 'interfaces'],
            'OP': ['capture'],
            'ONW': ['capture', 'other nodes in this live report'],
        }.items():
            levels[key]['raw_evidence_fields'] = references
        records.append({'node': node['name'], 'lab_id': node['lab_id'],
                        'levels': levels, 'coverage_complete': False,
                        'capture': capture,
                        'control_plane': {'configuration': node['configuration'],
                                          'isis': node['isis'], 'isis_routes': node['isis_routes']},
                        'forwarding_pre': route, 'routes_post': post,
                        'interfaces': node['link_observation']})
    probe_records = []
    for (source, destination, _), result in zip(probes, probe_results):
        if isinstance(result, BaseException):
            result = {'status': 'failed', 'error': str(result)}
        probe_records.append({'node': source['name'], 'lab_id': source['lab_id'],
                              'destination': destination, **result})
    errors.append('Live evidence acquired but packet correlation and independent decoding are not implemented; five-level execution coverage is not established')
    return {'source': 'live capture and IPv6 echo probes', 'started_at': started,
            'finished_at': datetime.now(timezone.utc).isoformat(),
            'status': 'partial' if records else 'missing', 'records': records,
            'probes': probe_records, 'errors': errors}
