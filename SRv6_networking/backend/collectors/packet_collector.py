"""Read an existing experiment's packet evidence without starting traffic.

The experiment pipeline supplies two independent packet decoders. This reader
checks their recorded agreement and retains the original capture references.
It does not claim to run those decoders again.
"""
import hashlib
import json
from pathlib import Path
import struct


class RunEvidence:
    """Read only files whose contents match the experiment's SHA-256 manifest."""

    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.hashes = {}
        self.checked = {}
        self.captures = {}
        for line in (self.directory / 'manifest.sha256').read_text().splitlines():
            digest, name = line.split(maxsplit=1)
            self.hashes[name.lstrip('*')] = digest

    def read(self, name: str) -> bytes:
        if name not in self.checked:
            path = (self.directory / name).resolve()
            if not path.is_relative_to(self.directory):
                raise ValueError(f'Evidence path leaves the run directory: {name}')
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != self.hashes.get(name):
                raise ValueError(f'Missing or mismatched manifest hash: {name}')
            self.checked[name] = data
        return self.checked[name]

    def records(self, name: str) -> list[dict]:
        rows = [json.loads(line) for line in self.read(name).splitlines() if line.strip()]
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError(f'Expected JSON objects in {name}')
        return rows

    def frame_hash(self, name: str, number: int) -> str | None:
        """Locate a frame in classic PCAP; never confuse its number with its bytes."""
        if name not in self.captures:
            data = self.read(name)
            formats = {b'\xd4\xc3\xb2\xa1': '<', b'\xa1\xb2\xc3\xd4': '>',
                       b'\x4d\x3c\xb2\xa1': '<', b'\xa1\xb2\x3c\x4d': '>'}
            if len(data) < 24 or data[:4] not in formats:
                raise ValueError(f'Expected classic PCAP (PCAPNG is unsupported): {name}')
            offset = 24
            hashes = []
            while offset < len(data):
                if offset + 16 > len(data):
                    raise ValueError(f'Truncated PCAP record: {name}')
                _, _, captured, _ = struct.unpack_from(formats[data[:4]] + 'IIII', data, offset)
                offset += 16
                if offset + captured > len(data):
                    raise ValueError(f'Truncated PCAP frame: {name}')
                hashes.append(hashlib.sha256(data[offset:offset + captured]).hexdigest())
                offset += captured
            self.captures[name] = hashes
        hashes = self.captures[name]
        return hashes[number - 1] if isinstance(number, int) and 1 <= number <= len(hashes) else None


def frame_key(reference: dict) -> tuple:
    return (reference.get('capture_file'), reference.get('frame_number'),
            reference.get('frame_sha256'))


def packet_state(observation: dict) -> dict:
    """Keep inner and outer headers separate across encapsulation/decapsulation."""
    state = observation['state']
    outer = state.get('ipv6') or {}
    layers = (state.get('encapsulation') or {}).get('layers') or []

    def header(value):
        result = {key: value.get(key) for key in (
            'source', 'destination', 'hop_limit', 'traffic_class', 'flow_label',
            'payload_length', 'next_header')}
        length = result['payload_length']
        result['packet_length'] = length + 40 if isinstance(length, int) else None
        return result

    return {'depth': len(layers), 'outer': header(outer),
            'inner': header(layers[-1] if layers else outer),
            'srh_present': state.get('srh_present'), 'srh': state.get('srh') or {},
            'compressed_sid': state.get('usid_carrier_state')}


def compare_packets(before: dict, after: dict) -> dict:
    """Compare only observed fields of the same logical header.

    Missing SRH is not a Segments Left value of zero. A NEXT-CSID destination
    change does not require Segments Left to decrease.
    """
    result = {}
    for layer in ('inner', 'outer', 'srh'):
        for field in sorted(set(before[layer]) | set(after[layer])):
            left, right = before[layer].get(field), after[layer].get(field)
            status = 'comparable'
            if layer == 'srh' and before['srh_present'] is False and after['srh_present'] is False:
                status = 'absent'
            elif (left is None or right is None or
                  (layer in ('outer', 'srh') and before['depth'] != after['depth']) or
                  (layer == 'srh' and not (before['srh_present'] and after['srh_present']))):
                status = 'not_comparable'
            item = {'before': left, 'after': right, 'status': status}
            if status == 'comparable':
                item['changed'] = left != right
                if type(left) is int and type(right) is int:
                    item['delta'] = right - left
            result[f'{layer}.{field}'] = item
    if before['srh_present'] is False and after['srh_present'] is False:
        result['srh.segments_left'] = {'before': None, 'after': None, 'status': 'absent'}
    return result


def collect_packets(evidence: RunEvidence, run_id: str) -> list[dict]:
    """Admit unique echo observations with raw frames and dual-reader agreement."""
    frames = {}
    for frame in evidence.records('derived/frames.jsonl'):
        key = (frame['observation_point']['capture_file'], frame['frame_number'], frame['frame_sha256'])
        if key in frames:
            raise ValueError('Duplicate frame provenance')
        frames[key] = frame
    observations = evidence.records('derived/observations.jsonl')
    seen = set()
    for observation in observations:
        issues = []
        key = frame_key(observation.get('evidence_ref', {}))
        frame = frames.get(key, {})
        if key in seen:
            raise ValueError('Duplicate observation provenance')
        seen.add(key)
        if observation.get('run_id') != run_id or frame.get('run_id') != run_id:
            issues.append('Run identity mismatch')
        if observation.get('correlation') != 'unique' or not observation.get('packet_id'):
            issues.append('Packet correlation is missing or ambiguous')
        # ICMPv6 error quotations are not primary probes.
        if observation.get('icmpv6_type') not in (128, 129):
            issues.append('Not a primary ICMPv6 echo packet')
        if frame.get('reliability', {}).get('status') != 'agree':
            issues.append('Independent frame readers did not agree')
        state = observation.get('state') or {}
        for field in ('ipv6', 'srh_present', 'srh', 'encapsulation'):
            if state.get(field) != frame.get(field):
                issues.append(f'Observation differs from decoded frame: {field}')
        icmp = frame.get('icmpv6') or {}
        for field in ('type', 'identifier', 'sequence', 'payload_sha256'):
            if observation.get('icmpv6_' + field) != icmp.get(field):
                issues.append(f'Packet identity differs from decoded frame: {field}')
        if not frame or evidence.frame_hash(key[0], key[1]) != key[2]:
            issues.append('Raw capture frame does not match provenance')
        observation['admission_errors'] = issues
    return observations
