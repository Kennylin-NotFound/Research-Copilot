"""Research Copilot HTTP API and background research worker."""
import asyncio
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.monitor import manager
from app.api.research import active_runs, router as research_router
from app.api.workspace import router as workspace_router
from app.local_config import get_settings
from app.runtime.worker import ResearchWorker
from app.security.http_body import RequestBodyLimit

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    manager.set_loop(asyncio.get_running_loop())
    worker = asyncio.create_task(ResearchWorker(settings.data_dir, active_runs).run())
    try:
        yield
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


app = FastAPI(title='Research Copilot API', lifespan=lifespan)
app.include_router(research_router)
app.include_router(workspace_router)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost'])
app.add_middleware(RequestBodyLimit, max_bytes=33 * 1024**2)
app.add_middleware(CORSMiddleware, allow_origins=[f'http://127.0.0.1:{settings.frontend_port}'],
                   allow_credentials=True, allow_methods=['*'], allow_headers=['*'])


@app.middleware('http')
async def request_boundary(request, call_next):
    origin = request.headers.get('origin')
    if origin and origin not in {settings.api_url, f'http://127.0.0.1:{settings.frontend_port}'}:
        return JSONResponse(status_code=403, content={'detail': 'Untrusted browser origin'})
    try:
        if int(request.headers.get('content-length', '0')) > 33 * 1024**2:
            return JSONResponse(status_code=413, content={'detail': 'Request too large'})
    except ValueError:
        return JSONResponse(status_code=400, content={'detail': 'Invalid Content-Length'})
    return await call_next(request)


@app.get('/health')
@app.get('/api/health')
def health():
    knowledge = 'not_configured'
    if settings.knowledge_enabled:
        knowledge = 'unavailable'
        try:
            response = requests.get(f'http://127.0.0.1:{settings.knowledge_port}/health', timeout=1)
            response.raise_for_status()
            identity = response.json()
            if identity.get('service') == 'knowledge' and identity.get('instance_id') == settings.instance_id:
                knowledge = 'ready'
        except (requests.RequestException, ValueError):
            pass
    return {'status': 'ok', 'product': 'research-copilot', **settings.public(), 'knowledge_base': knowledge}


@app.post('/api/task')
async def task_redirect():
    raise HTTPException(410, 'Use /api/workspace/projects to create research tasks')
