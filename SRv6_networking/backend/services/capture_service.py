"""Bounded binary capture transport. Packet interpretation lives elsewhere."""
import asyncio
import os
import uuid
from pathlib import Path


async def capture(reader, node: dict, directory: Path, ready: asyncio.Event, sidecar: bool = False) -> dict:
    result = {'node': node['id'], 'name': node['name'], 'file': node['id'] + '.pcap',
              'status': 'failed', 'ready': False, 'transport': 'network-namespace sidecar' if sidecar else 'container exec'}
    process = None
    try:
        inspected = await reader.inspect(node['id'])
        if inspected['status'] != 'available':
            raise ValueError(inspected.get('error', 'Container inspection failed'))
        row = inspected['data'][0]
        if row['Config']['Labels']['containerlab'] != node['lab_id'] or not row['State']['Running']:
            raise ValueError('Container identity or running state changed')
        # SLL2 preserves the real interface index and ingress/egress direction.
        command = ['docker', 'exec', node['id'], 'timeout', '-s', 'INT', '-k', '2', '12',
                   'tcpdump', '-i', 'any', '-y', 'LINUX_SLL2', '-U', '-nn', '-s', '0',
                   '-c', '10000', '-w', '-', 'ip6']
        if sidecar:
            image = os.environ.get('SRV6_CAPTURE_IMAGE', 'nicolaka/netshoot')
            command = ['docker', 'run', '--rm', '--pull', 'never', '--read-only',
                       '--cap-drop', 'ALL', '--cap-add', 'NET_RAW', '--network', 'container:' + node['id'],
                       '--name', 'srv6-capture-' + uuid.uuid4().hex, '--entrypoint', 'timeout', image,
                       *command[4:]]
        result['command'] = command
        with (directory / result['file']).open('wb' if sidecar else 'xb') as output:
            process = await asyncio.create_subprocess_exec(*command, stdout=output, stderr=asyncio.subprocess.PIPE)
            async def consume():
                lines = []
                async for line in process.stderr:
                    message = line.decode(errors='replace')
                    lines.append(message)
                    if 'listening on' in message:
                        result['ready'] = True
                        ready.set()
                return ''.join(lines)
            log_task = asyncio.create_task(consume())
            try:
                await asyncio.wait_for(process.wait(), 17)
                result['log'] = await log_task
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                if not log_task.done():
                    log_task.cancel()
                await asyncio.gather(log_task, return_exceptions=True)
        if not sidecar and not result['ready'] and ('No such file' in result.get('log', '') or 'not found' in result.get('log', '')):
            return await capture(reader, node, directory, ready, sidecar=True)
        result.update(code=process.returncode, status='available' if result['ready'] and process.returncode in (0, 124) else 'failed')
    except (OSError, ValueError, asyncio.TimeoutError) as exc:
        result['error'] = str(exc) or 'Capture timed out'
    finally:
        ready.set()  # Signals a settled startup, not necessarily success.
    return result
