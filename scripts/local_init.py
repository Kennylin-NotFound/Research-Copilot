"""Create private config, importing only explicitly allowed model/search settings."""
import argparse
import json
from pathlib import Path
import secrets
import sys
from uuid import uuid4

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.local_config import read_configuration

IMPORT_KEYS = ('OPENAI_BASE_URL', 'OPENAI_API_KEY', 'LLM_QWEN_MAX', 'TAVILY_API_KEY')


def initialize(root: Path, source: Path | None = None):
    root = root.resolve()
    private = root / '.local'
    private.mkdir(exist_ok=True)
    config = private / 'dev.env'
    if config.exists():
        settings, _ = read_configuration(root)
        return {'created': False, **settings.public()}
    if source is None or not source.is_file():
        raise ValueError('Provide --import-env with a configured local model/search file; values are never printed')
    imported = dotenv_values(source, interpolate=False)
    values = {key: imported.get(key) or '' for key in IMPORT_KEYS}
    if any(not value for value in values.values()):
        raise ValueError('Import file is missing required model/search keys')
    values.update(RC_INSTANCE_ID=str(uuid4()), RC_API_PORT='18181', RC_FRONTEND_PORT='15174',
                  RC_KNOWLEDGE_PORT='18182', RC_DATA_DIR='data', RC_LOG_DIR='logs',
                  RAGFLOW_API_URL='http://127.0.0.1:18182', RAGFLOW_API_KEY=secrets.token_urlsafe(32))
    # JSON quoted strings are valid dotenv values; never interpolate shell text.
    content = '# Private local configuration. Do not commit.\n' + '\n'.join(f'{key}={json.dumps(value, ensure_ascii=False)}' for key, value in values.items()) + '\n'
    with config.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(content)
    settings, _ = read_configuration(root)
    return {'created': True, **settings.public()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--import-env', type=Path)
    parser.add_argument('--public', action='store_true')
    args = parser.parse_args()
    try:
        if args.public:
            settings, _ = read_configuration()
            result = {**settings.public(), 'log_dir': str(settings.log_dir)}
        else:
            result = initialize(ROOT, args.import_env)
    except (ValueError, KeyError) as exc:
        # Validation messages name keys/constraints only, never credential values.
        raise SystemExit(str(exc))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
