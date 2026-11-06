"""Compare the topology drawing with interface evidence from running nodes."""

import re
from pathlib import Path
import yaml


def configured_topology(path: Path) -> dict:
    try:
        doc = yaml.safe_load(path.read_text())
        if not isinstance(doc, dict) or not isinstance(doc.get('topology'), dict):
            raise ValueError('Expected a topology mapping')
        lab = doc['name']
        links = []
        for i, link in enumerate(doc['topology'].get('links', [])):
            if not isinstance(link, dict):
                raise ValueError('Each topology link must be a mapping')
            endpoints = link.get('endpoints', [])
            if len(endpoints) != 2:
                continue
            parsed = [ep.rsplit(':', 1) if isinstance(ep, str)
                      else [ep['node'], ep['interface']] for ep in endpoints]
            (a, ai), (b, bi) = parsed
            links.append({'id': f'configured-{i}', 'lab_id': lab, 'source_name': a,
                          'target_name': b, 'source_interface': ai, 'target_interface': bi,
                          'status': 'configured_only', 'source': str(path)})
        return {'lab_id': lab, 'links': links, 'error': None}
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        return {'lab_id': None, 'links': [], 'error': f'Topology reference unavailable: {exc}'}


def link_interfaces(observation: dict) -> list[dict]:
    if observation['status'] == 'available':
        return observation['data']
    text = observation.get('fallback', {})
    if text.get('status') != 'available':
        return []
    # BusyBox's text output provides real interface/peer indexes but no JSON.
    return [{'ifindex': int(index), 'ifname': name,
             'link_index': int(peer) if peer else None, 'flags': flags.split(',')}
            for index, name, peer, flags in re.findall(
                r'^(\d+): ([^:@\s]+)(?:@if(\d+)|@[^:]+)?: <([^>]+)>', text['raw'], re.M)]


def resolve_links(nodes: list[dict], configured: list[dict]) -> tuple[list, list]:
    by_name = {(n['lab_id'], n['name']): n for n in nodes}
    observed, references = [], []
    for reference in configured:
        link = dict(reference)
        a = by_name.get((link['lab_id'], link['source_name']))
        b = by_name.get((link['lab_id'], link['target_name']))
        ai = next((i for i in (a or {}).get('interfaces', [])
                   if i.get('ifname') == link['source_interface']), None)
        bi = next((i for i in (b or {}).get('interfaces', [])
                   if i.get('ifname') == link['target_interface']), None)
        link['reason'] = 'Both running endpoints and reciprocal peer indexes are required'
        if a and b and a['running'] and b['running'] and ai and bi:
            if (ai.get('link_index') == bi.get('ifindex') and
                    bi.get('link_index') == ai.get('ifindex') and ai.get('link_index') is not None):
                link.update(source=a['id'], target=b['id'], status='observed',
                            evidence='configured endpoints + reciprocal live interface indexes',
                            operational='up' if all(
                                'UP' in interface.get('flags', []) and
                                'LOWER_UP' in interface.get('flags', [])
                                for interface in (ai, bi)) else 'down')
                link.pop('reason')
                observed.append(link)
        references.append(link)
    return observed, references


if __name__ == "__main__":
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from collectors._cli import main
    raise SystemExit(main('topology'))
