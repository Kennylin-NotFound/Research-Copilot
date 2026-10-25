"""Owned backend entry point used by the local process manager."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[name] = '1'
sys.path.insert(0, str(ROOT))
from app.local_config import get_settings

if __name__ == '__main__':
    import uvicorn
    settings = get_settings()
    from app.api.server import app
    uvicorn.run(app, host='127.0.0.1', port=settings.api_port, log_level='info')
