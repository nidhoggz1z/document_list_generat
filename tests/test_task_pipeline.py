import asyncio
import hashlib
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from main import DocumentStore
from task_models import TaskResult
from material_tree import iter_materials
from task_store import TaskStore
from model_client import ModelReply
from approval_extractor import extract_approval
from pdf_fixtures import make_pdf
from test_checklist_rule_inventory import snapshot, HEADER
from test_material_generation import decision
from test_phrase_materials import workbook, FILENAME

pipeline = __import__('task_pipeline') if importlib.util.find_spec('task_pipeline') else None


class PipelineModel:
    settings = SimpleNamespace(max_retries=0, secret='do-not-archive-model-key')

    def __init__(self):
        self.calls = []
        self.fail_batch = None
        self.unreadable = False
        self.zero = False
        self.approval_text = '甲 乙 批复全文'

    async def complete(self, stage, messages):
        self.calls.append(stage)
        if stage == 'extract':
            data = dict(pages=[dict(page_number=i, text=self.approval_text, tables=[],
                                    readable=not self.unreadable) for i in (1, 2)],
                saleable_products=[dict(name=n, page_number=1, source=n, conditions='') for n in ('甲', '乙')], facts=[])
        else:
            payload = json.loads(messages[1]['content'])
            if 'extractedProducts' in payload:
                return ModelReply(json.dumps({'matches': [dict(extractedProductName=name, checklistProductName='')
                    for name in payload['extractedProducts']]}), 'stop')
            if 'requestedRowKeys' in payload:
                return ModelReply(json.dumps({'decisions': [dict(rowKey=k, scope='NONE', matches=[], products=[])
                    for k in payload['requestedRowKeys']]}), 'stop')
            rules = {r['ruleKey']: r for r in payload['rules']}
            data = {'decisions': [decision(rules[k], payload['resolvedContext'])
                                  for k in payload['requestedRuleKeys']]}
            for d in data['decisions']:
                if self.zero:
                    d.update(action='CHECK', instances=[])
            if self.calls.count('generate') == self.fail_batch:
                return ModelReply('secret provider malformed output', 'stop')
        return ModelReply(json.dumps(data, ensure_ascii=False), 'stop')


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(pipeline, 'T20 pipeline is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lists = self.root / 'list'
        self.lists.mkdir()
        self.create_lists()
        self.documents = DocumentStore(self.lists)
        self.store = TaskStore(self.root / 'storage')
        self.business = dict(bsnEvtId='B', userNo='U', taskId='T')
        self.raw = make_pdf(('text', 'scan'))
        self.record = self.submit()
        self.model = PipelineModel()

    def create_lists(self, zero=False):
        (self.lists / FILENAME).write_bytes(workbook())
        for name in ('甲', '乙'):
            snap = snapshot([HEADER, ['1', '基本资料'], ['1.1', '借款人基本资料'],
                ['1', '营业执照', '核查项' if zero else '必备', '', ''], ['9', '最后一类']], name)
            (self.lists / (name + '材料清单.docx')).write_bytes(snap['raw_bytes'])

    def submit(self):
        gen, _ = self.store.accept_task(self.business, source_document_hash=hashlib.sha256(self.raw).hexdigest())
        path = self.store.generation_path(gen)
        path.parent.mkdir(parents=True)
        path.write_bytes(self.raw)
        return self.store.claim_next()

    def runner(self, **kwargs):
        return pipeline.TaskPipeline(self.store, self.documents, self.model, batch_size=1, **kwargs)

    def saved(self):
        return self.store.get_task('B', 'T')

    async def test_uncertain_exclusion_is_not_restored_by_final_pipeline_validation(self):
        async def exclude(business, approval, snaps, rules, facts, client, *args, **kwargs):
            decisions = [decision(rule, facts) for rule in rules]
            for item in decisions:
                item.update(action='EXCLUDE', instances=[])
            return decisions

        with self.assertRaisesRegex(pipeline.TaskPipelineError, '结果投影'):
            await self.runner(generator=exclude).run_task(self.record)
        artifact = self.saved()['artifacts']['decisions']
        decisions = json.loads(Path(artifact['path']).read_text(encoding='utf-8'))['data']
        self.assertTrue(all(d['action'] == 'EXCLUDE' and not d['instances'] for d in decisions))

    async def test_two_products_full_pipeline_and_generation_artifacts(self):
        data = await self.runner().run_task(self.record)
        TaskResult.model_validate(data)
        self.assertNotIn('sourceDocumentHash', data)
        self.assertEqual(self.saved()['sourceDocumentHash'], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(data['total'], 2)
        self.assertEqual([p['productName'] for p in data['products']], ['甲', '乙'])
        self.assertEqual(self.model.calls, ['extract', 'generate', 'generate', 'generate'])
        saved = self.saved()
        self.assertEqual(saved['status'], 'PROCESSING')
        self.assertIsNone(saved['result'])
        self.assertEqual(set(saved['artifacts']), {'approval', 'checklists', 'rules', 'facts', 'decisions',
            'phrase-library', 'phrase-decisions', 'merged-materials', 'directories', 'product-matches'})
        for ref in saved['artifacts'].values():
            path = Path(ref['path'])
            self.assertTrue(path.is_relative_to(Path(self.record['pdfPath']).parent))
            raw = path.read_bytes()
            self.assertEqual(ref['sha256'], hashlib.sha256(raw).hexdigest())
            artifact = json.loads(raw)
            self.assertEqual(artifact['generationId'], self.record['generationId'])
            self.assertNotIn(self.model.settings.secret, raw.decode())
        manifest = json.loads(Path(saved['artifacts']['checklists']['path']).read_text('utf-8'))['data']
        for snap in manifest:
            self.assertEqual(hashlib.sha256(Path(snap['path']).read_bytes()).hexdigest(), snap['content_hash'])
        approval = json.loads(Path(saved['artifacts']['approval']['path']).read_text('utf-8'))['data']
        self.assertEqual([p['page_number'] for p in approval['pages']], [1, 2])
        self.assertTrue(self.store.finish_done(self.record['generationId'], data))

    async def test_name_variation_selects_existing_word_and_preserves_original_product(self):
        original = (self.lists / '甲材料清单.docx').read_bytes()
        normal = self.model.complete
        async def respond(stage, messages):
            if stage == 'extract':
                reply = await normal(stage, messages)
                value = json.loads(reply.content)
                value['pages'][0]['text'] = value['pages'][0]['text'].replace('甲', '甲别名')
                value['saleable_products'][0].update(name='甲别名', source='甲别名')
                return ModelReply(json.dumps(value), 'stop')
            payload = json.loads(messages[1]['content'])
            if 'extractedProducts' in payload:
                return ModelReply(json.dumps({'matches': [dict(extractedProductName='甲别名', checklistProductName='甲')]}), 'stop')
            return await normal(stage, messages)
        self.model.complete = respond
        data = await self.runner().run_task(self.record)
        self.assertEqual(data['products'][0], dict(extractedProductName='甲别名', productName='甲别名', checklistFileName='甲材料清单.docx'))
        self.assertEqual(data['total'], 2)
        self.assertEqual(next(iter_materials(data['items']))['productName'], '甲别名')
        self.assertEqual((self.lists / '甲材料清单.docx').read_bytes(), original)
        artifact = self.saved()['artifacts']['product-matches']
        matches = json.loads(Path(artifact['path']).read_text(encoding='utf-8'))['data']
        self.assertEqual(matches[0]['checklistProductName'], '甲')

    async def test_missing_library_fails_generation_but_word_retrieval_still_works(self):
        (self.lists / FILENAME).unlink()
        self.assertIn('营业执照', self.documents.retrieve('甲'))
        with self.assertRaisesRegex(pipeline.TaskPipelineError, '话术库快照.*缺失'):
            await self.runner().run_task(self.record)
        self.assertIsNone(self.saved()['result'])

    async def test_whole_bundle_output_and_original_excel_snapshot(self):
        original = (self.lists / FILENAME).read_bytes()
        normal = self.model.complete
        async def respond(stage, messages):
            if stage != 'generate' or 'requestedRowKeys' not in json.loads(messages[1]['content']):
                return await normal(stage, messages)
            payload = json.loads(messages[1]['content'])
            # No actual phrase hit: changing the library mid-task must not change its context.
            (self.lists / FILENAME).write_bytes(workbook([['新版', '新话术', '2.2 新合同']]))
            self.assertEqual(len(payload['phraseRules']), 2)
            return ModelReply(json.dumps({'decisions': [dict(rowKey=k, scope='NONE', matches=[], products=[])
                for k in payload['requestedRowKeys']]}), 'stop')
        self.model.complete = respond
        data = await self.runner().run_task(self.record)
        ref = self.saved()['artifacts']['phrase-library']
        manifest = json.loads(Path(ref['path']).read_text('utf-8'))['data']
        self.assertEqual(Path(manifest['path']).read_bytes(), original)
        self.assertEqual(manifest['content_hash'], hashlib.sha256(original).hexdigest())
        self.assertEqual(data['total'], 2)

    async def test_supplements_can_make_all_check_word_result_nonempty(self):
        self.create_lists(zero=True)
        self.model.zero = True
        self.model.approval_text = '甲 乙 由甲公司提供连带责任保证担保，股权融资款到账，并提供验资报告'
        normal = self.model.complete
        async def respond(stage, messages):
            if stage != 'generate' or 'requestedRowKeys' not in json.loads(messages[1]['content']):
                return await normal(stage, messages)
            context = json.loads(messages[1]['content'])
            ds = []
            for row in context['phraseRules']:
                ds.append(dict(rowKey=row['rowKey'], scope='COMMON',
                    matches=[dict(page_number=1, quote=row['phrase'])], products=[dict(productName=name,
                        materials=[dict(materialKey=m['materialKey'], applicability='APPLICABLE', categoryPath=['9'], instances=[dict(objectKey='', subject='',
                            materialName=m['name'], requirement='', wordRuleKey='')]) for m in row['materials']])
                        for name in ('甲', '乙')]))
            return ModelReply(json.dumps({'decisions': ds}, ensure_ascii=False), 'stop')
        self.model.complete = respond
        data = await self.runner().run_task(self.record)
        self.assertEqual(data['total'], 8)
        self.assertEqual([i['itemId'] for i in iter_materials(data['items'])], [f'R{i:04d}' for i in range(1, 9)])
        self.assertEqual([i['productName'] for i in iter_materials(data['items'])], ['甲'] * 4 + ['乙'] * 4)
        merged_ref = self.saved()['artifacts']['merged-materials']
        merged = json.loads(Path(merged_ref['path']).read_text('utf-8'))['data']
        self.assertEqual(sum(r['sourceRuleId'].startswith('话术库:') for r in merged['rules']), 6)
        self.assertEqual(sum(r['sourceRuleId'] == '1' for r in merged['rules']), 2)
        self.assertTrue(all('sourceRuleId' not in i and 'reason' not in i
                            for i in iter_materials(data['items'])))
        self.assertEqual(data['items'][0]['categories'][-1]['categoryId'], '9')

    async def test_one_failed_product_fails_entire_pipeline(self):
        (self.lists / '乙材料清单.docx').unlink()
        with self.assertRaises(pipeline.TaskPipelineError):
            await self.runner().run_task(self.record)
        self.assertEqual(self.model.calls, ['extract', 'generate'])
        self.assertIsNone(self.saved()['result'])

    async def test_page_or_extraction_failure_does_not_generate(self):
        self.model.unreadable = True
        with self.assertRaises(pipeline.TaskPipelineError):
            await self.runner().run_task(self.record)
        self.assertEqual(self.model.calls, ['extract'])
        self.model.calls.clear()
        Path(self.record['pdfPath']).write_bytes(b'broken')
        # Keep the recorded hash consistent to reach the real PDF parser.
        self.record['sourceDocumentHash'] = hashlib.sha256(b'broken').hexdigest()
        with self.assertRaises(pipeline.TaskPipelineError):
            await self.runner().run_task(self.record)
        self.assertEqual(self.model.calls, [])

    async def test_input_hash_mismatch_stops_before_model(self):
        Path(self.record['pdfPath']).write_bytes(make_pdf(('mixed',)))
        with self.assertRaises(pipeline.TaskPipelineError):
            await self.runner().run_task(self.record)
        self.assertEqual(self.model.calls, [])

    async def test_snapshot_remains_original_after_catalog_update(self):
        from material_generator import generate_material_decisions
        async def updating(*args, **kwargs):
            self.create_lists(zero=True)
            return await generate_material_decisions(*args, **kwargs)
        data = await self.runner(generator=updating).run_task(self.record)
        self.assertEqual(data['total'], 2)
        ref = self.saved()['artifacts']['checklists']
        manifest = json.loads(Path(ref['path']).read_text('utf-8'))['data']
        self.assertTrue(all('必备' in s['content'] for s in manifest))
        self.assertNotEqual(Path(manifest[0]['path']).read_bytes(),
                            (self.lists / '甲材料清单.docx').read_bytes())

    async def test_injected_generator_cannot_skip_final_coverage_check(self):
        async def missing(*args, **kwargs):
            return []
        with self.assertRaises(pipeline.TaskPipelineError):
            await self.runner(generator=missing).run_task(self.record)
        self.assertNotIn('decisions', self.saved()['artifacts'])

    async def test_replacement_at_final_projection_discards_result(self):
        original = pipeline.project_material_result
        def replacing(*args):
            result = original(*args)
            self.store.accept_task(self.business)
            return result
        with patch.object(pipeline, 'project_material_result', replacing):
            with self.assertRaises(asyncio.CancelledError):
                await self.runner().run_task(self.record)
        self.assertEqual(self.saved()['status'], 'PENDING')
        self.assertIsNone(self.saved()['result'])
        self.assertEqual(self.saved()['artifacts'], {})

    async def test_file_write_failure_stops_before_next_stage(self):
        with patch.object(pipeline, '_write', side_effect=OSError(self.model.settings.secret)):
            with self.assertRaises(pipeline.TaskPipelineError) as error:
                await self.runner().run_task(self.record)
        self.assertNotIn(self.model.settings.secret, str(error.exception))
        self.assertEqual(self.model.calls, ['extract'])
        self.assertEqual(self.saved()['artifacts'], {})

    async def test_failed_later_batch_returns_no_partial_result(self):
        self.model.fail_batch = 2
        with self.assertRaises(pipeline.TaskPipelineError):
            await self.runner().run_task(self.record)
        self.assertEqual(self.model.calls, ['extract', 'generate', 'generate'])
        self.assertNotIn('decisions', self.saved()['artifacts'])
        self.assertIsNone(self.saved()['result'])

    async def test_zero_materials_fail(self):
        self.create_lists(zero=True)
        self.model.zero = True
        with self.assertRaises(pipeline.TaskPipelineError):
            await self.runner().run_task(self.record)
        self.assertIsNone(self.saved()['result'])

    async def test_replacement_between_stages_stops_next_model_call(self):
        async def replacing(*args, **kwargs):
            value = await extract_approval(*args, **kwargs)
            self.store.accept_task(self.business)
            return value
        with self.assertRaises(asyncio.CancelledError):
            await self.runner(extractor=replacing).run_task(self.record)
        self.assertEqual(self.model.calls, ['extract'])
        self.assertEqual(self.saved()['artifacts'], {})
        self.assertEqual(self.saved()['status'], 'PENDING')

    async def test_rejected_stage_registration_cancels(self):
        with patch.object(self.store, 'save_artifact', return_value=False):
            with self.assertRaises(asyncio.CancelledError):
                await self.runner().run_task(self.record)
        self.assertEqual(self.model.calls, ['extract'])

    async def test_artifact_write_failure_is_safe_and_stops_pipeline(self):
        with patch.object(self.store, 'save_artifact', side_effect=OSError(self.model.settings.secret)):
            with self.assertRaises(pipeline.TaskPipelineError) as error:
                await self.runner().run_task(self.record)
        self.assertNotIn(self.model.settings.secret, str(error.exception))
        self.assertEqual(self.model.calls, ['extract'])

    async def test_cancel_before_start_and_in_flight_model(self):
        with self.assertRaises(asyncio.CancelledError):
            await self.runner().run_task(self.record, cancel_check=lambda: True)
        self.assertEqual(self.model.calls, [])
        started, stopped = asyncio.Event(), asyncio.Event()
        async def waiting(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        self.model.complete = waiting
        task = asyncio.create_task(self.runner().run_task(self.record))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stopped.is_set())

    async def test_thread_reader_stops_before_cancellation_returns(self):
        started, stopped = threading.Event(), threading.Event()
        def reading(path, cancel_check):
            started.set()
            try:
                while not cancel_check():
                    stopped.wait(0.01)
                raise asyncio.CancelledError()
            finally:
                stopped.set()
        task = asyncio.create_task(self.runner(page_reader=reading).run_task(self.record))
        self.assertTrue(await asyncio.to_thread(started.wait, 5))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        self.assertTrue(stopped.is_set())
        self.assertEqual(self.model.calls, [])

    async def test_repeated_cancellation_still_joins_reader(self):
        started, winding_down = asyncio.Event(), asyncio.Event()
        release, stopped = threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        def reading(path, cancel_check):
            loop.call_soon_threadsafe(started.set)
            try:
                while not cancel_check():
                    stopped.wait(0.01)
            except asyncio.CancelledError:
                pass
            loop.call_soon_threadsafe(winding_down.set)
            release.wait(5)
            stopped.set()
            raise asyncio.CancelledError()
        task = asyncio.create_task(self.runner(page_reader=reading).run_task(self.record))
        try:
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            await asyncio.wait_for(winding_down.wait(), 5)
            task.cancel()
            # Advance the loop deterministically through both cancellation handlers.
            for _ in range(10):
                await asyncio.sleep(0)
            self.assertFalse(task.done(), 'cancellation returned while reader still active')
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.to_thread(stopped.wait, 5)


if __name__ == '__main__':
    unittest.main()
