import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import task_store


class CommitFailure(sqlite3.Connection):
    def commit(self):
        raise sqlite3.OperationalError('injected recovery commit failure')


class TaskStoreRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(task_store.TaskStore, 'recover_interrupted'),
                        'T06 recover_interrupted is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = task_store.TaskStore(self.temp.name)
        self.result = json.loads((ROOT / 'docs/sdd/examples/generate.response.json').read_text(
            encoding='utf-8'))['data']

    def accept(self, business):
        return self.store.accept_task(dict(bsnEvtId=business, taskId='T', userNo='U'))[0]

    def snapshot(self):
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            return (connection.execute('SELECT * FROM tasks ORDER BY sequence').fetchall(),
                    connection.execute('SELECT * FROM task_sequence').fetchall())

    def seed(self):
        for business in ['done', 'failed', 'terminated']:
            generation = self.accept(business)
            self.store.claim_next()
            self.store.save_artifact(generation, 'snapshot', {'hash': business})
            if business == 'done':
                self.store.finish_done(generation, self.result)
            elif business == 'failed':
                self.store.finish_failed(generation, '原失败原因')
            else:
                self.store.accept_task(dict(bsnEvtId=business, taskId='new', userNo='U'))
                active = self.store.claim_next()
                self.store.finish_failed(active['generationId'], '新任务失败')
        processing = self.accept('processing')
        self.store.claim_next()
        self.store.save_artifact(processing, 'extraction', {'text': '已提取内容'})
        pending = self.accept('pending')
        return processing, pending

    def test_restart_fails_only_unfinished(self):
        self.seed()
        keys = [('done', 'T'), ('failed', 'T'), ('terminated', 'T'), ('terminated', 'new'),
                ('processing', 'T'), ('pending', 'T')]
        before = {key: self.store.get_task(*key) for key in keys}
        raw_before, sequence = self.snapshot()
        # Reopen the durable database; constructing a store alone must not recover it.
        reopened = task_store.TaskStore(self.temp.name)
        self.assertEqual(reopened.get_task('processing', 'T')['status'], 'PROCESSING')
        self.assertEqual(reopened.get_task('pending', 'T')['status'], 'PENDING')
        self.assertEqual(reopened.recover_interrupted(), 2)
        for key, old in before.items():
            saved = reopened.get_task(*key)
            if old['status'] in ['PENDING', 'PROCESSING']:
                self.assertEqual(saved['status'], 'FAILED')
                self.assertIn('服务中断', saved['reason'])
                self.assertTrue(saved['updatedAt'])
                for field in set(old) - {'status', 'reason', 'updatedAt'}:
                    self.assertEqual(saved[field], old[field], field)
            else:
                self.assertEqual(saved, old)
        raw_after, new_sequence = self.snapshot()
        self.assertEqual(raw_after[:4], raw_before[:4])  # Includes the original DONE JSON bytes.
        self.assertEqual(new_sequence, sequence)
        self.assertIsNone(reopened.claim_next())

    def test_recovery_is_idempotent_and_rejects_old_writes(self):
        generations = self.seed()
        reopened = task_store.TaskStore(self.temp.name)
        self.assertEqual(reopened.recover_interrupted(), 2)
        before = self.snapshot()
        self.assertEqual(reopened.recover_interrupted(), 0)
        for generation in generations:
            self.assertFalse(self.store.save_artifact(generation, 'late', {'text': '迟到'}))
            self.assertFalse(self.store.finish_done(generation, self.result))
            self.assertFalse(self.store.finish_failed(generation, '迟到异常'))
        self.assertEqual(self.snapshot(), before)
        # Failure recovery does not prevent a new submission/generation.
        new_generation = self.accept('processing')
        self.assertNotIn(new_generation, generations)
        self.assertEqual(reopened.claim_next()['generationId'], new_generation)

    def test_empty_store_recovery_is_noop(self):
        before = self.snapshot()
        self.assertEqual(self.store.recover_interrupted(), 0)
        self.assertEqual(self.snapshot(), before)

    def test_recovery_commit_failure_rolls_back_every_record(self):
        self.seed()
        before = self.snapshot()
        real_connect = sqlite3.connect
        def failing_connect(*args, **kwargs):
            return real_connect(*args, **kwargs, factory=CommitFailure)
        with patch.object(task_store.sqlite3, 'connect', side_effect=failing_connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'recovery commit failure'):
                self.store.recover_interrupted()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.store.recover_interrupted(), 2)


if __name__ == '__main__':
    unittest.main()
