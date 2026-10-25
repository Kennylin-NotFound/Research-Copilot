"""Ingest/query local files; chat attachments are a separate API."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.local_config import read_configuration
import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    upload = commands.add_parser('upload'); upload.add_argument('paths', nargs='+', type=Path)
    commands.add_parser('list')
    query = commands.add_parser('ask'); query.add_argument('question')
    remove = commands.add_parser('delete'); remove.add_argument('document_id')
    args = parser.parse_args()
    settings, values = read_configuration()
    if not settings.knowledge_enabled:
        raise SystemExit('Configure knowledge service first')
    base = values['RAGFLOW_API_URL']
    headers = {'Authorization': 'Bearer ' + values['RAGFLOW_API_KEY']}

    def show(response):
        response.raise_for_status()
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))

    if args.command == 'upload':
        for path in args.paths:
            with path.open('rb') as stream:
                show(requests.post(base + '/local/documents', headers=headers, files={'file': (path.name, stream)}, timeout=120))
    elif args.command == 'list':
        show(requests.get(base + '/local/documents', headers=headers, timeout=10))
    elif args.command == 'delete':
        show(requests.delete(base + '/local/documents/' + args.document_id, headers=headers, timeout=10))
    else:
        url = base + '/api/v1/chats/research-local'
        response = requests.post(url + '/sessions', headers=headers, json={'name': 'local-cli'}, timeout=10)
        response.raise_for_status(); identifier = response.json()['data']['id']
        try:
            show(requests.post(url + '/completions', headers=headers, json={'session_id': identifier, 'messages': [{'role': 'user', 'content': args.question}], 'stream': False}, timeout=100))
        finally:
            requests.delete(url + '/sessions', headers=headers, json={'ids': [identifier]}, timeout=10).raise_for_status()


if __name__ == '__main__':
    main()
