import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
if importlib.util.find_spec('main'):
    import main
else:
    main = None

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def make_docx(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = ET.Element('{%s}document' % W)
    body = ET.SubElement(doc, '{%s}body' % W)
    p = ET.SubElement(body, '{%s}p' % W)
    ET.SubElement(ET.SubElement(p, '{%s}r' % W), '{%s}t' % W).text = text
    with ZipFile(path, 'w') as z:
        z.writestr('word/document.xml', ET.tostring(doc))


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enterContext(patch.dict('os.environ', {'TASK_STORAGE_ROOT': str(self.root / 'storage')}))
        make_docx(self.root / '流动资金贷款材料清单.docx', '普通流贷正文，不能截断。')
        make_docx(self.root / '流动资金循环贷款材料清单.docx', '循环贷款正文，提及流动资金贷款也不应误召回。')
        make_docx(self.root / '个人委托贷款业务_材料清单.docx', '个人委托贷款材料正文')
        self.store = main.DocumentStore(self.root)

    def test_project_returns_only_its_exact_checklist(self):
        result = self.store.retrieve('流动资金贷款')
        self.assertEqual(result, '普通流贷正文，不能截断。')

    def test_retrieval_response_is_only_document_text(self):
        from fastapi.testclient import TestClient
        with TestClient(main.create_app(self.root)) as client:
            response = client.post('/retrieve', json={'param': '流动资金贷款'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.text, '普通流贷正文，不能截断。')
            self.assertEqual(response.headers['content-type'], 'text/plain; charset=utf-8')
            self.assertNotIn('content-disposition', response.headers)

    def test_cyclic_loan_is_a_separate_project(self):
        result = self.store.retrieve('流动资金循环贷款')
        self.assertEqual(result, '循环贷款正文，提及流动资金贷款也不应误召回。')

    def test_missing_exact_project_never_falls_back_to_similar_name(self):
        (self.root / '流动资金贷款材料清单.docx').unlink()
        with self.assertRaises(main.RetrievalError) as error:
            main.DocumentStore(self.root).retrieve('流动资金贷款')
        self.assertEqual(error.exception.status_code, 404)

    def test_underscore_before_checklist_suffix_is_filename_separator(self):
        result = self.store.retrieve('个人委托贷款业务')
        self.assertEqual(result, '个人委托贷款材料正文')
        with self.assertRaises(main.RetrievalError):
            self.store.retrieve('个人委托贷款')

    def test_only_outer_whitespace_is_trimmed(self):
        self.assertEqual(self.store.retrieve(' 流动资金贷款\n'), '普通流贷正文，不能截断。')
        for name in ['流动 资金贷款', '流动资金贷款材料清单', '流动资金贷款.docx', '流贷', '流贷类产品', '外汇流动资金贷款']:
            with self.subTest(name=name), self.assertRaises(main.RetrievalError) as error:
                self.store.retrieve(name)
            self.assertEqual(error.exception.status_code, 404)

    def test_invalid_inputs_are_rejected(self):
        for value in ['', '   ', None, 123, [], '../流动资金贷款', r'list\流动资金贷款', 'a' * 101, '\x00']:
            with self.subTest(value=value), self.assertRaises(main.RetrievalError) as error:
                self.store.retrieve(value)
            self.assertEqual(error.exception.status_code, 422)

    def test_duplicate_project_names_are_rejected(self):
        make_docx(self.root / '流动资金贷款_材料清单.docx', '同项目重复文件')
        with self.assertRaises(main.RetrievalError) as error:
            main.DocumentStore(self.root)
        self.assertEqual(error.exception.status_code, 409)

    def test_duplicates_in_subfolders_are_rejected(self):
        make_docx(self.root / '重复/流动资金贷款材料清单.docx', '重复')
        with self.assertRaises(main.RetrievalError):
            main.DocumentStore(self.root)

    def test_non_checklist_and_word_lock_files_are_ignored(self):
        (self.root / '无关审批要求.docx').write_bytes(b'not a checklist')
        (self.root / '~$流动资金贷款材料清单.docx').write_bytes(b'lock')
        self.assertEqual(len(main.DocumentStore(self.root).documents), 3)

    def test_missing_and_empty_directory_are_reported(self):
        for directory in [self.root / '不存在', self.root / '空目录']:
            if directory.name == '空目录':
                directory.mkdir()
            with self.assertRaises(main.RetrievalError):
                main.DocumentStore(directory)

    def test_empty_project_name_in_filename_is_rejected(self):
        make_docx(self.root / '材料清单.docx', '没有项目名称')
        with self.assertRaises(main.RetrievalError):
            main.DocumentStore(self.root)

    def test_corrupt_document_is_reported_not_silently_skipped(self):
        (self.root / '流动资金贷款材料清单.docx').write_bytes(b'broken')
        with self.assertRaises(main.RetrievalError):
            main.DocumentStore(self.root)

    def test_live_addition_updates_retrieval_projects_and_health(self):
        from fastapi.testclient import TestClient
        with TestClient(main.create_app(self.root)) as client:
            make_docx(self.root / '新增项目材料清单.docx', '新增材料正文')
            self.assertEqual(client.post('/retrieve', json={'param': '新增项目'}).text, '新增材料正文')
            self.assertIn('新增项目', [p['name'] for p in client.get('/projects').json()['projects']])
            self.assertEqual(client.get('/health').json()['document_count'], 4)

    def test_live_replacement_and_deletion_never_return_stale_content(self):
        path = self.root / '流动资金贷款材料清单.docx'
        make_docx(path, '这是替换后的新版材料，内容已经更新。')
        self.assertEqual(self.store.retrieve('流动资金贷款'), '这是替换后的新版材料，内容已经更新。')
        path.unlink()
        with self.assertRaises(main.RetrievalError) as error:
            self.store.retrieve('流动资金贷款')
        self.assertEqual(error.exception.status_code, 404)
        self.assertIn('循环贷款正文', self.store.retrieve('流动资金循环贷款'))

    def test_live_rename_removes_old_project_name(self):
        (self.root / '流动资金贷款材料清单.docx').rename(self.root / '新名称材料清单.docx')
        self.assertEqual(self.store.retrieve('新名称'), '普通流贷正文，不能截断。')
        with self.assertRaises(main.RetrievalError) as error:
            self.store.retrieve('流动资金贷款')
        self.assertEqual(error.exception.status_code, 404)

    def test_live_corrupt_replacement_reports_error_and_recovers(self):
        from fastapi.testclient import TestClient
        with TestClient(main.create_app(self.root)) as client:
            path = self.root / '流动资金贷款材料清单.docx'
            path.write_bytes(b'incomplete upload')
            self.assertEqual(client.post('/retrieve', json={'param': '流动资金贷款'}).status_code, 503)
            self.assertEqual(client.get('/health').status_code, 503)
            make_docx(path, '上传完成后的最新正文')
            result = client.post('/retrieve', json={'param': '流动资金贷款'})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.text, '上传完成后的最新正文')

    def test_live_duplicate_reports_conflict_and_recovers(self):
        path = self.root / '流动资金贷款_材料清单.docx'
        make_docx(path, '重复材料')
        with self.assertRaises(main.RetrievalError) as error:
            self.store.retrieve('流动资金贷款')
        self.assertEqual(error.exception.status_code, 409)
        path.unlink()
        self.assertEqual(self.store.retrieve('流动资金贷款'), '普通流贷正文，不能截断。')

    def test_live_empty_directory_recovers_when_document_is_added(self):
        for path in self.root.glob('*.docx'):
            path.unlink()
        with self.assertRaises(main.RetrievalError) as error:
            self.store.retrieve('流动资金贷款')
        self.assertEqual(error.exception.status_code, 503)
        make_docx(self.root / '新增项目材料清单.docx', '重新放入的材料')
        self.assertEqual(self.store.retrieve('新增项目'), '重新放入的材料')

    def test_live_concurrent_requests_receive_complete_updated_text(self):
        from concurrent.futures import ThreadPoolExecutor
        text = '完整更新正文\n' * 1000
        make_docx(self.root / '流动资金贷款材料清单.docx', text)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(self.store.retrieve, ['流动资金贷款'] * 12))
        self.assertEqual(results, [text] * 12)

    def test_live_change_during_refresh_is_rejected_then_retried(self):
        from unittest.mock import patch
        path = self.root / '流动资金贷款材料清单.docx'
        make_docx(path, '更新过程中的内容')
        original_reader = main.read_docx

        def changing_reader(source):
            content = original_reader(source)
            if source == path:
                make_docx(path, '更新完成后的最终正文')
            return content

        with patch('main.read_docx', side_effect=changing_reader):
            with self.assertRaises(main.RetrievalError) as error:
                self.store.retrieve('流动资金贷款')
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self.store.retrieve('流动资金贷款'), '更新完成后的最终正文')

    def test_live_unchanged_documents_reuse_parsed_content(self):
        from unittest.mock import patch
        with patch('main.read_docx', wraps=main.read_docx) as reader:
            self.assertEqual(self.store.retrieve('流动资金贷款'), '普通流贷正文，不能截断。')
            reader.assert_not_called()

    def test_word_tables_line_breaks_and_numbering(self):
        from docx_reader import read_docx
        path = self.root / 'numbering.docx'
        document = '''<w:document xmlns:w="%s"><w:body>
          <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>甲</w:t><w:br/><w:t>乙</w:t></w:r></w:p>
          <w:tbl><w:tr><w:tc><w:p><w:r><w:t>表头A</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>表头B</w:t></w:r></w:p></w:tc></w:tr>
          <w:tr><w:tc><w:p><w:r><w:t>值A</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>值B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
          <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>丙</w:t></w:r></w:p>
          <w:p><w:pPr><w:pStyle w:val="ListStyle"/></w:pPr><w:r><w:t>丁</w:t></w:r></w:p>
        </w:body></w:document>''' % W
        numbering = '''<w:numbering xmlns:w="%s"><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="chineseCounting"/><w:lvlText w:val="（%%1）"/></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="0"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="3"/></w:lvlOverride></w:num></w:numbering>''' % W
        styles = '''<w:styles xmlns:w="%s"><w:style w:styleId="ListStyle"><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr></w:style></w:styles>''' % W
        with ZipFile(path, 'w') as archive:
            archive.writestr('word/document.xml', document)
            archive.writestr('word/numbering.xml', numbering)
            archive.writestr('word/styles.xml', styles)
        content = read_docx(path)
        self.assertEqual(content, '（一） 甲\n乙\n[表格开始]\n表头A\t表头B\n值A\t值B\n[表格结束]\n（二） 丙\n（三） 丁')

    def test_api_contract_and_downloads(self):
        from fastapi.testclient import TestClient
        with TestClient(main.create_app(self.root)) as client:
            response = client.post('/retrieve', json={'param': '流动资金贷款'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.text, '普通流贷正文，不能截断。')
            for document in client.app.state.store.documents.values():
                download = client.get(document['download_url'])
                self.assertEqual(download.status_code, 200)
                self.assertEqual(download.content, (self.root / document['source']).read_bytes())
            self.assertEqual(client.post('/retrieve', json={'param': '流动资金'}).status_code, 404)
            for body in [{}, {'param': ''}, {'param': '  '}, {'param': 123}, {'param': ['流动资金贷款']}, {'param': '../流动资金贷款'}, {'param': '流动资金贷款', 'extra': 1}]:
                self.assertEqual(client.post('/retrieve', json=body).status_code, 422)
            self.assertEqual(client.get('/files/not-found').status_code, 404)
            self.assertEqual(client.get('/health').json()['document_count'], 3)
            projects = client.get('/projects').json()['projects']
            self.assertEqual(len(projects), 3)
            self.assertIn('流动资金贷款', [p['name'] for p in projects])
            self.assertEqual(client.get('/classes').json()['classes'], projects)
            schema = client.get('/openapi.json').json()
            self.assertEqual(schema['openapi'], '3.0.3')
            success = schema['paths']['/retrieve']['post']['responses']['200']['content']
            self.assertEqual(set(success), {'text/plain'})
            self.assertEqual(success['text/plain']['schema']['type'], 'string')
            self.assertNotIn('DocumentOutput', schema['components']['schemas'])

    def test_download_deleted_original_returns_not_found(self):
        from fastapi.testclient import TestClient
        with TestClient(main.create_app(self.root)) as client:
            document = client.app.state.store.documents['流动资金贷款']
            (self.root / document['source']).unlink()
            self.assertEqual(client.get(document['download_url']).status_code, 404)

    def test_real_documents_all_route_and_keep_every_text_node(self):
        root = Path(__file__).resolve().parents[1] / 'list'
        store = main.DocumentStore(root)
        files = sorted(p for p in root.glob('*.docx') if not p.name.startswith('~$'))
        self.assertGreater(len(files), 0)
        self.assertEqual(len(main.app.state.store.documents), len(files))
        for path in files:
            with self.subTest(file=path.name):
                name = path.stem.removesuffix('材料清单').removesuffix('_')
                content = store.retrieve(name)
                from docx_reader import read_docx
                self.assertEqual(content, read_docx(path))
                with ZipFile(path) as z:
                    tree = ET.fromstring(z.read('word/document.xml'))
                source_text = ''.join(t.text or '' for t in tree.iter('{%s}t' % W))
                offset = 0
                for node in tree.iter('{%s}t' % W):
                    text = node.text or ''
                    position = content.find(text, offset)
                    self.assertNotEqual(position, -1, '正文遗漏或顺序错误：' + text[:30])
                    offset = position + len(text)
                self.assertGreaterEqual(len(content), len(source_text))


if __name__ == '__main__':
    unittest.main()
