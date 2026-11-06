"""Collect configured source files separately from running configuration."""
import hashlib
from pathlib import Path


def collect_configured_files(topology: Path) -> dict:
    root = topology.parent.parent
    paths = {topology}
    errors = []
    for name in ('configs', 'deployment'):
        directory = root / name
        if not directory.is_dir():
            errors.append(f'Configuration directory unavailable: {directory}')
            continue
        paths.update(p for p in directory.rglob('*') if p.is_file())
    records = []
    for path in sorted(paths):
        try:
            raw = path.read_bytes()
            records.append({'path': str(path), 'resolved_path': str(path.resolve()),
                            'sha256': hashlib.sha256(raw).hexdigest(),
                            'content': raw.decode('utf-8'), 'status': 'available'})
        except (OSError, UnicodeError) as exc:
            errors.append(f'{path}: {exc}')
            records.append({'path': str(path), 'status': 'failed', 'error': str(exc)})
    return {'source': 'configured files; declarations are not installed-state evidence',
            'status': 'partial' if errors else 'available', 'files': records, 'errors': errors}


async def collect_configuration(reader, cid: str, lab: str) -> dict:
    return {name: await reader.observe(cid, lab, name) for name in (
        'running_config', 'frr_config', 'frr_daemons', 'forwarding', 'seg6_enabled')}


if __name__ == "__main__":
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from collectors._cli import main
    raise SystemExit(main('configurations'))
