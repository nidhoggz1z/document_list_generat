import hashlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from starlette.datastructures import UploadFile, Headers

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from task_store import TaskStore

if importlib.util.find_spec('task_files'):
    import task_files
else:
    task_files = None

PDF = b'%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n'
LIMIT = 10 * 1024 * 1024


class CommitFailure(sqlite3.Connection):
    def commit(self):
        raise sqlite3.OperationalError('injected commit failure')


class SubmissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(task_files, 'T07 task_files service is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore(self.temp.name)
        self.data = dict(bsnEvtId='A', taskId='T', userNo='U')
        self.cancel = Mock()

    def upload(self, content=PDF, filename='approval.pdf', mime='application/pdf'):
        upload = UploadFile(io.BytesIO(content), filename=filename,
                            headers=Headers({'content-type': mime}))
        self.addCleanup(upload.file.close)
        return upload

    async def submit(self, upload=None, data=None):
        return await task_files.submit_task(self.store, self.data if data is None else data,
                                            upload or self.upload(), notify_cancel=self.cancel)

    def files(self):
        return list(self.store.storage_root.glob('generations/*/source.pdf'))

    async def test_success_saves_original_bytes_hash_and_pointer_before_commit(self):
        accept = self.store.accept_task
        def check(data, **kwargs):
            path = self.store.generation_path(kwargs['generation_id'])
            self.assertEqual(path.read_bytes(), PDF)
            self.assertEqual(kwargs['source_document_hash'], hashlib.sha256(PDF).hexdigest())
            return accept(data, **kwargs)
        with patch.object(self.store, 'accept_task', side_effect=check):
            result = await self.submit(data={**self.data, 'userNo': ' U ', 'context': {'x': 1}})
        self.assertEqual(result, {**self.data, 'autoReply': 'task created'})
        saved = self.store.get_task('A', 'T')
        self.assertEqual(saved['status'], 'PENDING')
        self.assertEqual(Path(saved['pdfPath']).read_bytes(), PDF)
        self.assertEqual(saved['businessData']['context'], {'x': 1})

    async def test_rejected_upload_does_not_cancel_old_task(self):
        await self.submit()
        self.store.claim_next()
        self.cancel.reset_mock()
        before = self.store.get_task('A', 'T')
        paths = self.files()
        for content, status in [(b'', 422), (b'not pdf', 415),
                                (b'prefix' + PDF, 415), (b'%PDF-3.0\n', 415),
                                (b'%PDF-1.7fake', 415),
                                (PDF + b' ' * LIMIT, 413)]:
            with self.subTest(status=status, size=len(content)):
                with self.assertRaises(task_files.TaskSubmissionError) as error:
                    await self.submit(self.upload(content))
                self.assertEqual(error.exception.status_code, status)
                self.assertEqual(self.store.get_task('A', 'T'), before)
                self.assertEqual(self.files(), paths)
                self.cancel.assert_not_called()

    async def test_actual_size_boundary_and_untrusted_metadata(self):
        content = PDF + b' ' * (LIMIT - len(PDF))
        upload = self.upload(content, '../../outside.txt', 'text/plain')
        upload.size = 1
        await self.submit(upload)
        saved = self.store.get_task('A', 'T')
        self.assertEqual(Path(saved['pdfPath']).read_bytes(), content)
        self.assertEqual(Path(saved['pdfPath']).name, 'source.pdf')
        self.assertTrue(Path(saved['pdfPath']).is_relative_to(self.store.storage_root))
        upload = self.upload(content + b'x')
        upload.size = 1
        with self.assertRaises(task_files.TaskSubmissionError) as error:
            await self.submit(upload)
        self.assertEqual(error.exception.status_code, 413)

    async def test_invalid_business_input_keeps_old_task(self):
        await self.submit()
        before = self.store.get_task('A', 'T')
        self.cancel.reset_mock()
        for value in [{}, {**self.data, 'userNo': None}, {**self.data, 'ctrTamt': 1}]:
            with self.subTest(value=value), self.assertRaises(task_files.TaskSubmissionError) as error:
                await self.submit(data=value)
            self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(self.store.get_task('A', 'T'), before)
        self.cancel.assert_not_called()

    async def test_file_write_and_database_commit_failures_preserve_old_generation(self):
        await self.submit()
        before = self.store.get_task('A', 'T')
        paths = self.files()
        self.cancel.reset_mock()
        real_connect = sqlite3.connect
        def failing_connect(*args, **kwargs):
            return real_connect(*args, **kwargs, factory=CommitFailure)
        failures = [patch('task_files.os.fsync', side_effect=OSError('disk failure')),
                    patch.object(Path, 'open', side_effect=OSError('write failure')),
                    patch('task_store.sqlite3.connect', side_effect=failing_connect)]
        for failure in failures:
            with failure, self.assertRaises(task_files.TaskSubmissionError) as error:
                await self.submit()
            self.assertEqual(error.exception.status_code, 503)
            self.assertEqual(self.store.get_task('A', 'T'), before)
            self.assertEqual(self.files(), paths)
            self.cancel.assert_not_called()
        self.assertEqual(len(list((self.store.storage_root / 'generations').iterdir())), 1)

    async def test_notify_only_after_commit_and_only_replaced_generations(self):
        old = self.store.accept_task(self.data)[0]
        other = self.store.accept_task({**self.data, 'bsnEvtId': 'B'})[0]
        def notified(generations):
            self.assertEqual(generations, [old])
            self.assertNotEqual(self.store.get_task('A', 'T')['generationId'], old)
            self.assertEqual(self.store.get_task('B', 'T')['generationId'], other)
        self.cancel.side_effect = notified
        await self.submit()
        self.cancel.assert_called_once_with([old])

    async def test_notify_failure_keeps_success_and_files(self):
        self.store.accept_task(self.data)
        self.cancel.side_effect = RuntimeError('notification failed')
        with self.assertLogs('task_files', level='ERROR'):
            result = await self.submit()
        self.assertEqual(result['autoReply'], 'task created')
        self.assertTrue(Path(self.store.get_task('A', 'T')['pdfPath']).is_file())

    async def test_stream_is_chunked_and_read_failure_is_cleaned(self):
        upload = self.upload()
        original = upload.read
        calls = []
        async def read(size=-1):
            self.assertGreater(size, 0)
            calls.append(size)
            if len(calls) == 2:
                raise OSError('read failure')
            return await original(8)
        upload.read = read
        with self.assertRaises(task_files.TaskSubmissionError):
            await self.submit(upload)
        self.assertEqual(self.files(), [])
        self.assertIsNone(self.store.get_task('A', 'T'))

    async def test_failed_replacement_preserves_done_result_and_pdf(self):
        await self.submit()
        previous = self.store.get_task('A', 'T')
        self.store.claim_next()
        result = json.loads((Path(__file__).resolve().parents[1] /
            'docs/sdd/examples/generate.response.json').read_text(encoding='utf-8'))['data']
        self.store.finish_done(previous['generationId'], result)
        before = self.store.get_task('A', 'T')
        with patch.object(self.store, 'accept_task', side_effect=sqlite3.OperationalError('failure')):
            with self.assertRaises(task_files.TaskSubmissionError):
                await self.submit()
        self.assertEqual(self.store.get_task('A', 'T'), before)
        self.assertEqual(Path(previous['pdfPath']).read_bytes(), PDF)
        await self.submit(self.upload(b'%PDF-2.0\nnew bytes'))
        current = self.store.get_task('A', 'T')
        self.assertNotEqual(current['generationId'], previous['generationId'])
        self.assertEqual(Path(previous['pdfPath']).read_bytes(), PDF)
        self.assertEqual(Path(current['pdfPath']).read_bytes(), b'%PDF-2.0\nnew bytes')

    async def test_partial_write_failure_cleans_only_new_file(self):
        await self.submit()
        before = self.store.get_task('A', 'T')
        real_open = Path.open
        class FailingWriter:
            def __init__(self, path, *args, **kwargs):
                self.target = real_open(path, *args, **kwargs)
            def __enter__(self):
                return self
            def write(self, chunk):
                self.target.write(chunk[:4])
                raise OSError('partial write failure')
            def __exit__(self, *args):
                self.target.close()
        with patch.object(Path, 'open', FailingWriter):
            with self.assertRaises(task_files.TaskSubmissionError) as error:
                await self.submit()
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self.files(), [Path(before['pdfPath'])])
        self.assertEqual(self.store.get_task('A', 'T'), before)

    async def test_async_notification_is_awaited(self):
        old = self.store.accept_task(self.data)[0]
        observed = []
        async def notify(generations):
            observed.extend(generations)
        result = await task_files.submit_task(self.store, self.data, self.upload(), notify_cancel=notify)
        self.assertEqual(observed, [old])
        self.assertEqual(result['autoReply'], 'task created')


if __name__ == '__main__':
    unittest.main()
