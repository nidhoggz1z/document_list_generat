import asyncio
import hashlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docx_reader import W, read_docx
import main

rules = __import__('checklist_rules') if importlib.util.find_spec('checklist_rules') else None


def snapshot(rows, name='产品甲'):
    root = ET.Element(W + 'document')
    body = ET.SubElement(root, W + 'body')
    table = ET.SubElement(body, W + 'tbl')
    for row in rows:
        tr = ET.SubElement(table, W + 'tr')
        for index, text in enumerate(row):
            tc = ET.SubElement(tr, W + 'tc')
            if len(row) == 2 and index == 1:
                ET.SubElement(ET.SubElement(tc, W + 'tcPr'), W + 'gridSpan', {W + 'val': '4'})
            for line in text.split('\n'):
                ET.SubElement(ET.SubElement(ET.SubElement(tc, W + 'p'), W + 'r'), W + 't').text = line
    stream = io.BytesIO()
    with ZipFile(stream, 'w') as z:
        z.writestr('word/document.xml', ET.tostring(root))
    raw = stream.getvalue()
    return dict(name=name, raw_bytes=raw, content=read_docx(io.BytesIO(raw)),
                content_hash=hashlib.sha256(raw).hexdigest(), filename=name + '.docx')


HEADER = ['编号', '材料名称或核查事项', '提供要求', '适用条件及材料要求', '规程依据']


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(rules, 'T15 rule inventory is missing')

    def test_material_name_header_and_legacy_header_both_preserve_directories(self):
        for title in ('材料名称', '材料名称或核查事项'):
            header = [HEADER[0], title, *HEADER[2:]]
            snap = snapshot([header, ['1', '基本资料'], ['1.1', '借款人基本资料'],
                             ['1.1.1', '企业章程', '必备', '', '']])
            result = rules.build_rule_inventory([snap])
            self.assertEqual(result[0]['cells'][1], '企业章程')
            self.assertEqual([g['title'] for g in result[0]['groups']], ['基本资料', '借款人基本资料'])

    def test_all_real_checklists_have_complete_inventory(self):
        store = main.DocumentStore(Path(__file__).resolve().parents[1] / 'list')
        snapshots = store.snapshot(list(store.documents))
        self.assertEqual(len(snapshots), 75)
        result = rules.build_rule_inventory(snapshots)
        keys = [r['ruleKey'] for r in result]
        self.assertEqual(len(keys), len(set(keys)))
        for snap in snapshots:
            with ZipFile(io.BytesIO(snap['raw_bytes'])) as z:
                root = ET.fromstring(z.read('word/document.xml'))
            expected = [r for t in root.findall('.//' + W + 'tbl')
                        for r in t.findall(W + 'tr')[1:] if len(r.findall(W + 'tc')) == 5]
            actual = [r for r in result if r['productName'] == snap['name']]
            self.assertEqual(len(actual), len(expected), snap['name'])
            for entry, row in zip(actual, expected):
                expected_cells = ['\n'.join(''.join((n.text or '') if n.tag == W + 't' else
                    '\n' if n.tag in (W + 'br', W + 'cr') else '\t' if n.tag == W + 'tab' else ''
                    for n in p.iter()) for p in c.findall(W + 'p')) for c in row.findall(W + 'tc')]
                self.assertEqual(entry['cells'], expected_cells)
                self.assertEqual(entry['sourceRuleId'], expected_cells[0])
                self.assertTrue(entry['groups'])
                self.assertIn('2026', entry['documentContext'])
        self.assertGreater(max(sum(r['productName'] == s['name'] for r in result) for s in snapshots), 60)

    def test_duplicate_ids_multiline_groups_and_unlabelled_rows(self):
        snap = snapshot([HEADER, ['1', '基本资料'], ['1.1', '主体资料'],
                         ['1.1.1', '身份证\n其他证明', '如需', '第一行\n第二行', '条例'],
                         ['1.1.1', '核验', '核查项 按条件', '正文', ''],
                         ['', '附加材料', '按需', '', '']])
        result = rules.build_rule_inventory([snap, dict(snap, name='产品乙')])
        self.assertEqual(len(result), 6)
        self.assertEqual(len({r['ruleKey'] for r in result}), 6)
        self.assertEqual(result[0]['cells'][1], '身份证\n其他证明')
        self.assertEqual([g['title'] for g in result[0]['groups']], ['基本资料', '主体资料'])
        self.assertEqual(result[1]['rawMarker'], '核查项 按条件')
        self.assertEqual(result[1]['kind'], 'CHECK')
        self.assertEqual(result[0]['kind'], 'MATERIAL')
        self.assertEqual(result[2]['sourceRuleId'], 'table:1/row:6')
        self.assertEqual(result, rules.build_rule_inventory([snap, dict(snap, name='产品乙')]))

    def test_unknown_or_incomplete_layout_and_bad_snapshot_fail(self):
        for rows in [[HEADER], [HEADER, ['x', 'y', 'z']],
                     [HEADER, ['1', '', '通用', '', '']]]:
            with self.subTest(rows=rows), self.assertRaises(rules.RuleInventoryError):
                rules.build_rule_inventory([snapshot(rows)])
        good = snapshot([HEADER, ['1', '材料', '通用', '', '']])
        for changes in [{'raw_bytes': b'bad'}, {'content_hash': 'wrong'}, {'content': 'wrong'}]:
            with self.subTest(changes=changes), self.assertRaises(rules.RuleInventoryError):
                rules.build_rule_inventory([dict(good, **changes)])
        with self.assertRaises(rules.RuleInventoryError):
            rules.build_rule_inventory([good, good])
        with self.assertRaises(asyncio.CancelledError):
            rules.build_rule_inventory([good], cancel_check=lambda: True)

    def test_automatic_numbering_and_explicit_break_are_preserved(self):
        snap = snapshot([HEADER, ['1', '分组'], ['', '第一段', '通用', '', '']])
        with ZipFile(io.BytesIO(snap['raw_bytes'])) as z:
            root = ET.fromstring(z.read('word/document.xml'))
        p = root.findall('.//' + W + 'tr')[2].findall(W + 'tc')[1].find(W + 'p')
        props = ET.SubElement(p, W + 'pPr')
        num = ET.SubElement(props, W + 'numPr')
        ET.SubElement(num, W + 'numId', {W + 'val': '1'})
        run = p.find(W + 'r')
        ET.SubElement(run, W + 'br')
        ET.SubElement(run, W + 't').text = '第二行'
        numbering = ET.Element(W + 'numbering')
        abstract = ET.SubElement(numbering, W + 'abstractNum', {W + 'abstractNumId': '0'})
        level = ET.SubElement(abstract, W + 'lvl', {W + 'ilvl': '0'})
        ET.SubElement(level, W + 'numFmt', {W + 'val': 'decimal'})
        ET.SubElement(level, W + 'lvlText', {W + 'val': '%1.'})
        num = ET.SubElement(numbering, W + 'num', {W + 'numId': '1'})
        ET.SubElement(num, W + 'abstractNumId', {W + 'val': '0'})
        stream = io.BytesIO()
        with ZipFile(stream, 'w') as z:
            z.writestr('word/document.xml', ET.tostring(root))
            z.writestr('word/numbering.xml', ET.tostring(numbering))
        raw = stream.getvalue()
        snap.update(raw_bytes=raw, content_hash=hashlib.sha256(raw).hexdigest(), content=read_docx(io.BytesIO(raw)))
        self.assertEqual(rules.build_rule_inventory([snap])[0]['cells'][1], '1. 第一段\n第二行')


if __name__ == '__main__':
    unittest.main()
