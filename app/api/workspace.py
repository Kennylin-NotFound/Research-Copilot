"""Project/conversation UI API. All execution goes through the fenced research queue."""
from uuid import UUID, uuid4
from fastapi import APIRouter, HTTPException

from app.api import research
from app.domain.workspace import WorkspaceProjectInput, ConversationInput, ConversationEdit, MessageInput
from app.storage.workspace_store import WorkspaceStore
from app.runtime.jobs import JobQueue, request_hash

router = APIRouter(prefix='/api/workspace', tags=['workspace'])


def workspace():
    return WorkspaceStore(research.store())


@router.get('/projects')
def projects():
    return workspace().projects()


@router.get('/trash')
def trash_projects():
    return workspace().trash()


@router.delete('/projects/{project_id}')
def delete_project(project_id: UUID):
    try: return workspace().delete_project(project_id)
    except LookupError as exc: raise HTTPException(404, str(exc)) from exc
    except ValueError as exc: raise HTTPException(409, str(exc)) from exc


@router.post('/projects/{project_id}/restore')
def restore_project(project_id: UUID):
    try: return workspace().restore_project(project_id)
    except LookupError as exc: raise HTTPException(404, str(exc)) from exc


@router.post('/projects')
def create_project(inputs: WorkspaceProjectInput):
    questions = inputs.clarifications()
    if questions: raise HTTPException(422, '；'.join(questions))
    repository = research.store()
    brief = repository.revise_project(uuid4(), inputs)
    return WorkspaceStore(repository).ensure(brief.project_id, new=True, title=inputs.title.strip(), memory_enabled=inputs.memory_enabled)


@router.put('/projects/{project_id}/settings')
def settings(project_id: UUID, inputs: WorkspaceProjectInput):
    questions = inputs.clarifications()
    if questions: raise HTTPException(422, '；'.join(questions))
    try:
        return workspace().save_settings(project_id, inputs)
    except LookupError as exc: raise HTTPException(404, str(exc)) from exc
    except ValueError as exc: raise HTTPException(409, str(exc)) from exc


@router.get('/projects/{project_id}/conversations')
def conversations(project_id: UUID):
    try: return workspace().conversations(project_id)
    except LookupError as exc: raise HTTPException(404, str(exc)) from exc


@router.post('/projects/{project_id}/conversations')
def create_conversation(project_id: UUID, inputs: ConversationInput):
    if not inputs.title.strip(): raise HTTPException(422, '请输入对话名称')
    try: return workspace().create_conversation(project_id, inputs.title)
    except LookupError as exc: raise HTTPException(404, str(exc)) from exc


@router.patch('/projects/{project_id}/conversations/{conversation_id}')
def edit_conversation(project_id: UUID, conversation_id: UUID, inputs: ConversationEdit):
    if inputs.title is not None and not inputs.title.strip(): raise HTTPException(422, '请输入对话名称')
    try: return workspace().edit_conversation(project_id, conversation_id, title=inputs.title, archived=inputs.archived)
    except LookupError as exc: raise HTTPException(404, str(exc)) from exc
    except ValueError as exc: raise HTTPException(409, str(exc)) from exc


@router.get('/projects/{project_id}/conversations/{conversation_id}/messages')
def messages(project_id: UUID, conversation_id: UUID):
    try:
        ws = workspace()
        values = ws.messages(project_id, conversation_id)
        for value in values:
            if value['role'] == 'assistant' and value['run_id']:
                run = ws.store.get_run(project_id, value['run_id'])
                if value['kind'] == 'legacy': value['legacy_question'] = value['content']
                # A read-only projection updates pending/failed/cancelled bubbles
                # without a second persistence race with the publishing worker.
                value.update(status=run['status'], content=run.get('answer_preview') or '')
                value['run'] = {key: run.get(key) for key in ('run_id','status','scope','parent_run_id','answer_preview','follow_up_options','report','report_kind','stop_reason','report_unavailable_reason','error','source_version_ids','selected_finding_ids','incremental_delta')}
                value['run']['claim_types'] = list(dict.fromkeys(v['content']['claim_type'] for v in run.get('findings', []) if v['finding_id'] in run.get('selected_finding_ids', [])))
            else: value['status'] = 'sent'
        return values
    except LookupError as exc: raise HTTPException(404, str(exc)) from exc


@router.post('/projects/{project_id}/conversations/{conversation_id}/messages')
def send_message(project_id: UUID, conversation_id: UUID, inputs: MessageInput):
    repository = research.store()
    ws = WorkspaceStore(repository)
    queue = JobQueue(repository)
    # Stable across retries even when the server creates a fresh budget UUID.
    fingerprint = request_hash({'conversation_id': str(conversation_id), **inputs.model_dump(mode='json', exclude={'request_id': True, 'budget': {'budget_id'}})})
    try:
        conversation = ws.conversation(project_id, conversation_id)
        existing = queue.existing(project_id, inputs.request_id, fingerprint)
        if existing: return existing
        if conversation['archived']: raise ValueError('请先恢复已归档对话')
        content = inputs.content.strip()
        if not content: raise HTTPException(422, '请输入研究问题')
        values = ws.messages(project_id, conversation_id)
        parent_message = None
        if inputs.reply_to_message_id:
            parent_message = next((v for v in values if v['message_id'] == str(inputs.reply_to_message_id) and v['role'] == 'assistant'), None)
            if not parent_message: raise LookupError('引用的回答不属于当前对话')
        elif inputs.mode in ('auto','focused'):
            for value in reversed(values):
                if value['role'] == 'assistant' and value['kind'] != 'legacy':
                    prior = repository.get_run(project_id, value['run_id'])
                    if prior.get('report') and prior.get('selected_finding_ids'):
                        parent_message = value
                        break
        parent = repository.get_run(project_id, parent_message['run_id']) if parent_message else None
        mode = inputs.mode
        if mode == 'auto': mode = 'focused' if parent else ('overview' if repository.get_brief(project_id).template == 'industry' else 'quick')
        if mode == 'focused' and not parent: raise HTTPException(422, '请先选择一条已有回答，或使用快速解答')
        if inputs.incremental and not parent: raise HTTPException(422, '请先选择一条回答作为增量对照')
        if inputs.incremental and not inputs.memory_enabled: raise HTTPException(422, '开启项目记忆后才能进行增量对照')
        profile = ws.project(project_id)
        brief = repository.get_brief(project_id)
        run_inputs = research.RunInput(expected_revision=brief.project_revision, request_id=inputs.request_id,
            question=content, focus=('针对以下追问研究：' + content)[:1000] if mode == 'focused' else None,
            parent_run_id=UUID(parent['run_id']) if mode == 'focused' else None, mode=mode,
            memory_mode=('incremental' if inputs.incremental else 'reuse') if inputs.memory_enabled else 'off',
            baseline_run_id=UUID(parent['run_id']) if inputs.incremental else None,
            source_version_ids=inputs.source_version_ids, allow_network=inputs.allow_network,
            budget_override=inputs.budget or profile['preferences']['budget'], recheck_parent=True,
            inherit_source_selection=False)
        run = research.prepare_run(repository, project_id, run_inputs)
        run['conversation_id'] = str(conversation_id)
        run['reply_to_message_id'] = parent_message['message_id'] if parent_message else None
        run['user_question'] = content
        if parent:
            # Exactly one selected answer, not the entire conversation/project.
            run['conversation_context'] = {'previous_question': parent.get('scope', {}).get('question', '')[:1000],
                'previous_answer_outline': [v['content']['claim'][:600] for v in parent.get('findings', []) if v['finding_id'] in parent.get('selected_finding_ids', [])][:4],
                'trust': 'historical_unverified_context_only; cite current authorized source excerpts',
                'scope_changed': parent['project_revision'] != brief.project_revision}
        return queue.submit(run, inputs.request_id, fingerprint, message={'conversation_id': str(conversation_id), 'content': content})
    except LookupError as exc: raise HTTPException(404, str(exc)) from exc
    except ValueError as exc: raise HTTPException(409, str(exc)) from exc
