"""Directory-tree business behaviour, including historical result views."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from test_checklist_rule_inventory import snapshot, HEADER

tree = __import__('material_tree') if importlib.util.find_spec('material_tree') else None


def leaf(product='甲', source='1.1.1', number=1):
    return dict(itemId=f'R{number:04d}', sourceRuleId=source, itemType='MATERIAL', productName=product,
        materialName=f'[{product}]企业章程', sourceSystem='上银办', policyBasis='', publishDate='2026年',
        publishDepartment='上海银行', applicability='APPLICABLE', requirement='', reason='原材料理由')


def directory(identifier, name):
    return dict(categoryId=identifier, categoryName=name)


def metadata(name='甲'):
    return dict(productName=name, extractedProductName=name, checklistFileName=name + '材料清单.docx')


class TreeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(tree, 'Directory tree output is not implemented')
        self.catalog = {'甲': [[directory('1', '基本资料')],
            [directory('1', '基本资料'), directory('1.1', '借款人基本资料')],
            [directory('2', '合同资料')], [directory('2', '合同资料'), directory('2.1', '签约资料')],
            [directory('4', '本产品的最后类别')]]}

    def test_full_word_catalog_includes_final_empty_heading(self):
        snaps = [snapshot([HEADER, ['1', '基本资料'], ['1.1', '借款人基本资料'],
                           ['1.1.1', '企业章程', '必备', '', ''], ['9', '最后类别'] ], '甲')]
        result = tree.build_category_catalog(snaps)
        self.assertEqual(result['甲'], [[directory('1', '基本资料')],
            [directory('1', '基本资料'), directory('1.1', '借款人基本资料')], [directory('9', '最后类别')]])

    def test_word_heading_paragraph_breaks_match_indexed_directory_names(self):
        snaps = [snapshot([HEADER, ['1', '基本\n资料'], ['1.1', '借款人\n基本资料'],
                           ['1.1.1', '企业章程', '必备', '', '']], '甲')]
        result = tree.build_category_catalog(snaps)
        self.assertEqual(result['甲'][-1], [directory('1', '基本\n资料'), directory('1.1', '借款人\n基本资料')])

    def test_source_order_pruning_counts_and_global_ids(self):
        located = [(leaf(source='2.1.1'), self.catalog['甲'][3]),
                   (leaf(source='1.1.1', number=2), self.catalog['甲'][1]),
                   (leaf(source='话术库:行3:材料1', number=3), self.catalog['甲'][-1])]
        before = copy.deepcopy(located)
        items = tree.group_materials([metadata()], located, self.catalog)
        self.assertEqual([c['categoryId'] for c in items[0]['categories']], ['1', '2', '4'])
        flat = list(tree.iter_materials(items))
        self.assertEqual([i['sourceRuleId'] for i in flat], ['1.1.1', '2.1.1', '话术库:行3:材料1'])
        self.assertEqual([i['itemId'] for i in flat], ['R0001', 'R0002', 'R0003'])
        self.assertEqual(before, located)

    def test_deep_paths_different_product_names_and_empty_product(self):
        self.catalog['乙'] = [[directory('1', '乙的特殊名称')],
            [directory('1', '乙的特殊名称'), directory('1.1', '子类'), directory('1.1.1', '三级类')]]
        items = tree.group_materials([metadata(), metadata('乙'), metadata('丙')],
            [(leaf('甲'), self.catalog['甲'][1]), (leaf('乙'), self.catalog['乙'][1])], self.catalog)
        self.assertEqual(items[1]['categories'][0]['categoryName'], '乙的特殊名称')
        self.assertEqual(items[1]['categories'][0]['children'][0]['children'][0]['categoryName'], '三级类')
        self.assertEqual(items[2], dict(productName='丙', categories=[]))
        self.assertEqual(len(list(tree.iter_materials(items))), 2)

    def test_category_selection_uses_word_paths_and_unknown_last_root(self):
        self.assertEqual(tree.select_category(self.catalog, '甲', []), self.catalog['甲'][-1])
        self.assertEqual(tree.select_category(self.catalog, '甲', ['1', '1.1']), self.catalog['甲'][1])
        for bad in (['1.1'], ['invented'], ['1', '4'], ['1', 1]):
            with self.assertRaises(ValueError):
                tree.select_category(self.catalog, '甲', bad)

    def test_unclassified_only_when_no_original_category_can_be_recovered(self):
        items = tree.group_materials([metadata()], [(leaf(), [])], self.catalog)
        self.assertEqual(items[0]['categories'][0]['categoryName'], '未分类材料')

    def test_schema_validates_leaves_product_membership_pruning_and_ids(self):
        from task_models import HistoricalTaskResult
        data = dict(sourceDocumentHash='a' * 64, generatedAt='2026-10-27T02:00:00+00:00', total=1,
            products=[metadata()], items=tree.group_materials([metadata()], [(leaf(), self.catalog['甲'][1])], self.catalog))
        self.assertEqual(HistoricalTaskResult.model_validate(data).model_dump(mode='json'), data)
        invalid = []
        bad = copy.deepcopy(data); bad['total'] = 2; invalid.append(bad)
        bad = copy.deepcopy(data); bad['items'][0]['productName'] = '乙'; invalid.append(bad)
        bad = copy.deepcopy(data); bad['items'][0]['categories'][0]['children'][0]['materials'][0]['productName'] = '乙'; invalid.append(bad)
        bad = copy.deepcopy(data); bad['items'][0]['categories'][0]['children'][0]['materials'][0]['itemId'] = 'R0002'; invalid.append(bad)
        bad = copy.deepcopy(data); bad['items'][0]['categories'].append(dict(categoryId='9', categoryName='空', children=[], materials=[])); invalid.append(bad)
        for bad in invalid:
            with self.assertRaises(ValidationError):
                HistoricalTaskResult.model_validate(bad)


if __name__ == '__main__':
    unittest.main()
