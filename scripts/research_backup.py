"""Offline backup/isolated restore. Never overwrites live data or copies secrets."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.local_config import get_settings
from app.runtime.backup import snapshot, verify, restore


def data_path(value):
    path = value.resolve()
    if not path.is_relative_to(ROOT / 'data'):
        raise ValueError('Backup/restore path must remain in this repository data directory')
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['snapshot','verify','restore'])
    parser.add_argument('--source', type=Path)
    parser.add_argument('--target', type=Path)
    args = parser.parse_args()
    if (args.action == 'snapshot' and args.target is None) or (args.action != 'snapshot' and args.source is None) or (args.action == 'restore' and args.target is None):
        parser.error('snapshot requires --target; verify requires --source; restore requires both')
    if args.action == 'snapshot':
        settings = get_settings()
        for port in (settings.api_port, settings.knowledge_port):
            with socket.socket() as probe:
                probe.settimeout(.5)
                if probe.connect_ex(('127.0.0.1',port)) == 0:
                    raise ValueError('Stop this repository API and knowledge services before snapshotting')
        revision = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        result = snapshot(settings.data_dir, data_path(args.target), revision)
        print(json.dumps({'revision': revision, 'files': len(result['files'])}))
    elif args.action == 'verify':
        result = verify(data_path(args.source)); print(json.dumps({'revision':result['revision'],'files':len(result['files'])}))
    else:
        print(json.dumps(restore(data_path(args.source),data_path(args.target))))


if __name__ == '__main__': main()
