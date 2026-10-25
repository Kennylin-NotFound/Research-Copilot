"""Run the local knowledge service using its isolated frozen environment."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.local_config import get_settings
import uvicorn

if __name__ == '__main__':
    settings = get_settings()
    if not settings.knowledge_enabled:
        raise RuntimeError('Run knowledge_init.py with explicit Embedding configuration first')
    uvicorn.run('services.knowledge.app:app', host='127.0.0.1', port=settings.knowledge_port)
