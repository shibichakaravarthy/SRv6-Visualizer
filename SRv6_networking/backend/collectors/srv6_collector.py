"""Extract installed seg6/seg6local routes, retaining flavor fields; never read scripts."""
import ipaddress


def collect_srv6(routes: dict) -> dict:
    result = {'status': routes['status'], 'policies': [], 'local_sids': [], 'errors': []}
    if routes['status'] != 'available':
        result['errors'].append('Installed routes unavailable; inspect IPv6 route command evidence')
        return result
    for route in routes['data']:
        encap = route.get('encap') or {}
        fields = encap if isinstance(encap, dict) else route
        kind = (fields.get('encap_type', fields.get('type', route.get('encap_type')))
                if isinstance(encap, dict) else encap)
        if kind not in ('seg6', 'seg6local'):
            continue
        record = {'destination': route.get('dst'), 'interface': route.get('dev'),
                  'table': route.get('table'), 'raw_route': route}
        if kind == 'seg6':
            segments = fields.get('segs', fields.get('segments'))
            if isinstance(segments, str):
                segments = segments.split(',')
            try:
                if not isinstance(segments, list) or not segments:
                    raise ValueError('Missing segment list')
                record['segments'] = [str(ipaddress.IPv6Address(sid)) for sid in segments]
                record['mode'] = fields.get('mode')
                result['policies'].append(record)
            except (ValueError, TypeError) as exc:
                result['errors'].append(f'{route.get("dst")}: {exc}; raw route retained in IPv6 snapshot')
        else:
            record['flavors'] = fields.get('flavors', fields.get('flavor', fields.get('flavour')))
            record.update(action=fields.get('action'), next_hop=fields.get('nh6'),
                          lookup_table=fields.get('table', fields.get('vrftable')),
                          sid=route.get('dst'))
            if not record['action']:
                result['errors'].append(f'{route.get("dst")}: Missing LocalSID action')
            result['local_sids'].append(record)
    if result['errors']:
        result['status'] = 'partial'
    return result


if __name__ == "__main__":
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from collectors._cli import main
    raise SystemExit(main('srv6_policies'))
