import asyncio
import copy
import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
facts_module = __import__('material_facts') if importlib.util.find_spec('material_facts') else None


def pdf_fact(name, value='', *, product='甲', subject='借款人', present=True, negated=False, page=1):
    return dict(name=name, value=value, productName=product, subject=subject, present=present,
                negated=negated, page_number=page, source=value if present else '')


def approval(facts=(), names=('甲',)):
    return dict(products=[dict(productName=n, extractedProductName=n, creditRecords=[]) for n in names],
                facts=list(facts), pages=[])


def business():
    return dict(bsnEvtId='B', userNo='U', taskId='T', cstNm='借款公司', subcontracts=[
        dict(subcontractNum='S1', gnrNm='A', gnrId='A1', jntGnrInfs=[dict(cstNm='C')],
             SubcontractCollateralRels=[dict(cltlId='X', cltlNm='房产', cltlTp='')]),
        dict(subcontractNum='S2', gnrNm='B')])


class FactTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(facts_module, 'T16 fact resolution is missing')

    def resolve(self, extracted=None, data=None):
        return facts_module.resolve_material_facts(extracted or approval(), data or business())

    def test_explicit_pdf_negative_overrides_json(self):
        result = self.resolve(approval([pdf_fact('担保', '无担保', negated=True)]))
        self.assertFalse(any(o['kind'] in ('subcontract', 'guarantor', 'jointGuarantor', 'collateral') for o in result['objects']))
        self.assertFalse(any(f['origin'] == 'JSON' and f['name'] in ('担保人姓名', '共同担保人姓名', '押品名称') for f in result['facts']))
        negative = next(f for f in result['facts'] if f['name'] == '担保方式')
        self.assertEqual(negative['state'], 'explicit-negative')
        self.assertEqual(negative['origin'], 'PDF')

    def test_missing_names_supplement_objects_and_keep_parents(self):
        result = self.resolve(approval([pdf_fact('担保人姓名', present=False)]))
        objects = result['objects']
        guarantors = [o for o in objects if o['kind'] == 'guarantor']
        self.assertEqual([o['name'] for o in guarantors], ['A', 'B'])
        by_key = {o['objectKey']: o for o in objects}
        joint = next(o for o in objects if o['kind'] == 'jointGuarantor')
        self.assertEqual(by_key[joint['parentKey']]['kind'], 'subcontract')
        self.assertEqual([o['name'] for o in objects if o['kind'] == 'customer'], ['借款公司'])
        self.assertTrue(all(f['source'] for f in result['facts'] if f['state'] != 'unknown'))

    def test_json_objects_apply_to_all_approval_products_only(self):
        data = business(); data['aspdNm'] = '伪产品'
        result = self.resolve(approval(names=('甲', '乙')), data)
        self.assertEqual([p['productName'] for p in result['products']], ['甲', '乙'])
        for name in ('甲', '乙'):
            self.assertEqual([o['name'] for o in result['objects']
                              if o['productName'] == name and o['kind'] == 'guarantor'], ['A', 'B'])
        self.assertEqual(len({o['objectKey'] for o in result['objects']}), len(result['objects']))

    def test_product_and_subject_scopes_do_not_leak(self):
        result = self.resolve(approval([pdf_fact('担保方式', '无担保', negated=True),
            pdf_fact('额度', '200', product='乙', subject='A')], names=('甲', '乙')))
        self.assertFalse(any(o['kind'] == 'guarantor' and o['productName'] == '甲' for o in result['objects']))
        self.assertEqual([o['name'] for o in result['objects'] if o['kind'] == 'guarantor'], ['A', 'B'])
        amount = next(f for f in result['facts'] if f['name'] == '金额')
        self.assertEqual((amount['productName'], amount['subject']), ('乙', 'A'))
        self.assertEqual(next(o for o in result['objects'] if o['objectKey'] == amount['objectKey'])['name'], 'A')

    def test_collateral_unknown_type_and_empty_placeholder(self):
        result = self.resolve()
        collateral = next(o for o in result['objects'] if o['kind'] == 'collateral')
        self.assertEqual(collateral['typeState'], 'unknown')
        self.assertTrue(collateral['present'])
        data = business(); data['subcontracts'][0]['SubcontractCollateralRels'] = [dict(cltlId='', cltlNm='', cltlTp='')]
        self.assertFalse(any(o['kind'] == 'collateral' for o in self.resolve(data=data)['objects']))
        result = self.resolve(approval([pdf_fact('押品存在', '有押品')]), data)
        self.assertEqual(next(o for o in result['objects'] if o['kind'] == 'collateral')['typeState'], 'unknown')

    def test_pdf_values_override_empty_json_and_later_fact_wins(self):
        data = business(); data.update(cstNm='JSON名字', ctrTamt='10', subcontracts=[])
        extracted = approval([pdf_fact('客户名称', 'PDF名字'), pdf_fact('金额', '100'),
                              pdf_fact('金额', '200', page=2)])
        original = copy.deepcopy((extracted, data))
        result = self.resolve(extracted, data)
        self.assertEqual(next(o for o in result['objects'] if o['kind'] == 'customer')['name'], 'PDF名字')
        self.assertEqual([f['value'] for f in result['facts'] if f['name'] == '金额'], ['200'])
        self.assertEqual((extracted, data), original)
        extracted['facts'].append(pdf_fact('押品名称', '仓库', subject='押品X'))
        self.assertEqual(next(o for o in self.resolve(extracted, data)['objects'] if o['kind'] == 'collateral')['name'], '仓库')

    def test_codes_and_unknown_facts_are_retained_without_interpretation(self):
        data = business(); data['primGrtstl'] = '001'; data['extension'] = {'text': '业务原文'}
        result = self.resolve(approval([pdf_fact('其他条件', '原样保留', subject='A')]), data)
        self.assertEqual(next(f for f in result['facts'] if f['name'] == 'primGrtstl')['value'], '001')
        self.assertEqual(next(f for f in result['facts'] if f['name'] == '其他条件')['value'], '原样保留')
        unknown = self.resolve(approval([pdf_fact('押品类型', present=False)]))
        self.assertTrue(any(f['name'] == '押品类型' and f['state'] == 'unknown' for f in unknown['facts']))

    def test_invalid_product_set_and_cancellation_fail(self):
        for extracted in [approval(names=()), approval(names=('甲', '甲')),
                          approval([pdf_fact('金额', '100', product='假产品')])]:
            with self.subTest(extracted=extracted), self.assertRaises(facts_module.MaterialFactError):
                self.resolve(extracted)
        with self.assertRaises(asyncio.CancelledError):
            facts_module.resolve_material_facts(approval(), business(), cancel_check=lambda: True)

    def test_no_collateral_blocks_all_json_collateral_but_keeps_guarantors(self):
        result = self.resolve(approval([pdf_fact('押品存在', '无押品', negated=True)]))
        self.assertFalse(any(o['kind'] == 'collateral' for o in result['objects']))
        self.assertEqual([o['name'] for o in result['objects'] if o['kind'] == 'guarantor'], ['A', 'B'])
        self.assertFalse(any(f['origin'] == 'JSON' and f['name'] == '押品名称' for f in result['facts']))
        keys = {o['objectKey'] for o in result['objects']}
        self.assertTrue(all(f['objectKey'] in keys for f in result['facts']))

    def test_subject_negative_never_suppresses_another_guarantor(self):
        result = self.resolve(approval([pdf_fact('担保方式', '无需A担保', subject='A', negated=True)]))
        self.assertEqual([o['name'] for o in result['objects'] if o['kind'] == 'guarantor'], ['B'])
        self.assertTrue(any(f['negated'] and f['subject'] == 'A' for f in result['facts']))

    def test_later_positive_replaces_negative_and_absent_does_not_erase(self):
        result = self.resolve(approval([pdf_fact('担保方式', '无担保', negated=True),
            pdf_fact('担保方式', '保证', page=2), pdf_fact('担保方式', present=False, page=3)]))
        self.assertEqual([o['name'] for o in result['objects'] if o['kind'] == 'guarantor'], ['A', 'B'])
        self.assertEqual([f['value'] for f in result['facts'] if f['name'] == '担保方式'], ['保证'])

    def test_pdf_named_guarantor_overrides_json_generic_role_name(self):
        data = business(); data['subcontracts'] = [dict(gnrNm='JSON名字')]
        result = self.resolve(approval([pdf_fact('担保人姓名', '批复名字', subject='担保人')]), data)
        self.assertEqual([o['name'] for o in result['objects'] if o['kind'] == 'guarantor'], ['批复名字'])
        self.assertEqual([f['value'] for f in result['facts'] if f['name'] == '担保人姓名'], ['批复名字'])

    def test_whitespace_is_unknown_and_repeated_subject_is_not_arbitrarily_linked(self):
        data = business(); data['ctrTamt'] = ''; data['cstNm'] = '   '
        data['subcontracts'][1]['gnrNm'] = 'A'
        result = self.resolve(approval([pdf_fact('其他条件', '仅A', subject='A')]), data)
        condition = next(f for f in result['facts'] if f['name'] == '其他条件')
        obj = next(o for o in result['objects'] if o['objectKey'] == condition['objectKey'])
        self.assertEqual(obj['kind'], 'subject')
        self.assertEqual(len(obj['relatedObjectKeys']), 2)
        self.assertFalse(any(f['name'] == '客户名称' for f in result['facts']))

    def test_repeated_ambiguous_scope_still_uses_later_fact(self):
        data = business(); data['subcontracts'][1]['gnrNm'] = 'A'
        extracted = approval([pdf_fact('金额', '100', subject='A'),
                              pdf_fact('金额', '200', subject='A', page=2)])
        result = self.resolve(extracted, data)
        self.assertEqual([f['value'] for f in result['facts'] if f['name'] == '金额'], ['200'])

    def test_unmentioned_subject_fact_never_attaches_to_borrower(self):
        result = self.resolve(approval([pdf_fact('押品类型', present=False, subject='PDF押品')]))
        fact = next(f for f in result['facts'] if f['origin'] == 'PDF')
        obj = next(o for o in result['objects'] if o['objectKey'] == fact['objectKey'])
        self.assertEqual(obj['name'], 'PDF押品')
        self.assertEqual(obj['kind'], 'subject')

    def test_global_fact_applies_to_all_products_and_newer_product_fact_overrides(self):
        result = self.resolve(approval([pdf_fact('金额', '100', product=''),
                                       pdf_fact('金额', '200', product='乙', page=2)], names=('甲', '乙')))
        self.assertEqual([(f['productName'], f['value']) for f in result['facts'] if f['name'] == '金额'],
                         [('甲', '100'), ('乙', '200')])


if __name__ == '__main__':
    unittest.main()
