"""Keep iproute2 JSON fields (including multipath, metrics and table IDs)."""
async def collect_ipv6(reader, cid: str, lab: str) -> dict:
    return {
        'interfaces': await reader.json_observation(cid, lab, 'interfaces'),
        'routes': await reader.json_observation(cid, lab, 'routes'),
        'neighbors': await reader.json_observation(cid, lab, 'neighbors'),
        'rules': await reader.json_observation(cid, lab, 'rules'),
    }


if __name__ == "__main__":
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from collectors._cli import main
    raise SystemExit(main('ipv6_routes'))
