"""Five-level evidence coverage from thesis §4.4, Table 4.3, and §4.5.

Live inventory alone cannot establish packet execution. Import a completed
experiment directory to connect installed state to packet-specific evidence.
Historical evidence is always labelled with its original run, never today's
Docker collection ID.
"""
import ipaddress
from pathlib import Path
import sys

import yaml

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.packet_collector import RunEvidence, collect_packets, compare_packets, frame_key, packet_state

LEVELS = {
    'OCP': 'Run-associated installed policy or LocalSID behaviour',
    'ON': 'Correlated ingress and egress around the processing node',
    'ODP': 'Relevant route, next hop, interface, table or VRF',
    'OP': 'Comparable before/after packet fields',
    'ONW': 'Subsequent observation of the same packet',
}


def level(status='missing', reason='', **evidence):
    return {'status': status, 'reason': reason, **evidence}


def same_packet(left: dict, right: dict) -> bool:
    keys = ('run_id', 'packet_id', 'traffic_direction', 'icmpv6_type',
            'icmpv6_identifier', 'icmpv6_sequence', 'icmpv6_payload_sha256')
    if not all(left.get(key) is not None and left.get(key) == right.get(key) for key in keys):
        return False
    first, second = packet_state(left)['inner'], packet_state(right)['inner']
    return all(first.get(key) is not None and first[key] == second.get(key)
               for key in ('source', 'destination'))


def collect_event(event, observations, evidence, run_id):
    """Report evidence coverage; upstream behaviour interpretation stays separate."""
    levels = {name: level(reason=description) for name, description in LEVELS.items()}
    record = {'run_id': run_id, 'event_id': event.get('event_id'),
              'packet_id': event.get('packet_correlation_id'), 'node': event.get('node'),
              'behaviour': event.get('declared_behaviour'), 'levels': levels,
              'upstream_evidence_status': event.get('evidence_status'),
              'coverage_complete': False}
    if event.get('run_id') != run_id:
        raise ValueError('Execution event belongs to another run')
    references = event.get('evidence', [])
    by_frame = {frame_key(row['evidence_ref']): row for row in observations}
    pair = [by_frame.get(frame_key(ref)) for ref in references]
    if len(pair) != 2 or not all(pair):
        return record
    before, after = pair
    if (before['admission_errors'] or after['admission_errors'] or
            not same_packet(before, after) or before['packet_id'] != record['packet_id'] or
            before.get('traffic_direction') != event.get('traffic_direction')):
        levels['ON']['reason'] = 'Missing, unreliable, or ambiguous correlated frames'
        return record
    # A direct same-node bracket is deliberately conservative. Topology-inferred
    # brackets require additional link evidence and are not silently accepted.
    if not (before['observation_point']['node'] == after['observation_point']['node'] == record['node']
            and before.get('observation_direction') == 'ingress'
            and after.get('observation_direction') == 'egress'):
        levels['ON']['reason'] = 'Direct ingress/egress bracket not established'
        return record
    levels['ON'] = level('available', 'Same packet at ingress and egress', frames=references)
    left, right = packet_state(before), packet_state(after)
    levels['OP'] = level('available', 'Recorded dual-reader agreement; layer-aware comparison',
                         before=left, after=right, fields=compare_packets(left, right))

    forwarding = event.get('forwarding_transition') or {}
    match = forwarding.get('matched_forwarding_entry') or {}
    entry = match.get('entry')
    source = match.get('source_file')
    # Use the state archived WITH this experiment, never the current Docker FIB.
    if entry and source and Path(source).name == source:
        state_files = []
        for phase in ('pre', 'post'):
            name = f'raw/node_state/{phase}/{record["node"]}/{source}'
            if name in evidence.hashes:
                lines = evidence.read(name).decode().splitlines()
                if entry.strip() in [line.strip() for line in lines]:
                    state_files.append(name)
        # A preserved entry must also cover the packet's arriving destination.
        try:
            network = ipaddress.IPv6Network(entry.split()[0], strict=False)
            relevant = ipaddress.IPv6Address(left['outer']['destination']) in network
        except (ValueError, IndexError, TypeError):
            relevant = False
        if state_files and relevant:
            levels['OCP'] = level('available', 'Installed entry retained in this run',
                                  entry=entry, source_files=state_files)
            egress = after['observation_point']['interface']
            if match.get('egress_interface') == egress:
                levels['ODP'] = level('available', 'Archived matched entry and observed egress agree',
                                      forwarding=forwarding, source_files=state_files)

    # Require another node, ingress direction, the same identity and a later time.
    timestamp = after.get('capture_timestamp_epoch')
    next_nodes = set(forwarding.get('departed_on_link') or []) - {record['node']}
    later = [row for row in observations if not row['admission_errors']
             and same_packet(after, row) and row['observation_point']['node'] != record['node']
             and row['observation_point']['node'] in next_nodes
             and row.get('observation_direction') == 'ingress'
             and isinstance(timestamp, (int, float))
             and isinstance(row.get('capture_timestamp_epoch'), (int, float))
             and row['capture_timestamp_epoch'] >= timestamp]
    if later:
        next_row = min(later, key=lambda row: row['capture_timestamp_epoch'])
        levels['ONW'] = level('available', 'Same packet observed at a subsequent node',
                              frame=next_row['evidence_ref'], point=next_row['observation_point'])
    # Do not infer a domain boundary from a behaviour name alone.
    elif event.get('terminal_leaves_observed_domain') is True and str(record['behaviour']).startswith(('End.DX', 'End.DT')):
        levels['ONW'] = level('not_applicable', 'Run explicitly declares departure from the observed domain')
    record['coverage_complete'] = all(item['status'] in ('available', 'not_applicable')
                                      for item in levels.values())
    record['interpretation'] = ('Evidence coverage is not an independent RFC behaviour verdict; '
                                'missing evidence does not prove non-execution.')
    return record


def collect_observations(run_directory: Path | None = None) -> dict:
    """Return explicit missing evidence when no packet experiment is supplied."""
    result = {'status': 'missing', 'source': 'archived experiment evidence',
              'run_id': None, 'records': [], 'errors': [], 'levels': LEVELS}
    if run_directory is None:
        result['errors'] = ['No evidence run supplied: live state alone does not cover ON, OP or ONW.']
        return result
    try:
        evidence = RunEvidence(run_directory)
        metadata = yaml.safe_load(evidence.read('run.yaml'))
        run_id = metadata['run_id']
        result.update(run_id=run_id, run_directory=str(evidence.directory),
                      lab_id=metadata.get('lab_name'), started=metadata.get('started'),
                      finished=metadata.get('finished'))
        observations = collect_packets(evidence, run_id)
        events = evidence.records('derived/execution_events.jsonl')
        result['records'] = [collect_event(event, observations, evidence, run_id) for event in events]
        result['status'] = ('available' if result['records'] and all(
            row['coverage_complete'] for row in result['records']) else 'partial' if events else 'missing')
        result['verified_files'] = {name: evidence.hashes[name] for name in evidence.checked}
        if result['status'] != 'available':
            result['errors'].append('Some events lack one or more observation levels; inspect records[].levels.')
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
        result.update(status='failed', records=[], errors=[f'Cannot import experiment evidence: {exc}'])
    return result


def main():
    from collectors._cli import main as run_collector
    return run_collector('observations')


if __name__ == '__main__':
    raise SystemExit(main())
