"""Offline, verified snapshots. Secrets and operational configuration are excluded."""
from contextlib import closing
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sqlite3

from app.domain.research import utc_now

DIRECTORIES = ('product', 'sources', 'checkpoints', 'knowledge')


def checksum(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): value.update(block)
    return value.hexdigest()


def sqlite_copy(source, target):
    with closing(sqlite3.connect(source)) as old, closing(sqlite3.connect(target)) as new:
        old.backup(new)
        if new.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite snapshot failed integrity check')
        if new.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('SQLite snapshot has invalid foreign keys')


def safe_child(root, relative):
    parts = PurePosixPath(relative)
    if parts.is_absolute() or '..' in parts.parts or '\\' in relative or ':' in relative:
        raise ValueError('Snapshot contains unsafe relative path')
    target = Path(root).joinpath(*parts.parts)
    if not target.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('Snapshot path escapes destination')
    current = Path(root)
    for part in parts.parts:
        current = current / part
        if current.is_symlink() or current.is_junction():
            raise ValueError('Snapshot cannot traverse a filesystem link')
    return target


def snapshot(data_dir, output, revision):
    data_dir, output = Path(data_dir).resolve(), Path(output).resolve()
    if output.exists(): raise ValueError('Backup destination must be new')
    from app.storage.research_store import ResearchStore
    store = ResearchStore(data_dir)
    # The CLI also verifies API/KB are stopped. Caller must quiesce any other
    # local writers; refuse an unfinished research task instead of losing it.
    with store.reader() as db:
        count = db.execute("SELECT COUNT(*) FROM research_runs WHERE json_extract(payload,'$.status') IN ('queued','running')").fetchone()[0]
        if count: raise ValueError('Finish or cancel active research before an offline snapshot')
    output.mkdir(parents=True)
    manifest = {'format': 1, 'created_at': utc_now().isoformat(), 'revision': revision, 'scope': list(DIRECTORIES), 'files': []}
    for directory in DIRECTORIES:
        origin = data_dir / directory
        if origin.is_symlink() or origin.is_junction(): raise ValueError('Backup source cannot be linked')
        if not origin.exists(): continue
        for path in sorted(origin.rglob('*')):
            if path.is_symlink() or path.is_junction(): raise ValueError('Backup source cannot be linked')
            if not path.is_file() or path.name.endswith(('-wal','-shm','-journal')): continue
            relative = path.relative_to(data_dir).as_posix()
            destination = safe_child(output, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with path.open('rb') as stream: sqlite = stream.read(16) == b'SQLite format 3\x00'
            if sqlite: sqlite_copy(path, destination)
            else: shutil.copyfile(path, destination)
            manifest['files'].append({'path': relative, 'sha256': checksum(destination), 'size': destination.stat().st_size, 'sqlite': sqlite})
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    verify(output)
    return manifest


def verify(source):
    source = Path(source)
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') != 1 or manifest.get('scope') != list(DIRECTORIES):
        raise ValueError('Unsupported snapshot format')
    seen = set()
    for item in manifest['files']:
        relative = item['path']
        if relative in seen or not PurePosixPath(relative).parts or PurePosixPath(relative).parts[0] not in DIRECTORIES:
            raise ValueError('Duplicate or out-of-scope snapshot path')
        seen.add(relative)
        path = safe_child(source, relative)
        if not path.is_file() or path.stat().st_size != item['size'] or checksum(path) != item['sha256']:
            raise ValueError('Snapshot file hash/size mismatch')
    if 'product/research.sqlite3' not in seen: raise ValueError('Snapshot is missing business database')
    return manifest


def restore(source, target):
    manifest = verify(source)  # Verify all inputs before creating a destination.
    target = Path(target).resolve()
    if target.exists(): raise ValueError('Restore destination must be new; in-place overwrite is forbidden')
    target.mkdir(parents=True)
    for item in manifest['files']:
        destination = safe_child(target, item['path'])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(safe_child(source, item['path']), destination)
        if checksum(destination) != item['sha256']: raise ValueError('Restored file hash mismatch')
    from app.storage.research_store import ResearchStore
    store = ResearchStore(target)
    with store.reader() as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Restored product database failed integrity checks')
        versions = db.execute('SELECT project_id,source_version_id,payload FROM source_versions').fetchall()
    from app.domain.research import SourceVersion
    for row in versions:
        version = SourceVersion.model_validate_json(row['payload'])
        if str(version.spec.project_id) != row['project_id'] or str(version.source_version_id) != row['source_version_id']:
            raise ValueError('Restored source identity differs from database row')
        # Offline integrity check includes trash without restoring its access.
        # Never remove tombstones or relax ordinary API/storage read guards.
        store._read_raw_version(version)
        store._read_extracted_version(version)
    return {'revision': manifest['revision'], 'files': len(manifest['files']), 'source_versions': len(versions)}
