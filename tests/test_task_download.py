"""Download before acceptance; failed requests must leave old tasks intact."""
import asyncio
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import task_files
from task_store import TaskStore

PDF = b'%PDF-1.7\nfixture\n%%EOF\n'


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore(Path(self.temp.name) / 'storage')
        self.data = dict(bsnEvtId='A', userNo='U', taskId='T',
                         replyImageUrl='http://10.0.0.8/approval?id=1&token=private')
        self.submit = getattr(task_files, 'submit_task_from_url', None)
        self.assertIsNotNone(self.submit, 'JSON URL download submission is missing')

    async def send(self, handler, data=None):
        def factory(**kwargs):
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)
        with patch.object(task_files, 'PDFHTTPClient', side_effect=factory):
            return await self.submit(self.store, self.data if data is None else data)

    async def test_download_follow_redirect_and_save_exact_bytes(self):
        requests = []
        def handle(request):
            requests.append(request)
            if request.url.path == '/approval':
                return httpx.Response(302, headers={'Location': '/actual'})
            return httpx.Response(200, content=PDF, headers={'Content-Type': 'application/octet-stream'})
        result = await self.send(handle)
        self.assertEqual(result['autoReply'], 'task created')
        self.assertEqual(len(requests), 2)
        saved = self.store.get_task('A', 'T')
        self.assertEqual(Path(saved['pdfPath']).read_bytes(), PDF)
        self.assertEqual(saved['sourceDocumentHash'], hashlib.sha256(PDF).hexdigest())
        self.assertEqual(saved['businessData'], self.data)

    async def test_failed_downloads_preserve_old_task_and_cleanup(self):
        self.store.accept_task(self.data)
        self.store.claim_next()
        before = self.store.get_task('A', 'T')
        def failure(error):
            def handle(request):
                raise error('private URL and credentials', request=request)
            return handle
        cases = [(lambda r: httpx.Response(404, text='private'), 502),
                 (lambda r: httpx.Response(500), 502),
                 (failure(httpx.ConnectError), 502), (failure(httpx.ReadTimeout), 504),
                 (lambda r: httpx.Response(200, content=b''), 422),
                 (lambda r: httpx.Response(200, content=b'<html>login</html>'), 415),
                 (lambda r: httpx.Response(200, content=PDF + b'x' * task_files.MAX_PDF_BYTES), 413)]
        for handler, status in cases:
            with self.subTest(status=status):
                with self.assertRaises(task_files.TaskSubmissionError) as caught:
                    await self.send(handler)
                self.assertEqual(caught.exception.status_code, status)
                self.assertNotIn('private', str(caught.exception))
                self.assertEqual(self.store.get_task('A', 'T'), before)
                self.assertFalse(list(self.store.storage_root.glob('generations/*/source.pdf')))

    async def test_invalid_urls_and_business_fields_do_not_access_network(self):
        def forbidden(request):
            self.fail('invalid input reached network')
        for value in (None, '', ' ', 1, [], '/file.pdf', 'file:///tmp/a.pdf',
                      'ftp://example/a.pdf', 'http://', 'http://user:pass@host/a.pdf',
                      'http://host:bad/a.pdf', 'http://host/a.pdf#fragment'):
            with self.subTest(value=value), self.assertRaises(task_files.TaskSubmissionError) as caught:
                await self.send(forbidden, {**self.data, 'replyImageUrl': value})
            self.assertEqual(caught.exception.status_code, 422)
        with self.assertRaises(task_files.TaskSubmissionError):
            await self.send(forbidden, {**self.data, 'taskId': ''})
        self.assertIsNone(self.store.get_task('A', 'T'))

    async def test_slow_download_does_not_replace_old_task_before_acceptance(self):
        self.store.accept_task(self.data)
        before = self.store.get_task('A', 'T')
        started, release = asyncio.Event(), asyncio.Event()
        async def handle(request):
            started.set()
            await release.wait()
            return httpx.Response(200, content=PDF)
        pending = asyncio.create_task(self.send(handle))
        try:
            await asyncio.wait_for(started.wait(), 2)
            self.assertEqual(self.store.get_task('A', 'T'), before)
            release.set()
            await pending
            self.assertNotEqual(self.store.get_task('A', 'T')['generationId'], before['generationId'])
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    async def test_total_deadline_and_redirect_loop_leave_no_task(self):
        async def slow(request):
            await asyncio.sleep(1)
            return httpx.Response(200, content=PDF)
        with patch.object(task_files, 'DOWNLOAD_TIMEOUT_SECONDS', .01):
            with self.assertRaises(task_files.TaskSubmissionError) as caught:
                await self.send(slow)
        self.assertEqual(caught.exception.status_code, 504)
        with self.assertRaises(task_files.TaskSubmissionError) as caught:
            await self.send(lambda r: httpx.Response(302, headers={'Location': str(r.url)}))
        self.assertEqual(caught.exception.status_code, 502)
        self.assertIsNone(self.store.get_task('A', 'T'))

    async def test_interrupted_stream_is_closed_without_replacing_old_task(self):
        closed = []
        class BrokenStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield PDF
                raise httpx.ReadError('private download URL')
            async def aclose(self):
                closed.append(True)
        self.store.accept_task(self.data)
        before = self.store.get_task('A', 'T')
        with self.assertRaises(task_files.TaskSubmissionError) as caught:
            await self.send(lambda r: httpx.Response(200, stream=BrokenStream()))
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(closed, [True])
        self.assertEqual(self.store.get_task('A', 'T'), before)
        self.assertFalse(list(self.store.storage_root.glob('generations/*/source.pdf')))


if __name__ == '__main__':
    unittest.main()
