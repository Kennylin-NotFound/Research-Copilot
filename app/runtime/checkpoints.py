"""Attempt-isolated async SQLite checkpoints; never share a writer after fencing."""
from contextlib import asynccontextmanager, closing
import sqlite3

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.runtime.jobs import assert_lease


@asynccontextmanager
async def task_checkpointer(store, claim):
    directory = store._safe_path('checkpoints', claim['run_id'])
    directory.mkdir(parents=True, exist_ok=True)
    target = store._safe_path('checkpoints', claim['run_id'], f"attempt-{claim['attempt']}.sqlite3")
    # A new attempt owns a new file. An old worker may still have a transport
    # request in flight; its checkpoint writes cannot overwrite this attempt.
    if claim['attempt'] > 1 and not target.exists():
        previous = store._safe_path('checkpoints', claim['run_id'], f"attempt-{claim['attempt']-1}.sqlite3")
        if previous.exists():
            with closing(sqlite3.connect(previous)) as source, closing(sqlite3.connect(target)) as destination:
                source.backup(destination)
    connection = await aiosqlite.connect(target)
    class FencedSaver(AsyncSqliteSaver):
        def check(self):
            with store.reader() as db:
                assert_lease(db, claim['run_id'], store.lease)

        async def aput(self, *args, **kwargs):
            self.check()
            return await super().aput(*args, **kwargs)

        async def aput_writes(self, *args, **kwargs):
            self.check()
            return await super().aput_writes(*args, **kwargs)
    try:
        # No pickle fallback / arbitrary module deserialization.
        saver = FencedSaver(connection, serde=JsonPlusSerializer(pickle_fallback=False, allowed_msgpack_modules=[], allowed_json_modules=[]))
        await saver.setup()
        yield saver
    finally:
        await connection.close()
