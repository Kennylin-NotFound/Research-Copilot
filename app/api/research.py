"""Domain research API. Project revisions are separate from execution identity."""
from uuid import UUID, uuid4
import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, UploadFile, File, Query, WebSocket, WebSocketDisconnect, Depends
from starlette.requests import HTTPConnection
from fastapi.responses import Response
from pydantic import BaseModel, Field, ConfigDict

from app.domain.project import ProjectInput, TEMPLATES
from app.domain.stages import ResearchMode, PROFILES, question_scope
from app.local_config import get_settings
from app.storage.research_store import ResearchStore
from app.domain.research import SourceSpec, RunBudget
from app.security.paths import MAX_FILE_BYTES

def require_active_project(connection: HTTPConnection):
    project_id = connection.path_params.get('project_id')
    if project_id and store().project_deleted(project_id):
        raise HTTPException(404, '项目已删除，请先在回收站恢复')


router = APIRouter(prefix='/api/research', tags=['research'], dependencies=[Depends(require_active_project)])
active_runs = {}


def store():
    return ResearchStore(get_settings().data_dir)


@router.get('/templates')
def templates():
    return TEMPLATES


@router.get('/stages')
def stages():
    return PROFILES


@router.get('/projects')
def projects():
    return store().list_projects()


def save_project(project_id, inputs):
    questions = inputs.clarifications()
    if questions:
        raise HTTPException(422, {'status': 'needs_clarification', 'questions': questions})
    try:
        return store().revise_project(project_id, inputs)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/projects')
def create_project(inputs: ProjectInput):
    return save_project(uuid4(), inputs)


@router.put('/projects/{project_id}')
def update_project(project_id: UUID, inputs: ProjectInput):
    try:
        store().get_brief(project_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return save_project(project_id, inputs)


@router.get('/projects/{project_id}')
def project(project_id: UUID, revision: int | None = None):
    try:
        return store().get_brief(project_id, revision)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/projects/{project_id}/sources')
def sources(project_id: UUID):
    return store().list_sources(project_id)


@router.get('/projects/{project_id}/sources/{source_id}')
def source(project_id: UUID, source_id: UUID):
    try:
        repository = store()
        return {'source': repository.get_source(project_id, source_id), 'extracted': repository.read_extracted(project_id, source_id)}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/projects/{project_id}/sources/{source_id}/raw')
def raw_source(project_id: UUID, source_id: UUID):
    try:
        return Response(store().read_raw(project_id, source_id), media_type='application/octet-stream', headers={'Content-Disposition': 'attachment; filename="source.bin"'})
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/projects/{project_id}/sources')
async def upload_source(project_id: UUID, file: UploadFile = File(...)):
    try:
        repository = store()
        repository.get_brief(project_id)
        # This product endpoint accepts only formats with deterministic locators.
        filename = (file.filename or 'source.txt').replace('\\', '/').split('/')[-1]
        suffix = filename.rsplit('.', 1)[-1].lower()
        if suffix not in ('pdf', 'txt', 'md'):
            raise HTTPException(415, 'Research sources accept PDF, TXT or Markdown')
        raw = await file.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise HTTPException(413, 'Source exceeds 8 MiB')
        import hashlib
        from uuid import NAMESPACE_URL, uuid5
        file_id = uuid5(NAMESPACE_URL, str(project_id) + ':' + hashlib.sha256(raw).hexdigest())
        return repository.save_source(SourceSpec(project_id=project_id, kind='pdf' if suffix == 'pdf' else 'text', origin='upload:' + str(file_id), title=filename[:1000]), raw)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        await file.close()


class RunInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(strict=True, ge=1)
    request_id: UUID = Field(default_factory=uuid4)
    source_version_ids: list[UUID] = Field(default_factory=list, max_length=12)
    route: Literal['auto', 'direct', 'dimensions'] = 'auto'
    mode: ResearchMode = 'quick'
    parent_run_id: UUID | None = None
    focus: str | None = Field(default=None, min_length=6, max_length=1000)
    memory_mode: Literal['off', 'reuse', 'incremental'] = 'off'
    baseline_run_id: UUID | None = None
    budget_override: RunBudget | None = None
    allow_network: bool | None = None
    question: str | None = Field(default=None, min_length=1, max_length=4000)
    recheck_parent: bool = False
    inherit_source_selection: bool = True


@router.post('/projects/{project_id}/runs')
async def start_run(project_id: UUID, inputs: RunInput):
    repository = store()
    try:
        from app.runtime.jobs import JobQueue, request_hash
        fingerprint = request_hash(inputs.model_dump(mode='json', exclude={'request_id': True, 'budget_override': {'budget_id'}}))
        existing = JobQueue(repository).existing(project_id, inputs.request_id, fingerprint)
        if existing: return existing
        return JobQueue(repository).submit(prepare_run(repository, project_id, inputs), inputs.request_id, fingerprint)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def prepare_run(repository, project_id, inputs):
    from app.agent.research_engine import ResearchEngineAdapter, new_run
    try:
        brief = repository.get_brief(project_id)
        if brief.project_revision != inputs.expected_revision:
            raise HTTPException(409, 'Project revision changed')
        if inputs.budget_override is not None:
            brief = brief.model_copy(update={'budget': inputs.budget_override})
        if inputs.allow_network is not None:
            brief = brief.model_copy(update={'source_policy': brief.source_policy.model_copy(update={'allow_network': inputs.allow_network})})
        from app.runtime.memory import ProjectMemory
        memories = ProjectMemory(repository)
        if inputs.memory_mode == 'incremental':
            if not inputs.baseline_run_id:
                raise HTTPException(422, '增量研究需要选择对照成果')
            baseline = repository.get_run(project_id, inputs.baseline_run_id)
            if not baseline.get('selected_finding_ids') or not baseline.get('report'):
                raise HTTPException(422, '增量基线必须已有带来源的成果')
        elif inputs.baseline_run_id:
            raise HTTPException(422, '只有增量研究可指定对照成果')
        parent = None
        if inputs.mode == 'focused':
            if not inputs.parent_run_id or not inputs.focus or not inputs.focus.strip():
                raise HTTPException(422, '请选择已有成果和具体深入方向')
            parent = repository.get_run(project_id, inputs.parent_run_id)
            if parent['project_revision'] != brief.project_revision and not inputs.recheck_parent:
                raise HTTPException(409, '项目范围已改变，请先基于当前版本形成初步成果')
            if parent['status'] not in ('completed', 'partial') or not parent.get('selected_finding_ids') or not parent.get('report'):
                raise HTTPException(422, '父任务尚无可复用的研究结论，请先完成快速解答或概览')
        elif inputs.parent_run_id or inputs.focus:
            raise HTTPException(422, '只有定向深入可指定父任务与方向')
        if inputs.mode in ('quick', 'overview') and inputs.route == 'dimensions':
            raise HTTPException(422, '首轮使用单Agent，维度委派仅在深入研究中开放')
        source_ids = inputs.source_version_ids or ([UUID(value) for value in parent['source_version_ids'][:12]] if parent and inputs.inherit_source_selection else [])
        remembered = memories.retrieve(project_id, inputs.question or inputs.focus or brief.question, brief.as_of) if inputs.memory_mode != 'off' else []
        # A source deselected by the user is not silently re-authorized by memory.
        remembered = [value for value in remembered if set(value['source_version_ids']) <= set(map(str, source_ids))]
        exclusions = [sid for value in memories.list(project_id, include_inactive=False) if value.get('outcome') == 'excluded' for sid in value['source_version_ids']] if inputs.memory_mode != 'off' else []
        if source_ids and not set(map(str, source_ids)) - set(exclusions) and not brief.source_policy.allow_network:
            raise HTTPException(422, '选中资料均已被项目记忆排除；请停用排除记录或选择其他资料')
        for sid in source_ids:
            repository.get_source(project_id, sid)
        if not source_ids and not brief.source_policy.allow_network:
            raise HTTPException(422, '请先选择或上传研究资料，或开启联网研究')
        run = new_run(brief, source_ids, inputs.route, mode=inputs.mode, parent_run_id=inputs.parent_run_id, focus=inputs.focus)
        run['network_allowed'] = brief.source_policy.allow_network
        if inputs.question and run.get('scope'):
            run['scope'] = question_scope(run['scope'], inputs.question.strip())
        if parent and parent['project_revision'] != brief.project_revision:
            run['inherit_parent_evidence'] = False
        run.update(memory_mode=inputs.memory_mode, memory_ids=[value['memory_id'] for value in remembered], excluded_source_version_ids=exclusions, baseline_run_id=str(inputs.baseline_run_id) if inputs.baseline_run_id else None)
        if parent:
            run['source_urls'] = {sid: url for sid, url in parent.get('source_urls', {}).items() if sid in run['source_version_ids']}
        return run
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def observed_run(repository, project_id, run_id):
    run = repository.get_run(project_id, run_id)
    if not run.get('job_id') and run['status'] in ('queued', 'running') and str(run_id) not in active_runs:
        run['status'] = 'interrupted'
        run['error'] = 'Server process no longer owns this Run; graph recovery is not yet implemented'
        repository.update_run(run)
    if 'budget' in run:
        from app.runtime.budget import BudgetLedger
        run['budget_state'] = BudgetLedger(repository, run).snapshot()
    from app.agent.research_report import restore_stopped_report
    restore_stopped_report(repository, run)
    if run.get('scope') and run.get('report') and not run.get('answer_preview'):
        from app.agent.research_report import render_preview
        from app.domain.findings import Finding
        run['answer_preview'] = render_preview(repository, repository.get_brief(project_id, run['project_revision']), run, [Finding.model_validate(item) for item in run['findings']])
    run['project_usage'] = repository.project_usage(project_id)
    return run


@router.get('/projects/{project_id}/runs')
def runs(project_id: UUID):
    repository = store()
    return [observed_run(repository, project_id, run['run_id']) for run in repository.list_runs(project_id)]


@router.get('/projects/{project_id}/runs/{run_id}')
def run_detail(project_id: UUID, run_id: UUID):
    try:
        return observed_run(store(), project_id, run_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/projects/{project_id}/runs/{run_id}/cancel')
async def cancel_run(project_id: UUID, run_id: UUID):
    from app.runtime.jobs import JobQueue
    repository = store()
    try:
        existing = repository.get_run(project_id, run_id)
        if existing.get('job_id'):
            result = JobQueue(repository).cancel(project_id, run_id)
            task = active_runs.get(str(run_id))
            if task and not task.done():
                task.cancel()
            return observed_run(repository, project_id, run_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    result = run_detail(project_id, run_id)
    task = active_runs.get(str(run_id))
    if task and not task.done():
        # Record even if cancelled before execute() enters its try/finally.
        from app.domain.research import utc_now
        result['status'] = 'cancelled'
        result['finished_at'] = utc_now().isoformat()
        result['report'] = None
        store().update_run(result)
        if 'budget' in result:
            from app.runtime.budget import BudgetLedger
            BudgetLedger(store(), result).stop('cancelled')
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return run_detail(project_id, run_id)
    return result


@router.get('/projects/{project_id}/runs/{run_id}/report')
def report(project_id: UUID, run_id: UUID):
    result = run_detail(project_id, run_id)
    if not result.get('report'):
        raise HTTPException(409, 'No published report for this Run')
    return Response(result['report'], media_type='text/markdown; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="research-{run_id}.md"'})


@router.get('/projects/{project_id}/runs/{run_id}/attempts')
def run_attempts(project_id: UUID, run_id: UUID):
    from app.runtime.jobs import JobQueue
    try:
        return JobQueue(store()).attempts(project_id, run_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/projects/{project_id}/runs/{run_id}/events')
def run_events(project_id: UUID, run_id: UUID, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)):
    try:
        return store().events_after(project_id, run_id, after, limit)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.websocket('/projects/{project_id}/runs/{run_id}/events/ws')
async def event_socket(websocket: WebSocket, project_id: UUID, run_id: UUID, last_seq: int = Query(0, ge=0)):
    settings = get_settings()
    if websocket.headers.get('origin') not in (None, settings.api_url, f'http://127.0.0.1:{settings.frontend_port}'):
        await websocket.close(code=1008)
        return
    repository = store()
    try:
        repository.get_run(project_id, run_id)
    except LookupError:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        while True:
            page = repository.events_after(project_id, run_id, last_seq)
            await websocket.send_json(page)
            last_seq = page['last_seq']
            if not page['has_more']:
                result = repository.get_run(project_id, run_id)
                if result['status'] not in ('queued', 'running'):
                    await websocket.close(code=1000)
                    break
                await asyncio.sleep(.5)
    except (WebSocketDisconnect, RuntimeError):
        pass


@router.get('/projects/{project_id}/memories')
def project_memories(project_id: UUID):
    from app.runtime.memory import ProjectMemory
    try:
        return ProjectMemory(store()).list(project_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete('/projects/{project_id}/memories/{memory_id}')
def delete_memory(project_id: UUID, memory_id: UUID):
    from app.runtime.memory import ProjectMemory
    try:
        return ProjectMemory(store()).delete(project_id, memory_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


class MemoryReplacement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    replacement_id: UUID


@router.post('/projects/{project_id}/memories/{memory_id}/supersede')
def replace_memory(project_id: UUID, memory_id: UUID, inputs: MemoryReplacement):
    from app.runtime.memory import ProjectMemory
    try:
        return ProjectMemory(store()).supersede(project_id, memory_id, inputs.replacement_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class SourceExclusion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_version_id: UUID
    reason: str = Field(min_length=6, max_length=1000)


@router.post('/projects/{project_id}/memory-exclusions')
def exclude_memory_source(project_id: UUID, inputs: SourceExclusion):
    from app.runtime.memory import ProjectMemory
    try:
        return ProjectMemory(store()).exclude_source(project_id, inputs.source_version_id, inputs.reason)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/projects/{project_id}/memories/{memory_id}/history')
def memory_history(project_id: UUID, memory_id: UUID):
    import json
    repository = store()
    with repository.reader() as db:
        if not db.execute('SELECT 1 FROM research_memories WHERE project_id=? AND memory_id=?', (str(project_id), str(memory_id))).fetchone():
            raise HTTPException(404, 'Project memory not found')
        return [json.loads(row[0]) for row in db.execute('SELECT payload FROM memory_revisions WHERE memory_id=? ORDER BY revision', (str(memory_id),))]
