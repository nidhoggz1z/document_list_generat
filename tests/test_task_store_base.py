import importlib
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
store_module = importlib.import_module('task_store') if importlib.util.find_spec('task_store') else None


class TaskStoreBaseTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(store_module, 'T03 task_store implementation is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'storage'
        self.store = store_module.TaskStore(self.root)

    def test_identity_survives_reopen(self):
        data = {'bsnEvtId': ' A ', 'taskId': ' T1 ', 'userNo': ' U ',
                'contractNum': '', 'extension': {'values': ['甲', None]}}
        record = self.store.insert_task(data)
        data['extension']['values'].append('later')
        reopened = store_module.TaskStore(self.root)
        saved = reopened.get_task('A', 'T1')
        self.assertEqual(saved, record)
        self.assertEqual(saved['userNo'], 'U')
        self.assertEqual(saved['businessData']['extension']['values'], ['甲', None])
        self.assertEqual(saved['status'], 'PENDING')
        self.assertIsNone(saved['result'])
        self.assertIsNone(saved['reason'])
        self.assertTrue(saved['createdAt'])
        self.assertIsNone(reopened.get_task('missing', 'T1'))

    def test_composite_key_and_saved_user(self):
        for business, task in [('A', 'T1'), ('A', 'T2'), ('B', 'T1')]:
            self.store.insert_task({'bsnEvtId': business, 'taskId': task, 'userNo': 'creator'})
        before = self.store.get_task('A', 'T1')
        # Queries use only the composite key; callers' userNo cannot alter it.
        for query_user in ['creator', 'another-user']:
            self.assertEqual(self.store.get_task('A', 'T1')['userNo'], 'creator', query_user)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.insert_task({'bsnEvtId': 'A', 'taskId': 'T1', 'userNo': 'different'})
        self.assertEqual(self.store.get_task('A', 'T1'), before)
        self.assertEqual(self.store.get_task('A', 'T2')['status'], 'PENDING')
        self.assertEqual(self.store.get_task('B', 'T1')['status'], 'PENDING')

    def test_generation_paths_and_sequence_are_service_owned(self):
        first = self.store.insert_task({'bsnEvtId': '../A', 'taskId': '../T', 'userNo': 'U',
                                        'pdfPath': '../../outside', 'generationId': '../evil'})
        second = store_module.TaskStore(self.root).insert_task(
            {'bsnEvtId': 'B', 'taskId': 'T', 'userNo': 'U'})
        self.assertNotEqual(first['generationId'], second['generationId'])
        self.assertGreater(second['sequence'], first['sequence'])
        for record in [first, second]:
            path = Path(record['pdfPath'])
            self.assertEqual(path.parent.name, record['generationId'])
            self.assertTrue(path.is_relative_to(self.root))
            self.assertFalse(path.exists())  # PDF persistence belongs to T07.

    def test_invalid_input_does_not_insert_or_consume_sequence(self):
        with self.assertRaises(ValueError):
            self.store.insert_task({'bsnEvtId': ' ', 'taskId': 'T', 'userNo': 'U'})
        record = self.store.insert_task({'bsnEvtId': 'A', 'taskId': 'T', 'userNo': 'U'})
        self.assertEqual(record['sequence'], 1)

    def test_result_reason_and_artifact_pointers_survive_reopen(self):
        self.store.insert_task({'bsnEvtId': 'A', 'taskId': 'T', 'userNo': 'U'},
                               source_document_hash='a' * 64)
        connection = sqlite3.connect(self.store.db_path)
        try:
            with connection:
                connection.execute('UPDATE tasks SET status = ?, result = ?, reason = ?, artifacts = ?',
                                   ('FAILED', '{"saved":true}', '保存的原因', '{"extraction":"internal/path"}'))
        finally:
            connection.close()
        saved = store_module.TaskStore(self.root).get_task('A', 'T')
        self.assertEqual(saved['sourceDocumentHash'], 'a' * 64)
        self.assertEqual(saved['result'], {'saved': True})
        self.assertEqual(saved['reason'], '保存的原因')
        self.assertEqual(saved['artifacts'], {'extraction': 'internal/path'})


if __name__ == '__main__':
    unittest.main()
