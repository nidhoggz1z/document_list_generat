import inspect
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import main
from task_settings import TaskSettings
from task_store import TaskStore


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.documents = self.root / 'list'
        self.documents.mkdir()
        with ZipFile(self.documents / '测试材料清单.docx', 'w') as archive:
            archive.writestr('word/document.xml',
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>正文</w:t></w:r></w:p></w:body></w:document>')
        self.store = TaskStore(self.root / 'storage')
        self.data = dict(bsnEvtId='A', taskId='T', userNo='saved-user')
        self.body = {**self.data, 'userNo': 'different-user'}
        self.result = json.loads((ROOT / 'docs/sdd/examples/generate.response.json').read_text(
            encoding='utf-8'))['data']
        self.assertIn('task_store', inspect.signature(main.create_app).parameters,
                      'T08 create_app store injection is missing')
        self.app = main.create_app(self.documents, task_store=self.store)
        self.client = self.enterContext(TestClient(self.app))

    def query(self, body=None):
        return self.client.post('/api/v1/tasks/query', json=self.body if body is None else body)

    def accept(self):
        return self.store.accept_task(self.data)[0]

    def test_all_five_states_have_exact_public_fields(self):
        generation = self.accept()
        for status in ['PENDING', 'PROCESSING', 'FAILED', 'TERMINATED', 'DONE']:
            with self.subTest(status=status):
                if status == 'PROCESSING':
                    self.store.claim_next()
                elif status == 'FAILED':
                    self.store.finish_failed(generation, '解析失败')
                elif status == 'TERMINATED':
                    generation = self.accept()
                    self.store.accept_task({**self.data, 'taskId': 'replacement'})
                elif status == 'DONE':
                    generation = self.accept()
                    self.store.claim_next()
                    self.store.finish_done(generation, self.result)
                response = self.query()
                self.assertEqual(response.status_code, 200)
                expected = {**self.data, 'status': status}
                if status == 'FAILED':
                    expected['reason'] = '解析失败'
                if status == 'DONE':
                    expected.update(message='生成完成', data=self.result)
                self.assertEqual(response.json(), expected)

    def test_unknown_and_composite_identity_and_whitespace(self):
        self.accept()
        self.assertEqual(self.query({**self.body, 'bsnEvtId': 'B'}).json(), {'reply': '任务未创建'})
        response = self.query({**self.body, 'taskId': 'unknown'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'reply': '任务未创建'})
        padded = {key: ' ' + value + '\n' for key, value in self.body.items()}
        self.assertEqual(self.query(padded).json(), {**self.data, 'status': 'PENDING'})

    def test_invalid_identity_and_json_use_reply_without_mutation(self):
        self.accept()
        before = self.store.get_task('A', 'T')
        for key in self.body:
            missing = {k: v for k, v in self.body.items() if k != key}
            for body in [missing] + [{**self.body, key: value} for value in [None, '', ' \n', 1, [], {}]]:
                with self.subTest(body=body):
                    response = self.query(body)
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(set(response.json()), {'reply'})
        for raw, status in [('{', 400), (b'\xff', 400), ('', 422), ('null', 422), ('[]', 422), ('1', 422)]:
            with self.subTest(raw=raw):
                response = self.client.post('/api/v1/tasks/query', content=raw,
                                            headers={'Content-Type': 'application/json'})
                self.assertEqual(response.status_code, status)
                self.assertEqual(set(response.json()), {'reply'})
        self.assertEqual(self.query({**self.body, 'extra': 'field'}).status_code, 422)
        self.assertEqual(self.store.get_task('A', 'T'), before)

    def test_query_never_calls_model_or_reads_rules_or_writes_tasks(self):
        generation = self.accept()
        self.store.claim_next()
        self.store.finish_done(generation, self.result)
        before = self.store.get_task('A', 'T')
        # A fresh store instance reads persisted history, with no generation setup.
        self.app.state.task_store = TaskStore(self.store.storage_root)
        with patch.object(main.DocumentStore, 'refresh', side_effect=AssertionError('rule read')), \
             patch.object(TaskSettings, 'require_models', side_effect=AssertionError('model config')), \
             patch.object(TaskStore, 'accept_task', side_effect=AssertionError('write')), \
             patch.object(TaskStore, 'finish_done', side_effect=AssertionError('generate')), \
             patch.object(main, 'read_docx', side_effect=AssertionError('document read')):
            for _ in range(2):
                response = self.query()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['data'], self.result)
        self.assertEqual(self.store.get_task('A', 'T'), before)

    def test_new_result_omits_public_hash_but_preserves_internal_file_hash(self):
        result = dict(self.result)
        result.pop('sourceDocumentHash', None)
        generation = self.store.accept_task(self.data, source_document_hash='a' * 64)[0]
        self.store.claim_next()
        self.assertTrue(self.store.finish_done(generation, result))
        before = self.store.get_task('A', 'T')
        self.app.state.task_store = TaskStore(self.store.storage_root)
        response = self.query()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data'], result)
        self.assertNotIn('sourceDocumentHash', response.json()['data'])
        self.assertEqual(before['sourceDocumentHash'], 'a' * 64)
        self.assertEqual(self.store.get_task('A', 'T'), before)

    def test_historical_tree_with_hash_keeps_its_original_json(self):
        result = dict(self.result, sourceDocumentHash='b' * 64)
        generation = self.accept()
        self.store.claim_next()
        self.assertTrue(self.store.finish_done(generation, result))
        before = self.store.get_task('A', 'T')
        response = self.query()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data'], result)
        self.assertEqual(self.store.get_task('A', 'T'), before)

    def test_default_store_is_initialized_by_lifespan(self):
        storage = self.root / 'default-storage'
        with patch.dict('os.environ', {'TASK_STORAGE_ROOT': str(storage)}):
            with TestClient(main.create_app(self.documents)) as client:
                response = client.post('/api/v1/tasks/query', json=self.body)
                self.assertTrue((storage / 'tasks.sqlite3').exists())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'reply': '任务未创建'})

    def test_store_failure_and_unexpected_error_are_safe_reply(self):
        for error, status in [(sqlite3.OperationalError('private-db-path'), 503),
                              (OSError('private-path'), 503), (RuntimeError('private-value'), 500)]:
            with self.subTest(status=status), patch.object(self.store, 'get_task', side_effect=error):
                response = self.query()
                self.assertEqual(response.status_code, status)
                self.assertEqual(set(response.json()), {'reply'})
                self.assertNotIn('private', response.text)

    def test_new_error_mapping_does_not_change_retrieve(self):
        response = self.client.post('/retrieve', json={'param': ''})
        self.assertEqual(response.status_code, 422)
        self.assertIn('detail', response.json())
        self.assertNotIn('reply', response.json())
        self.assertEqual(self.client.post('/retrieve', json={'param': '测试'}).text, '正文')


if __name__ == '__main__':
    unittest.main()
