"""Strict exclusions and first-level directory gates; no external model calls."""
import copy
import json
import unittest

from test_material_generation import inputs, decision, FakeModel, generator
import test_phrase_materials as phrase_tests
import test_task_pipeline as pipeline_tests
from test_checklist_rule_inventory import snapshot, HEADER
from material_tree import iter_materials
from model_client import ModelReply
from material_facts import resolve_material_facts


class StrictReviewTests(unittest.TestCase):
    def setUp(self):
        _, _, _, self.rules, self.context = inputs()

    def validate(self, d):
        return generator.validate_material_decisions([d], self.rules, self.context)[0]

    def test_only_explicit_applicability_is_kept(self):
        for state in ('APPLICABLE', 'UNKNOWN', 'NOT_APPLICABLE', None):
            with self.subTest(state=state):
                d = decision(self.rules[0], self.context)
                if state is None:
                    d.pop('applicability')
                else:
                    d['applicability'] = state
                result = self.validate(d)
                self.assertEqual(result['action'], 'KEEP' if state == 'APPLICABLE' else 'EXCLUDE')
                self.assertEqual(bool(result['instances']), state == 'APPLICABLE')
                self.assertEqual(self.validate(result), result)

    def test_incomplete_objects_and_unbound_instance_are_excluded(self):
        for generic in (False, True):
            d = decision(self.rules[0], self.context, ('guarantor',))
            if generic:
                d['instances'] = decision(self.rules[0], self.context)['instances']
            else:
                d['instances'].pop()
            before = copy.deepcopy(d)
            result = self.validate(d)
            self.assertEqual(result['action'], 'EXCLUDE')
            self.assertEqual(result['instances'], [])
            self.assertEqual(d, before)

    def test_explicit_kept_object_does_not_restore_excluded_objects(self):
        d = decision(self.rules[0], self.context, ('guarantor',))
        removed = d['instances'].pop()
        d['excludedObjects'] = [dict(objectKey=removed['objectKey'], reason='不确定，排除',
                                     evidence=removed['evidence'])]
        result = self.validate(d)
        self.assertEqual([i['subject'] for i in result['instances']], ['A'])
        self.assertEqual([i['objectKey'] for i in result['excludedObjects']], [removed['objectKey']])
        self.assertEqual(self.validate(result), result)

    def test_unknown_fact_cannot_support_keep(self):
        d = decision(self.rules[0], self.context)
        obj = self.context['objects'][0]
        self.context['facts'].append(dict(objectKey=obj['objectKey'], productName='甲', name='条件',
            source='$.unknown', state='unknown', present=False))
        d['evidence'].append(dict(type='FACT', objectKey=obj['objectKey'], name='条件', source='$.unknown'))
        result = self.validate(d)
        self.assertEqual(result['action'], 'EXCLUDE')
        self.assertEqual(result['instances'], [])

    def test_unknown_fact_on_specific_object_excludes_instead_of_failing(self):
        d = decision(self.rules[0], self.context, ('guarantor',))
        obj = d['instances'][0]['objectKey']
        self.context['facts'].append(dict(objectKey=obj, productName='甲', name='类型',
            source='$.unknown', state='unknown', present=False))
        d['instances'][0]['evidence'] = [d['evidence'][0],
            dict(type='FACT', objectKey=obj, name='类型', source='$.unknown')]
        result = self.validate(d)
        self.assertEqual(result['action'], 'EXCLUDE')

    def test_missing_scoped_objects_cannot_be_replaced_with_generic_material(self):
        d = decision(self.rules[0], self.context, ('collateral',))
        result = self.validate(d)
        self.assertEqual(result['action'], 'EXCLUDE')
        self.assertEqual(result['instances'], [])

    def test_unsupported_exclusion_never_restores_materials(self):
        d = decision(self.rules[0], self.context, ('guarantor',))
        d.update(action='EXCLUDE', instances=[])
        d['evidence'][0]['quote'] = '无法核对的引文'
        result = self.validate(d)
        self.assertEqual(result['action'], 'EXCLUDE')
        self.assertEqual(result['instances'], [])
        self.assertNotIn('无法核对的引文', str(result))
        self.assertEqual(self.validate(result), result)


class StageGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_level_filter_before_model_and_no_child_title_override(self):
        business, approval, snaps, rules, _ = inputs(4)
        business['grtstlRcmbNm'] = '放贷'
        titles = ['合同签约资料', '放款资料', '基本资料', '产品专项及补充材料']
        for rule, title in zip(rules, titles):
            rule['groups'][0]['title'] = title
        # A matching child/file title must not rescue a mismatched root.
        rules[0]['groups'][1]['title'] = '放款资料'
        rules[0]['cells'][1] = '放款合同'
        context = resolve_material_facts(approval, business)

        def respond(messages):
            payload = json.loads(messages[1]['content'])
            self.assertNotIn(rules[0]['ruleKey'], payload['requestedRuleKeys'])
            return {'decisions': [decision(r, context) for r in rules
                                  if r['ruleKey'] in payload['requestedRuleKeys']]}

        model = FakeModel(responder=respond)
        result = await generator.generate_material_decisions(business, approval, snaps, rules, context, model)
        self.assertEqual([d['action'] for d in result], ['EXCLUDE', 'KEEP', 'KEEP', 'KEEP'])
        self.assertEqual(len(model.calls), 1)

    async def test_all_directories_mismatched_never_calls_model(self):
        business, approval, snaps, rules, _ = inputs()
        business['grtstlRcmbNm'] = '抵押、保证'
        rules[0]['groups'][0]['title'] = '合同签约资料'
        context = resolve_material_facts(approval, business)
        model = FakeModel()
        result = await generator.generate_material_decisions(business, approval, snaps, rules, context, model)
        self.assertEqual(result[0]['action'], 'EXCLUDE')
        self.assertEqual(model.calls, [])

    async def test_multiple_terms_match_either_root_and_no_cross_product_leak(self):
        business, approval, snaps, rules, _ = inputs(3)
        business['grtstlRcmbNm'] = '抵押、保证'
        for r, title in zip(rules, ('抵押资料', '保证资料', '质押资料')):
            r['groups'][0]['title'] = title
        context = resolve_material_facts(approval, business)
        model = FakeModel(responder=lambda messages: {'decisions': [decision(r, context) for r in rules
            if r['ruleKey'] in json.loads(messages[1]['content'])['requestedRuleKeys']]})
        result = await generator.generate_material_decisions(business, approval, snaps, rules, context, model)
        self.assertEqual([d['action'] for d in result], ['KEEP', 'KEEP', 'EXCLUDE'])


class StrictPhraseTests(unittest.IsolatedAsyncioTestCase):
    setUp = phrase_tests.PhraseGenerationTests.setUp
    response = phrase_tests.PhraseGenerationTests.response
    generate = phrase_tests.PhraseGenerationTests.generate

    async def test_unknown_product_scope_is_excluded(self):
        data = self.response()
        for d in data['decisions']:
            d['scope'] = 'UNKNOWN'
        ds = await self.generate(FakeModel([data]))
        self.assertTrue(all(d['scope'] == 'NONE' and not d['products'] for d in ds))

    async def test_unknown_or_missing_leaf_applicability_cannot_restore_word_material(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                data = self.response()
                leaf = data['decisions'][0]['products'][0]['materials'][0]
                if missing:
                    leaf.pop('applicability', None)
                else:
                    leaf['applicability'] = 'UNKNOWN'
                ds = await self.generate(FakeModel([data]))
                self.assertEqual(ds[0]['products'][0]['materials'][0]['instances'], [])

    async def test_confirmed_leaf_does_not_force_uncertain_bundle_leaf_to_be_included(self):
        data = self.response()
        leaves = data['decisions'][0]['products'][0]['materials']
        leaves[0]['applicability'] = 'APPLICABLE'
        leaves[1].update(applicability='UNKNOWN', instances=[])
        ds = await self.generate(FakeModel([data]))
        self.assertTrue(ds[0]['products'][0]['materials'][0]['instances'])
        self.assertEqual(ds[0]['products'][0]['materials'][1]['instances'], [])


class StagePipelineTests(unittest.IsolatedAsyncioTestCase):
    setUp = pipeline_tests.PipelineTests.setUp
    create_lists = pipeline_tests.PipelineTests.create_lists
    submit = pipeline_tests.PipelineTests.submit
    runner = pipeline_tests.PipelineTests.runner
    saved = pipeline_tests.PipelineTests.saved

    def staged_lists(self):
        for name in ('甲', '乙'):
            snap = snapshot([HEADER, ['1', '基本资料'], ['1.1', '营业执照', '必备', '', ''],
                ['2', '合同签约资料'], ['2.1', '放款子目录'],
                ['2.1.1', '签约合同', '必备', '', ''],
                ['3', '放款资料'], ['3.1', '放款凭证', '必备', '', '']], name)
            (self.lists / (name + '材料清单.docx')).write_bytes(snap['raw_bytes'])
        self.business['grtstlRcmbNm'] = '放贷'
        self.record = self.submit()

    async def test_supplements_cannot_restore_mismatched_root_by_word_or_excel_path(self):
        self.staged_lists()
        normal = self.model.complete
        self.model.approval_text = '甲 乙 由甲公司提供连带责任保证担保'

        async def respond(stage, messages):
            if stage != 'generate':
                return await normal(stage, messages)
            payload = json.loads(messages[1]['content'])
            if 'requestedRuleKeys' in payload:
                denied = {r['ruleKey'] for r in payload['rules'] if r['groups'][0]['sourceRuleId'] == '2'}
                self.assertFalse(denied & set(payload['requestedRuleKeys']))
                return await normal(stage, messages)
            rows = []
            for row in payload['phraseRules']:
                if row['phrase'] not in self.model.approval_text:
                    rows.append(dict(rowKey=row['rowKey'], scope='NONE', matches=[], products=[]))
                    continue
                products = []
                for name in ('甲', '乙'):
                    signing = next(r for r in payload['rules'] if r['productName'] == name and r['sourceRuleId'] == '2.1.1')
                    products.append(dict(productName=name, materials=[dict(materialKey=m['materialKey'], applicability='APPLICABLE',
                        categoryPath=['2'] if index < 2 else ['3'], instances=[dict(objectKey='', subject='',
                            materialName=m['name'], requirement='已明确适用',
                            wordRuleKey=signing['ruleKey'] if index == 0 else '')])
                        for index, m in enumerate(row['materials'])]))
                rows.append(dict(rowKey=row['rowKey'], scope='COMMON',
                    matches=[dict(page_number=1, quote=row['phrase'])], products=products))
            return ModelReply(json.dumps({'decisions': rows}, ensure_ascii=False), 'stop')

        self.model.complete = respond
        data = await self.runner().run_task(self.record)
        self.assertEqual(data['total'], 6)
        for product in data['items']:
            self.assertEqual([c['categoryId'] for c in product['categories']], ['1', '3'])
        self.assertFalse(any('签约合同' in i['materialName'] or '公司章程' in i['materialName']
                             for i in iter_materials(data['items'])))

    async def test_pipeline_rechecks_custom_generator_before_phrase_merge(self):
        self.staged_lists()

        async def ignore_stage(business, approval, snaps, rules, facts, client, *args, **kwargs):
            return [decision(r, facts) for r in rules]

        data = await self.runner(generator=ignore_stage).run_task(self.record)
        self.assertEqual(data['total'], 4)
        self.assertTrue(all(c['categoryId'] != '2' for p in data['items'] for c in p['categories']))
