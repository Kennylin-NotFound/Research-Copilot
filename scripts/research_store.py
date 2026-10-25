"""Local domain-store CLI. import-web reads a local snapshot; it never fetches a URL."""
import argparse
from datetime import date
import json
from pathlib import Path
import sys
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.domain.research import ResearchBrief, SourceSpec
from app.local_config import read_configuration
from app.storage.research_store import ResearchStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, help='Optional test data directory, confined to repository/data')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init')
    brief = commands.add_parser('create-brief')
    brief.add_argument('--project-id', type=UUID, default=None)
    brief.add_argument('--revision', type=int, default=1)
    brief.add_argument('--question', required=True)
    brief.add_argument('--template', choices=('technical', 'industry'), required=True)
    brief.add_argument('--as-of', type=date.fromisoformat, required=True)
    brief.add_argument('--dimensions', nargs='+', required=True)
    for kind in ('web', 'pdf', 'text'):
        ingest = commands.add_parser('import-' + kind)
        ingest.add_argument('--project-id', type=UUID, required=True)
        ingest.add_argument('--file', type=Path, required=True)
        ingest.add_argument('--title', required=True)
        if kind == 'web':
            ingest.add_argument('--url', required=True)
        else:
            ingest.add_argument('--file-id', type=UUID, default=None, help='Reuse this stable ID when importing a revised file')
    listing = commands.add_parser('list-sources')
    listing.add_argument('--project-id', type=UUID, required=True)
    verify = commands.add_parser('verify-source')
    verify.add_argument('--project-id', type=UUID, required=True)
    verify.add_argument('--source-version-id', type=UUID, required=True)
    evidence = commands.add_parser('verify-evidence')
    evidence.add_argument('--project-id', type=UUID, required=True)
    evidence.add_argument('--evidence-id', type=UUID, required=True)
    args = parser.parse_args()
    settings, _ = read_configuration()
    data_dir = (ROOT / args.data_dir).resolve() if args.data_dir else settings.data_dir
    if not data_dir.is_relative_to(ROOT / 'data'):
        raise ValueError('CLI data directory must remain inside repository/data')
    store = ResearchStore(data_dir)
    if args.command == 'init':
        result = store.schema()
    elif args.command == 'create-brief':
        result = store.save_brief(ResearchBrief(project_id=args.project_id or uuid4(), project_revision=args.revision,
                                              question=args.question, template=args.template, dimensions=tuple(args.dimensions), as_of=args.as_of)).model_dump(mode='json')
    elif args.command.startswith('import-'):
        kind = args.command.removeprefix('import-')
        origin = args.url if kind == 'web' else 'upload:' + str(args.file_id or uuid4())
        with args.file.open('rb') as source:
            raw = source.read(8 * 1024**2 + 1)
        result = store.save_source(SourceSpec(project_id=args.project_id, kind=kind, origin=origin, title=args.title), raw).model_dump(mode='json')
    elif args.command == 'list-sources':
        result = [version.model_dump(mode='json') for version in store.list_sources(args.project_id)]
    elif args.command == 'verify-source':
        version = store.get_source(args.project_id, args.source_version_id)
        raw = store.read_raw(args.project_id, args.source_version_id)
        text = store.read_extracted(args.project_id, args.source_version_id)
        result = {'source_version_id': str(version.source_version_id), 'raw_sha256': version.raw_sha256,
                  'text_sha256': version.text_sha256, 'raw_size': len(raw), 'unit_count': len(text.units), 'verified': True}
    else:
        evidence = store.get_evidence(args.project_id, args.evidence_id)
        result = {'verified': True, **evidence.model_dump(mode='json')}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
