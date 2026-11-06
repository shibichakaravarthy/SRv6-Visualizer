"""The only subprocess boundary: fixed observations, never user-supplied commands."""
import asyncio
import json
import ipaddress
import re
import subprocess
from datetime import datetime, timezone

COMMANDS = {
    'packets': ('timeout', '-s', 'INT', '-k', '2', '10', 'tcpdump',
                '-i', 'any', '-nn', '-e', '-tt', '-vvv', '-XX', '-l',
                '-s', '0', '-c', '200', 'ip6'),
    'running_config': ('vtysh', '-c', 'show running-config'),
    'frr_config': ('cat', '/etc/frr/frr.conf'),
    'frr_daemons': ('cat', '/etc/frr/daemons'),
    'forwarding': ('cat', '/proc/sys/net/ipv6/conf/all/forwarding'),
    'seg6_enabled': ('cat', '/proc/sys/net/ipv6/conf/all/seg6_enabled'),
    'interfaces': ('ip', '-j', '-6', 'address', 'show'),
    'interfaces_text': ('ip', '-6', 'address', 'show'),
    'links': ('ip', '-j', '-d', 'link', 'show'),
    'links_text': ('ip', 'link', 'show'),
    'routes': ('ip', '-j', '-6', '-d', 'route', 'show', 'table', 'all'),
    'routes_text': ('ip', '-6', 'route', 'show', 'table', 'all'),
    'isis': ('vtysh', '-c', 'show isis neighbor json'),
    'isis_text': ('vtysh', '-c', 'show isis neighbor'),
    'isis_routes': ('vtysh', '-c', 'show ipv6 route isis json'),
    'isis_routes_text': ('vtysh', '-c', 'show ipv6 route isis'),
    'neighbors': ('ip', '-j', '-6', 'neigh', 'show'),
    'neighbors_text': ('ip', '-6', 'neigh', 'show'),
    'rules': ('ip', '-j', '-6', 'rule', 'show'),
    'rules_text': ('ip', '-6', 'rule', 'show'),
}
ID = re.compile(r'^[0-9a-f]{64}$')


def _run_sync(args: list[str], timeout: float) -> dict:
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, check=False, shell=False)
        return {'status': 'available' if result.returncode == 0 else 'failed',
                'code': result.returncode, 'raw': result.stdout.decode(errors='replace'),
                'error': result.stderr.decode(errors='replace').strip() or None}
    except subprocess.TimeoutExpired as exc:
        return {'status': 'failed', 'code': None,
                'raw': (exc.stdout or b'').decode(errors='replace'),
                'error': 'Command timed out'}
    except OSError as exc:
        return {'status': 'failed', 'code': None, 'raw': '', 'error': str(exc)}


async def _run(args: list[str], timeout: float = 12) -> dict:
    # Keep the event loop responsive; subprocess.run kills and reaps on timeout.
    return await asyncio.to_thread(_run_sync, args, timeout)


def decoded(result: dict, expected: type | tuple[type, ...]) -> dict:
    result = dict(result)
    result['data'] = None
    if result['status'] != 'available':
        return result
    try:
        value = json.loads(result['raw'])
        if not isinstance(value, expected):
            raise ValueError(f'Unexpected JSON shape (expected {expected})')
        if isinstance(value, list) and any(not isinstance(row, dict) for row in value):
            raise ValueError('Expected JSON object records')
        result['data'] = value
    except (ValueError, TypeError) as exc:
        result.update(status='parse_error', error=str(exc))
    return result


class DockerReader:
    async def discover(self) -> dict:
        result = await _run(['docker', 'ps', '-a', '--no-trunc', '--filter',
                             'label=containerlab', '--format', '{{.ID}}'])
        result['data'] = []
        if result['status'] != 'available':
            return result
        for cid in result['raw'].splitlines():
            inspected = await self.inspect(cid)
            if inspected['status'] != 'available':
                result.setdefault('errors', []).append(inspected.get('error') or inspected['raw'])
                continue
            result['data'].append(inspected['data'][0])
        if result.get('errors'):
            result['status'] = 'partial'
        return result

    async def inspect(self, cid: str) -> dict:
        if not ID.fullmatch(cid):
            return {'status': 'failed', 'raw': '', 'error': 'Invalid full container ID'}
        result = decoded(await _run(['docker', 'inspect', '--type', 'container', cid]), list)
        if result['status'] == 'available':
            rows = result['data']
            if (len(rows) != 1 or rows[0].get('Id') != cid or
                    not (rows[0].get('Config', {}).get('Labels') or {}).get('containerlab')):
                result.update(status='failed', error='Containerlab identity validation failed')
        return result

    async def observe(self, cid: str, lab: str, command: str) -> dict:
        if command not in COMMANDS:
            raise ValueError('Observation is not allowlisted')
        # Recheck immutable ID, membership and state before every exec; names can be reused.
        inspected = await self.inspect(cid)
        if inspected['status'] != 'available':
            return inspected
        row = inspected['data'][0]
        if row['Config']['Labels']['containerlab'] != lab:
            return {'status': 'failed', 'raw': '', 'error': 'Lab identity changed'}
        if not row.get('State', {}).get('Running'):
            return {'status': 'missing', 'raw': '', 'error': 'Container is not running'}
        result = await _run(['docker', 'exec', cid, *COMMANDS[command]], timeout=15)
        if command == 'packets' and result.get('code') == 124:
            result.update(status='available', capture_window_elapsed=True)
        result['observed_at'] = datetime.now(timezone.utc).isoformat()
        result['command'] = list(COMMANDS[command])
        message = result.get('raw', '') + (result.get('error') or '')
        if 'executable file not found' in message:
            result.update(status='missing', error=f'{COMMANDS[command][0]} is not installed in this container')
        return result

    async def configure(self, cid: str, lab: str, command: list[str]) -> dict:
        """Execute only commands produced by ConfigService, with a fresh identity check."""
        if command[:3] != ['ip', '-6', 'route'] and command[:3] != ['vtysh', '-c', 'configure terminal']:
            raise ValueError('Unsupported configuration command')
        inspected = await self.inspect(cid)
        if inspected['status'] != 'available':
            return inspected
        row = inspected['data'][0]
        if row['Config']['Labels']['containerlab'] != lab or not row.get('State', {}).get('Running'):
            return {'status':'failed','raw':'','error':'Lab identity or running state changed'}
        return await _run(['docker', 'exec', cid, *command], timeout=15)

    async def probe(self, cid: str, lab: str, destination: str, source: str | None = None, pattern: str | None = None) -> dict:
        address = ipaddress.IPv6Address(destination)
        if address.is_multicast or address.is_link_local or address.is_unspecified or address.is_loopback:
            raise ValueError('Probe requires a unicast lab address')
        inspected = await self.inspect(cid)
        if inspected['status'] != 'available':
            return inspected
        row = inspected['data'][0]
        if row['Config']['Labels']['containerlab'] != lab or not row.get('State', {}).get('Running'):
            return {'status': 'missing', 'raw': '', 'error': 'Lab node is not running'}
        command = ['ping', '-6', '-n', '-c', '3', '-W', '1', str(address)]
        if source is not None:
            command += ['-I', str(ipaddress.IPv6Address(source))]
        if pattern is not None:
            if not re.fullmatch(r'[0-9a-f]{32}', pattern):
                raise ValueError('Invalid probe pattern')
            command += ['-p', pattern]
        result = await _run(['docker', 'exec', cid, *command], timeout=8)
        return {**result, 'command': command, 'observed_at': datetime.now(timezone.utc).isoformat()}

    async def json_observation(self, cid: str, lab: str, command: str,
                               expected: type = list) -> dict:
        result = decoded(await self.observe(cid, lab, command), expected)
        if result['status'] not in ('available', 'missing'):
            # Text is evidence, not a made-up empty structured result.
            result['fallback'] = await self.observe(cid, lab, command + '_text')
            if result['fallback']['status'] == 'available':
                result['status'] = 'unsupported' if result.get('code') else 'parse_error'
        return result


if __name__ == "__main__":
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from collectors._cli import main
    raise SystemExit(main('docker'))
