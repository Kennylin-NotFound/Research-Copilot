"""Explicit application configuration."""
from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
from uuid import UUID

from dotenv import dotenv_values

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_KEYS = {
    'RC_INSTANCE_ID', 'RC_API_PORT', 'RC_FRONTEND_PORT', 'RC_KNOWLEDGE_PORT',
    'RC_DATA_DIR', 'RC_LOG_DIR', 'OPENAI_BASE_URL', 'LLM_QWEN_MAX', 'RAGFLOW_API_URL',
    'RC_KNOWLEDGE_ENABLED', 'RC_KNOWLEDGE_NAME', 'LIGHT_RAG_EMBED_BASE', 'LIGHT_RAG_EMBED_MODEL',
}
SECRET_KEYS = {'OPENAI_API_KEY', 'TAVILY_API_KEY', 'RAGFLOW_API_KEY', 'LIGHT_RAG_EMBED_KEY'}
ALLOWED_KEYS = PUBLIC_KEYS | SECRET_KEYS
LEGACY_PORTS = {8000, 5173, 18080, 18081, 15173, 18082, 13307, 3306, 3307}


@dataclass(frozen=True)
class LocalSettings:
    repository_root: Path
    config_file: Path
    instance_id: str
    api_port: int
    frontend_port: int
    knowledge_port: int
    data_dir: Path
    log_dir: Path
    knowledge_enabled: bool = False

    @property
    def knowledge_data_dir(self):
        return self.data_dir / 'knowledge'

    @property
    def knowledge_log_dir(self):
        return self.log_dir / 'knowledge'

    @property
    def output_dir(self):
        return self.data_dir / 'artifacts'

    @property
    def upload_dir(self):
        return self.data_dir / 'uploads'

    @property
    def api_url(self):
        return f'http://127.0.0.1:{self.api_port}'

    def public(self):
        return {'instance_id': self.instance_id, 'api_port': self.api_port,
                'frontend_port': self.frontend_port, 'knowledge_port': self.knowledge_port,
                'api_url': self.api_url, 'knowledge_enabled': self.knowledge_enabled}


def read_configuration(repository_root=REPOSITORY_ROOT):
    root = Path(repository_root).resolve()
    config = Path(os.environ.get('RC_CONFIG_FILE', str(root / '.local/dev.env'))).resolve()
    if not config.is_relative_to(root / '.local') or not config.is_file():
        raise ValueError('Use an existing config inside this repository .local directory; run local_init.py first')
    # Disable ${...} interpolation: inherited credentials must not influence file values.
    values = {k: v for k, v in dotenv_values(config, interpolate=False).items() if k in ALLOWED_KEYS and v is not None}
    instance_id = str(UUID(values['RC_INSTANCE_ID']))
    ports = [int(values[k]) for k in ('RC_API_PORT', 'RC_FRONTEND_PORT', 'RC_KNOWLEDGE_PORT')]
    if len(set(ports)) != 3 or any(not 1024 <= p <= 65535 or p in LEGACY_PORTS for p in ports):
        raise ValueError('Ports must be distinct, unprivileged, and not reserved')
    directories = []
    for key, parent in (('RC_DATA_DIR', 'data'), ('RC_LOG_DIR', 'logs')):
        directory = (root / values.get(key, parent)).resolve()
        if not directory.is_relative_to(root / parent):
            raise ValueError(f'{key} must remain inside repository {parent}')
        directories.append(directory)
    expected_kb_url = f'http://127.0.0.1:{ports[2]}'
    if values.get('RAGFLOW_API_URL') != expected_kb_url:
        raise ValueError('Knowledge URL must point to the reserved local knowledge port')
    missing = [key for key in ('OPENAI_BASE_URL', 'OPENAI_API_KEY', 'LLM_QWEN_MAX', 'TAVILY_API_KEY', 'RAGFLOW_API_KEY') if not values.get(key)]
    if missing:
        raise ValueError('Missing local configuration keys: ' + ', '.join(missing))
    enabled = values.get('RC_KNOWLEDGE_ENABLED', '0')
    if enabled not in ('0', '1'):
        raise ValueError('RC_KNOWLEDGE_ENABLED must be 0 or 1')
    if enabled == '1':
        required = ('LIGHT_RAG_EMBED_BASE', 'LIGHT_RAG_EMBED_KEY', 'LIGHT_RAG_EMBED_MODEL')
        if any(not values.get(key, '').strip() for key in required):
            raise ValueError('Enabled knowledge service requires explicit Embedding configuration')
    return LocalSettings(root, config, instance_id, *ports, *directories, enabled == '1'), values


@lru_cache(maxsize=1)
def get_settings():
    settings, values = read_configuration()
    # Only this explicit file supplies SDK settings, even if the shell has older values.
    os.environ.update(values)
    for directory in (settings.output_dir, settings.upload_dir, settings.log_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return settings
