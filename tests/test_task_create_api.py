import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import main
from task_files import submit_task_from_url
from task_store import TaskStore
from download_fixture import pdf_download
from test_service import make_docx

PDF = b'%PDF-1.7\nfixture\n%%EOF\n'


class CreateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.documents = self.root / 'list'
        make_docx(self.documents / '测试材料清单.docx', '正文')
        self.store = TaskStore(self.root / 'storage')
        self.ids = dict(bsnEvtId='A', taskId='T', userNo='U')
        self.data = dict(self.ids, replyImageUrl='http://10.0.0.8/approval.pdf')
        self.app = main.create_app(self.documents, task_store=self.store)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)
        self.content = PDF
        download = pdf_download(lambda: self.content)
        download.start()
        self.addCleanup(download.stop)

    async def post(self, data=None, content=PDF):
        self.content = content
        return await self.client.post('/api/v1/tasks', json=self.data if data is None else data)

    def assert_reply(self, response, status):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(set(response.json()), {'reply'})
        self.assertTrue(response.json()['reply'])

    async def test_json_url_saved_and_ack_does_not_wait_for_model(self):
        release = asyncio.Event()
        workers = []
        async def submission(store, data, *, notify_cancel):
            result = await submit_task_from_url(store, data, notify_cancel=notify_cancel)
            workers.append(asyncio.create_task(release.wait()))
            return result
        self.app.state.task_submitter = submission
        try:
            response = await asyncio.wait_for(self.post(data={**self.data, 'userNo': ' U '}), 2)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {**self.ids, 'autoReply': 'task created'})
            self.assertFalse(workers[0].done())
            query = await self.client.post('/api/v1/tasks/query', json=self.ids)
            self.assertEqual(query.json(), {**self.ids, 'status': 'PENDING'})
            self.assertEqual(Path(self.store.get_task('A', 'T')['pdfPath']).read_bytes(), PDF)
        finally:
            release.set()
            await asyncio.gather(*workers)

    async def test_invalid_business_or_url_preserves_old_task(self):
        self.store.accept_task(self.ids)
        self.store.claim_next()
        before = self.store.get_task('A', 'T')
        cases = [{}, [], 1, {**self.data, 'ctrTamt': 1}, self.ids,
                 {'businessData': self.data}] + [
            {**self.data, key: value} for key in self.data for value in (None, '', ' ', 1, [])]
        for data in cases:
            with self.subTest(data=data):
                self.assert_reply(await self.post(data), 422)
        self.assertEqual(self.store.get_task('A', 'T'), before)
        self.assertFalse(list(self.store.storage_root.glob('generations/*/source.pdf')))

    async def test_malformed_json_and_old_multipart_are_rejected(self):
        for raw in ('{', 'bad json', '{"context": NaN}', '{"context": Infinity}',
                    json.dumps(self.data)[:-1] + ', "extra":NaN}'):
            self.assert_reply(await self.client.post('/api/v1/tasks', content=raw,
                              headers={'Content-Type': 'application/json'}), 400)
        self.assert_reply(await self.client.post('/api/v1/tasks', files={
            'businessData': (None, json.dumps(self.data)), 'file': ('a.pdf', PDF)}), 400)
        self.assertIsNone(self.store.get_task('A', 'T'))

    async def test_pdf_and_storage_failures_keep_previous_result(self):
        self.store.accept_task(self.ids)
        before = self.store.get_task('A', 'T')
        for content, status in [(b'', 422), (b'not pdf', 415), (PDF + b'x' * (10 * 1024 * 1024), 413)]:
            self.assert_reply(await self.post(content=content), status)
        for target, error in [('task_files.os.fsync', OSError('private-path')),
                              ('task_store.TaskStore.accept_task', sqlite3.OperationalError('private-db')),
                              ('task_store.TaskStore.accept_task', RuntimeError('private-internal'))]:
            with patch(target, side_effect=error):
                response = await self.post()
            self.assert_reply(response, 500 if isinstance(error, RuntimeError) else 503)
            self.assertNotIn('private', response.text)
        self.assertEqual(self.store.get_task('A', 'T'), before)
        self.assertFalse(list(self.store.storage_root.glob('generations/*/source.pdf')))

    async def test_exact_size_limit_accepted(self):
        self.assertEqual((await self.post(content=PDF + b' ' * (10 * 1024 * 1024 - len(PDF)))).status_code, 200)

    async def test_openapi_describes_direct_json_url(self):
        schema = self.app.openapi()
        operation = schema['paths']['/api/v1/tasks']['post']
        content = operation['requestBody']['content']
        self.assertEqual(set(content), {'application/json'})
        body = schema['components']['schemas'][content['application/json']['schema']['$ref'].split('/')[-1]]
        self.assertEqual(set(body['required']), {*self.ids, 'replyImageUrl'})
        self.assertNotIn('file', body['properties'])
        for status in (400, 422, 413, 415, 502, 503, 504, 500):
            self.assertIn(str(status), operation['responses'])

    async def test_cancellation_notification_after_successful_download(self):
        old = self.store.accept_task(self.ids)[0]
        observed = []
        async def notify(generations):
            observed.extend(generations)
        self.app.state.task_notify_cancel = notify
        self.assertEqual((await self.post()).status_code, 200)
        self.assertEqual(observed, [old])

    async def test_missing_store_and_legacy_errors_unchanged(self):
        self.app.state.task_store = None
        self.assert_reply(await self.post(), 503)
        response = await self.client.post('/retrieve', json={'param': ''})
        self.assertEqual(response.status_code, 422)
        self.assertIn('detail', response.json())
        self.assertEqual((await self.client.post('/retrieve', json={'param': '测试'})).text, '正文')


if __name__ == '__main__':
    unittest.main()
