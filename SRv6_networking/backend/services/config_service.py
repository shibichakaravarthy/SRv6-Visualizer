"""Small runtime editor: validated commands, live preview, and before/after audit."""
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import shlex
from typing import Literal
import uuid
from pydantic import BaseModel, ConfigDict, Field, model_validator
from collectors.topology_collector import link_interfaces


class Change(BaseModel):
    model_config = ConfigDict(extra='forbid')
    device: str = Field(pattern=r'^[0-9a-f]{64}$')
    kind: Literal['ipv6', 'policy', 'sid', 'frr'] = 'policy'
    operation: Literal['replace', 'delete'] = 'replace'
    prefix: str = ''
    interface: str = Field(default='', max_length=15, pattern=r'^[a-zA-Z0-9_.-]*$')
    table: int = Field(default=254, ge=1, le=4294967295)
    metric: int = Field(default=1024, ge=1, le=4294967295)
    gateway: str = ''
    segments: list[str] = Field(default_factory=list, max_length=16)
    mode: Literal['encap', 'encap.red', 'inline'] = 'encap'
    action: Literal['End', 'End.X', 'End.DX6', 'End.DT6'] = 'End'
    lookup_table: int = Field(default=254, ge=1, le=4294967295)
    next_csid: bool = False
    lblen: int = Field(default=32, ge=8, le=120)
    nflen: int = Field(default=16, ge=8, le=120)
    commands: str = Field(default='', max_length=12000)
    expected: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def validate_fields(self):
        if self.kind == 'frr':
            if self.operation != 'replace' or not self.commands.strip():
                raise ValueError('Enter FRR configuration commands')
            return self
        self.prefix = str(ipaddress.IPv6Network(self.prefix, strict=False))
        network = ipaddress.IPv6Network(self.prefix)
        if network.network_address.is_multicast or network.network_address.is_link_local or network.network_address.is_loopback:
            raise ValueError('Use a data-plane unicast prefix')
        if self.table == 255:
            raise ValueError('The kernel local table is not editable')
        if self.gateway:
            self.gateway = address(self.gateway, link_local=True)
        self.segments = [address(s) for s in self.segments]
        if self.operation == 'delete':
            return self
        if not self.interface or self.interface == 'eth0':
            raise ValueError('Choose a data-plane interface')
        if self.kind == 'policy' and not self.segments:
            raise ValueError('An SR policy needs at least one SID')
        if self.kind == 'sid' and self.action in ('End.X', 'End.DX6') and not self.gateway:
            raise ValueError('This SID action requires an IPv6 next hop')
        if self.next_csid and (self.kind != 'sid' or self.action != 'End' or self.lblen % 8 or self.nflen % 8 or self.lblen + self.nflen > 128):
            raise ValueError('NEXT-CSID needs End and byte-aligned lengths totaling at most 128 bits')
        return self


def address(value, link_local=False):
    if '%' in value:
        raise ValueError('Use the interface field for scoped addresses')
    ip = ipaddress.IPv6Address(value)
    if ip.is_multicast or ip.is_loopback or ip.is_unspecified or (ip.is_link_local and not link_local):
        raise ValueError('Expected a unicast IPv6 address')
    return str(ip)


def table_id(value):
    return {'main':254, 'local':255, 'default':253}.get(str(value), value)


def route_key(route):
    return (str(ipaddress.IPv6Network('::/0' if route.get('dst') == 'default' else route.get('dst', '::/0'), strict=False)),
            int(table_id(route.get('table', 254))), int(route.get('metric', 1024)))


def selected_routes(routes, change):
    return [r for r in routes if route_key(r) == (change.prefix, change.table, change.metric)]


def fingerprint(value):
    # Counters/expiry can change without a configuration edit.
    def clean(item):
        if isinstance(item, dict):
            return {k:clean(v) for k,v in item.items() if k not in ('packets','bytes','errors','expires','used','lastuse','stats')}
        return [clean(v) for v in item] if isinstance(item, list) else item
    return hashlib.sha256(json.dumps(clean(value), sort_keys=True).encode()).hexdigest()


def route_command(change):
    command = ['ip','-6','route','del' if change.operation == 'delete' else 'replace', change.prefix,
               'table',str(change.table),'metric',str(change.metric)]
    if change.operation == 'delete':
        return command
    if change.kind == 'policy':
        command += ['encap','seg6','mode',change.mode,'segs',','.join(change.segments)]
    elif change.kind == 'sid':
        command += ['encap','seg6local','action',change.action]
        if change.action in ('End.X','End.DX6'):
            command += ['nh6',change.gateway]
        if change.action == 'End.DT6':
            command += ['table',str(change.lookup_table)]
        if change.next_csid:
            command += ['flavors','next-csid','lblen',str(change.lblen),'nflen',str(change.nflen)]
    if change.kind == 'ipv6' and change.gateway:
        command += ['via',change.gateway]
    return command + ['dev',change.interface]


def frr_commands(text, interfaces):
    """A bounded configuration subset, never an unrestricted vtysh/shell terminal."""
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith('!')]
    if not lines or len(lines) > 60:
        raise ValueError('Use 1–60 FRR configuration lines')
    context = 'global'
    for line in lines:
        if line == 'exit':
            context = 'global'; continue
        if line.startswith('interface '):
            name = line.split(' ',1)[1]
            if context != 'global' or name not in interfaces or name == 'eth0':
                raise ValueError('Use a live data-plane interface from global configuration mode')
            context = 'interface'; continue
        if re.fullmatch(r'router isis [a-zA-Z0-9_-]{1,32}', line) and context == 'global':
            context = 'isis'; continue
        command = line.removeprefix('no ')
        if context == 'interface':
            if command.startswith('ipv6 address '):
                ipaddress.IPv6Interface(command[len('ipv6 address '):]); continue
            if re.fullmatch(r'ipv6 router isis [a-zA-Z0-9_-]{1,32}', command): continue
            if command in ('isis passive','isis circuit-type level-2-only','isis circuit-type level-1','isis circuit-type level-1-2'): continue
            match = re.fullmatch(r'isis metric (\d+)(?: level-[12])?', command)
            if match and 0 <= int(match[1]) <= 16777215: continue
        if context == 'isis':
            if re.fullmatch(r'net [0-9a-fA-F.]{10,60}', command): continue
            if command in ('is-type level-1','is-type level-2-only','is-type level-1-2','metric-style wide','metric-style narrow','metric-style transition'): continue
        raise ValueError(f'Unsupported command or context: {line}. Use interface IPv6/IS-IS settings or router isis settings; exit returns to global mode.')
    args = ['vtysh','-c','configure terminal']
    for line in lines:
        args += ['-c',line]
    return args + ['-c','end']


class ConfigService:
    def __init__(self, reader, directory: Path):
        self.reader, self.directory = reader, directory

    async def preview(self, change, view):
        node = next((n for n in view['nodes'] if n['id'] == change.device), None)
        if not node or not node['running'] or node['role'] != 'router':
            raise ValueError('Select a running, discovered router')
        links = await self.reader.json_observation(node['id'],node['lab_id'],'links')
        interfaces = [i['ifname'] for i in link_interfaces(links)]
        command_name = 'running_config' if change.kind == 'frr' else 'routes'
        before = (await self.reader.observe(node['id'],node['lab_id'],command_name) if change.kind == 'frr'
                  else await self.reader.json_observation(node['id'],node['lab_id'],command_name))
        if before['status'] != 'available':
            raise ValueError('Cannot read live configuration: ' + str(before.get('error')))
        if change.kind == 'frr':
            command = frr_commands(change.commands,interfaces)
            current = before['raw']
        else:
            if change.operation != 'delete' and change.interface not in interfaces:
                raise ValueError('Interface is not present on this router')
            current = selected_routes(before['data'],change)
            if len(current) > 1:
                raise ValueError('Ambiguous/multipath route: edit it through its owning routing process')
            if any(r.get('protocol') not in (None,'boot','static',3,4) or r.get('dev') == 'eth0' or r.get('multipath') or r.get('nhid') for r in current):
                raise ValueError('This route is managed by the kernel or routing protocol; edit its owning configuration instead')
            if change.operation == 'delete' and not current:
                raise ValueError('No route matches that prefix, table, and metric')
            command = route_command(change)
        return {'device':node, 'command':command, 'command_text':shlex.join(command),
                'before':current, 'observation':before,
                'expected':fingerprint({'change':change.model_dump(exclude={'expected'}),'current':current}),
                'persistence':'Runtime only. Download the command to retain it in your lab configuration.'}

    async def apply(self, change, view):
        plan = await self.preview(change,view)
        if not change.expected or plan['expected'] != change.expected:
            raise RuntimeError('Configuration changed or preview is missing. Preview again before applying.')
        node = plan['device']
        self.directory.mkdir(parents=True,exist_ok=True)
        path = self.directory / (uuid.uuid4().hex + '.json')
        record = {'created_at':datetime.now(timezone.utc).isoformat(),'device':node['name'],
                  'change':change.model_dump(),'command':plan['command'],'before':plan['observation'],
                  'status':'applying','runtime_only':True}
        path.write_text(json.dumps(record,indent=2))  # Preserve recovery context BEFORE mutation.
        result = await self.reader.configure(node['id'],node['lab_id'],plan['command'])
        after = (await self.reader.observe(node['id'],node['lab_id'],'running_config') if change.kind == 'frr'
                 else await self.reader.json_observation(node['id'],node['lab_id'],'routes'))
        ok = result['status'] == 'available' and after['status'] == 'available'
        if change.kind != 'frr' and ok:
            matches = selected_routes(after['data'],change)
            ok = not matches if change.operation == 'delete' else len(matches) == 1 and matches_change(matches[0],change)
        # vtysh may print command errors even when returning zero.
        if change.kind == 'frr' and frr_errors(plan['observation'], result):
            ok = False
        record.update(result=result,after=after,status='verified' if ok and change.kind != 'frr' else 'applied' if ok else 'failed')
        path.write_text(json.dumps(record,indent=2))
        return {'status':record['status'], 'result':result,'after':after,'audit_file':path.name,
                'message': ('Route change verified in the live kernel table.' if change.kind != 'frr' else
                            'FRR commands accepted. Review the collected running configuration; multi-line edits are not atomic.') if ok else
                           'Command failed or verification was unavailable. Review before/after evidence; some FRR lines may have applied.',
                'before':plan['before'],'command_text':plan['command_text'],'runtime_only':True}


def matches_change(route, change):
    if route.get('dev') != change.interface: return False
    encap = route.get('encap') or {}
    if not isinstance(encap,dict): return False
    kind = encap.get('encap_type',encap.get('type'))
    if change.kind == 'ipv6':
        return not encap and route.get('gateway','') == change.gateway
    if change.kind == 'policy':
        return kind == 'seg6' and encap.get('mode') == change.mode and encap.get('segs') == change.segments
    if kind != 'seg6local' or encap.get('action') != change.action: return False
    if change.action in ('End.X','End.DX6') and encap.get('nh6') != change.gateway: return False
    if change.action == 'End.DT6' and str(encap.get('table')) != str(change.lookup_table): return False
    if change.next_csid:
        flavor = encap.get('flavors',{})
        geometry = flavor if isinstance(flavor,dict) else encap
        return 'next-csid' in str(flavor) and int(geometry.get('lblen',32)) == change.lblen and int(geometry.get('nflen',16)) == change.nflen
    return not encap.get('flavors')


def frr_errors(before, result):
    """Keep pre-existing startup warnings visible without misreporting a rejected edit."""
    existing = set((before.get('error') or '').splitlines())
    new_errors = [line for line in (result.get('error') or '').splitlines() if line not in existing]
    new_errors += [line for line in result.get('raw','').splitlines()
                   if line.startswith('%') and line != '% Configuration applied with notes:']
    return new_errors
