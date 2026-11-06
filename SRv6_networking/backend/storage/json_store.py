"""Immutable collection history with atomically refreshed latest JSON files."""
import fcntl
import tempfile
import uuid
import json
import os
from pathlib import Path
import re
from models.schemas import Snapshot

NAMES = ('topology', 'ipv6_routes', 'srv6_policies', 'docker', 'isis', 'configurations', 'observations')
COLLECTION_ID = re.compile(r'^[0-9a-f]{32}$')


class JsonStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def history(self) -> list[dict]:
        records = []
        for path in (self.directory / 'collections').glob('*/manifest.json'):
            record = json.loads(path.read_text())
            if record.get('collection_id') != path.parent.name:
                raise ValueError('Invalid collection manifest identity')
            records.append(record)
        return sorted(records, key=lambda r: (r['collected_at'], r['collection_id']), reverse=True)

    def read_many(self, names=NAMES, collection_id=None) -> dict[str, Snapshot]:
        if any(name not in NAMES for name in names):
            raise ValueError('Unknown snapshot')
        try:
            if collection_id is not None and not COLLECTION_ID.fullmatch(collection_id):
                raise ValueError('Invalid collection ID')
            history = self.history()
            if collection_id is None:
                collection_id = history[0]['collection_id'] if history else None
            elif not any(row['collection_id'] == collection_id for row in history):
                raise FileNotFoundError('Collection not published')
            directory = self.directory / 'collections' / collection_id if collection_id else self.directory
            result = {}
            for name in names:
                path = directory / f'{name}.json'
                if not path.exists() and (collection_id is None or name == 'observations'):
                    # Older six-file generations predate packet-evidence support.
                    # Keep their live snapshots readable without inventing evidence.
                    result[name] = Snapshot(collection_id=collection_id,
                                            errors=['Snapshot was not collected'])
                else:
                    result[name] = Snapshot.model_validate_json(path.read_text())
                    if collection_id and result[name].collection_id != collection_id:
                        raise ValueError('Snapshot collection identity mismatch')
            return result
        except (OSError, ValueError) as exc:
            return {name: Snapshot(status='failed', errors=[f'Cannot read snapshot: {exc}']) for name in names}

    def read(self, name: str) -> Snapshot:
        return self.read_many((name,))[name]

    @staticmethod
    def _write(path: Path, value: dict):
        with path.open('x') as file:
            json.dump(value, file, indent=2, allow_nan=False)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())

    @staticmethod
    def _sync(directory: Path):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def write(self, name: str, snapshot: Snapshot):
        """Legacy/fixture import. Refuse to overwrite any pre-existing file."""
        if name not in NAMES:
            raise ValueError('Unknown snapshot')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._write(self.directory / f'{name}.json', snapshot.model_dump())

    def update_latest(self, name: str, snapshot: Snapshot):
        """Archive each result, then atomically refresh the named latest JSON file."""
        if name not in NAMES:
            raise ValueError('Unknown snapshot')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self.directory / f'{name}.json'
        history = self.directory / 'history' / name
        history.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.directory / f'.{name}.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            # Preserve even a pre-existing latest file from before history was enabled.
            if target.exists() and not any(history.glob('*.json')):
                with (history / f'previous-{uuid.uuid4().hex}.json').open('xb') as backup:
                    backup.write(target.read_bytes())
                    backup.flush()
                    os.fsync(backup.fileno())
            value = snapshot.model_dump()
            self._write(history / f'{uuid.uuid4().hex}.json', value)
            self._sync(history)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', dir=self.directory, delete=False) as file:
                    temporary = Path(file.name)
                    json.dump(value, file, indent=2, allow_nan=False)
                    file.write('\n')
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, target)
                self._sync(self.directory)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return target

    def publish(self, snapshots: dict[str, Snapshot]):
        if set(snapshots) != set(NAMES):
            raise ValueError('A collection must contain every snapshot')
        first = snapshots['topology']
        cid = first.collection_id
        if not cid or not COLLECTION_ID.fullmatch(cid) or not first.collected_at:
            raise ValueError('Collection identity and timestamp required')
        if any(s.collection_id != cid or s.collected_at != first.collected_at for s in snapshots.values()):
            raise ValueError('Mixed collection generations')
        parent = self.directory / 'collections'
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory = parent / cid
        directory.mkdir(mode=0o700)  # A collision is an error, never an overwrite.
        for name, snapshot in snapshots.items():
            self._write(directory / f'{name}.json', snapshot.model_dump())
        manifest = {'collection_id': cid, 'collected_at': first.collected_at,
                    'lab_ids': first.lab_ids, 'files': [f'{name}.json' for name in NAMES],
                    'statuses': {name: s.status for name, s in snapshots.items()}}
        self._write(directory / 'manifest.pending', manifest)
        self._sync(directory)
        # Readers only discover completed manifests. Snapshot files are immutable.
        os.rename(directory / 'manifest.pending', directory / 'manifest.json')
        self._sync(directory)
        self._sync(parent)
        for name, snapshot in snapshots.items():
            self.update_latest(name, snapshot)
        return directory
