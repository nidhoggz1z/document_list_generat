"""T28: all CLI checks are offline; expected data is never submitted."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from pdf_fixtures import make_pdf
from material_tree import group_materials, iter_materials

live = __import__('verify_task_live') if importlib.util.find_spec('verify_task_live') else None


class LiveValidationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(live, 'T28 CLI is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = self.root / 'input.pdf'
        self.pdf.write_bytes(make_pdf(('text',)))
        self.business = self.root / 'business.json'
        self.business.write_text(json.dumps(dict(bsnEvtId='B', userNo='U', taskId='T',
            replyImageUrl='http://10.0.0.8/approval.pdf')), encoding='utf-8')
        self.expected = self.root / 'expected.json'
        self.expected.write_text(json.dumps(dict(products=['甲'], items=[dict(productName='甲',
            sourceRuleId='1', materialName='人工预期名称')])), encoding='utf-8')
        self.report = self.root / 'report.json'
        self.args = live.parser().parse_args(['--base-url', 'http://offline.test', '--pdf', str(self.pdf),
            '--business-data', str(self.business), '--expected', str(self.expected), '--report', str(self.report)])
        self.requests = []
        sample = json.loads((ROOT / 'docs/sdd/examples/generate.response.json').read_text(encoding='utf-8'))
        self.done = sample
        # Use the public contract example but bind its identity and PDF hash to this fixture.
        import hashlib
        self.done.update(bsnEvtId='B', userNo='U', taskId='T')
        data = self.done['data']
        data['sourceDocumentHash'] = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        data['products'] = [dict(extractedProductName='甲', productName='甲', checklistFileName='甲材料清单.docx')]
        item = dict(next(iter_materials(data['items'])), productName='甲', materialName='[甲]模型不同文字',
                    sourceRuleId='1', reason='历史理由', itemId='R0001')
        data['items'] = group_materials(data['products'], [(item, [])], {})
        data['total'] = 1

    def run_cli(self, responder=None):
        def handle(request):
            self.requests.append(request)
            if responder:
                response = responder(request)
                if response is not None:
                    return response
            if request.url.path == '/health':
                return httpx.Response(200, json={'status': 'healthy'})
            if request.url.path == '/api/v1/tasks':
                return httpx.Response(200, json=dict(bsnEvtId='B', userNo='U', taskId='T', autoReply='task created'))
            return httpx.Response(200, json=self.done)
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            code = live.run(self.args, client=client)
        return code, json.loads(self.report.read_text(encoding='utf-8'))

    def test_invalid_inputs_do_not_submit_task(self):
        for invalid in ({}, {'products': ['甲'], 'items': []}, {'products': ['甲'], 'items': [dict(productName='乙', sourceRuleId='1')]}):
            self.expected.write_text(json.dumps(invalid), encoding='utf-8')
            code, report = self.run_cli()
            self.assertNotEqual(code, 0)
            self.assertEqual(report['status'], 'ERROR')
            self.assertFalse(self.requests)

    def test_missing_fixture_and_invalid_business_or_pdf_do_not_submit(self):
        self.pdf.unlink()
        self.assertNotEqual(self.run_cli()[0], 0)
        self.pdf.write_bytes(b'not-pdf')
        self.assertNotEqual(self.run_cli()[0], 0)
        self.pdf.write_bytes(make_pdf(('text',)))
        self.business.write_text('{"bsnEvtId":null}', encoding='utf-8')
        self.assertNotEqual(self.run_cli()[0], 0)
        self.assertFalse(self.requests)

    def test_unreachable_service_does_not_submit(self):
        def unreachable(request):
            raise httpx.ConnectError('no server', request=request)
        code, report = self.run_cli(unreachable)
        self.assertNotEqual(code, 0)
        self.assertIn('服务', report['error'])
        self.assertEqual(len(self.requests), 1)

    def test_counts_pass_while_text_and_business_review_stay_pending(self):
        code, report = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(report['status'], 'COUNTS_MATCH')
        self.assertEqual(report['manualConclusion'], '待人工核对文字、OCR及适用性')
        self.assertEqual(report['missingItems'], [])
        self.assertEqual(report['extraItems'], [])
        submitted = next(r for r in self.requests if r.url.path == '/api/v1/tasks')
        self.assertEqual(submitted.headers['content-type'], 'application/json')
        self.assertEqual(json.loads(submitted.content)['replyImageUrl'], 'http://10.0.0.8/approval.pdf')
        self.assertNotIn(self.pdf.read_bytes(), submitted.content)
        self.assertNotIn('人工预期名称'.encode(), submitted.content)
        self.assertEqual(next(iter_materials(report['actual']['data']['items']))['materialName'], '[甲]模型不同文字')
        self.assertEqual(json.loads(self.expected.read_text(encoding='utf-8'))['items'][0]['materialName'], '人工预期名称')

    def test_url_only_acceptance_needs_no_local_pdf(self):
        self.args.pdf = None
        self.pdf.unlink()
        code, report = self.run_cli()
        self.assertEqual(code, 0, report)
        self.assertNotIn('sourceDocumentHash', report)

    def test_duplicate_rule_counts_detect_missing_expansion(self):
        value = json.loads(self.expected.read_text(encoding='utf-8'))
        value['items'] *= 2
        self.expected.write_text(json.dumps(value), encoding='utf-8')
        code, report = self.run_cli()
        self.assertNotEqual(code, 0)
        self.assertEqual(report['missingItems'], [dict(productName='甲', sourceRuleId='1', count=1)])
        self.assertEqual(report['coverage'][0]['expectedCount'], 2)
        self.assertEqual(report['coverage'][0]['actualCount'], 1)

    def test_new_output_compares_material_names_without_source_rule_ids(self):
        self.done['data'].pop('sourceDocumentHash', None)
        item = next(iter_materials(self.done['data']['items']))
        item.pop('sourceRuleId')
        item.pop('reason')
        expected_item = dict(productName='甲', materialName=item['materialName'])
        self.expected.write_text(json.dumps(dict(products=['甲'], items=[expected_item])), encoding='utf-8')
        code, report = self.run_cli()
        self.assertEqual(code, 0, report)
        self.assertEqual(report['status'], 'COUNTS_MATCH')
        self.assertNotIn('sourceRuleId', report['coverage'][0])
        self.assertEqual(report['coverage'][0]['materialName'], item['materialName'])
        expected_item['materialName'] = '[甲]缺失材料'
        self.expected.write_text(json.dumps(dict(products=['甲'], items=[expected_item])), encoding='utf-8')
        code, report = self.run_cli()
        self.assertNotEqual(code, 0)
        self.assertEqual(report['status'], 'COUNTS_DIFFER')

    def test_historical_response_hash_is_still_checked_when_present(self):
        self.done['data']['sourceDocumentHash'] = 'a' * 64
        code, report = self.run_cli()
        self.assertNotEqual(code, 0)
        self.assertIn('哈希不匹配', report['error'])

    def test_extra_rule_and_product_differences_fail(self):
        value = dict(products=['乙'], items=[dict(productName='乙', sourceRuleId='2')])
        self.expected.write_text(json.dumps(value), encoding='utf-8')
        code, report = self.run_cli()
        self.assertNotEqual(code, 0)
        self.assertEqual(report['missingProducts'], ['乙'])
        self.assertEqual(report['extraProducts'], ['甲'])
        self.assertEqual(len(report['extraItems']), 1)

    def test_failed_terminated_and_bad_done_report_failure(self):
        for response in (dict(bsnEvtId='B', userNo='U', taskId='T', status='FAILED', reason='失败'),
                         dict(bsnEvtId='B', userNo='U', taskId='T', status='TERMINATED'),
                         dict(self.done, data={})):
            with self.subTest(response=response):
                self.assertNotEqual(self.run_cli(lambda r: httpx.Response(200, json=response)
                    if r.url.path.endswith('/query') else None)[0], 0)

    def test_poll_deadline_reports_timeout(self):
        self.args.wait_seconds = .01
        self.args.poll_seconds = .005
        pending = dict(bsnEvtId='B', userNo='U', taskId='T', status='PENDING')
        code, report = self.run_cli(lambda r: httpx.Response(200, json=pending)
                                  if r.url.path.endswith('/query') else None)
        self.assertNotEqual(code, 0)
        self.assertIn('等待', report['error'])

    def test_report_cannot_overwrite_input(self):
        self.args.report = str(self.expected)
        before = self.expected.read_bytes()
        with self.assertRaises(ValueError):
            self.run_cli()
        self.assertEqual(self.expected.read_bytes(), before)
        self.assertFalse(self.requests)

    def test_malformed_health_response_reports_failure_without_submit(self):
        code, report = self.run_cli(lambda r: httpx.Response(200, json=[]))
        self.assertNotEqual(code, 0)
        self.assertEqual(report['status'], 'ERROR')
        self.assertEqual(len(self.requests), 1)


if __name__ == '__main__':
    unittest.main()
