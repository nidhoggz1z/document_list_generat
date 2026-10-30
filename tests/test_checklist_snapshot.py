import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import main
from docx_reader import read_docx
from test_service import make_docx


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.names = ['流动资金贷款', '流动资金循环贷款', '外汇流动资金贷款']
        for name in self.names:
            make_docx(self.root / (name + '材料清单.docx'), name + '完整正文')
        self.store = main.DocumentStore(self.root)

    def test_multi_product_snapshot_is_consistent(self):
        result = self.store.snapshot([' ' + self.names[1] + ' ', self.names[0]])
        self.assertEqual([r['name'] for r in result], [self.names[1], self.names[0]])
        for item in result:
            raw = (self.root / item['filename']).read_bytes()
            self.assertEqual(item['raw_bytes'], raw)
            self.assertEqual(item['content_hash'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(item['content'], read_docx(io.BytesIO(raw)))
        make_docx(self.root / result[0]['filename'], '新内容')
        self.assertEqual(result[0]['content'], self.names[1] + '完整正文')
        self.assertEqual(self.store.snapshot([self.names[1]])[0]['content'], '新内容')

    def test_changes_during_read_reject_mixed_snapshot_and_recover(self):
        original = Path.read_bytes
        changed = False
        def replacing(path):
            nonlocal changed
            data = original(path)
            if not changed and path.parent == self.root:
                changed = True
                make_docx(self.root / (self.names[1] + '材料清单.docx'), '替换的新内容')
            return data
        with patch.object(Path, 'read_bytes', replacing):
            with self.assertRaises(main.RetrievalError):
                self.store.snapshot(self.names)
        self.assertEqual(self.store.snapshot(self.names)[1]['content'], '替换的新内容')

    def test_actual_read_failure_is_not_masked_by_cached_content(self):
        with patch.object(Path, 'read_bytes', return_value=b'corrupt actual bytes'):
            with self.assertRaises(main.RetrievalError):
                self.store.snapshot(self.names)
        self.assertEqual(len(self.store.snapshot(self.names)), 3)

    def test_missing_duplicate_corrupt_and_invalid_requests_fail(self):
        for names in [[], '流动资金贷款', [None], [''], ['../产品'], [self.names[0], ' ' + self.names[0]],
                      [self.names[0], '流贷'], [self.names[0], '流动 资金贷款']]:
            with self.subTest(names=names), self.assertRaises(main.RetrievalError):
                self.store.snapshot(names)
        duplicate = self.root / (self.names[0] + '_材料清单.docx')
        make_docx(duplicate, '重复')
        with self.assertRaises(main.RetrievalError):
            self.store.snapshot(self.names)
        duplicate.unlink()
        (self.root / (self.names[1] + '材料清单.docx')).write_bytes(b'bad')
        with self.assertRaises(main.RetrievalError):
            self.store.snapshot(self.names)

    def test_exact_names_and_caller_mutation_do_not_affect_retrieve(self):
        result = self.store.snapshot(self.names)
        self.assertEqual(len(result), 3)
        result[0]['content'] = '调用者修改'
        self.assertEqual(self.store.retrieve(self.names[0]), self.names[0] + '完整正文')
        for name in ['流贷', '流动资金贷款材料清单', '流动资金贷款.docx']:
            with self.assertRaises(main.RetrievalError):
                self.store.snapshot([name])

    def test_all_real_checklist_snapshots_match_actual_bytes(self):
        store = main.DocumentStore(Path(__file__).resolve().parents[1] / 'list')
        names = list(store.documents)
        self.assertEqual(len(names), 75)
        snapshots = store.snapshot(names)
        self.assertEqual([s['name'] for s in snapshots], names)
        for item in snapshots:
            self.assertEqual(item['content_hash'], hashlib.sha256(item['raw_bytes']).hexdigest())
            self.assertEqual(item['content'], store.retrieve(item['name']))
            self.assertEqual(item['content'], read_docx(io.BytesIO(item['raw_bytes'])))


if __name__ == '__main__':
    unittest.main()
