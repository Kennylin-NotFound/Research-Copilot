"""Run this repository's Vite with public settings and no model credentials."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.local_config import read_configuration, SECRET_KEYS

if __name__ == '__main__':
    settings, _ = read_configuration()
    environment = os.environ.copy()
    for key in SECRET_KEYS | {'LIGHT_RAG_CHAT_KEY', 'LIGHT_RAG_EMBED_KEY', 'LIGHT_RAG_API_KEY'}:
        environment.pop(key, None)
    environment.update(RC_INSTANCE_ID=settings.instance_id, RC_API_URL=settings.api_url,
                       VITE_API_BASE_URL=settings.api_url,
                       VITE_WS_BASE_URL=settings.api_url.replace('http://', 'ws://'))
    node = shutil.which('node')
    vite = ROOT / 'frontend/node_modules/vite/bin/vite.js'
    if not node or not vite.is_file():
        raise SystemExit('Install frontend dependencies before starting')
    result = subprocess.run([node, str(vite), '--host', '127.0.0.1', '--port', str(settings.frontend_port), '--strictPort'],
                            cwd=ROOT / 'frontend', env=environment)
    raise SystemExit(result.returncode)
