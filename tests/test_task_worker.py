import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
if importlib.util.find_spec('task_worker'):
    from task_worker import TaskWorker
else:
    TaskWorker = None
from task_store import TaskStore


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(TaskWorker, 'T21 TaskWorker missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore(self.temp.name)
        self.result = json.loads((ROOT / 'docs/sdd/examples/generate.response.json').read_text(
            encoding='utf-8'))['data']
        self.started, self.release = {}, {}
        self.order = []
        self.active = self.peak = 0
        self.late = None
        self.fail = None
        self.worker = TaskWorker(self.store, self)
        self.addAsyncCleanup(self.worker.stop)

    async def run_task(self, record):
        name = record['bsnEvtId'] + record['taskId']
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.order.append(name)
        self.started.setdefault(name, asyncio.Event()).set()
        try:
            try:
                await self.release.setdefault(name, asyncio.Event()).wait()
            except asyncio.CancelledError:
                if self.late != name:
                    raise
            if self.fail == name:
                raise RuntimeError('secret-provider-output')
            return self.result
        finally:
            self.active -= 1

    def accept(self, business, task):
        gen, cancelled = self.store.accept_task(dict(bsnEvtId=business, taskId=task, userNo='U'))
        self.worker.notify(cancelled)
        return gen

    async def wait_started(self, name):
        await asyncio.wait_for(self.started.setdefault(name, asyncio.Event()).wait(), 2)

    def finish(self, name):
        self.release.setdefault(name, asyncio.Event()).set()

    async def drain(self):
        # Observe completion through the persistent state, yielding without wall-clock sleeps.
        async def completed():
            while any(self.store.get_task(n[0], n[1:])['status'] == 'PROCESSING' for n in self.order):
                await asyncio.sleep(0)
        await asyncio.wait_for(completed(), 2)

    async def test_fifo_replacement_with_one_active_task(self):
        self.accept('A', '1')
        self.accept('B', '1')
        self.accept('C', '1')
        self.worker.start()
        await self.wait_started('A1')
        self.accept('A', '2')
        await self.wait_started('B1')
        self.finish('B1')
        await self.wait_started('C1')
        self.finish('C1')
        await self.wait_started('A2')
        self.finish('A2')
        await self.drain()
        self.assertEqual(self.order, ['A1', 'B1', 'C1', 'A2'])
        self.assertEqual(self.peak, 1)
        self.assertEqual(self.store.get_task('A', '1')['status'], 'TERMINATED')
        self.assertEqual(self.store.get_task('A', '2')['status'], 'DONE')

    async def test_pending_replacement_does_not_cancel_other_business(self):
        for b in 'ABC':
            self.accept(b, '1')
        self.worker.start()
        await self.wait_started('A1')
        self.accept('B', '2')
        await asyncio.sleep(0)
        self.assertEqual(self.order, ['A1'])
        self.finish('A1')
        await self.wait_started('C1')
        self.finish('C1')
        await self.wait_started('B2')
        self.finish('B2')
        await self.drain()
        self.assertEqual(self.order, ['A1', 'C1', 'B2'])
        self.assertEqual(self.store.get_task('B', '1')['status'], 'TERMINATED')

    async def test_consecutive_replacements_skip_obsolete_pending_generation(self):
        self.accept('A', '1')
        self.accept('B', '1')
        self.worker.start()
        await self.wait_started('A1')
        self.accept('A', '2')
        self.accept('A', '3')
        self.fail = 'A3'
        await self.wait_started('B1')
        self.finish('B1')
        await self.wait_started('A3')
        self.finish('A3')
        await self.drain()
        self.assertEqual(self.order, ['A1', 'B1', 'A3'])
        self.assertEqual(self.peak, 1)
        self.assertEqual(self.store.get_task('A', '2')['status'], 'TERMINATED')
        self.assertEqual(self.store.get_task('A', '3')['status'], 'FAILED')

    async def test_late_success_and_failure_cannot_revive_same_key(self):
        for fail in (None, 'A1'):
            self.order.clear()
            self.started.clear()
            self.release.clear()
            self.late, self.fail = 'A1', fail
            old = self.accept('A', '1')
            self.worker.start()
            await self.wait_started('A1')
            new = self.accept('A', '1')
            # The second A1 start uses the same Event; stop joins all execution.
            self.finish('A1')
            async def second_start():
                while len(self.order) < 2:
                    await asyncio.sleep(0)
            await asyncio.wait_for(second_start(), 2)
            await self.drain()
            await self.worker.stop()
            record = self.store.get_task('A', '1')
            self.assertNotEqual(old, new)
            self.assertEqual(record['generationId'], new)
            self.assertIn(record['status'], ('DONE', 'FAILED'))
            self.assertFalse(self.store.finish_failed(old, 'late'))
            self.assertFalse(self.store.finish_done(old, self.result))
            self.assertEqual(self.peak, 1)

    async def test_failure_continues_consumer_and_redacts_exception(self):
        self.fail = 'A1'
        self.accept('A', '1')
        self.accept('B', '1')
        self.worker.start()
        await self.wait_started('A1')
        self.finish('A1')
        await self.wait_started('B1')
        self.finish('B1')
        await self.drain()
        record = self.store.get_task('A', '1')
        self.assertEqual(record['status'], 'FAILED')
        self.assertNotIn('secret', record['reason'])
        self.assertEqual(self.store.get_task('B', '1')['status'], 'DONE')

    async def test_idle_wakeup_start_idempotence_and_shutdown(self):
        self.worker.start()
        self.worker.start()
        await asyncio.sleep(0)
        self.accept('A', '1')
        await self.wait_started('A1')
        self.accept('B', '1')
        await self.worker.stop()
        self.assertEqual(self.active, 0)
        self.assertEqual(self.order, ['A1'])
        self.assertEqual(self.store.get_task('A', '1')['status'], 'PROCESSING')
        self.assertEqual(self.store.get_task('B', '1')['status'], 'PENDING')
