import asyncio
import copy
import unittest

from test_material_generation import generator, inputs, decision, FakeModel


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(generator, 'T18 decision validator is missing')
        self.business, self.approval, self.snapshots, self.rules, self.context = inputs()
        self.valid = [decision(self.rules[0], self.context, ('guarantor',))]

    def validate(self, data=None):
        return generator.validate_material_decisions(
            self.valid if data is None else data, self.rules, self.context)

    def reject(self, change):
        data = copy.deepcopy(self.valid)
        change(data)
        with self.assertRaises(generator.MaterialGenerationError):
            self.validate(data)

    def test_missing_rule_is_rejected_and_missing_subject_excludes_rule(self):
        self.reject(lambda ds: ds.clear())
        data = copy.deepcopy(self.valid)
        data[0]['instances'].pop()
        result = self.validate(data)[0]
        self.assertEqual(result['instances'], [])
        self.assertIn('严格审核排除', result['reason'])

    def test_duplicate_extra_and_cross_product_rules_are_rejected(self):
        self.reject(lambda ds: ds.append(copy.deepcopy(ds[0])))
        self.reject(lambda ds: ds[0].update(ruleKey='伪规则'))
        self.reject(lambda ds: ds[0].update(productName='乙'))

    def test_fabricated_or_duplicate_object_is_rejected(self):
        self.reject(lambda ds: ds[0]['instances'][0].update(objectKey='伪主体'))
        self.reject(lambda ds: ds[0]['instances'].append(copy.deepcopy(ds[0]['instances'][0])))
        self.reject(lambda ds: ds[0]['instances'][0].update(subject='B'))

    def test_a_evidence_cannot_be_used_for_b(self):
        self.reject(lambda ds: ds[0]['instances'][1].update(evidence=ds[0]['instances'][0]['evidence']))

    def test_unknown_fact_causes_strict_exclusion(self):
        obj = self.context['objects'][0]
        fact = dict(objectKey=obj['objectKey'], productName='甲', name='类型', source='$.unknown',
                    state='unknown', present=False)
        self.context['facts'].append(fact)
        ref = dict(type='FACT', objectKey=obj['objectKey'], name='类型', source='$.unknown')
        excluded = dict(self.valid[0], action='EXCLUDE', instances=[], excludedObjects=[],
                        evidence=[*self.valid[0]['evidence'], ref])
        result = self.validate([excluded])[0]
        self.assertEqual(result['action'], 'EXCLUDE')
        self.assertEqual(result['instances'], [])

    def test_explicit_fact_supports_legal_exclusion_and_retains_decision(self):
        obj = self.context['objects'][0]
        self.context['facts'].append(dict(objectKey=obj['objectKey'], productName='甲', name='担保方式',
                                         source='无担保', state='explicit-negative', present=True))
        ref = dict(type='FACT', objectKey=obj['objectKey'], name='担保方式', source='无担保')
        excluded = dict(self.valid[0], action='EXCLUDE', instances=[], excludedObjects=[],
                        evidence=[*self.valid[0]['evidence'], ref])
        result = self.validate([excluded])
        self.assertEqual(result[0]['action'], 'EXCLUDE')
        self.assertEqual(result[0]['instances'], [])
        self.assertEqual(result[0]['evidence'][-1], ref)

    def test_check_cannot_masquerade_as_material_or_vice_versa(self):
        self.reject(lambda ds: ds[0].update(action='CHECK', instances=[]))
        self.rules[0]['kind'] = 'CHECK'
        with self.assertRaises(generator.MaterialGenerationError):
            self.validate()
        self.valid[0].update(action='CHECK', instances=[], excludedObjects=[])
        self.assertEqual(self.validate()[0]['action'], 'CHECK')

    def test_malformed_sources_actions_and_instances_are_rejected(self):
        changes = [lambda ds: ds[0].update(action='UNKNOWN'),
                   lambda ds: ds[0].update(evidence=[]),
                   lambda ds: ds[0]['evidence'][0].update(quote='编造原文'),
                   lambda ds: ds[0]['instances'][0].update(materialName=''),
                   lambda ds: ds[0]['instances'][0].update(itemType='CHECK'),
                   lambda ds: ds[0]['instances'][0].update(evidence=[]),
                   lambda ds: ds[0].update(objectKinds=['invented']),
                   lambda ds: ds[0].update(instances=None)]
        for change in changes:
            with self.subTest(change=change):
                self.reject(change)

    def test_scope_parent_links_and_order(self):
        parent = next(o for o in self.context['objects'] if o['kind'] == 'subcontract')
        self.valid[0]['scopeObjectKey'] = parent['objectKey']
        self.valid[0]['instances'] = self.valid[0]['instances'][:1]
        self.assertEqual(len(self.validate()[0]['instances']), 1)
        self.valid[0]['instances'][0]['evidence'].append(dict(type='OBJECT', objectKey=parent['objectKey'], source=parent['source']))
        self.assertEqual(len(self.validate()), 1)
        self.valid[0]['scopeObjectKey'] = '不存在'
        with self.assertRaises(generator.MaterialGenerationError):
            self.validate()

    def test_unknown_object_type_excludes_without_inventing_generic_material(self):
        self.valid = [decision(self.rules[0], self.context, ('collateral',))]
        self.assertEqual(self.validate()[0]['action'], 'EXCLUDE')
        self.assertEqual(self.validate()[0]['instances'], [])
        self.reject(lambda ds: ds[0]['instances'][0].update(subject='伪姓名'))

    def test_per_object_exclusion_requires_its_own_explicit_fact(self):
        instance = self.valid[0]['instances'].pop()
        obj = next(o for o in self.context['objects'] if o['objectKey'] == instance['objectKey'])
        exclusion = dict(objectKey=obj['objectKey'], reason='明确不适用', evidence=instance['evidence'])
        self.valid[0]['excludedObjects'] = [exclusion]
        self.assertEqual(len(self.validate()[0]['instances']), 1)
        fact = dict(objectKey=obj['objectKey'], productName='甲', name='免提供', source='免提供',
                    present=True, state='present')
        self.context['facts'].append(fact)
        exclusion['evidence'].append(dict(type='FACT', objectKey=obj['objectKey'], name='免提供', source='免提供'))
        self.assertEqual(len(self.validate()[0]['instances']), 1)

    def test_validation_does_not_mutate_input_and_normalizes_object_order(self):
        self.valid[0]['instances'].reverse()
        before = copy.deepcopy(self.valid)
        result = self.validate()
        self.assertEqual(self.valid, before)
        self.assertEqual([i['subject'] for i in result[0]['instances']], ['A', 'B'])

    def test_cancelled_validation(self):
        with self.assertRaises(asyncio.CancelledError):
            generator.validate_material_decisions(self.valid, self.rules, self.context, lambda: True)

    def test_whole_exclusion_can_use_separate_facts_for_all_objects(self):
        refs = copy.deepcopy(self.valid[0]['evidence'])
        for instance in self.valid[0]['instances']:
            key = instance['objectKey']
            self.context['facts'].append(dict(objectKey=key, productName='甲', name='免提供',
                                             source=key + ':免提供', present=True, state='present'))
            refs.append(dict(type='FACT', objectKey=key, name='免提供', source=key + ':免提供'))
        self.valid[0].update(action='EXCLUDE', instances=[], evidence=refs)
        self.assertEqual(self.validate()[0]['action'], 'EXCLUDE')
        self.valid[0]['evidence'].pop()
        result = self.validate()[0]
        self.assertEqual(result['action'], 'EXCLUDE')
        self.assertEqual(result['instances'], [])

    def test_cross_product_object_and_fact_sources_are_rejected(self):
        other = copy.deepcopy(self.context['objects'][0])
        other.update(objectKey='乙:$', productName='乙')
        self.context['objects'].append(other)
        self.reject(lambda ds: ds[0]['instances'][0]['evidence'].append(
            dict(type='OBJECT', objectKey=other['objectKey'], source=other['source'])))

    def test_unknown_facts_exclude_without_restoring_missing_material(self):
        excluded = copy.deepcopy(self.valid)
        excluded[0].update(action='EXCLUDE', instances=[])
        self.assertEqual(len(self.validate(excluded)[0]['instances']), 0)
        excluded = copy.deepcopy(self.valid)
        excluded[0].update(instances=[], excludedObjects=[dict(
            objectKey=i['objectKey'], reason='未知所以排除', evidence=i['evidence'])
            for i in excluded[0]['instances']])
        result = self.validate(excluded)[0]
        self.assertEqual(result['action'], 'EXCLUDE')
        self.assertEqual(len(result['instances']), 0)
        self.assertEqual(result['excludedObjects'], [])


class StrictExclusionTests(unittest.TestCase):
    def setUp(self):
        _, _, _, self.rules, self.context = inputs()
        self.valid = decision(self.rules[0], self.context, ('guarantor',))

    def validate(self, data):
        return generator.validate_material_decisions([data], self.rules, self.context)[0]

    def explicit_ref(self, key):
        self.context['facts'].append(dict(objectKey=key, productName='甲', name='免提供',
            source=key + ':免提供', present=True, state='present'))
        return dict(type='FACT', objectKey=key, name='免提供', source=key + ':免提供')

    def test_partial_exclusion_proof_excludes_without_rebuilding_materials(self):
        d = copy.deepcopy(self.valid)
        d.update(action='EXCLUDE', instances=[], excludedObjects=[])
        d['evidence'].append(self.explicit_ref(self.valid['instances'][0]['objectKey']))
        before = copy.deepcopy(d)
        result = self.validate(d)
        self.assertEqual(result['action'], 'EXCLUDE')
        self.assertEqual(result['instances'], [])
        self.assertEqual(result['excludedObjects'], [])
        self.assertIn('严格审核排除', result['reason'])
        self.assertEqual(d, before)
        self.assertEqual(self.validate(result), result)

    def test_uncertain_objects_stay_excluded_and_preserve_confirmed_instance(self):
        d = copy.deepcopy(self.valid)
        existing = d['instances'][0]
        existing.update(requirement='原模型细化要求', sourceSystem='原文系统', publishDate='2025年')
        proven = d['instances'].pop()
        third = copy.deepcopy(next(o for o in self.context['objects'] if o['objectKey'] == proven['objectKey']))
        third.update(objectKey='third-guarantor', name='C', source='$.third')
        self.context['objects'].append(third)
        d['excludedObjects'] = [dict(objectKey=proven['objectKey'], reason='明确免提供',
            evidence=[*proven['evidence'], self.explicit_ref(proven['objectKey'])]),
            dict(objectKey=third['objectKey'], reason='未确认', evidence=[d['evidence'][0],
                dict(type='OBJECT', objectKey=third['objectKey'], source=third['source'])])]
        result = self.validate(d)
        self.assertEqual([i['subject'] for i in result['instances']], ['A'])
        self.assertEqual(len(result['excludedObjects']), 2)
        self.assertEqual(result['instances'][0]['requirement'], '原模型细化要求')
        self.assertEqual(result['instances'][0]['sourceSystem'], '原文系统')
        self.assertEqual(result['instances'][0]['publishDate'], '2025年')
        self.assertEqual(self.validate(result), result)

    def test_fallback_respects_parent_scope(self):
        d = copy.deepcopy(self.valid)
        target = next(o for o in self.context['objects'] if o['objectKey'] == d['instances'][0]['objectKey'])
        d.update(action='EXCLUDE', scopeObjectKey=target['parentKey'], instances=[])
        result = self.validate(d)
        self.assertEqual(result['instances'], [])

    def test_one_kept_object_never_restores_other_proven_exclusions(self):
        d = copy.deepcopy(self.valid)
        excluded = d['instances'].pop()
        d['excludedObjects'] = [dict(objectKey=excluded['objectKey'], reason='有明确免提供依据',
            evidence=[*excluded['evidence'], self.explicit_ref(excluded['objectKey'])])]
        result = self.validate(d)
        self.assertEqual([i['subject'] for i in result['instances']], ['A'])
        self.assertEqual(len(result['excludedObjects']), 1)
        self.assertEqual(self.validate(result), result)

    def test_unknown_object_is_excluded_without_generic_material(self):
        d = decision(self.rules[0], self.context, ('collateral',))
        d.update(action='EXCLUDE', instances=[])
        result = self.validate(d)
        self.assertEqual(result['action'], 'EXCLUDE')
        self.assertEqual(result['instances'], [])

    def test_unreliable_exclusion_citations_never_restore_materials(self):
        for change in (lambda d: d['evidence'][0].update(quote='不存在的原文'),
                       lambda d: d['evidence'].append(dict(type='FACT', objectKey='0:$',
                           name='编造事实', source='编造来源'))):
            with self.subTest(change=change):
                d = copy.deepcopy(self.valid)
                d.update(action='EXCLUDE', instances=[])
                change(d)
                result = self.validate(d)
                self.assertEqual(result['action'], 'EXCLUDE')
                self.assertEqual(result['instances'], [])
                self.assertEqual(self.validate(result), result)

    def test_missing_objects_discards_incomplete_model_details_and_excludes(self):
        d = copy.deepcopy(self.valid)
        d['instances'] = d['instances'][:1]
        d['instances'][0].update(materialName='模型细化身份证明', requirement='仅模型要求')
        result = self.validate(d)
        self.assertEqual(result['action'], 'EXCLUDE')
        self.assertEqual(result['instances'], [])
        self.assertEqual(self.validate(result), result)

    def test_bad_per_object_exclusion_quote_keeps_only_confirmed_object(self):
        d = copy.deepcopy(self.valid)
        excluded = d['instances'].pop()
        excluded['evidence'][0]['quote'] = '上市公司担保时提供'
        d['excludedObjects'] = [dict(objectKey=excluded['objectKey'], reason='模型拟排除',
                                     evidence=excluded['evidence'])]
        result = self.validate(d)
        self.assertEqual([i['subject'] for i in result['instances']], ['A'])
        self.assertEqual(len(result['excludedObjects']), 1)
        self.assertNotIn('上市公司担保时提供', str(result))
        self.assertEqual(self.validate(result), result)


class ValidationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_collateral_generic_instance_excludes_without_model_repair(self):
        from material_facts import resolve_material_facts
        from material_result import project_material_result
        from material_tree import iter_materials
        data = list(inputs())
        data[0]['subcontracts'] = [dict(subcontractNum='S1', frmCtrTpNm='抵押合同',
                                       SubcontractCollateralRels=[])]
        data[1]['facts'] = [dict(name='押品存在', value='有抵押', present=True, negated=False,
            page_number=1, source='抵押', productName='甲', subject='借款人')]
        data[4] = resolve_material_facts(data[1], data[0])
        generic = decision(data[3][0], data[4])
        generic['objectKinds'] = ['collateral']
        model = FakeModel([{'decisions': [generic]}], retries=0)
        result = await generator.generate_material_decisions(*data, model, lambda: False)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(result[0]['action'], 'EXCLUDE')
        self.assertEqual(result[0]['instances'], [])
        data[1]['products'][0]['extractedProductName'] = '甲'
        data[2][0]['filename'] = '甲材料清单.docx'
        from material_result import MaterialResultError
        with self.assertRaises(MaterialResultError):
            project_material_result(data[1], data[2], data[3], result, 'a' * 64)

    async def test_uncertain_exclusion_is_not_projected_without_model_repair(self):
        from material_result import project_material_result
        from material_tree import iter_materials
        data = inputs()
        d = decision(data[3][0], data[4], ('guarantor',))
        d.update(action='EXCLUDE', instances=[])
        model = FakeModel([{'decisions': [d]}], retries=0)
        result = await generator.generate_material_decisions(*data, model, lambda: False)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(result[0]['action'], 'EXCLUDE')
        data[1]['products'][0]['extractedProductName'] = '甲'
        data[2][0]['filename'] = '甲材料清单.docx'
        from material_result import MaterialResultError
        with self.assertRaises(MaterialResultError):
            project_material_result(data[1], data[2], data[3], result, 'a' * 64)

    async def test_missing_object_excludes_rule_without_model_repair(self):
        self.assertIsNotNone(generator, 'T18 decision validator is missing')
        data = inputs()
        valid = decision(data[3][0], data[4], ('guarantor',))
        invalid = copy.deepcopy(valid)
        invalid['instances'].pop()
        model = FakeModel([{'decisions': [invalid]}, {'decisions': [valid]}])
        result = await generator.generate_material_decisions(*data, model, lambda: False)
        self.assertEqual(len(result[0]['instances']), 0)
        self.assertEqual(len(model.calls), 1)
        self.assertIn('严格审核排除', result[0]['reason'])


class GenericObjectFallbackTests(unittest.TestCase):
    def setUp(self):
        from material_facts import resolve_material_facts
        business, approval, _, self.rules, _ = inputs()
        business['subcontracts'] = [dict(subcontractNum='S1', frmCtrTpNm='抵押合同',
                                         SubcontractCollateralRels=[])]
        approval['facts'] = [dict(name='押品存在', value='有抵押', present=True, negated=False,
            page_number=1, source='抵押', productName='甲', subject='借款人')]
        self.context = resolve_material_facts(approval, business)
        self.rules[0]['cells'][1:4] = ['购房合同、预售许可证、购房款支付凭证及发票',
                                     '期房抵押时提供', '按实际购置及付款情况提供']

    def generic(self, kinds=('collateral',), context=None):
        d = decision(self.rules[0], context or self.context)
        d['objectKinds'] = list(kinds)
        d['instances'][0].update(materialName='模型推测的具体押品材料',
                                 requirement='模型推测的期房要求', policyBasis='模型推测的依据')
        return d

    def validate(self, d, context=None):
        return generator.validate_material_decisions([d], self.rules, context or self.context)[0]

    def test_unknown_collateral_is_excluded_without_inventing_type_or_name(self):
        d = self.generic()
        before = copy.deepcopy((d, self.context))
        result = self.validate(d)
        self.assertEqual(result['action'], 'EXCLUDE')
        self.assertEqual(result['excludedObjects'], [])
        self.assertEqual(result['instances'], [])
        self.assertIn('严格审核排除', result['reason'])
        self.assertEqual((d, self.context), before)
        self.assertEqual(self.validate(result), result)

    def test_unbound_generic_instance_is_excluded_within_parent_scope(self):
        _, _, _, _, context = inputs()
        d = self.generic(('guarantor',), context)
        result = self.validate(d, context)
        self.assertEqual(result['action'], 'EXCLUDE')
        self.assertEqual(result['instances'], [])
        parent = next(o for o in context['objects'] if o['kind'] == 'subcontract')
        d['scopeObjectKey'] = parent['objectKey']
        self.assertEqual(self.validate(d, context)['instances'], [])

    def test_generic_instance_does_not_hide_fabricated_identity_or_sources(self):
        other = dict(self.context['objects'][0], objectKey='乙:$', productName='乙')
        self.context['objects'].append(other)
        changes = [lambda d: d['instances'][0].update(objectKey='不存在的押品'),
                   lambda d: d['instances'][0].update(subject='虚构押品'),
                   lambda d: d['instances'][0]['evidence'][0].update(quote='不存在的原文'),
                   lambda d: d['instances'][0]['evidence'].append(
                       dict(type='OBJECT', objectKey=other['objectKey'], source=other['source'])),
                   lambda d: d['instances'].append(copy.deepcopy(d['instances'][0])),
                   lambda d: d['instances'][0].update(evidence=[])]
        for change in changes:
            with self.subTest(change=change):
                d = self.generic()
                change(d)
                with self.assertRaises(generator.MaterialGenerationError):
                    self.validate(d)

    def test_generic_instance_cannot_replace_a_named_object_outside_scope(self):
        _, _, _, _, context = inputs()
        d = self.generic(('guarantor',), context)
        parents = [o for o in context['objects'] if o['kind'] == 'subcontract']
        other = next(o for o in context['objects'] if o['kind'] == 'guarantor' and o['parentKey'] == parents[1]['objectKey'])
        d['scopeObjectKey'] = parents[0]['objectKey']
        d['instances'].append(dict(objectKey=other['objectKey'], subject=other['name'],
            materialName='其他主体的材料', requirement='', reason='错误关联',
            evidence=[*d['evidence'], dict(type='OBJECT', objectKey=other['objectKey'], source=other['source'])]))
        with self.assertRaises(generator.MaterialGenerationError):
            self.validate(d, context)
