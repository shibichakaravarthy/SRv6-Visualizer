"""Adapt Linux cooked captures to the preserved thesis's independent readers.

Original bytes, frame numbers, and hashes remain authoritative. The synthesized
Ethernet envelope is solely a decoder adapter, never original wire evidence.
"""
import hashlib
import os
from pathlib import Path
import struct
import sys

THESIS = Path(os.environ.get('SRV6_THESIS_SRC', str(Path(__file__).resolve().parents[3] / 'Prototype/my thesis/src')))


def decode_capture(path: Path, node: dict, pattern: str) -> tuple[list, list]:
    if not THESIS.is_dir():
        raise ValueError('Thesis decoder source unavailable; set SRV6_THESIS_SRC')
    if str(THESIS) not in sys.path:
        sys.path.insert(0, str(THESIS))
    from srv6obs.extract import _scapy_read, _manual_read, _cross_check
    data = path.read_bytes()
    formats = {b'\xd4\xc3\xb2\xa1': ('<', 1e6), b'\xa1\xb2\xc3\xd4': ('>', 1e6),
               b'\x4d\x3c\xb2\xa1': ('<', 1e9), b'\xa1\xb2\x3c\x4d': ('>', 1e9)}
    if len(data) < 24 or data[:4] not in formats:
        raise ValueError('Missing or unsupported PCAP header')
    endian, scale = formats[data[:4]]
    if struct.unpack_from(endian + 'I', data, 20)[0] != 276:
        raise ValueError('Capture must preserve SLL2 interface indexes and direction')
    interfaces = {i['ifindex']: i['ifname'] for i in node['interfaces']}
    offset, number, records, errors = 24, 0, [], []
    while offset < len(data):
        number += 1
        if offset + 16 > len(data):
            errors.append('Truncated PCAP record'); break
        sec, fraction, size, original_size = struct.unpack_from(endian + 'IIII', data, offset)
        offset += 16
        raw = data[offset:offset + size]; offset += size
        if len(raw) != size or size != original_size or size < 20:
            errors.append(f'Frame {number}: truncated'); continue
        protocol, _, index, _, direction, _ = struct.unpack_from('!HHIHBB', raw)
        if protocol != 0x86dd or direction not in (0, 1, 2, 3, 4) or interfaces.get(index) in (None, 'eth0', 'lo'):
            continue
        network = raw[20:]
        # Job nonce must be in the ICMP payload, verified again after decoding.
        if bytes.fromhex(pattern) not in network:
            continue
        ethernet = bytes(12) + b'\x86\xdd' + network
        scapy, manual = _scapy_read(ethernet), _manual_read(ethernet)
        reliability = _cross_check(scapy, manual)
        icmp = manual.get('icmpv6') or {}
        if icmp.get('type') not in (128, 129):
            continue
        if reliability['status'] != 'agree':
            errors.append(f'Frame {number}: independent readers disagree'); continue
        # _payload_view retains payload_hex in the existing thesis reader.
        payload = icmp.get('payload_hex', '')
        if pattern not in payload:
            continue
        inner = (manual.get('encapsulation', {}).get('layers') or [manual['ipv6']])[-1]
        records.append({'node': node['id'], 'name': node['name'], 'interface': interfaces[index],
                        'direction': 'egress' if direction == 4 else 'ingress',
                        'timestamp': sec + fraction / scale, 'frame': number,
                        'capture': path.name, 'frame_sha256': hashlib.sha256(raw).hexdigest(),
                        'wire_sha256': hashlib.sha256(network).hexdigest(),
                        'packet_key': [icmp['type'], icmp['identifier'], icmp['sequence'],
                                       icmp['payload_sha256'], inner['source'], inner['destination']],
                        'state': manual, 'reliability': reliability})
    return records, errors
