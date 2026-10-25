"""Import only Embedding settings into the existing repository-private config."""
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
from app.local_config import read_configuration

EMBED_KEYS = ('LIGHT_RAG_EMBED_BASE', 'LIGHT_RAG_EMBED_KEY', 'LIGHT_RAG_EMBED_MODEL')


def initialize_knowledge(root=ROOT, source=None):
    settings, values = read_configuration(root)
    if settings.knowledge_enabled:
        return {'enabled': True, 'changed': False, 'port': settings.knowledge_port}
    incoming = dotenv_values(Path(source), interpolate=False) if source else values
    if any(not (incoming.get(key) or '').strip() for key in EMBED_KEYS):
        raise ValueError('Provide all three LIGHT_RAG_EMBED_* fields in an explicit local file')
    # Preserve application identity and authentication settings.
    values.update({key: incoming[key] for key in EMBED_KEYS})
    values.update(RC_KNOWLEDGE_ENABLED='1', RC_KNOWLEDGE_NAME='领域研究资料助手')
    temporary = settings.config_file.with_name('knowledge-' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(''.join(key + '=' + json.dumps(value, ensure_ascii=False) + '\n'
                                     for key, value in values.items()), encoding='utf-8', newline='\n')
        temporary.replace(settings.config_file)
    finally:
        temporary.unlink(missing_ok=True)
    return {'enabled': True, 'changed': True, 'port': settings.knowledge_port}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--import-env', type=Path)
    args = parser.parse_args()
    print(json.dumps(initialize_knowledge(source=args.import_env)))
