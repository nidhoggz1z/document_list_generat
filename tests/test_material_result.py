import asyncio
import copy
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from task_models import TaskResult
from material_tree import iter_materials
from test_material_generation import inputs, decision

result_module = __import__('material_result') if importlib.util.find_spec('material_result') else None

SOURCE_SYSTEM = '房产所在地房地产登记部门办理登记，经营单位核验及保管产证、现房抵押登记证明；具体系统名称未明确。'
POLICY_BASIS = '法人按揭规程 第十三条（二）；第二十一条（一）4；第二十七条（二）。'
DEPARTMENT = '上海银行（办公室印发）；拟稿及解释：总行公司业务部（252号）。'
PACKED_BASIS = (f'来源系统：{SOURCE_SYSTEM}\n制度依据：{POLICY_BASIS}\n'
                f'发布依据：上银办〔2026〕252号。\n发布部门：{DEPARTMENT}')


class MaterialResultTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(result_module, 'T19 result projection is missing')
        self.business, self.approval, self.snaps, self.rules, self.context = inputs(2)
        self.approval['products'][0]['extractedProductName'] = '甲'
        self.snaps[0]['filename'] = '甲材料清单.docx'
        self.decisions = [decision(r, self.context) for r in self.rules]

    def project(self, **kwargs):
        return result_module.project_material_result(self.approval, self.snaps, self.rules,
            self.decisions, 'a' * 64, **kwargs)

    def test_cross_product_materials_are_not_merged(self):
        self.approval['products'].append(dict(productName='乙', extractedProductName='乙'))
        self.snaps.append(dict(name='乙', filename='乙材料清单.docx'))
        self.rules[1].update(productName='乙', ruleKey='乙:1')
        self.decisions = [decision(r, self.context) for r in self.rules]
        for d in self.decisions:
            d['instances'][0]['materialName'] = '营业执照'
        self.decisions.reverse()
        data = self.project()
        self.assertEqual([i['materialName'] for i in iter_materials(data['items'])], ['[甲]营业执照', '[乙]营业执照'])
        self.assertEqual([i['itemId'] for i in iter_materials(data['items'])], ['R0001', 'R0002'])
        self.assertEqual(data['total'], 2)
        TaskResult.model_validate(data)

    def test_subjects_requirements_and_no_duplicate_personalization(self):
        self.decisions = [decision(r, self.context, ('guarantor',)) for r in self.rules]
        for d in self.decisions:
            for i in d['instances']:
                i.update(requirement='有效期内')
        self.decisions[0]['instances'][0]['materialName'] = '[甲]A身份证明（有效期内）'
        data = self.project()
        self.assertEqual([i['materialName'] for i in iter_materials(data['items'])],
            ['[甲]身份证明（A）（有效期内）', '[甲]身份证明（B）（有效期内）'] * 2)
        self.assertEqual([i['itemId'] for i in iter_materials(data['items'])], [f'R{i:04d}' for i in range(1, 5)])

    def test_already_formatted_name_and_nested_subject_parentheses(self):
        subject = '瑞奕熙（上海）智能科技有限公司'
        expected = f'[甲]法定代表人身份证件（{subject}）（有效期内）'
        for name in ('法定代表人身份证件', subject + '法定代表人身份证件', expected):
            with self.subTest(name=name):
                instance = dict(materialName=name, subject=subject, requirement='有效期内')
                self.assertEqual(result_module.material_name('甲', instance), expected)

    def test_new_json_omits_reason_rule_and_publication_reference(self):
        self.rules[0]['cells'][4] = PACKED_BASIS
        for item in iter_materials(self.project()['items']):
            self.assertTrue({'reason', 'sourceRuleId', 'publishBasis'}.isdisjoint(item))
            self.assertNotIn('上银办〔2026〕252号', ''.join(item.values()))

    def test_inline_metadata_is_split_and_word_values_take_priority(self):
        date = '2026年9月29日（380号）；2026年7月30日（252号）。'
        self.rules[0]['cells'][4] = (
            f'来源系统：放款材料。制度依据：{POLICY_BASIS}'
            f'发布依据：上银办〔2026〕380号；上银办〔2026〕252号。'
            f'发布日期：{date}发布部门：{DEPARTMENT}')
        self.decisions[0]['instances'][0].update(publishDate='2026年', publishDepartment='模型部门')
        before = copy.deepcopy((self.rules, self.decisions))
        item = next(iter_materials(self.project()['items']))
        self.assertEqual([item[k] for k in ('sourceSystem', 'policyBasis', 'publishDate', 'publishDepartment')],
                         ['放款材料。', POLICY_BASIS, date, DEPARTMENT])
        self.assertEqual(before, (self.rules, self.decisions))

    def test_packed_metadata_inside_source_system_is_also_split(self):
        for packed in (PACKED_BASIS, PACKED_BASIS.removeprefix('来源系统：')):
            with self.subTest(packed=packed):
                self.decisions[0]['instances'][0]['sourceSystem'] = packed.replace('\n', ' ')
                item = next(iter_materials(self.project()['items']))
                self.assertEqual(item['sourceSystem'], SOURCE_SYSTEM)
                self.assertEqual(item['policyBasis'], POLICY_BASIS)
                self.assertEqual(item['publishDepartment'], DEPARTMENT)

    def test_metadata_defaults_and_explicit_values(self):
        self.decisions[0]['instances'][0].update(sourceSystem='原文系统', policyBasis='原文制度第1条',
            publishDate='2024年3月', publishDepartment='原文部门')
        self.decisions[1]['instances'][0].update(sourceSystem='  ', publishDate='', publishDepartment='')
        before = copy.deepcopy((self.approval, self.snaps, self.rules, self.decisions))
        data = self.project()
        a, b = iter_materials(data['items'])
        self.assertEqual([a[k] for k in ('sourceSystem', 'policyBasis', 'publishDate', 'publishDepartment')],
                         ['原文系统', '原文制度第1条', '2024年3月', '原文部门'])
        self.assertEqual([b[k] for k in ('sourceSystem', 'policyBasis', 'publishDate', 'publishDepartment')],
                         ['上银办', '', '2026年', '上海银行'])
        self.assertEqual(a['itemType'], 'MATERIAL')
        self.assertEqual(a['applicability'], 'APPLICABLE')
        self.assertEqual(set(data), {'generatedAt', 'total', 'products', 'items'})
        self.assertEqual(set(a), set(__import__('task_models').MaterialItem.model_fields))
        self.assertNotIn(None, a.values())
        self.assertEqual(before, (self.approval, self.snaps, self.rules, self.decisions))

    def test_word_labeled_metadata_overrides_model_fields_without_nested_labels(self):
        self.rules[0]['cells'][4] = PACKED_BASIS
        instance = self.decisions[0]['instances'][0]
        instance.update(sourceSystem='模型系统', policyBasis='模型依据', publishDepartment='模型部门',
                        publishDate='2025年')
        before = copy.deepcopy((self.rules, self.decisions))
        item = next(iter_materials(self.project()['items']))
        self.assertEqual([item[k] for k in ('sourceSystem', 'policyBasis', 'publishDepartment', 'publishDate')],
                         [SOURCE_SYSTEM, POLICY_BASIS, DEPARTMENT, '2025年'])
        self.assertEqual(before, (self.rules, self.decisions))
        self.assertNotIn('发布依据', ''.join(item.values()))

    def test_excluded_rule_never_restores_original_regulation_cell(self):
        from material_generator import validate_material_decisions
        self.rules[0]['cells'][4] = PACKED_BASIS
        self.decisions[0].update(action='EXCLUDE', instances=[])
        self.decisions = validate_material_decisions(self.decisions, self.rules, self.context)
        data = self.project()
        self.assertEqual(data['total'], 1)
        self.assertEqual(next(iter_materials(data['items']))['policyBasis'], '')

    def test_packed_model_metadata_and_partial_word_labels(self):
        self.decisions[0]['instances'][0]['policyBasis'] = PACKED_BASIS.replace('：', ': ')
        self.rules[0]['cells'][4] = '发布部门：原文部门\n拟稿及解释：原文解释部门'
        item = next(iter_materials(self.project()['items']))
        self.assertEqual(item['sourceSystem'], SOURCE_SYSTEM)
        self.assertEqual(item['policyBasis'], POLICY_BASIS)
        self.assertEqual(item['publishDepartment'], '原文部门\n拟稿及解释：原文解释部门')

    def test_text_exports_use_requested_metadata_order(self):
        self.rules[0]['cells'][4] = PACKED_BASIS
        self.decisions[0]['instances'][0].update(sourceSystem=SOURCE_SYSTEM, policyBasis=POLICY_BASIS,
            publishDepartment=DEPARTMENT, requirement='及时办理登记')
        response = dict(taskId='TEST', status='DONE', data=self.project())
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            for folder in ('manual_test_20261027', 'manual_test_20261027_002',
                           'manual_test_20261027_002_strict', 'manual_test_20261027_003'):
                with self.subTest(folder=folder):
                    spec = importlib.util.spec_from_file_location('export_case', root / folder / 'run_test.py')
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    target = Path(directory) / 'result.txt'
                    module.export_text(response, target=target)
                    text = target.read_text(encoding='utf-8-sig')
                    self.assertNotIn('PDF SHA-256', text)
                    start = text.index('R0001 ')
                    block = text[start:text.index('R0002 ', start)]
                    labels = ['具体要求', '来源系统', '制度依据', '发布日期', '发布部门']
                    offsets = [block.index(label + '：') for label in labels]
                    self.assertEqual(offsets, sorted(offsets))
                    for label in ('来源系统', '制度依据', '发布部门', '发布日期'):
                        self.assertEqual(block.count(label + '：'), 1)
                    self.assertNotIn('发布依据：', block)
                    self.assertNotIn('适用理由：', block)
                    self.assertNotIn('来源规则：', block)

    def test_legacy_text_export_hides_audit_fields_without_changing_input(self):
        response = dict(taskId='OLD', status='DONE', data=self.project())
        for item in iter_materials(response['data']['items']):
            item.update(reason='历史理由', sourceRuleId='1.1', sourceSystem=PACKED_BASIS)
        before = copy.deepcopy(response)
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('legacy_export', root / 'manual_test_20261027/run_test.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'old.txt'
            module.export_text(response, target=target)
            text = target.read_text(encoding='utf-8-sig')
        self.assertNotIn('适用理由：', text)
        self.assertNotIn('来源规则：', text)
        self.assertNotIn('发布依据：', text)
        self.assertEqual(text.count('来源系统：'), 2)
        self.assertEqual(text.count('制度依据：'), 2)
        self.assertEqual(before, response)

    def test_over_sixty_items_and_source_position_id(self):
        self.business, self.approval, self.snaps, self.rules, self.context = inputs(74)
        self.approval['products'][0]['extractedProductName'] = '甲'
        self.snaps[0]['filename'] = '甲材料清单.docx'
        self.rules[0]['sourceRuleId'] = 'table:1/row:2'
        self.decisions = [decision(r, self.context) for r in self.rules]
        data = self.project()
        self.assertEqual(data['total'], 74)
        self.assertEqual(list(iter_materials(data['items']))[-1]['itemId'], 'R0074')
        self.assertNotIn('sourceRuleId', next(iter_materials(data['items'])))

    def test_excluded_product_is_kept_in_metadata_and_zero_fails(self):
        self.approval['products'].append(dict(productName='乙', extractedProductName='乙'))
        self.snaps.append(dict(name='乙', filename='乙材料清单.docx'))
        self.rules[0].update(productName='乙', ruleKey='乙:1')
        self.decisions = [decision(r, self.context) for r in self.rules]
        self.decisions[0].update(action='CHECK', instances=[])
        data = self.project()
        self.assertEqual(data['total'], 1)
        self.assertEqual([p['productName'] for p in data['products']], ['甲', '乙'])
        self.assertEqual(data['items'][0]['productName'], '甲')
        self.decisions[1].update(action='EXCLUDE', instances=[])
        with self.assertRaises(result_module.MaterialResultError):
            self.project()

    def test_missing_material_display_stays_empty_without_invented_name(self):
        self.decisions[0]['instances'][0].update(materialName='', subject='A', requirement='有效期内')
        self.assertEqual(next(iter_materials(self.project()['items']))['materialName'], '')

    def test_missing_coverage_invalid_hash_and_cancel(self):
        with self.assertRaises(asyncio.CancelledError):
            self.project(cancel_check=lambda: True)
        self.decisions.pop()
        with self.assertRaises(result_module.MaterialResultError):
            self.project()
        self.decisions = [decision(r, self.context) for r in self.rules]
        with self.assertRaises(result_module.MaterialResultError):
            result_module.project_material_result(self.approval, self.snaps, self.rules, self.decisions, 'bad')


if __name__ == '__main__':
    unittest.main()
