"""Explicit ping execution -> capture -> reconstruction -> durable evidence."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid
from collectors.packet_collector import RunEvidence
from services.capture_service import capture
from services.evidence_service import decode_capture
from services.journey_service import reconstruct


def ping_statistics(raw):
    match = re.search(r'(\d+) packets transmitted, (\d+) (?:packets )?received.*?([\d.]+)% packet loss', raw)
    latency = re.search(r'(?:rtt|round-trip).*?= ([\d.]+)/([\d.]+)/([\d.]+)', raw)
    return {'transmitted': int(match[1]) if match else None, 'received': int(match[2]) if match else None,
            'loss_percent': float(match[3]) if match else None,
            'rtt_ms': dict(zip(('min', 'avg', 'max'), map(float, latency.groups()))) if latency else None}


class PingService:
    def __init__(self, reader, directory: Path):
        self.reader, self.directory = reader, directory
        self.task = None
        self.status = {'state': 'idle'}
        self.saved_view = None
        candidates = sorted(directory.glob('*/result.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            try:
                evidence = RunEvidence(candidates[0].parent)
                for name in evidence.hashes:
                    evidence.read(name)
                self.status = json.loads(evidence.read('result.json'))
                self.saved_view = json.loads(evidence.read('snapshot.json'))
            except (OSError, ValueError, KeyError) as exc:
                self.status = {'state': 'failed', 'errors': [f'Saved evidence could not be verified: {exc}'], 'journeys': []}

    def start(self, view, source_id, destination_id, source_address, destination_address):
        if self.task and not self.task.done():
            raise RuntimeError('A ping job is already running')
        source, destination = self.validate_selection(view, source_id, destination_id, source_address, destination_address)
        self.status = {'id': uuid.uuid4().hex, 'state': 'running', 'phase': 'capture_startup',
                       'started_at': datetime.now(timezone.utc).isoformat(), 'source': source_id,
                       'destination': destination_id, 'source_address': source_address,
                       'destination_address': destination_address, 'journeys': [], 'errors': []}
        self.saved_view = view
        self.task = asyncio.create_task(self.run(view, source, destination))
        return self.status

    def validate_selection(self, view, source_id, destination_id, source_address, destination_address):
        nodes = {n['id']: n for n in view['nodes']}
        source, destination = nodes.get(source_id), nodes.get(destination_id)
        if not source or not destination or source_id == destination_id:
            raise ValueError('Select two distinct discovered devices')
        if source['lab_id'] != destination['lab_id'] or not all(n['running'] and n['role'] in ('host', 'client', 'server') for n in (source, destination)):
            raise ValueError('Both endpoints must be running clients in the same lab')
        for node, address in ((source, source_address), (destination, destination_address)):
            if address not in [a['address'] for a in node['addresses']]:
                raise ValueError('Select a collected data-plane IPv6 address')
        return source, destination

    async def run(self, view, source, destination):
        tasks = []
        directory = self.directory / self.status['id']
        try:
            directory.mkdir(parents=True, mode=0o700)
            (directory / 'snapshot.json').write_text(json.dumps(view, indent=2))
            nodes = [n for n in view['nodes'] if n['running'] and n['lab_id'] == source['lab_id']]
            pattern = uuid.uuid4().hex
            self.status['payload_pattern'] = pattern
            ready = [asyncio.Event() for _ in nodes]
            tasks = [asyncio.create_task(capture(self.reader, n, directory, event)) for n, event in zip(nodes, ready)]
            try:
                await asyncio.wait_for(asyncio.gather(*(e.wait() for e in ready)), 6)
            except asyncio.TimeoutError:
                self.status['errors'].append('Some capture startups did not settle before ping')
            self.status['phase'] = 'ping'
            from services.workspace_service import addresses
            for endpoint, address in ((source, self.status['source_address']), (destination, self.status['destination_address'])):
                observed = await self.reader.json_observation(endpoint['id'], endpoint['lab_id'], 'interfaces')
                if address not in [a['address'] for a in addresses({'ipv6_addresses': observed})]:
                    raise ValueError(f'{endpoint["name"]}: selected IPv6 address is no longer verified; refresh topology')
            ping = await self.reader.probe(source['id'], source['lab_id'], self.status['destination_address'],
                                           self.status['source_address'], pattern)
            self.status.update(ping=ping, statistics=ping_statistics(ping.get('raw', '')), phase='evidence')
            captures = await asyncio.gather(*tasks)
            self.status['captures'] = captures
            observations = []
            for node, result in zip(nodes, captures):
                if result['status'] != 'available':
                    self.status['errors'].append(f'{node["name"]}: {result.get("error") or result.get("log") or "Capture unavailable"}')
                    continue
                try:
                    rows, errors = await asyncio.to_thread(decode_capture, directory / result['file'], node, pattern)
                    observations.extend(rows)
                    self.status['errors'].extend(f'{node["name"]}: {e}' for e in errors)
                except Exception as exc:
                    self.status['errors'].append(f'{node["name"]}: decoder unavailable: {exc}')
            (directory / 'observations.json').write_text(json.dumps(observations, indent=2))
            links = [l for l in view['links'] if l['status'] == 'observed']
            self.status['journeys'] = reconstruct(observations, links, source['id'], destination['id'],
                self.status['source_address'], self.status['destination_address'], view['snapshots']['srv6_policies']['records'])
            if not self.status['journeys']:
                self.status['errors'].append('No correlated hop-level evidence. Ping output is retained; packet movement is unavailable.')
            self.status['state'] = 'complete' if ping['status'] == 'available' else 'failed'
        except asyncio.CancelledError:
            self.status.update(state='failed', errors=['Job interrupted; capture windows are bounded to 12 seconds'])
            raise
        except Exception as exc:
            self.status.update(state='failed', errors=[str(exc)])
        finally:
            # Let bounded in-container captures finish before returning, including shutdown.
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self.status['finished_at'] = datetime.now(timezone.utc).isoformat()
            if directory.exists():
                (directory / 'result.json').write_text(json.dumps(self.status, indent=2))
                files = sorted(p for p in directory.iterdir() if p.is_file() and p.name != 'manifest.sha256')
                (directory / 'manifest.sha256').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in files))
