import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from model_client import ModelReply

matching = __import__('product_matching') if importlib.util.find_spec('product_matching') else None


class Model:
    def __init__(self, *replies, retries=0):
        self.replies, self.calls = list(replies), []
        self.settings = SimpleNamespace(max_retries=retries)

    async def complete(self, stage, messages):
        self.calls.append((stage, json.loads(json.dumps(messages))))
        value = self.replies.pop(0)
        return ModelReply(json.dumps(value, ensure_ascii=False), 'stop')


class MatchingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(matching, 'product-to-checklist matching is missing')

    async def test_exact_names_need_no_model_and_keep_product_order(self):
        model = Model()
        result = await matching.match_product_checklists(['甲', '乙'], ['乙', '甲'], model)
        self.assertEqual([r['checklistProductName'] for r in result], ['甲', '乙'])
        self.assertTrue(all(r['method'] == 'EXACT' for r in result))
        self.assertEqual(model.calls, [])

    async def test_model_maps_approved_name_variation_from_existing_catalog(self):
        model = Model({'matches': [{'extractedProductName': '法人房产按揭贷款', 'checklistProductName': '法人按揭贷款'}]})
        result = await matching.match_product_checklists(['法人房产按揭贷款'], ['法人按揭贷款', '个人额度房贷'], model)
        self.assertEqual(result, [{'extractedProductName': '法人房产按揭贷款', 'checklistProductName': '法人按揭贷款', 'method': 'MODEL'}])
        stage, messages = model.calls[0]
        self.assertEqual(stage, 'generate')
        self.assertEqual(json.loads(messages[1]['content'])['catalog'], ['法人按揭贷款', '个人额度房贷'])

    async def test_invented_ambiguous_duplicate_and_missing_selections_fail(self):
        for rows in ([], [{'extractedProductName': '甲别名', 'checklistProductName': ''}],
                     [{'extractedProductName': '甲别名', 'checklistProductName': '不存在'}],
                     [{'extractedProductName': '另一个产品', 'checklistProductName': '甲'}],
                     [{'extractedProductName': '甲别名', 'checklistProductName': '甲'}] * 2):
            with self.subTest(rows=rows), self.assertRaises(matching.ProductMatchingError):
                await matching.match_product_checklists(['甲别名'], ['甲'], Model({'matches': rows}))

    async def test_invalid_reply_can_be_repaired_with_full_candidates(self):
        model = Model({}, {'matches': [{'extractedProductName': '甲别名', 'checklistProductName': '甲'}]}, retries=1)
        result = await matching.match_product_checklists(['甲别名'], ['甲', '乙'], model)
        self.assertEqual(result[0]['checklistProductName'], '甲')
        self.assertEqual(model.calls[1][1][1], model.calls[0][1][1])

    async def test_cancelled_matching_does_not_call_model(self):
        model = Model()
        with self.assertRaises(asyncio.CancelledError):
            await matching.match_product_checklists(['甲别名'], ['甲'], model, lambda: True)
        self.assertFalse(model.calls)


if __name__ == '__main__':
    unittest.main()
