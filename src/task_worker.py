"""One durable FIFO consumer. Notifications are hints, never a second queue."""
import asyncio
import logging
import sqlite3
from pathlib import Path
from time import perf_counter

from task_pipeline import TaskPipelineError
from service_log import event

logger = logging.getLogger(__name__)


class StorageOwnership:
    """Hold an exclusive transaction in a separate DB for this storage root."""
    def __init__(self, storage_root):
        self.root = Path(storage_root).resolve()
        self.connection = None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.root / 'owner.sqlite3', timeout=0,
                                     isolation_level=None)
        try:
            connection.execute('BEGIN EXCLUSIVE')
        except sqlite3.Error:
            connection.close()
            raise RuntimeError('任务存储已被其他服务实例占用或无法锁定') from None
        self.connection = connection
        return self

    def __exit__(self, *args):
        self.connection.close()
        self.connection = None


class TaskWorker:
    def __init__(self, store, pipeline):
        self.store, self.pipeline = store, pipeline
        self._wake = asyncio.Event()
        self._consumer = self._active = None
        self._generation = None
        self._stopping = False

    def start(self):
        if self._consumer is None:
            self._stopping = False
            self._consumer = asyncio.create_task(self._run(), name='task-worker')

    def notify(self, cancelled=()):
        """Call on the worker event loop, only after acceptance commits."""
        if (self._generation in cancelled and self._active is not None
                and not self._active.done() and not self._active.cancelling()):
            self._active.cancel()
        self._wake.set()

    async def stop(self):
        self._stopping = True
        self._wake.set()
        if self._active is not None and not self._active.done() and not self._active.cancelling():
            self._active.cancel()
        if self._consumer is not None:
            await self._consumer
            self._consumer = None

    async def _run(self):
        while not self._stopping:
            self._wake.clear()
            try:
                record = self.store.claim_next()
                if record is None:
                    await self._wake.wait()
                    continue
                self._generation = record['generationId']
                started = perf_counter()
                event('任务开始处理', record, status='PROCESSING')
                self._active = asyncio.create_task(self.pipeline.run_task(record))
                try:
                    result = await self._active
                    if not self._stopping:
                        if self.store.finish_done(self._generation, result):
                            event('任务处理完成', record, status='DONE',
                                  duration_ms=round((perf_counter() - started) * 1000, 2))
                except asyncio.CancelledError:
                    event('任务处理取消', record, reason='服务停止' if self._stopping else '被新任务替换',
                          duration_ms=round((perf_counter() - started) * 1000, 2))
                    pass  # Replacement is already TERMINATED; shutdown is recovered on restart.
                except Exception as error:
                    if not self._stopping:
                        reason = str(error) if isinstance(error, TaskPipelineError) else '任务处理失败'
                        if self.store.finish_failed(self._generation, reason):
                            event('任务处理失败', record, level=logging.ERROR, status='FAILED', reason=reason,
                                  error_type=type(error).__name__,
                                  duration_ms=round((perf_counter() - started) * 1000, 2))
                finally:
                    self._active = self._generation = None
            except (OSError, sqlite3.Error):
                logger.error('任务存储暂不可用，等待重试')
                try:
                    await asyncio.wait_for(self._wake.wait(), 1)
                except TimeoutError:
                    pass
