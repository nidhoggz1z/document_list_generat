import asyncio
import copy
import importlib.util
import json
import sys
import unittest
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from model_client import ModelReply, ModelCallError
from pdf_reader import PdfPage

if importlib.util.find_spec('approval_extractor'):
    import approval_extractor as extractor
else:
    extractor = None


class FakeModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    async def complete(self, stage, messages):
        self.calls.append((stage, messages))
        reply = next(self.replies)
        if isinstance(reply, BaseException):
            raise reply
        return reply if isinstance(reply, ModelReply) else ModelReply(json.dumps(reply, ensure_ascii=False), 'stop')


def payload(number=1):
    text = '授信品种：短期贷款；可售产品：流动资金贷款；额度100万元；无担保'
    return {
        'pages': [{'page_number': number, 'text': text, 'tables': [], 'readable': True}],
        'saleable_products': [{'name': '流动资金贷款', 'page_number': number,
                              'source': '可售产品：流动资金贷款', 'conditions': '额度100万元'}],
        'facts': [{'name': '担保', 'value': '无担保', 'present': True, 'negated': True,
                   'page_number': number, 'source': '无担保',
                   'productName': '流动资金贷款', 'subject': '借款人'}]}


class ExtractionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(extractor, 'T13 approval_extractor is missing')
        self.pages = [PdfPage(1, '本地文本', b'png-one'), PdfPage(2, '', b'png-two')]

    async def test_saleable_products_and_page_coverage(self):
        first, second = payload(), payload(2)
        second['pages'][0]['text'] = '可售产品：流动资金贷款，额度200万元；可售产品：流动资金循环贷款'
        second['pages'][0]['tables'] = [['可售产品', '额度'], ['流动资金贷款', '200万元']]
        second['saleable_products'][0]['conditions'] = '额度200万元'
        second['saleable_products'].append(dict(name='流动资金循环贷款', page_number=2,
            source='可售产品：流动资金循环贷款', conditions='单独条件'))
        second['facts'] = []
        model = FakeModel([first, second])
        result = await extractor.extract_approval(self.pages, model,
            ['流动资金贷款', '流动资金循环贷款'], lambda: False, batch_size=1)
        self.assertEqual([p['productName'] for p in result['products']], ['流动资金贷款', '流动资金循环贷款'])
        self.assertEqual([r['conditions'] for r in result['products'][0]['creditRecords']], ['额度100万元', '额度200万元'])
        self.assertEqual([p['page_number'] for p in result['pages']], [1, 2])
        self.assertEqual(result['pages'][1]['tables'], second['pages'][0]['tables'])
        self.assertTrue(result['facts'][0]['negated'])
        self.assertTrue(result['facts'][0]['present'])
        self.assertEqual(result['facts'][0]['subject'], '借款人')
        for index, (stage, messages) in enumerate(model.calls):
            self.assertEqual(stage, 'extract')
            content = messages[-1]['content']
            self.assertTrue(any(c['type'] == 'image_url' for c in content))
            self.assertIn('data:image/png;base64,', json.dumps(content))
            self.assertIn('本地文本' if index == 0 else 'page_number', json.dumps(content, ensure_ascii=False))
        self.assertIn('流动资金贷款', json.dumps(model.calls[1], ensure_ascii=False))

    async def test_all_pages_in_one_request_by_default(self):
        response = payload()
        response['pages'].append(dict(page_number=2, text='附页正文', tables=[], readable=True))
        result = await extractor.extract_approval(self.pages, FakeModel([response]), [], lambda: False)
        self.assertEqual(len(result['pages']), 2)

    async def test_invalid_reference_retries_with_original_page_images_and_context(self):
        invalid = payload()
        invalid['facts'][0]['source'] = '不存在的原文'
        model = FakeModel([invalid, payload()])
        model.settings = SimpleNamespace(max_retries=1)
        result = await extractor.extract_approval(self.pages[:1], model, [], lambda: False)
        self.assertEqual(len(result['pages']), 1)
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(model.calls[0][1][1], model.calls[1][1][1])
        self.assertIn('SOURCE_NOT_CONTIGUOUS_IN_PAGE', model.calls[1][1][-1]['content'])

    async def test_retry_budget_does_not_accept_unfixed_references(self):
        invalid = payload()
        invalid['facts'][0]['source'] = '不存在的原文'
        model = FakeModel([copy.deepcopy(invalid), copy.deepcopy(invalid)])
        model.settings = SimpleNamespace(max_retries=1)
        with self.assertRaisesRegex(extractor.ApprovalExtractionError, 'SOURCE_NOT_CONTIGUOUS_IN_PAGE'):
            await extractor.extract_approval(self.pages[:1], model, [], lambda: False)
        self.assertEqual(len(model.calls), 2)

    async def test_prompt_defines_key_facts_and_source_order_without_dropping_other_facts(self):
        response = payload()
        response['facts'].append(dict(response['facts'][0], name='自定义条件', negated=False))
        model = FakeModel([response])
        result = await extractor.extract_approval(self.pages[:1], model, [], lambda: False)
        prompt = model.calls[0][1][0]['content']
        for name in ['担保方式', '担保人姓名', '共同担保人姓名', '客户名称', '从合同编号', '押品存在', '押品名称', '押品类型', '原文顺序']:
            self.assertIn(name, prompt)
        self.assertEqual(result['facts'][1]['name'], '自定义条件')

    async def test_confirmed_blank_page_is_still_covered(self):
        response = payload()
        response['pages'].append(dict(page_number=2, text='', tables=[], readable=True))
        result = await extractor.extract_approval(self.pages, FakeModel([response]), [], lambda: False)
        self.assertEqual(result['pages'][1]['text'], '')

    async def test_cross_page_continuation_keeps_original_product(self):
        second = payload(2)
        second['pages'][0]['text'] = '续上页：额度200万元，须落实抵押'
        second['saleable_products'][0].update(source='额度200万元，须落实抵押', conditions='额度200万元，须落实抵押')
        second['facts'] = []
        result = await extractor.extract_approval(self.pages, FakeModel([payload(), second]), [], lambda: False, batch_size=1)
        self.assertEqual(len(result['products']), 1)
        self.assertEqual(result['products'][0]['creditRecords'][1]['page_number'], 2)

    async def test_real_transport_receives_text_and_image_bytes(self):
        import base64
        import httpx
        from model_client import ModelClient
        from task_settings import TaskSettings
        seen = []
        def handler(request):
            body = json.loads(request.content)
            self.assertEqual(body['model'], 'fixture-vision')
            parts = body['messages'][-1]['content']
            self.assertIn('本地文本', str(parts))
            encoded = next(p['image_url']['url'] for p in parts if p['type'] == 'image_url')
            self.assertEqual(base64.b64decode(encoded.split(',', 1)[1]), self.pages[0].image_png)
            seen.append(body)
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
                'message': {'content': json.dumps(payload(), ensure_ascii=False)}}]})
        settings = TaskSettings.from_env({'EXTRACT_MODEL_API_URL': 'https://fixture.test/completions',
            'EXTRACT_MODEL_API_KEY': 'fixture-only', 'EXTRACT_MODEL_NAME': 'fixture-vision'})
        async with ModelClient(settings, transport=httpx.MockTransport(handler)) as model:
            result = await extractor.extract_approval(self.pages[:1], model, [], lambda: False)
        self.assertEqual(len(seen), 1)
        self.assertEqual(result['products'][0]['productName'], '流动资金贷款')

    async def test_invalid_outputs_fail_entire_extraction(self):
        variants = []
        for field, value in [('pages', []), ('saleable_products', []), ('facts', 'bad')]:
            item = payload(); item[field] = value; variants.append(item)
        for field, value in [('readable', False), ('readable', 1), ('page_number', 2),
                             ('text', None), ('tables', 'bad')]:
            item = payload(); item['pages'][0][field] = value; variants.append(item)
        item = payload(); item['pages'] *= 2; variants.append(item)
        for field, value in [('source', '编造的来源'), ('name', ''), ('conditions', None)]:
            item = payload(); item['saleable_products'][0][field] = value; variants.append(item)
        for field, value in [('present', 'true'), ('negated', None), ('productName', '另一个产品'),
                             ('source', '编造'), ('subject', None)]:
            item = payload(); item['facts'][0][field] = value; variants.append(item)
        item = payload(); item['facts'][0]['present'] = False; variants.append(item)
        variants.extend([{}, [], ModelReply('{', 'stop'), ModelReply('{}', 'length')])
        for item in variants:
            with self.subTest(item=item), self.assertRaises(extractor.ApprovalExtractionError):
                await extractor.extract_approval(self.pages[:1], FakeModel([item]), [], lambda: False)

    async def test_missing_fact_is_distinct_from_explicit_negative(self):
        response = payload()
        absent = copy.deepcopy(response['facts'][0])
        absent.update(name='担保人姓名', value='', present=False, negated=False, source='')
        response['facts'].append(absent)
        result = await extractor.extract_approval(self.pages[:1], FakeModel([response]), [], lambda: False)
        self.assertFalse(result['facts'][1]['present'])
        self.assertFalse(result['facts'][1]['negated'])

    async def test_catalog_never_substitutes_for_source_or_supplies_products(self):
        response = payload(); response['saleable_products'] = []
        with self.assertRaises(extractor.ApprovalExtractionError):
            await extractor.extract_approval(self.pages[:1], FakeModel([response]), ['流动资金贷款'], lambda: False)
        response = payload(); response['saleable_products'][0]['name'] = ' 短期贷款 '
        with self.assertRaises(extractor.ApprovalExtractionError):
            await extractor.extract_approval(self.pages[:1], FakeModel([response]), ['短期贷款'], lambda: False)

    async def test_cancellation_before_during_and_between_batches(self):
        model = FakeModel([payload()])
        with self.assertRaises(asyncio.CancelledError):
            await extractor.extract_approval(self.pages, model, [], lambda: True)
        self.assertFalse(model.calls)
        cancelled = False
        class CancellingModel(FakeModel):
            async def complete(self, *args):
                nonlocal cancelled
                result = await super().complete(*args)
                cancelled = True
                return result
        model = CancellingModel([payload()])
        with self.assertRaises(asyncio.CancelledError):
            await extractor.extract_approval(self.pages, model, [], lambda: cancelled, batch_size=1)
        self.assertEqual(len(model.calls), 1)
        started = asyncio.Event()
        class WaitingModel:
            async def complete(self, *args):
                started.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(extractor.extract_approval(self.pages, WaitingModel(), [], lambda: False))
        await started.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_later_failed_batch_and_invalid_input_publish_nothing(self):
        with self.assertRaises(ModelCallError):
            await extractor.extract_approval(self.pages, FakeModel([payload(), ModelCallError('失败')]), [], lambda: False, batch_size=1)
        for pages in [[], self.pages[::-1], [self.pages[0], self.pages[0]], self.pages[1:]]:
            model = FakeModel([])
            with self.subTest(pages=pages), self.assertRaises(extractor.ApprovalExtractionError):
                await extractor.extract_approval(pages, model, [], lambda: False)
            self.assertFalse(model.calls)


if __name__ == '__main__':
    unittest.main()
