"""T24: deterministic HTTP-to-SQLite races; AC references identify coverage."""
import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from task_integration_fixture import IntegrationFixture
from material_tree import iter_materials


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fx = IntegrationFixture(self.temp.name)

    def accepted(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['autoReply'], 'task created')

    async def test_excel_hit_and_incomplete_bundle_over_create_query_api(self):
        from task_models import MaterialItem
        f = self.fx
        f.modes.update(A1='phrase_hit', B1='phrase_incomplete')
        async with f.running():
            f.release('A1')
            self.accepted(await f.post())
            body = await f.terminal()
            self.assertEqual(body['status'], 'DONE', body)
            self.assertEqual(body['data']['total'], 5)
            extras = body['data']['items'][0]['categories'][-1]['materials']
            self.assertEqual(len(extras), 3)
            self.assertTrue(all(i['productName'] == '甲' for i in extras))
            self.assertTrue(any('上市公司公告（如需）' in i['materialName'] for i in extras))
            self.assertTrue(all(set(i) == set(MaterialItem.model_fields) for i in extras))
            self.assertTrue(all('sourceRuleId' not in i and 'reason' not in i
                                for i in iter_materials(body['data']['items'])))
            self.assertEqual(body['data']['items'][0]['categories'][-1]['categoryName'], '最后一类')
            f.release('B1')
            self.accepted(await f.post('B'))
            failed = await f.terminal('B')
            self.assertEqual(failed['status'], 'FAILED', failed)
            self.assertNotIn('data', failed)
            self.assertIsNone(f.store.get_task('B', '1')['result'])
            self.assertEqual(await f.query(), body)

    async def test_fifo_replacements_and_completed_history(self):
        # AC-01/05/08: A1,B1,C1 + A2 => B1,C1,A2; ack precedes model completion.
        f = self.fx
        async with f.running():
            for b in 'ABC':
                self.accepted(await f.post(b))
            await f.wait_started('A1')
            self.assertEqual((await f.query())['status'], 'PROCESSING')
            self.accepted(await f.post('A', '2'))
            await f.wait_started('B1')
            self.assertEqual((await f.query())['status'], 'TERMINATED')
            for b, t in [('B', '1'), ('C', '1'), ('A', '2')]:
                await f.wait_started(b + t)
                f.release(b + t)
                self.assertEqual((await f.terminal(b, t))['status'], 'DONE')
            self.assertEqual(f.order, ['A1', 'B1', 'C1', 'A2'])
            history = await f.query('A', '2')
            self.accepted(await f.post('A', '3'))
            self.assertEqual(await f.query('A', '2'), history)
            f.release('A3')
            self.assertEqual((await f.terminal('A', '3'))['status'], 'DONE')
            self.assertEqual(f.peak, 1)

    async def test_pending_replacement_and_consecutive_same_business_submissions(self):
        # AC-05/06: commit sequence, not request launch order, determines the survivor.
        f = self.fx
        async with f.running():
            for b in 'ABC':
                self.accepted(await f.post(b))
            await f.wait_started('A1')
            self.accepted(await f.post('B', '2'))
            self.assertEqual((await f.query('B'))['status'], 'TERMINATED')
            self.assertEqual(f.order, ['A1'])
            f.release('A1')
            await f.wait_started('C1')
            f.release('C1')
            await f.wait_started('B2')
            f.release('B2')
            await f.terminal('B', '2')
            self.assertEqual(f.order, ['A1', 'C1', 'B2'])
        # Fresh store: two concurrently launched replacements; at most five tasks per store.
        f = IntegrationFixture(Path(self.temp.name) / 'concurrent')
        async with f.running():
            self.accepted(await f.post('A', '2'))
            await f.wait_started('A2')
            responses = await asyncio.gather(f.post('A', '3'), f.post('A', '4'))
            for r in responses:
                self.accepted(r)
            records = [f.store.get_task('A', t) for t in ('2', '3', '4')]
            records.sort(key=lambda r: r['sequence'])
            self.assertEqual([r['status'] for r in records[:-1]], ['TERMINATED', 'TERMINATED'])
            winner = records[-1]['taskId']
            f.release('A' + winner)
            self.assertEqual((await f.terminal('A', winner))['status'], 'DONE')
            self.assertEqual(f.peak, 1)

    async def test_invalid_input_and_file_or_transaction_failure_do_not_replace(self):
        # AC-03: reject before commit; no termination, sequence advance, or orphan upload.
        f = self.fx
        async with f.running():
            self.accepted(await f.post())
            await f.wait_started('A1')
            before = f.store.get_task('A', '1')
            files = set(f.settings.storage_root.rglob('source.pdf'))
            self.assertEqual((await f.post(task='2', raw=b'bad')).status_code, 415)
            with patch('task_files.os.fsync', side_effect=OSError('disk failure')):
                self.assertEqual((await f.post(task='2')).status_code, 503)
            with f.store._connection(write=True) as conn:
                conn.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON tasks BEGIN "
                             "SELECT RAISE(ABORT, 'injected'); END")
            try:
                self.assertEqual((await f.post(task='2')).status_code, 503)
            finally:
                with f.store._connection(write=True) as conn:
                    conn.execute('DROP TRIGGER fail_insert')
            self.assertEqual(f.store.get_task('A', '1'), before)
            self.assertIsNone(f.store.get_task('A', '2'))
            self.assertEqual(set(f.settings.storage_root.rglob('source.pdf')), files)
            self.assertFalse(f.cancelled)
            self.accepted(await f.post(task='2'))
            self.assertEqual(f.store.get_task('A', '2')['sequence'], before['sequence'] + 1)
            f.release('A2')
            self.assertEqual((await f.terminal('A', '2'))['status'], 'DONE')

    async def test_late_result_cannot_overwrite_replacement(self):
        # AC-07/08: transport deliberately ignores cancellation, succeeds/fails late.
        f = self.fx
        async with f.running():
            for mode in ('late_success', 'late_failure'):
                old_marker = mode
                f.modes[old_marker] = mode
                self.accepted(await f.post(marker=old_marker))
                await f.wait_started(old_marker)
                old = f.store.get_task('A', '1')
                new_marker = mode + '_new'
                new_raw = f.raw + b'\n%new-generation\n'
                self.accepted(await f.post(marker=new_marker, raw=new_raw))
                current = f.store.get_task('A', '1')
                self.assertNotEqual(old['generationId'], current['generationId'])
                await asyncio.wait_for(f.event(f.cancelled, old_marker).wait(), 5)
                self.assertEqual(current['status'], 'PENDING')
                f.event(f.late_gates, old_marker).set()
                await f.wait_started(new_marker)
                f.release(new_marker)
                self.assertEqual((await f.terminal())['status'], 'DONE')
                saved = f.store.get_task('A', '1')
                self.assertFalse(f.store.finish_failed(old['generationId'], 'late'))
                self.assertFalse(f.store.finish_done(old['generationId'], saved['result']))
                self.assertFalse(f.store.save_artifact(old['generationId'], 'late', {}))
                self.assertEqual(f.store.get_task('A', '1'), saved)
                self.assertEqual(saved['businessData']['cstNm'], new_marker)
                self.assertEqual(Path(old['pdfPath']).read_bytes(), f.raw)
                self.assertEqual(Path(saved['pdfPath']).read_bytes(), new_raw)
                self.assertNotIn('sourceDocumentHash', saved['result'])
                self.assertEqual(saved['sourceDocumentHash'], hashlib.sha256(new_raw).hexdigest())
                for ref in saved['artifacts'].values():
                    self.assertTrue(Path(ref['path']).is_relative_to(Path(saved['pdfPath']).parent))
                    self.assertEqual(json.loads(Path(ref['path']).read_bytes())['generationId'], saved['generationId'])
            self.assertEqual(f.peak, 1)

    async def test_same_key_same_and_changed_input_rerun(self):
        # AC-08: identical input also creates a fresh generation and ordered queue slot.
        f = self.fx
        async with f.running():
            previous = None
            for marker in ('same', 'same', 'changed'):
                f.release(marker)
                self.accepted(await f.post(marker=marker))
                self.assertEqual((await f.terminal())['status'], 'DONE')
                saved = f.store.get_task('A', '1')
                if previous:
                    self.assertNotEqual(saved['generationId'], previous['generationId'])
                    self.assertEqual(saved['sequence'], previous['sequence'] + 1)
                previous = saved
            self.assertEqual(f.order, ['same', 'same', 'changed'])

    async def test_more_than_sixty_materials_and_generation_artifacts(self):
        # AC-17: two products, 35 rows each; no truncation and complete archived inputs.
        with tempfile.TemporaryDirectory() as folder:
            f = IntegrationFixture(folder, count=35)
            async with f.running():
                f.release('A1')
                self.accepted(await f.post())
                body = await f.terminal()
                self.assertEqual(body['status'], 'DONE', body)
                self.assertEqual(body['data']['total'], 70)
                self.assertEqual([i['itemId'] for i in iter_materials(body['data']['items'])], [f'R{i:04d}' for i in range(1, 71)])
                saved = f.store.get_task('A', '1')
                self.assertEqual(saved['businessData']['cstNm'], 'A1')
                self.assertEqual(Path(saved['pdfPath']).read_bytes(), f.raw)
                self.assertEqual(set(saved['artifacts']), {'approval', 'checklists', 'rules', 'facts', 'decisions',
                    'phrase-library', 'phrase-decisions', 'merged-materials', 'directories', 'product-matches'})
                for ref in saved['artifacts'].values():
                    path = Path(ref['path'])
                    self.assertTrue(path.is_relative_to(Path(saved['pdfPath']).parent))
                    raw = path.read_bytes()
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), ref['sha256'])
                    self.assertEqual(json.loads(raw)['generationId'], saved['generationId'])
                    self.assertNotIn(f.secret.encode(), raw)
                manifest = json.loads(Path(saved['artifacts']['checklists']['path']).read_text('utf-8'))['data']
                for snap in manifest:
                    path = Path(snap['path'])
                    self.assertTrue(path.is_relative_to(Path(saved['pdfPath']).parent))
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), snap['content_hash'])
                self.assertNotIn(f.secret, json.dumps(saved))
                for path in f.settings.storage_root.rglob('*'):
                    if path.is_file():
                        self.assertNotIn(f.secret.encode(), path.read_bytes(), str(path))
                self.assertEqual(f.calls['A1', 'generate'], 5)  # four Word batches plus phrase library

    async def test_product_failure_and_http_retry_exhaustion_allow_next_task(self):
        # AC-18: first product cannot leak a partial DONE; HTTP retries are finite.
        f = self.fx
        f.modes.update(A1='bad_second_product', B1='http_failure')
        f.batch_size = 1
        async with f.running():
            for b in 'ABC':
                self.accepted(await f.post(b))
                f.release(b + '1')
            for b in 'AB':
                body = await f.terminal(b)
                self.assertEqual(body['status'], 'FAILED', body)
                self.assertTrue(body['reason'])
                self.assertNotIn('data', body)
                self.assertNotIn(f.secret, json.dumps(body))
                self.assertIsNone(f.store.get_task(b, '1')['result'])
            self.assertEqual(f.calls['A1', 'generate'], 4)  # first product + 3 malformed attempts
            self.assertEqual(f.calls['B1', 'generate'], 3)
            self.assertEqual((await f.terminal('C'))['status'], 'DONE')
            self.assertEqual(f.order, ['A1', 'B1', 'C1'])

    async def test_completion_and_replacement_both_commit_orders(self):
        # AC-05/07: pause after real projection, immediately before worker publication.
        f = self.fx
        async with f.running():
            real_run = f.app.state.task_worker.pipeline.run_task
            for business, replace_first in [('A', True), ('B', False)]:
                ready, publish = asyncio.Event(), asyncio.Event()
                async def paused(record):
                    data = await real_run(record)
                    if record['taskId'] == '1':
                        ready.set()
                        try:
                            await publish.wait()
                        except asyncio.CancelledError:
                            await publish.wait()  # force stale successful output to reach worker CAS
                    return data
                with patch.object(f.app.state.task_worker.pipeline, 'run_task', side_effect=paused):
                    try:
                        f.release(business + '1')
                        self.accepted(await f.post(business))
                        await asyncio.wait_for(ready.wait(), 5)
                        if not replace_first:
                            publish.set()
                            self.assertEqual((await f.terminal(business))['status'], 'DONE')
                        self.accepted(await f.post(business, '2'))
                        self.assertEqual((await f.query(business))['status'], 'TERMINATED' if replace_first else 'DONE')
                    finally:
                        publish.set()
                    f.release(business + '2')
                    self.assertEqual((await f.terminal(business, '2'))['status'], 'DONE')

    async def test_restart_fails_unfinished_preserves_all_terminal_states(self):
        # AC-09: real shutdown cancels HTTP; startup recovers before consuming.
        f = self.fx
        async with f.running():
            f.release('D1')
            self.accepted(await f.post('D'))
            done = await f.terminal('D')
            f.modes['E1'] = 'bad_second_product'
            f.release('E1')
            self.accepted(await f.post('E'))
            failed = await f.terminal('E')
            self.accepted(await f.post('A'))
            await f.wait_started('A1')
            self.accepted(await f.post('B'))
            self.accepted(await f.post('B', '2'))
            terminated = await f.query('B')
        self.assertEqual(f.active, 0)
        self.assertTrue(f.app.state.task_client._http.is_closed)
        old_order = list(f.order)
        async with f.running():
            for b, t in [('A', '1'), ('B', '2')]:
                body = await f.query(b, t)
                self.assertEqual(body['status'], 'FAILED')
                self.assertIn('服务中断', body['reason'])
            self.assertEqual(await f.query('D'), done)
            self.assertEqual(await f.query('E'), failed)
            self.assertEqual(await f.query('B'), terminated)
            self.assertEqual(f.order, old_order)
            self.assertIsNone(f.store.claim_next())
