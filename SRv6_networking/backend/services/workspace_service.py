"""Presentation adapter over existing collectors; does not collect again."""
import ipaddress
import yaml
from collectors.info_collector_improved import classify_node_role


def addresses(node):
    result = []
    for interface in node.get('ipv6_addresses', {}).get('data') or []:
        if interface.get('ifname') in ('eth0', 'lo'):
            continue
        for row in interface.get('addr_info', []):
            try:
                address = ipaddress.IPv6Address(row.get('local', ''))
            except ValueError:
                continue
            if not (address.is_link_local or address.is_loopback or address.is_multicast or address.is_unspecified):
                result.append({'address': str(address), 'interface': interface['ifname'], 'prefixlen': row.get('prefixlen')})
    return result


def workspace(store, topology_path):
    snapshots = {name: value.model_dump() for name, value in store.read_many().items()}
    try:
        specs = yaml.safe_load(topology_path.read_text())['topology']['nodes']
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        specs = {}
    nodes = []
    for node in snapshots['topology']['records']:
        role = classify_node_role(specs[node['name']]) if node['name'] in specs else 'unknown'
        nodes.append({**node, 'role': role, 'addresses': addresses(node)})
    return {'snapshots': snapshots, 'nodes': nodes, 'links': snapshots['topology']['configured_links'],
            'collection_id': snapshots['topology']['collection_id'],
            'collected_at': snapshots['topology']['collected_at']}
