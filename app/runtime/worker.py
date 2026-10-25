"""One local queue consumer. API startup restarts this loop, not a research Run."""
import asyncio
import logging
from uuid import uuid4

from app.runtime.jobs import JobQueue, LeaseLost
from app.storage.research_store import ResearchStore

logger = logging.getLogger(__name__)


class ResearchWorker:
    def __init__(self, data_dir, active_runs, *, lease_seconds=30):
        self.data_dir, self.active_runs = data_dir, active_runs
        self.owner = str(uuid4())
        self.lease_seconds = lease_seconds

    async def renew(self, queue, claim, execution):
        while True:
            await asyncio.sleep(self.lease_seconds / 4)
            try:
                queue.heartbeat(claim, self.lease_seconds)
            except LeaseLost:
                execution.cancel()
                return

    async def process(self, claim):
        from app.agent.research_engine import ResearchEngineAdapter
        from app.runtime.checkpoints import task_checkpointer
        store = ResearchStore(self.data_dir)
        store.lease = (claim['job_id'], claim['lease_token'])
        queue = JobQueue(store)
        run = store.get_run(claim['project_id'], claim['run_id'])
        brief = store.get_brief(claim['project_id'], run['project_revision'])
        run['attempt'] = claim['attempt']
        run['attempt_id'] = claim['attempt_id']
        try:
            async with task_checkpointer(store, claim) as saver:
                engine = ResearchEngineAdapter(store, brief, run, checkpointer=saver)
                # Scope/source/elapsed budget validated before graph execution.
                engine.validate_resume()
                await engine.execute()
            queue.finish(claim, run['status'])
        except LeaseLost:
            logger.info('Research attempt fenced: %s', claim['attempt_id'])
        except asyncio.CancelledError:
            # User cancellation has already revoked the lease. On API shutdown
            # leave this Run recoverable; no refund and no synthetic completion.
            try:
                queue.release(claim)
            except LeaseLost:
                pass
            raise
        except Exception as exc:
            # Fail closed on a corrupt source/checkpoint or unsupported state.
            # Do not silently restart the entire research from its first prompt.
            try:
                run.update(status='failed', phase='finished', report=None,
                           error=type(exc).__name__ + ': ' + str(exc)[:300])
                store.update_run(run)
                queue.finish(claim, 'recovery_rejected')
            except LeaseLost:
                pass
            logger.exception('Research worker rejected attempt %s', claim['attempt_id'])

    async def run(self):
        queue = JobQueue(ResearchStore(self.data_dir))
        while True:
            claim = queue.claim(self.owner, self.lease_seconds)
            if not claim:
                await asyncio.sleep(.25)
                continue
            execution = asyncio.create_task(self.process(claim))
            self.active_runs[claim['run_id']] = execution
            heartbeat = asyncio.create_task(self.renew(queue, claim, execution))
            try:
                await execution
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
                    raise
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                self.active_runs.pop(claim['run_id'], None)
