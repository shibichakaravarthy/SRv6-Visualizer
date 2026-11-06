#!/usr/bin/env python3
"""Phase 1: collect actual lab state and publish immutable JSON snapshots."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'backend'))
from services.discovery_service import DiscoveryService
from storage.json_store import JsonStore, NAMES


async def collect(topology: Path, output: Path, evidence_run: Path | None = None) -> int:
    service = DiscoveryService(JsonStore(output), topology, evidence_run=evidence_run)
    service.start()
    await service.task
    cid = service.status['collection_id']
    snapshots = service.store.read_many(collection_id=cid)
    valid = all(s.collection_id == cid for s in snapshots.values())
    summary = {
        'collection': service.status,
        'json_verified': valid,
        'directory': str(output / 'collections' / cid),
        'observed_links': len(snapshots['topology'].links),
        'headend_policies': sum(len(r['policies']) for r in snapshots['srv6_policies'].records),
        'local_sids': sum(len(r['local_sids']) for r in snapshots['srv6_policies'].records),
        'snapshots': {name: {'status': s.status, 'records': len(s.records)}
                      for name, s in snapshots.items()},
    }
    print(json.dumps(summary, indent=2))
    return 0 if valid and snapshots['topology'].records and service.status['state'] in ('available', 'partial') else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--topology', type=Path, default=ROOT / 'topology/SRv6_lab.yml',
                        help='Read-only configured topology reference')
    parser.add_argument('--output', type=Path, default=ROOT / 'backend/data/snapshots',
                        help='Snapshot storage directory; latest named files are refreshed; collection history is retained')
    parser.add_argument('--evidence-run', type=Path,
                        help='Completed experiment directory with manifest, PCAPs and decoded evidence')
    args = parser.parse_args()
    return asyncio.run(collect(args.topology.resolve(), args.output.resolve(), args.evidence_run))


if __name__ == '__main__':
    raise SystemExit(main())
