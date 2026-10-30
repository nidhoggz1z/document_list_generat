import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import task_store


class CommitFailure(sqlite3.Connection):
    def commit(self):
        raise sqlite3.OperationalError('injected commit failure')


class TaskStoreQueueTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(task_store.TaskStore, 'accept_task'),
                        'T04 atomic accept_task implementation is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = task_store.TaskStore(self.temp.name)

    def accept(self, business, task, **extra):
        return self.store.accept_task(dict(bsnEvtId=business, taskId=task, userNo='creator', **extra))

    def sql(self, statement, parameters=()):
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            with connection:
                return connection.execute(statement, parameters).fetchall()

    def queue(self):
        return self.sql("SELECT taskId FROM tasks WHERE status = 'PENDING' ORDER BY sequence")

    def snapshot(self):
        return (self.sql('SELECT * FROM tasks ORDER BY sequence'),
                self.sql('SELECT * FROM task_sequence'))

    def initial_queue(self):
        generation, _ = self.accept('A', 'A1')
        self.sql("UPDATE tasks SET status = 'PROCESSING' WHERE taskId = 'A1'")
        self.accept('B', 'B1')
        self.accept('C', 'C1')
        return generation

    def test_replacement_moves_to_tail_atomically(self):
        old = self.initial_queue()
        generation, cancelled = self.accept('A', 'A2')
        self.assertEqual(cancelled, [old])
        self.assertEqual(self.queue(), [('B1',), ('C1',), ('A2',)])
        self.assertEqual(self.store.get_task('A', 'A1')['status'], 'TERMINATED')
        self.assertEqual(self.store.get_task('A', 'A2')['generationId'], generation)
        self.assertTrue(self.store.get_task('A', 'A1')['reason'])

    def test_pending_replacement_does_not_preempt_other_business(self):
        self.initial_queue()
        _, cancelled = self.accept('B', 'B2')
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(self.queue(), [('C1',), ('B2',)])
        self.assertEqual(self.store.get_task('A', 'A1')['status'], 'PROCESSING')
        self.assertEqual(self.store.get_task('B', 'B1')['status'], 'TERMINATED')

    def test_all_unfinished_same_business_terminated_terminal_history_preserved(self):
        # Seed the defensive case of multiple old unfinished tasks using T03 insert.
        records = []
        for task, status in [('A1', 'PROCESSING'), ('A2', 'PENDING'),
                             ('done', 'DONE'), ('failed', 'FAILED'), ('ended', 'TERMINATED')]:
            record = self.store.insert_task(dict(bsnEvtId='A', taskId=task, userNo='old'))
            records.append(record)
            self.sql('UPDATE tasks SET status = ?, result = ?, reason = ? WHERE taskId = ?',
                     (status, json.dumps({'saved': task}) if status == 'DONE' else None,
                      'original reason' if status in ('FAILED', 'TERMINATED') else None, task))
        before = {task: self.store.get_task('A', task) for task in ['done', 'failed', 'ended']}
        _, cancelled = self.accept('A', 'A3')
        self.assertEqual(cancelled, [r['generationId'] for r in records[:2]])
        self.assertEqual(self.queue(), [('A3',)])
        for task, record in before.items():
            self.assertEqual(self.store.get_task('A', task), record)

    def test_same_key_reruns_same_and_changed_input_with_new_paths(self):
        for initial_status in ['PENDING', 'PROCESSING', 'DONE', 'FAILED', 'TERMINATED']:
            with self.subTest(initial_status=initial_status):
                business = initial_status
                first, _ = self.accept(business, 'T', contractNum='first')
                self.sql('UPDATE tasks SET status = ?, result = ?, artifacts = ?, reason = ? '
                         'WHERE bsnEvtId = ?',
                         (initial_status, '{"old": true}', '{"old": true}', 'old', business))
                old = self.store.get_task(business, 'T')
                second, cancelled = self.accept(business, 'T', contractNum='first')
                self.assertEqual(cancelled, [first] if initial_status in ('PENDING', 'PROCESSING') else [])
                third, cancelled = self.store.accept_task(dict(
                    bsnEvtId=business, taskId='T', userNo='new-user', contractNum='changed'))
                current = self.store.get_task(business, 'T')
                self.assertEqual(cancelled, [second])
                self.assertEqual(len({first, second, third}), 3)
                self.assertEqual(current['userNo'], 'new-user')
                self.assertEqual(current['businessData']['contractNum'], 'changed')
                self.assertNotEqual(current['pdfPath'], old['pdfPath'])
                self.assertGreater(current['sequence'], old['sequence'])
                self.assertIsNone(current['result'])
                self.assertIsNone(current['reason'])
                self.assertEqual(current['artifacts'], {})

    def test_write_failure_rolls_back_termination_overwrite_and_sequence(self):
        self.accept('A', 'T')
        self.sql("CREATE TRIGGER fail_insert BEFORE INSERT ON tasks BEGIN "
                 "SELECT RAISE(ABORT, 'injected write failure'); END")
        before = self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):
            self.accept('A', 'T')
        self.assertEqual(self.snapshot(), before)
        self.sql('DROP TRIGGER fail_insert')
        self.accept('A', 'T')
        self.assertEqual(self.store.get_task('A', 'T')['sequence'], 2)

    def test_commit_failure_rolls_back_old_result_and_queue(self):
        self.initial_queue()
        self.sql("UPDATE tasks SET status = 'DONE', result = '{\"saved\":true}' WHERE taskId = 'A1'")
        before = self.snapshot()
        real_connect = sqlite3.connect
        def failing_connect(*args, **kwargs):
            return real_connect(*args, **kwargs, factory=CommitFailure)
        with patch.object(task_store.sqlite3, 'connect', side_effect=failing_connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'injected commit failure'):
                self.accept('A', 'A1')
            with self.assertRaises(sqlite3.OperationalError):
                self.accept('B', 'B2')
        self.assertEqual(self.snapshot(), before)
        self.accept('D', 'D1')
        self.assertEqual(self.store.get_task('D', 'D1')['sequence'], 4)

    def test_invalid_input_keeps_previous_task(self):
        self.accept('A', 'T')
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.store.accept_task(dict(bsnEvtId='A', taskId='T', userNo=' '))
        self.assertEqual(self.snapshot(), before)

    def test_preallocated_generation_and_invalid_or_duplicate_ids(self):
        generation = uuid4()
        expected_path = self.store.generation_path(generation)
        data = dict(bsnEvtId='A', taskId='T', userNo='U')
        accepted, _ = self.store.accept_task(data, generation_id=generation,
                                           source_document_hash='b' * 64)
        self.assertEqual(accepted, generation.hex)
        record = self.store.get_task('A', 'T')
        self.assertEqual(Path(record['pdfPath']), expected_path)
        self.assertEqual(record['sourceDocumentHash'], 'b' * 64)
        before = self.snapshot()
        for value in [generation, '../outside']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.accept_task(data, generation_id=value)
            self.assertEqual(self.snapshot(), before)

    def test_concurrent_same_business_leaves_only_last_committed_task_pending(self):
        count = 5
        stores = [task_store.TaskStore(self.temp.name) for _ in range(count)]
        barrier = Barrier(count)
        def submit(index):
            barrier.wait(timeout=10)
            return stores[index].accept_task(dict(bsnEvtId='A', taskId=str(index), userNo='U'))
        with ThreadPoolExecutor(max_workers=count) as executor:
            results = list(executor.map(submit, range(count)))
        rows = self.sql('SELECT generationId, status, sequence FROM tasks ORDER BY sequence')
        self.assertEqual([r[2] for r in rows], list(range(1, count + 1)))
        self.assertEqual([r[1] for r in rows], ['TERMINATED'] * (count - 1) + ['PENDING'])
        self.assertEqual({g for _, cancelled in results for g in cancelled}, {r[0] for r in rows[:-1]})

    def test_concurrent_same_key_has_one_current_generation(self):
        count = 5
        barrier = Barrier(count)
        def submit(index):
            barrier.wait(timeout=10)
            return self.accept('A', 'T', contractNum=str(index))
        with ThreadPoolExecutor(max_workers=count) as executor:
            results = list(executor.map(submit, range(count)))
        current = self.store.get_task('A', 'T')
        cancelled = {g for _, values in results for g in values}
        self.assertEqual({g for g, _ in results} - cancelled, {current['generationId']})
        self.assertEqual(current['sequence'], count)
        self.assertEqual(self.queue(), [('T',)])


if __name__ == '__main__':
    unittest.main()
