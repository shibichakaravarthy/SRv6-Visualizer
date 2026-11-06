"""Evidence-only physical transfers; no shortest-path or SID-list expansion."""
from collections import defaultdict
import ipaddress
from collectors.packet_collector import packet_state, compare_packets


def reconstruct(observations, links, source, destination, source_address, destination_address, installed):
    endpoints = {(link[side], link[side + '_interface']) for link in links for side in ('source', 'target')}
    groups = defaultdict(list)
    for row in observations:
        key = row['packet_key']
        addresses = (source_address, destination_address) if key[0] == 128 else (destination_address, source_address)
        if tuple(key[-2:]) == addresses and (row['node'], row['interface']) in endpoints:
            groups[tuple(key)].append(row)
    journeys = []
    for key, rows in sorted(groups.items(), key=lambda item: min(r['timestamp'] for r in item[1])):
        direction = 'request' if key[0] == 128 else 'reply'
        start, finish = (source, destination) if direction == 'request' else (destination, source)
        events, issues = [], []
        for link in links:
            for a, b in (('source', 'target'), ('target', 'source')):
                outgoing = [r for r in rows if r['node'] == link[a] and r['interface'] == link[a + '_interface'] and r['direction'] == 'egress']
                incoming = [r for r in rows if r['node'] == link[b] and r['interface'] == link[b + '_interface'] and r['direction'] == 'ingress']
                matches = [(x, y) for x in outgoing for y in incoming if x['wire_sha256'] == y['wire_sha256'] and 0 <= y['timestamp'] - x['timestamp'] < 2]
                if len(matches) != 1:
                    if outgoing or incoming:
                        issues.append(f'{link["id"]}: missing or ambiguous paired capture')
                    continue
                x, y = matches[0]
                events.append({'kind': 'transfer', 'evidence': 'observed', 'link_id': link['id'],
                               'source': x['node'], 'target': y['node'], 'source_interface': x['interface'],
                               'target_interface': y['interface'], 'timestamp': x['timestamp'],
                               'description': f'{x["name"]}:{x["interface"]} → {y["name"]}:{y["interface"]}',
                               'before': x, 'after': y})
        for node in {r['node'] for r in rows}:
            incoming = [r for r in rows if r['node'] == node and r['direction'] == 'ingress']
            outgoing = [r for r in rows if r['node'] == node and r['direction'] == 'egress']
            if len(incoming) != 1 or len(outgoing) != 1:
                continue
            x, y = incoming[0], outgoing[0]
            if y['timestamp'] < x['timestamp']:
                issues.append(f'{x["name"]}: capture order inconsistent'); continue
            before, after = packet_state(x), packet_state(y)
            if before['depth'] < after['depth']:
                description = 'Encapsulation observed: outer IPv6 header added'
            elif before['depth'] > after['depth']:
                description = 'Decapsulation observed: outer IPv6 header removed'
            elif before['srh'].get('segments_left') != after['srh'].get('segments_left') or before['outer']['destination'] != after['outer']['destination']:
                description = 'SRv6 header transition observed; specific SID behavior is inferred from installed state'
            else:
                description = 'Forwarding observed across ingress and egress'
            context = next((r for r in installed if r['id'] == node), {})
            matching = []
            for sid in context.get('local_sids', []):
                try:
                    network = ipaddress.IPv6Network(sid['sid'], strict=False)
                    if ipaddress.IPv6Address(before['outer']['destination']) in network:
                        matching.append((network.prefixlen, sid))
                except (KeyError, ValueError, TypeError):
                    continue
            matching.sort(key=lambda item: item[0], reverse=True)
            expected = matching[0][1] if matching else None
            if expected:
                description += f'; installed {expected.get("action", "unknown")} SID matches ingress destination (inferred execution)'
            events.append({'kind': 'processing', 'evidence': 'observed', 'node': node,
                           'timestamp': x['timestamp'], 'description': f'{x["name"]}: {description}',
                           'before': x, 'after': y, 'fields': compare_packets(before, after),
                           'installed_context': context, 'matched_sid': expected,
                           'behavior_certainty': 'inferred; installed state is context, not proof of kernel action'})
        transfers = [e for e in events if e['kind'] == 'transfer']
        current, visited, path = start, set(), [start]
        while current != finish and current not in visited:
            visited.add(current)
            candidates = [e for e in transfers if e['source'] == current]
            if len(candidates) != 1:
                break
            current = candidates[0]['target']; path.append(current)
        complete = current == finish and len(transfers) == len(path) - 1
        # Every transit node must also have a unique ingress/egress bracket.
        complete = complete and all(any(e['kind'] == 'processing' and e['node'] == n for e in events) for n in path[1:-1])
        if not complete:
            issues.append('End-to-end coverage incomplete; only independently evidenced links are replayed. No movement is drawn across gaps.')
        events.sort(key=lambda e: (e['timestamp'], e['kind'] == 'processing'))
        journeys.append({'packet_id': f'{direction}-{key[1]}-{key[2]}', 'direction': direction,
                         'sequence': key[2], 'complete': complete, 'observed_path': path,
                         'issues': issues, 'events': events})
    return journeys
