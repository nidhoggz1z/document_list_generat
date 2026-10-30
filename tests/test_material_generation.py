import asyncio
import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from model_client import ModelReply, ModelCallError
from material_facts import resolve_material_facts

generator = __import__('material_generator') if importlib.util.find_spec('material_generator') else None


def inputs(count=1):
    business = dict(bsnEvtId='B', userNo='U', taskId='T', cstNm='公司',
                    subcontracts=[dict(subcontractNum='S1', gnrNm='A'),
                                  dict(subcontractNum='S2', gnrNm='B')])
    approval = dict(pages=[dict(page_number=1, text='全部批复正文', tables=[['完整表格']])],
                    products=[dict(productName='甲', creditRecords=[])], facts=[])
    snapshots = [dict(name='甲', content='全部清单全文，包括非本批规则')]
    rules = [dict(productName='甲', ruleKey=f'甲:{i}', sourceRuleId=str(i),
                  kind='MATERIAL', cells=[str(i), '身份证明', '必备', '按对象提供', '依据'],
                  groups=[dict(sourceRuleId='1', title='基本资料'), dict(sourceRuleId='1.1', title='借款人资料')])
             for i in range(count)]
    return business, approval, snapshots, rules, resolve_material_facts(approval, business)


def decision(rule, context, kinds=()):
    selected = [o for o in context['objects'] if o['kind'] in kinds]
    evidence = [dict(type='RULE', ruleKey=rule['ruleKey'], quote=rule['cells'][1])]
    instances = []
    for obj in selected or [None]:
        refs = copy.deepcopy(evidence)
        if obj:
            refs.append(dict(type='OBJECT', objectKey=obj['objectKey'], source=obj['source']))
        instances.append(dict(objectKey=obj['objectKey'] if obj else '',
                              subject=obj['name'] if obj else '', materialName=rule['cells'][1],
                              requirement='', reason='原规则明确适用', evidence=refs))
    return dict(productName=rule['productName'], ruleKey=rule['ruleKey'], action='KEEP',
                applicability='APPLICABLE', objectKinds=list(kinds), scopeObjectKey='', reason='原规则明确适用',
                evidence=evidence, instances=instances, excludedObjects=[])


class FakeModel:
    def __init__(self, replies=(), responder=None, retries=2):
        self.replies = iter(replies)
        self.responder = responder
        self.settings = SimpleNamespace(max_retries=retries)
        self.calls = []

    async def complete(self, stage, messages):
        self.calls.append((stage, copy.deepcopy(messages)))
        response = self.responder(messages) if self.responder else next(self.replies)
        if isinstance(response, BaseException):
            raise response
        return response if isinstance(response, ModelReply) else ModelReply(json.dumps(response, ensure_ascii=False), 'stop')


class GenerationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(generator, 'T17 material_generator is missing')
        self.data = inputs()

    async def run_generate(self, model, **kwargs):
        return await generator.generate_material_decisions(*self.data, model, lambda: False, **kwargs)

    async def test_batches_keep_all_three_contexts(self):
        self.data = inputs(73)
        business, approval, snapshots, rules, context = self.data
        before = copy.deepcopy(self.data)
        by_key = {r['ruleKey']: r for r in rules}
        def respond(messages):
            payload = json.loads(messages[1]['content'])
            self.assertEqual(payload['businessData'], business)
            self.assertEqual(payload['approval'], approval)
            self.assertEqual(payload['checklists'], snapshots)
            self.assertEqual(payload['rules'], rules)
            self.assertEqual(payload['resolvedContext'], context)
            return {'decisions': [decision(by_key[k], context) for k in payload['requestedRuleKeys']]}
        model = FakeModel(responder=respond)
        result = await self.run_generate(model, batch_size=20)
        self.assertEqual([d['ruleKey'] for d in result], list(by_key))
        self.assertEqual(len(model.calls), 4)
        self.assertTrue(all(stage == 'generate' for stage, _ in model.calls))
        self.assertEqual(self.data, before)

    async def test_invalid_json_and_missing_rules_repaired_with_full_context(self):
        valid = {'decisions': [decision(self.data[3][0], self.data[4])]}
        model = FakeModel([ModelReply('not json', 'stop'), {'decisions': []}, valid])
        self.assertEqual(len(await self.run_generate(model)), 1)
        self.assertEqual(len(model.calls), 3)
        original = json.loads(model.calls[0][1][1]['content'])
        for _, messages in model.calls[1:]:
            self.assertEqual(json.loads(messages[1]['content']), original)
            self.assertIn('修复', messages[-1]['content'])

    async def test_retry_exhaustion_and_zero_budget(self):
        for retries in (0, 2):
            model = FakeModel([{'decisions': []}] * (retries + 1), retries=retries)
            with self.assertRaises(generator.MaterialGenerationError):
                await self.run_generate(model)
            self.assertEqual(len(model.calls), retries + 1)

    async def test_truncation_and_transport_failure_are_not_repaired(self):
        for response in (ModelReply('{}', 'length'), ModelCallError('失败', finish_reason='length'),
                         ModelCallError('网络重试耗尽')):
            model = FakeModel([response])
            with self.assertRaises((generator.MaterialGenerationError, ModelCallError)):
                await self.run_generate(model)
            self.assertEqual(len(model.calls), 1)

    async def test_later_batch_failure_does_not_return_partial_result(self):
        self.data = inputs(2)
        model = FakeModel([{'decisions': [decision(self.data[3][0], self.data[4])]},
                           {'decisions': []}], retries=0)
        with self.assertRaises(generator.MaterialGenerationError):
            await self.run_generate(model, batch_size=1)
        self.assertEqual(len(model.calls), 2)

    async def test_no_capacity_limit_or_silent_context_truncation(self):
        self.data[1]['pages'][0]['text'] = '完整正文' * 100000
        model = FakeModel([{'decisions': [decision(self.data[3][0], self.data[4])]}])
        await self.run_generate(model)
        self.assertEqual(json.loads(model.calls[0][1][1]['content'])['approval'], self.data[1])

    async def test_cancel_before_call_and_before_repair(self):
        model = FakeModel([{'decisions': []}])
        with self.assertRaises(asyncio.CancelledError):
            await generator.generate_material_decisions(*self.data, model, lambda: True)
        self.assertEqual(model.calls, [])
        with self.assertRaises(asyncio.CancelledError):
            await generator.generate_material_decisions(*self.data, model, lambda: bool(model.calls))
        self.assertEqual(len(model.calls), 1)

    async def test_cancel_in_flight(self):
        started = asyncio.Event()
        class WaitingModel:
            settings = SimpleNamespace(max_retries=2)
            async def complete(self, *args):
                started.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(self.run_generate(WaitingModel()))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_cancel_between_batches(self):
        self.data = inputs(2)
        model = FakeModel([{'decisions': [decision(self.data[3][0], self.data[4])]}])
        checks_after_reply = 0
        def cancel():
            nonlocal checks_after_reply
            if model.calls:
                checks_after_reply += 1
            return checks_after_reply >= 2
        with self.assertRaises(asyncio.CancelledError):
            await generator.generate_material_decisions(*self.data, model, cancel, batch_size=1)
        self.assertEqual(len(model.calls), 1)

    async def test_multiple_products_keep_context_and_restore_rule_order(self):
        business, approval, snapshots, rules, _ = inputs(2)
        approval['products'].append(dict(productName='乙', creditRecords=[]))
        snapshots.append(dict(name='乙', content='乙完整清单'))
        rules[1].update(productName='乙', ruleKey='乙:1')
        context = resolve_material_facts(approval, business)
        self.data = business, approval, snapshots, rules, context
        model = FakeModel([{'decisions': [decision(r, context) for r in reversed(rules)]}])
        result = await self.run_generate(model)
        self.assertEqual([d['productName'] for d in result], ['甲', '乙'])

    async def test_invalid_batch_settings_and_inventory_fail_before_call(self):
        model = FakeModel()
        for kwargs in ({'batch_size': 0}, {'batch_size': True}, {'max_retries': -1}):
            with self.assertRaises(generator.MaterialGenerationError):
                await self.run_generate(model, **kwargs)
        self.data[3].append(copy.deepcopy(self.data[3][0]))
        with self.assertRaises(generator.MaterialGenerationError):
            await self.run_generate(model)
        self.assertEqual(model.calls, [])

    async def test_real_longest_checklist_is_processed_without_rule_limit(self):
        from main import DocumentStore
        from checklist_rules import build_rule_inventory
        store = DocumentStore(Path(__file__).resolve().parents[1] / 'list')
        snapshots = store.snapshot(list(store.documents))
        all_rules = build_rule_inventory(snapshots)
        name = max((s['name'] for s in snapshots), key=lambda n: sum(r['productName'] == n for r in all_rules))
        business, approval, _, _, _ = inputs()
        approval['products'] = [dict(productName=name, creditRecords=[])]
        rules = [r for r in all_rules if r['productName'] == name]
        self.assertGreater(len(rules), 70)
        self.data = (business, approval, [s for s in snapshots if s['name'] == name], rules,
                     resolve_material_facts(approval, business))
        by_key = {r['ruleKey']: r for r in rules}
        def respond(messages):
            ds = []
            for key in json.loads(messages[1]['content'])['requestedRuleKeys']:
                d = decision(by_key[key], self.data[4])
                if by_key[key]['kind'] == 'CHECK':
                    d.update(action='CHECK', instances=[])
                ds.append(d)
            return {'decisions': ds}
        result = await self.run_generate(FakeModel(responder=respond))
        self.assertEqual(len(result), len(rules))
        self.assertGreater(sum(len(d['instances']) for d in result), 60)
