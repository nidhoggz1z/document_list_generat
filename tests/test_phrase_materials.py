"""Business constraints for Excel-backed supplemental materials; no live model."""
import asyncio
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from xml.etree import ElementTree as E
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from test_material_generation import inputs, decision, FakeModel
from model_client import ModelReply
from material_tree import iter_materials

library = __import__('phrase_rules') if importlib.util.find_spec('phrase_rules') else None
materials = __import__('phrase_materials') if importlib.util.find_spec('phrase_materials') else None
FILENAME = '话术库对应材料.xlsx'
S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def workbook(rows=None, shared=False):
    if rows is None:
        rows = [['话术一', '由甲公司提供连带责任保证担保',
                 '企业担保人：\n1.2 基本资料\n（1）营业执照\n（2）公司章程\n4.1 上市公司公告（如需）'],
                ['话术一', '股权融资款到账，并提供验资报告', '借款人：\n3.1 审批材料\n（1）验资报告']]
    sheet = E.Element('worksheet', xmlns=S)
    data = E.SubElement(sheet, 'sheetData')
    strings = E.Element('sst', xmlns=S)
    for number, values in enumerate([['模块话术序号', '标准话术', '对应的放款影像材料'], *rows], 1):
        row = E.SubElement(data, 'row', r=str(number))
        for column, value in zip('ABC', values):
            cell = E.SubElement(row, 'c', r=f'{column}{number}', t='s' if shared else 'inlineStr')
            if shared:
                E.SubElement(cell, 'v').text = str(len(strings))
                E.SubElement(E.SubElement(strings, 'si'), 't').text = value
            else:
                E.SubElement(E.SubElement(cell, 'is'), 't').text = value
    stream = io.BytesIO()
    with ZipFile(stream, 'w') as z:
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets>'
                   '<sheet name="审批决议话术库对应材料" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   f'<Relationship Id="rId1" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml', E.tostring(sheet))
        if shared:
            z.writestr('xl/sharedStrings.xml', E.tostring(strings))
    return stream.getvalue()


class PhraseLibraryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(library, 'Excel supplemental source is not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / FILENAME

    def test_inline_shared_blank_rows_and_duplicate_phrase_ids(self):
        for shared in (False, True):
            raw = workbook(shared=shared)
            self.path.write_bytes(raw)
            snap = library.snapshot_phrase_library(self.root)
            self.assertEqual(snap['raw_bytes'], raw)
            self.assertEqual(snap['content_hash'], hashlib.sha256(raw).hexdigest())
            self.assertEqual([r['rowKey'] for r in snap['rules']], ['row:2', 'row:3'])
            self.assertEqual([r['phraseId'] for r in snap['rules']], ['话术一', '话术一'])
            self.assertEqual([m['name'] for m in snap['rules'][0]['materials']],
                             ['营业执照', '公司章程', '上市公司公告（如需）'])
            self.assertEqual(len(snap['rules'][1]['materials']), 1)

    def test_real_library_has_19_rules_and_keeps_original_bytes(self):
        root = Path(__file__).resolve().parents[1] / 'list'
        snap = library.snapshot_phrase_library(root)
        self.assertEqual(len(snap['rules']), 19)
        self.assertEqual(snap['raw_bytes'], (root / FILENAME).read_bytes())
        self.assertEqual(sum(r['phraseId'] == '担保方式标准话术8' for r in snap['rules']), 2)
        self.assertIn('股东会决议/董事会决议', [m['name'] for r in snap['rules'] for m in r['materials']])

    def test_missing_corrupt_incomplete_or_empty_library_fails(self):
        for raw in (None, b'broken', workbook([]), workbook([['话术', '', '2.2 借款合同']]),
                    workbook([['话术', '出现', '借款人：']])):
            self.path.unlink(missing_ok=True)
            if raw is not None:
                self.path.write_bytes(raw)
            with self.assertRaises(library.PhraseLibraryError):
                library.snapshot_phrase_library(self.root)

    def test_updates_use_new_snapshot_without_mutating_old_one(self):
        self.path.write_bytes(workbook())
        old = library.snapshot_phrase_library(self.root)
        self.path.write_bytes(workbook([['新话术', '新条件', '2.2 新合同']]))
        new = library.snapshot_phrase_library(self.root)
        self.assertNotEqual(old['content_hash'], new['content_hash'])
        self.assertEqual(len(old['rules']), 2)
        self.assertEqual(len(new['rules']), 1)

    def test_cancel_is_observed(self):
        self.path.write_bytes(workbook())
        with self.assertRaises(asyncio.CancelledError):
            library.snapshot_phrase_library(self.root, lambda: True)


class PhraseGenerationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(materials, 'Excel matching and merging are not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / FILENAME).write_bytes(workbook())
        self.snap = library.snapshot_phrase_library(root)
        self.business, self.approval, self.snaps, self.rules, self.context = inputs()
        self.approval['pages'][0]['text'] = '甲 由甲公司提供连带责任保证担保，股权融资款到账，并提供验资报告。'
        self.rules[0]['cells'][1] = '营业执照或其他主体资格证明'

    def response(self, matched=True):
        result = []
        for row in self.snap['rules']:
            product = dict(productName='甲', materials=[dict(materialKey=m['materialKey'], applicability='APPLICABLE', categoryPath=['1'], instances=[dict(
                objectKey='', subject='', materialName=m['name'], requirement='整包收取，不按如需过滤',
                wordRuleKey=self.rules[0]['ruleKey'] if m['name'] == '营业执照' else '')]) for m in row['materials']])
            result.append(dict(rowKey=row['rowKey'], scope='EXPLICIT' if matched else 'NONE',
                matches=[dict(page_number=1, quote=row['phrase'])] if matched else [],
                products=[product] if matched else []))
        return {'decisions': result}

    async def generate(self, model, **kwargs):
        return await materials.generate_phrase_materials(self.business, self.approval, self.snaps,
            self.rules, self.context, self.snap, model, **kwargs)

    def merge(self, ds, base=None):
        return materials.merge_phrase_materials(self.rules,
            [decision(r, self.context) for r in self.rules] if base is None else base,
            ds, self.snap)

    async def test_no_match_never_adds_materials(self):
        ds = await self.generate(FakeModel([self.response(False)]))
        rules, decisions = self.merge(ds)
        self.assertEqual(rules, self.rules)
        self.assertEqual(len(decisions), 1)

    async def test_word_categories_win_and_unlocated_excel_is_excluded(self):
        from material_tree import catalog_from_rules, rule_category_path
        self.rules[0]['groups'] = [dict(sourceRuleId='1', title='基本资料'),
                                   dict(sourceRuleId='1.1', title='借款人基本资料')]
        catalog = catalog_from_rules(self.rules, ['甲'])
        catalog['甲'].append([dict(categoryId='9', categoryName='最后一类')])
        response = self.response()
        for d in response['decisions']:
            for m in d['products'][0]['materials']:
                m['categoryPath'] = []
        model = FakeModel([response])
        ds = await self.generate(model, category_catalog=catalog)
        self.assertEqual(json.loads(model.calls[0][1][1]['content'])['categoryCatalog'], catalog)
        rules, _ = materials.merge_phrase_materials(self.rules, [decision(self.rules[0], self.context)],
            ds, self.snap, category_catalog=catalog)
        self.assertEqual(rule_category_path(rules[0]), catalog['甲'][1])
        self.assertEqual(len(rules), 1)
        self.assertEqual(ds[0]['products'][0]['materials'][1]['applicability'], 'UNKNOWN')

    async def test_model_can_choose_existing_subcategory_but_cannot_invent_one(self):
        catalog = {'甲': [[dict(categoryId='1', categoryName='基本资料')],
            [dict(categoryId='1', categoryName='基本资料'), dict(categoryId='1.1', categoryName='借款人资料')]]}
        data = self.response()
        for d in data['decisions']:
            for m in d['products'][0]['materials']:
                m['categoryPath'] = ['1', '1.1']
        ds = await self.generate(FakeModel([data]), category_catalog=catalog)
        rules, _ = materials.merge_phrase_materials(self.rules, [decision(self.rules[0], self.context)],
            ds, self.snap, category_catalog=catalog)
        self.assertTrue(all(r['groups'][-1]['sourceRuleId'] == '1.1' for r in rules[1:]))
        data['decisions'][0]['products'][0]['materials'][0]['categoryPath'] = ['不存在']
        with self.assertRaises(materials.PhraseMaterialError):
            await self.generate(FakeModel([data], retries=0), category_catalog=catalog)

    async def test_whole_bundle_excel_priority_and_word_name_number(self):
        ds = await self.generate(FakeModel([self.response()]))
        base = [decision(self.rules[0], self.context)]
        base[0].update(action='EXCLUDE', instances=[])
        before = copy.deepcopy((self.rules, base, self.snap))
        rules, decisions = self.merge(ds, base)
        self.assertEqual(decisions[0]['action'], 'KEEP')
        self.assertEqual(decisions[0]['instances'][0]['materialName'], self.rules[0]['cells'][1])
        self.assertEqual(rules[0]['sourceRuleId'], '0')
        self.assertEqual(len(rules), 4)
        self.assertTrue(any('上市公司公告' in i['materialName'] for d in decisions for i in d['instances']))
        self.assertTrue(any(r['sourceRuleId'] == '话术库:行3:材料1' for r in rules))
        self.assertIn('批复第1页', decisions[0]['instances'][0]['reason'])
        self.assertEqual(before, (self.rules, base, self.snap))

    async def test_missing_row_bundle_item_or_forged_quote_fails(self):
        cases = []
        data = self.response(); data['decisions'].pop(); cases.append(data)
        data = self.response(); data['decisions'][0]['products'][0]['materials'].pop(); cases.append(data)
        data = self.response(); data['decisions'][0]['matches'][0]['quote'] = '原文不存在'; cases.append(data)
        data = self.response(); data['decisions'][0]['matches'][0]['quote'] = ''; cases.append(data)
        data = self.response(); data['decisions'][0]['matches'][0]['page_number'] = True; cases.append(data)
        data = self.response(False); data['decisions'][0]['products'] = self.response()['decisions'][0]['products']; cases.append(data)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(materials.PhraseMaterialError):
                await self.generate(FakeModel([data], retries=0))

    async def test_unknown_product_scope_excludes_instead_of_expanding_to_all_products(self):
        self.approval['products'].append(dict(productName='乙', creditRecords=[]))
        data = self.response()
        for d in data['decisions']:
            d['scope'] = 'UNKNOWN'
        ds = await self.generate(FakeModel([data], retries=0))
        self.assertTrue(all(d['scope'] == 'NONE' and d['products'] == [] for d in ds))

    async def test_explicit_product_scope_and_no_cross_product_word_mapping(self):
        self.approval['products'].append(dict(productName='乙', creditRecords=[]))
        data = self.response()
        for d in data['decisions']:
            d['scope'] = 'EXPLICIT'
        await self.generate(FakeModel([data]))
        data['decisions'][0]['products'][0]['productName'] = '乙'
        with self.assertRaises(materials.PhraseMaterialError):
            await self.generate(FakeModel([data], retries=0))

    async def test_personalized_instances_are_separate_and_foreign_objects_fail(self):
        data = self.response()
        source = data['decisions'][0]['products'][0]['materials'][0]
        original = source['instances'][0]
        source['instances'] = [dict(original, objectKey=o['objectKey'], subject=o['name'])
                               for o in self.context['objects'] if o['kind'] == 'guarantor']
        ds = await self.generate(FakeModel([data]))
        base = [decision(self.rules[0], self.context, ('guarantor',))]
        rules, decisions = self.merge(ds, base)
        self.assertEqual([i['subject'] for i in decisions[0]['instances']], ['A', 'B'])
        self.assertEqual(len(decisions[0]['instances']), 2)
        source['instances'][0]['objectKey'] = 'invented'
        with self.assertRaises(materials.PhraseMaterialError):
            await self.generate(FakeModel([data], retries=0))

    async def test_check_item_promoted_and_repeated_materials_merge(self):
        self.rules[0]['kind'] = 'CHECK'
        data = self.response()
        data['decisions'][1]['products'][0]['materials'][0]['instances'][0].update(
            wordRuleKey=self.rules[0]['ruleKey'], materialName='营业执照')
        base = [decision(self.rules[0], self.context)]
        base[0].update(action='CHECK', instances=[])
        ds = await self.generate(FakeModel([data]))
        rules, decisions = self.merge(ds, base)
        self.assertEqual(len(decisions[0]['instances']), 1)
        self.assertEqual(decisions[0]['action'], 'KEEP')
        self.assertEqual(rules[0]['kind'], 'MATERIAL')

    async def test_generic_word_material_is_refined_to_subject_without_duplicate(self):
        data = self.response()
        customer = next(o for o in self.context['objects'] if o['kind'] == 'customer')
        item = data['decisions'][0]['products'][0]['materials'][0]['instances'][0]
        item.update(objectKey=customer['objectKey'], subject=customer['name'])
        base = [decision(self.rules[0], self.context)]
        base[0]['instances'][0].update(sourceSystem='原文系统', policyBasis='原文依据',
                                      requirement='旧Word条件，如需才提供')
        ds = await self.generate(FakeModel([data]))
        _, decisions = self.merge(ds, base)
        self.assertEqual(len(decisions[0]['instances']), 1)
        final = decisions[0]['instances'][0]
        self.assertEqual(final['subject'], '公司')
        self.assertEqual(final['sourceSystem'], '原文系统')
        self.assertEqual(final['policyBasis'], '原文依据')
        self.assertNotIn('旧Word条件', final['requirement'])

    async def test_exact_same_name_uses_word_even_without_model_mapping(self):
        self.rules[0]['cells'][1] = '营业执照'
        data = self.response()
        data['decisions'][0]['products'][0]['materials'][0]['instances'][0]['wordRuleKey'] = ''
        ds = await self.generate(FakeModel([data]))
        rules, decisions = self.merge(ds)
        self.assertEqual(sum(r['cells'][1] == '营业执照' for r in rules), 1)
        self.assertEqual(len(decisions[0]['instances']), 1)

    async def test_batches_keep_full_context_and_cover_all_rows(self):
        response = self.response(False)
        def respond(messages):
            payload = json.loads(messages[1]['content'])
            self.assertEqual(payload['approval'], self.approval)
            self.assertEqual(len(payload['phraseRules']), 2)
            return {'decisions': [d for d in response['decisions'] if d['rowKey'] in payload['requestedRowKeys']]}
        model = FakeModel(responder=respond)
        result = await self.generate(model, batch_size=1)
        self.assertEqual(len(result), 2)
        self.assertEqual(len(model.calls), 2)

    async def test_material_names_do_not_embed_outline_headers_or_source_numbers(self):
        from material_result import project_material_result
        self.approval['products'][0]['extractedProductName'] = '甲'
        self.snaps[0]['filename'] = '甲材料清单.docx'
        data = self.response()
        for d in data['decisions']:
            for m in d['products'][0]['materials']:
                for instance in m['instances']:
                    instance['requirement'] = ''
        ds = await self.generate(FakeModel([data]))
        rules, decisions = self.merge(ds)
        result = project_material_result(self.approval, self.snaps, rules, decisions, 'a' * 64)
        self.assertEqual([i['materialName'] for i in iter_materials(result['items'])],
            ['[甲]营业执照或其他主体资格证明', '[甲]公司章程', '[甲]上市公司公告（如需）', '[甲]验资报告'])

    async def test_table_quote_is_valid_but_cross_page_quote_is_not(self):
        self.approval['pages'][0]['tables'] = [[self.snap['rules'][0]['phrase']]]
        self.approval['pages'][0]['text'] = self.snap['rules'][1]['phrase']
        await self.generate(FakeModel([self.response()]))
        data = self.response()
        data['decisions'][0]['matches'][0]['quote'] = '不存在的跨页拼接'
        with self.assertRaises(materials.PhraseMaterialError):
            await self.generate(FakeModel([data], retries=0))

    async def test_cancel_in_flight_phrase_call(self):
        model = FakeModel()
        started, ended = asyncio.Event(), asyncio.Event()
        async def complete(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                ended.set()
        model.complete = complete
        task = asyncio.create_task(self.generate(model))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(ended.is_set())

    async def test_retry_full_context_truncation_and_cancellation(self):
        model = FakeModel([{'decisions': []}, self.response()])
        await self.generate(model)
        self.assertEqual(len(model.calls), 2)
        payload = json.loads(model.calls[0][1][1]['content'])
        self.assertEqual(payload['approval'], self.approval)
        self.assertEqual(len(payload['phraseRules']), 2)
        self.assertEqual(json.loads(model.calls[1][1][1]['content']), payload)
        with self.assertRaises(materials.PhraseMaterialError):
            await self.generate(FakeModel([ModelReply('{}', 'length')]))
        model = FakeModel([self.response()])
        with self.assertRaises(asyncio.CancelledError):
            await self.generate(model, cancel_check=lambda: True)
        self.assertEqual(model.calls, [])


if __name__ == '__main__':
    unittest.main()
