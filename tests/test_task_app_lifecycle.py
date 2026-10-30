import asyncio
import inspect
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import main
import task_worker
import task_settings
from task_settings import TaskSettings
from task_store import TaskStore
from download_fixture import pdf_download
from test_service import make_docx
from test_task_pipeline import PipelineModel
from test_checklist_rule_inventory import snapshot, HEADER
from pdf_fixtures import make_pdf
from task_pipeline import TaskPipeline
from test_phrase_materials import workbook, FILENAME

PDF = b'%PDF-1.7\nfixture\n%%EOF\n'


class BlockingPipeline:
    def __init__(self):
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.active = 0

    async def run_task(self, record):
        self.active += 1
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.active -= 1
            self.cancelled.set()


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.assertIn('task_settings', inspect.signature(main.create_app).parameters,
                      'T22 lifespan settings injection missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.documents = self.root / 'list'
        make_docx(self.documents / '测试材料清单.docx', '正文')
        self.settings = TaskSettings.from_env({'TASK_STORAGE_ROOT': str(self.root / 'storage')})
        self.store = TaskStore(self.settings.storage_root)
        self.data = dict(bsnEvtId='A', taskId='1', userNo='U')

    def app(self, **kwargs):
        return main.create_app(self.documents, task_settings=self.settings, **kwargs)

    def configured(self):
        return TaskSettings.from_env({'TASK_STORAGE_ROOT': str(self.settings.storage_root),
            **{stage + '_MODEL_' + key: value for stage in ('EXTRACT', 'GENERATE')
               for key, value in dict(API_URL='http://127.0.0.1:1/chat', NAME='test', API_KEY='local-test').items()}})

    def post(self, client, data=None, content=PDF):
        with pdf_download(content):
            return client.post('/api/v1/tasks', json={**(data or self.data),
                'replyImageUrl': 'http://10.0.0.8/approval.pdf'})

    def test_real_pipeline_upload_to_done_and_persisted_query(self):
        (self.documents / FILENAME).write_bytes(workbook())
        for name in ('甲', '乙'):
            snap = snapshot([HEADER, ['1', '营业执照', '必备', '', '']], name)
            (self.documents / (name + '材料清单.docx')).write_bytes(snap['raw_bytes'])
        model = PipelineModel()
        def factory(tasks, documents, client):
            return TaskPipeline(tasks, documents, model)
        with patch.object(main, 'TaskPipeline', side_effect=factory):
            with TestClient(main.create_app(self.documents, task_settings=self.configured())) as client:
                self.assertEqual(self.post(client, content=make_pdf(('text', 'scan'))).status_code, 200)
                async def terminal():
                    async def wait():
                        while self.store.get_task('A', '1')['status'] in ('PENDING', 'PROCESSING'):
                            await asyncio.sleep(0)
                    await asyncio.wait_for(wait(), 5)
                client.portal.call(terminal)
                response = client.post('/api/v1/tasks/query', json=self.data).json()
                self.assertEqual(response['status'], 'DONE', response)
                self.assertEqual(len(response['data']['products']), 2)
        with TestClient(self.app()) as client:
            self.assertEqual(client.post('/api/v1/tasks/query', json=self.data).json(), response)

    def test_invalid_model_timeout_or_retry_config_preserves_retrieval(self):
        for key, value in [('MODEL_REQUEST_TIMEOUT_SECONDS', 'nan'), ('MODEL_MAX_RETRIES', '-1')]:
            with self.subTest(key=key), patch.dict(os.environ, {
                    'TASK_STORAGE_ROOT': str(self.settings.storage_root), key: value}):
                with TestClient(main.create_app(self.documents)) as client:
                    self.assertEqual(self.post(client).status_code, 503)
                    self.assertEqual(client.post('/retrieve', json={'param': '测试'}).text, '正文')
                    self.assertEqual(client.post('/api/v1/tasks/query', json=self.data).status_code, 200)

    def test_second_owner_cannot_recover_active_tasks(self):
        first = self.app()
        with TestClient(first):
            generation, _ = self.store.accept_task(self.data)
            self.store.claim_next()
            with self.assertRaisesRegex(RuntimeError, '存储.*占用'):
                with TestClient(self.app()):
                    self.fail('second owner started')
            self.assertEqual(self.store.get_task('A', '1')['status'], 'PROCESSING')
            self.assertEqual(self.store.get_task('A', '1')['generationId'], generation)
        with TestClient(self.app()) as client:
            response = client.post('/api/v1/tasks/query', json=self.data).json()
            self.assertEqual(response['status'], 'FAILED')
            self.assertIn('服务中断', response['reason'])

    def test_invalid_model_file_preserves_retrieval_and_history_without_leaking_key(self):
        path = self.root / 'model-api.env'
        secret = 'fixture-secret-not-for-response'
        path.write_text('UNKNOWN_SETTING=' + secret, encoding='utf-8')
        with patch.object(task_settings, 'DEFAULT_CONFIG_FILE', path), patch.dict(os.environ, {
                'TASK_STORAGE_ROOT': str(self.settings.storage_root)}):
            with TestClient(main.create_app(self.documents)) as client:
                response = self.post(client)
                self.assertEqual(response.status_code, 503)
                self.assertIn('model-api.env', response.json()['reply'])
                self.assertNotIn(secret, response.text)
                self.assertEqual(client.post('/retrieve', json={'param': '测试'}).text, '正文')
                self.assertEqual(client.post('/api/v1/tasks/query', json=self.data).status_code, 200)
                self.assertEqual(client.get('/health').status_code, 200)

    def test_owner_is_exclusive_across_processes_and_releases(self):
        code = ('import sys; sys.path.insert(0, sys.argv[1]); '
                'from task_worker import StorageOwnership; '
                '\nwith StorageOwnership(sys.argv[2]): print("owned")')
        def child():
            return subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'src'),
                str(self.settings.storage_root)], capture_output=True, text=True, timeout=10)
        with task_worker.StorageOwnership(self.settings.storage_root):
            self.assertNotEqual(child().returncode, 0)
        result = child()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('owned', result.stdout)

    def test_missing_models_rejects_creation_preserves_history_and_retrieval(self):
        generation, _ = self.store.accept_task(self.data)
        self.store.claim_next()
        result = json.loads((ROOT / 'docs/sdd/examples/generate.response.json').read_text(
            encoding='utf-8'))['data']
        self.store.finish_done(generation, result)
        with TestClient(self.app()) as client:
            response = self.post(client)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(set(response.json()), {'reply'})
            self.assertIn('配置', response.json()['reply'])
            self.assertEqual(client.post('/retrieve', json={'param': '测试'}).text, '正文')
            self.assertEqual(client.post('/api/v1/tasks/query', json=self.data).json()['data'], result)
        self.assertEqual(self.store.get_task('A', '1')['generationId'], generation)
        self.assertFalse((self.settings.storage_root / 'generations').exists())

    def test_commit_wakes_worker_and_shutdown_joins_then_restart_recovers(self):
        pipeline = BlockingPipeline()
        app = main.create_app(self.documents, task_settings=self.configured())
        with patch.object(main, 'TaskPipeline', return_value=pipeline):
            with TestClient(app) as client:
                worker = app.state.task_worker
                model = app.state.task_client
                self.assertEqual(self.post(client).status_code, 200)
                self.assertTrue(pipeline.started.wait(2))
                self.assertEqual(self.store.get_task('A', '1')['status'], 'PROCESSING')
            self.assertTrue(pipeline.cancelled.is_set())
            self.assertEqual(pipeline.active, 0)
            self.assertIsNone(worker._consumer)
            self.assertTrue(model._http.is_closed)
        with TestClient(app) as client:
            self.assertEqual(client.post('/api/v1/tasks/query', json=self.data).json()['status'], 'FAILED')

    def test_replacement_hook_observes_committed_state_and_cancels_active_task(self):
        pipeline = BlockingPipeline()
        observed = []
        def hook(generations):
            observed.append((generations, self.store.get_task('A', '1')['status']))
        app = main.create_app(self.documents, task_settings=self.configured(), notify_cancel=hook)
        with patch.object(main, 'TaskPipeline', return_value=pipeline):
            with TestClient(app) as client:
                self.assertEqual(self.post(client).status_code, 200)
                self.assertTrue(pipeline.started.wait(2))
                generation = self.store.get_task('A', '1')['generationId']
                response = self.post(client, {**self.data, 'taskId': '2'})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(pipeline.cancelled.wait(2))
                self.assertEqual(observed, [([generation], 'TERMINATED')])

    def test_recovery_precedes_worker_and_covers_pending_and_processing(self):
        self.store.accept_task(self.data)
        self.store.claim_next()
        self.store.accept_task({**self.data, 'bsnEvtId': 'B'})
        pipeline = BlockingPipeline()
        def factory(*args):
            for b in 'AB':
                self.assertEqual(self.store.get_task(b, '1')['status'], 'FAILED')
            return pipeline
        with patch.object(main, 'TaskPipeline', side_effect=factory):
            with TestClient(self.app()):
                self.assertFalse(pipeline.started.is_set())

    def test_startup_failure_releases_ownership(self):
        with patch.object(main, 'TaskPipeline', side_effect=RuntimeError('setup failure')):
            with self.assertRaisesRegex(RuntimeError, 'setup failure'):
                with TestClient(self.app()):
                    pass
        with TestClient(self.app()) as client:
            self.assertEqual(client.get('/health').status_code, 200)

    def test_default_storage_is_not_created_at_import_or_factory_time(self):
        storage = self.root / 'new-storage'
        with patch.dict(os.environ, {'TASK_STORAGE_ROOT': str(storage)}):
            app = main.create_app(self.documents)
            self.assertFalse(storage.exists())
            with TestClient(app):
                self.assertTrue((storage / 'tasks.sqlite3').exists())
