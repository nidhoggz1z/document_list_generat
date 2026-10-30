import json
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import task_store


class CommitFailure(sqlite3.Connection):
    def commit(self):
        raise sqlite3.OperationalError('injected commit failure')


class TaskStoreTransitionTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(task_store.TaskStore, 'claim_next'), 'T05 claim_next is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = task_store.TaskStore(self.temp.name)
        self.result = json.loads((ROOT / 'docs/sdd/examples/generate.response.json').read_text(
            encoding='utf-8'))['data']

    def accept(self, business='A', task='T'):
        return self.store.accept_task(dict(bsnEvtId=business, taskId=task, userNo='U'))[0]

    def assert_rejected(self, generation):
        self.assertFalse(self.store.save_artifact(generation, 'extraction', {'late': True}))
        self.assertFalse(self.store.finish_done(generation, self.result))
        self.assertFalse(self.store.finish_failed(generation, '迟到异常'))

    def test_claim_fifo_and_empty_queue(self):
        self.assertIsNone(self.store.claim_next())
        generations = [self.accept(business) for business in ['A', 'B', 'C']]
        for generation in generations:
            record = self.store.claim_next()
            self.assertEqual(record['generationId'], generation)
            self.assertEqual(record['status'], 'PROCESSING')
            self.assertEqual(self.store.get_task(record['bsnEvtId'], 'T'), record)
            self.assertTrue(self.store.finish_failed(generation, '处理失败'))
        self.assertIsNone(self.store.claim_next())

    def test_pending_and_unknown_generations_cannot_publish(self):
        generation = self.accept()
        before = self.store.get_task('A', 'T')
        self.assert_rejected(generation)
        self.assert_rejected('missing')
        self.assertEqual(self.store.get_task('A', 'T'), before)

    def test_artifacts_merge_and_survive_reopen(self):
        generation = self.accept()
        self.store.claim_next()
        self.assertTrue(self.store.save_artifact(generation, 'extraction', {'text': '批复'}))
        self.assertTrue(self.store.save_artifact(generation, 'snapshot', {'hash': 'abc'}))
        self.assertTrue(self.store.save_artifact(generation, 'extraction', {'text': '全文'}))
        saved = task_store.TaskStore(self.temp.name).get_task('A', 'T')
        self.assertEqual(saved['artifacts'], {'extraction': {'text': '全文'}, 'snapshot': {'hash': 'abc'}})
        self.assertEqual(saved['status'], 'PROCESSING')

    def test_stale_generation_cannot_publish(self):
        for same_key in [True, False]:
            with self.subTest(same_key=same_key):
                old = self.accept()
                self.store.claim_next()
                self.accept(task='T' if same_key else 'new')
                # Exercise the generation check even when the replacement is PROCESSING.
                self.store.claim_next()
                before = [self.store.get_task('A', key) for key in ['T', 'new']]
                self.assert_rejected(old)
                self.assertEqual([self.store.get_task('A', key) for key in ['T', 'new']], before)

    def test_terminal_states_are_immutable(self):
        for state in ['DONE', 'FAILED', 'TERMINATED']:
            with self.subTest(state=state):
                generation = self.accept(business=state)
                self.store.claim_next()
                if state == 'DONE':
                    self.assertTrue(self.store.finish_done(generation, self.result))
                elif state == 'FAILED':
                    self.assertTrue(self.store.finish_failed(generation, '解析失败'))
                else:
                    self.accept(business=state, task='new')
                before = self.store.get_task(state, 'T')
                self.assert_rejected(generation)
                self.assertEqual(self.store.get_task(state, 'T'), before)
                if state == 'TERMINATED':
                    self.store.claim_next()
                    self.store.finish_failed(self.store.get_task(state, 'new')['generationId'], '失败')

    def test_done_publishes_complete_result_and_preserves_artifacts(self):
        generation = self.accept()
        self.store.claim_next()
        self.store.save_artifact(generation, 'snapshot', {'hash': 'abc'})
        self.assertTrue(self.store.finish_done(generation, self.result))
        saved = task_store.TaskStore(self.temp.name).get_task('A', 'T')
        self.assertEqual(saved['status'], 'DONE')
        self.assertEqual(saved['result'], self.result)
        self.assertIsNone(saved['reason'])
        self.assertEqual(saved['artifacts'], {'snapshot': {'hash': 'abc'}})

    def test_invalid_result_or_reason_cannot_change_processing_record(self):
        generation = self.accept()
        self.store.claim_next()
        before = self.store.get_task('A', 'T')
        for result in [None, {}, {**self.result, 'total': 0}]:
            with self.subTest(result=result), self.assertRaises(ValueError):
                self.store.finish_done(generation, result)
        for reason in [None, '', ' \n', 1]:
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                self.store.finish_failed(generation, reason)
        self.assertEqual(self.store.get_task('A', 'T'), before)

    def test_commit_failure_rolls_back_claim_artifact_and_done(self):
        generation = self.accept()
        real_connect = sqlite3.connect
        def failing_connect(*args, **kwargs):
            return real_connect(*args, **kwargs, factory=CommitFailure)
        operations = [lambda: self.store.claim_next(),
                      lambda: self.store.save_artifact(generation, 'x', 'value'),
                      lambda: self.store.finish_done(generation, self.result)]
        for index, operation in enumerate(operations):
            if index == 1:
                self.store.claim_next()
            before = self.store.get_task('A', 'T')
            with patch.object(task_store.sqlite3, 'connect', side_effect=failing_connect):
                with self.assertRaisesRegex(sqlite3.OperationalError, 'injected commit failure'):
                    operation()
            self.assertEqual(self.store.get_task('A', 'T'), before)
        self.assertTrue(self.store.finish_done(generation, self.result))

    def test_concurrent_claims_do_not_claim_same_record(self):
        generation = self.accept()
        barrier = Barrier(2)
        stores = [task_store.TaskStore(self.temp.name) for _ in range(2)]
        def claim(index):
            barrier.wait(timeout=10)
            return stores[index].claim_next()
        with ThreadPoolExecutor(max_workers=2) as executor:
            claimed = list(executor.map(claim, range(2)))
        self.assertEqual([r['generationId'] for r in claimed if r], [generation])
        self.assertEqual(claimed.count(None), 1)

    def test_completion_and_replacement_follow_commit_order(self):
        for completion_first in [True, False]:
            with self.subTest(completion_first=completion_first):
                generation = self.accept()
                self.store.claim_next()
                committed = Event()
                second_store = task_store.TaskStore(self.temp.name)
                def finish():
                    if not completion_first:
                        self.assertTrue(committed.wait(timeout=10))
                    result = self.store.finish_done(generation, self.result)
                    if completion_first:
                        committed.set()
                    return result
                def replace():
                    if completion_first:
                        self.assertTrue(committed.wait(timeout=10))
                    second_store.accept_task(dict(bsnEvtId='A', taskId='new', userNo='U'))
                    if not completion_first:
                        committed.set()
                with ThreadPoolExecutor(max_workers=2) as executor:
                    finished = executor.submit(finish)
                    replaced = executor.submit(replace)
                    self.assertEqual(finished.result(timeout=15), completion_first)
                    replaced.result(timeout=15)
                saved = self.store.get_task('A', 'T')
                self.assertEqual(saved['status'], 'DONE' if completion_first else 'TERMINATED')
                self.assertEqual(saved['result'], self.result if completion_first else None)
                self.store.claim_next()
                self.store.finish_failed(self.store.get_task('A', 'new')['generationId'], '失败')


if __name__ == '__main__':
    unittest.main()
