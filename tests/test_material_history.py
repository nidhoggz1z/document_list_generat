"""Read-only migration of persisted flat DONE results using their own artifacts."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import main
from task_store import TaskStore
from material_tree import iter_materials, group_materials
from test_service import make_docx
from test_material_tree import leaf, metadata
from test_checklist_rule_inventory import snapshot, HEADER

history = __import__('material_history') if importlib.util.find_spec('material_history') else None


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(history, 'Historical tree conversion is not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = TaskStore(self.root / 'storage')
        self.ids = dict(bsnEvtId='B', userNo='U', taskId='T')
        self.generation = self.store.accept_task(self.ids)[0]
        self.store.claim_next()
        self.stages = self.store.generation_path(self.generation).parent / 'stages'
        self.stages.mkdir(parents=True)
        self.flat = dict(sourceDocumentHash='a' * 64, generatedAt='2026-10-26T02:00:00+00:00', total=2,
            products=[metadata()], items=[leaf(source='2.1.1'), leaf(source='1.1.1', number=2)])
        self.rules = [dict(productName='甲', ruleKey='r1', sourceRuleId='1.1.1', groups=[
                dict(sourceRuleId='1', title='原始基本资料'), dict(sourceRuleId='1.1', title='原始借款人资料')]),
            dict(productName='甲', ruleKey='r2', sourceRuleId='2.1.1', groups=[
                dict(sourceRuleId='2', title='原始合同资料'), dict(sourceRuleId='2.1', title='原始合同子类')])]

    def artifact(self, name, data):
        path = self.stages / (name + '.json')
        raw = json.dumps(dict(generationId=self.generation, data=data), ensure_ascii=False).encode('utf-8')
        path.write_bytes(raw)
        ref = dict(generationId=self.generation, path=str(path), sha256=hashlib.sha256(raw).hexdigest())
        self.store.save_artifact(self.generation, name, ref)
        return path

    def finish_legacy(self):
        # Old databases legitimately contain this shape; new finish_done requires a tree.
        with self.store._connection(write=True) as connection:
            connection.execute("UPDATE tasks SET status='DONE', result=? WHERE generationId=?",
                (json.dumps(self.flat, ensure_ascii=False), self.generation))
        return self.store.get_task('B', 'T')

    def convert(self, record):
        return history.historical_result(record, self.store)

    def test_restore_source_directories_and_renumber_without_writing_history(self):
        self.artifact('rules', self.rules)
        record = self.finish_legacy()
        before = copy.deepcopy(record)
        converted = self.convert(record)
        self.assertEqual([c['categoryName'] for c in converted['items'][0]['categories']],
                         ['原始基本资料', '原始合同资料'])
        leaves = list(iter_materials(converted['items']))
        self.assertEqual([i['sourceRuleId'] for i in leaves], ['1.1.1', '2.1.1'])
        self.assertEqual([i['itemId'] for i in leaves], ['R0001', 'R0002'])
        self.assertEqual(converted['generatedAt'], self.flat['generatedAt'])
        self.assertEqual(converted['sourceDocumentHash'], self.flat['sourceDocumentHash'])
        self.assertEqual(self.store.get_task('B', 'T'), before)
        self.assertEqual(record, before)

    def test_missing_or_corrupt_artifacts_keep_every_leaf_as_unclassified(self):
        for corrupt in (False, True):
            if corrupt:
                # Reopen a PROCESSING fixture so an artifact can be registered.
                with self.store._connection(write=True) as connection:
                    connection.execute("UPDATE tasks SET status='PROCESSING' WHERE generationId=?", (self.generation,))
                path = self.artifact('rules', self.rules)
                path.write_bytes(b'corrupt')
            converted = self.convert(self.finish_legacy())
            self.assertEqual(converted['items'][0]['categories'][0]['categoryName'], '未分类材料')
            self.assertEqual(len(list(iter_materials(converted['items']))), 2)

    def test_fallback_to_original_docx_snapshot_not_current_list(self):
        snap = snapshot([HEADER, ['1', '旧版资料'], ['1.1', '旧版借款人资料'],
            ['1.1.1', '企业章程', '必备', '', ''], ['2', '旧版合同'], ['2.1', '旧版签约'],
            ['2.1.1', '借款合同', '必备', '', '']], '甲')
        path = self.stages / 'checklist-0001.docx'
        path.write_bytes(snap['raw_bytes'])
        self.artifact('checklists', [{k: v for k, v in snap.items() if k != 'raw_bytes'} | {'path': str(path)}])
        converted = self.convert(self.finish_legacy())
        self.assertEqual(converted['items'][0]['categories'][0]['categoryName'], '旧版资料')

    def test_old_excel_only_material_falls_back_to_original_last_word_root(self):
        self.flat['items'][1]['sourceRuleId'] = '话术库:行15:材料2'
        rules = self.rules + [dict(productName='甲', ruleKey='excel', sourceRuleId='话术库:行15:材料2')]
        self.artifact('merged-materials', dict(rules=rules, decisions=[]))
        converted = self.convert(self.finish_legacy())
        self.assertEqual(converted['items'][0]['categories'][0]['categoryId'], '2')
        self.assertEqual(len(list(iter_materials(converted['items']))), 2)

    def test_ambiguous_same_source_id_is_not_assigned_to_arbitrary_category(self):
        self.rules[1]['sourceRuleId'] = '1.1.1'
        self.flat['items'] = [leaf()]
        self.flat['total'] = 1
        self.artifact('rules', self.rules)
        converted = self.convert(self.finish_legacy())
        self.assertEqual(converted['items'][0]['categories'][0]['categoryName'], '未分类材料')

    def test_same_source_id_can_be_disambiguated_with_original_instance(self):
        self.rules[1]['sourceRuleId'] = '1.1.1'
        self.flat.update(items=[leaf()], total=1)
        self.flat['items'][0]['materialName'] = '[甲]A企业章程（有效期内）'
        self.artifact('merged-materials', dict(rules=self.rules, decisions=[
            dict(ruleKey='r1', instances=[dict(materialName='企业章程', subject='A', requirement='有效期内')]),
            dict(ruleKey='r2', instances=[dict(materialName='借款合同', subject='', requirement='')])]))
        converted = self.convert(self.finish_legacy())
        self.assertEqual(converted['items'][0]['categories'][0]['categoryName'], '原始基本资料')

    def test_valid_hash_cannot_authorize_different_generation_artifact(self):
        raw = json.dumps(dict(generationId='0' * 32, data=self.rules), ensure_ascii=False).encode('utf-8')
        path = self.stages / 'wrong-generation.json'
        path.write_bytes(raw)
        self.store.save_artifact(self.generation, 'rules', dict(generationId=self.generation,
            path=str(path), sha256=hashlib.sha256(raw).hexdigest()))
        converted = self.convert(self.finish_legacy())
        self.assertEqual(converted['items'][0]['categories'][0]['categoryName'], '未分类材料')

    def test_invalid_merged_rule_list_falls_back_to_original_rules(self):
        self.artifact('rules', self.rules)
        self.artifact('merged-materials', dict(rules='旧版不完整索引', decisions=[]))
        converted = self.convert(self.finish_legacy())
        self.assertEqual(converted['items'][0]['categories'][0]['categoryName'], '原始基本资料')

    def test_artifact_reference_cannot_escape_generation_or_mix_generations(self):
        outside = self.root / 'outside.json'
        raw = json.dumps(dict(generationId=self.generation, data=self.rules), ensure_ascii=False).encode('utf-8')
        outside.write_bytes(raw)
        self.store.save_artifact(self.generation, 'rules', dict(generationId=self.generation,
            path=str(outside), sha256=hashlib.sha256(raw).hexdigest()))
        converted = self.convert(self.finish_legacy())
        self.assertEqual(converted['items'][0]['categories'][0]['categoryName'], '未分类材料')

    def test_query_returns_tree_without_reading_current_rules_or_calling_models(self):
        self.artifact('rules', self.rules)
        original = self.finish_legacy()
        documents = self.root / 'list'
        make_docx(documents / '测试材料清单.docx', '当前目录不能影响历史')
        app = main.create_app(documents, task_store=self.store)
        with TestClient(app) as client, \
             patch.object(main.DocumentStore, 'refresh', side_effect=AssertionError('current rules')), \
             patch.object(self.store, 'finish_done', side_effect=AssertionError('history write')):
            response = client.post('/api/v1/tasks/query', json=self.ids)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['data']['items'][0]['categories'][0]['categoryName'], '原始基本资料')
        self.assertEqual(self.store.get_task('B', 'T'), original)

    def test_saved_tree_keeps_legacy_fields_and_titles_over_http(self):
        saved = dict(self.flat, items=group_materials(self.flat['products'],
            [(item, []) for item in self.flat['items']], {}))
        self.finish_legacy()
        with self.store._connection(write=True) as connection:
            connection.execute('UPDATE tasks SET result=? WHERE generationId=?',
                (json.dumps(saved, ensure_ascii=False), self.generation))
        before = copy.deepcopy(self.store.get_task('B', 'T'))
        documents = self.root / 'list'
        make_docx(documents / '测试材料清单.docx', '测试目录')
        with TestClient(main.create_app(documents, task_store=self.store)) as client:
            response = client.post('/api/v1/tasks/query', json=self.ids)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data'], saved)
        self.assertEqual(self.store.get_task('B', 'T'), before)


if __name__ == '__main__':
    unittest.main()
