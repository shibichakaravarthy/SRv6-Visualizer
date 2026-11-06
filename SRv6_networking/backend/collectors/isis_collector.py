"""FRR releases differ: preserve text when neighbor JSON is unsupported."""
async def collect_isis(reader, cid: str, lab: str) -> dict:
    return await reader.json_observation(cid, lab, 'isis', (dict, list))


async def collect_isis_routes(reader, cid: str, lab: str) -> dict:
    """Read IS-IS routes from FRR's IPv6 RIB, preserving any text fallback."""
    return await reader.json_observation(cid, lab, 'isis_routes', (dict, list))


if __name__ == "__main__":
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from collectors._cli import main
    raise SystemExit(main('isis'))
