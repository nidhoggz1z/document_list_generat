"""T23 / AC-19: exercise HTTP, then check the documented schema independently."""
import copy
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import main
import task_models
from task_store import TaskStore
from download_fixture import pdf_download
from test_service import make_docx


def accepts(value, schema, schemas):
    """Subset used by this contract: fail closed for the documented value types."""
    if '$ref' in schema:
        return accepts(value, schemas[schema['$ref'].split('/')[-1]], schemas)
    for union in ('oneOf', 'anyOf'):
        if union in schema:
            count = sum(accepts(value, s, schemas) for s in schema[union])
            return count == 1 if union == 'oneOf' else count > 0
    if value is None:
        return schema.get('nullable', False) or schema.get('type') == 'null'
    if 'enum' in schema and value not in schema['enum']:
        return False
    if 'const' in schema and value != schema['const']:
        return False
    kind = schema.get('type')
    if kind == 'object':
        return (type(value) is dict and set(schema.get('required', [])) <= value.keys()
                and (schema.get('additionalProperties', True) or value.keys() <= schema['properties'].keys())
                and all(accepts(v, schema['properties'][k], schemas)
                        for k, v in value.items() if k in schema.get('properties', {})))
    if kind == 'array':
        return (type(value) is list and len(value) >= schema.get('minItems', 0)
                and all(accepts(v, schema['items'], schemas) for v in value))
    if kind == 'string':
        return (type(value) is str and len(value) >= schema.get('minLength', 0)
                and ('pattern' not in schema or re.search(schema['pattern'], value) is not None))
    if kind == 'integer':
        return type(value) is int and value >= schema.get('minimum', float('-inf'))
    return False


def shape(schema):
    """Compare field/type/enum/null semantics across OpenAPI 3.0 and Pydantic."""
    if '$ref' in schema:
        return schema['$ref'].split('/')[-1]
    if 'anyOf' in schema:
        return sorted([shape(s) for s in schema['anyOf']], key=str)
    if schema.get('nullable'):
        return sorted([shape({k: v for k, v in schema.items() if k != 'nullable'}),
                       shape({'type': 'null'})], key=str)
    return dict(type=schema.get('type'), enum=schema.get('enum', [schema['const']] if 'const' in schema else None),
                required=sorted(schema.get('required', [])), extra=schema.get('additionalProperties'),
                properties={k: shape(v) for k, v in schema.get('properties', {}).items()},
                items=shape(schema['items']) if 'items' in schema else None)


class ContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        make_docx(root / 'list/测试材料清单.docx', '完整正文\n第二段')
        self.store = TaskStore(root / 'storage')
        self.app = main.create_app(root / 'list', task_store=self.store)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)
        self.ids = dict(bsnEvtId='B001', userNo='U001', taskId='T001')
        self.api = json.loads((ROOT / 'docs/sdd/personalized-checklist.openapi.json').read_text('utf-8'))
        self.runtime = self.app.openapi()
        self.done = json.loads((ROOT / 'docs/sdd/examples/generate.response.json').read_text('utf-8'))

    def check_response(self, response, path, status=200):
        self.assertEqual(response.status_code, status, response.text)
        for api in (self.api, self.runtime):
            schemas = api['components']['schemas']
            responses = api['paths'][path]['post']['responses']
            self.assertIn(str(status), responses)
            schema = responses[str(status)]['content']['application/json']['schema']
            self.assertTrue(accepts(response.json(), schema, schemas), response.text)
        return response.json()

    async def create(self, data=None, raw=b'%PDF-1.7\nfixture'):
        payload = self.ids if data is None else data
        if isinstance(payload, dict) and payload:
            payload = {**payload, 'replyImageUrl': 'http://10.0.0.8/approval.pdf'}
        with pdf_download(raw):
            return await self.client.post('/api/v1/tasks', json=payload)

    async def query(self):
        return await self.client.post('/api/v1/tasks/query', json={**self.ids, 'userNo': 'other'})

    async def test_real_routes_match_confirmed_contract(self):
        path = '/api/v1/tasks/query'
        self.assertEqual(self.check_response(await self.query(), path), {'reply': '任务未创建'})
        for status in ('PENDING', 'PROCESSING', 'TERMINATED', 'FAILED', 'DONE'):
            with self.subTest(status=status):
                self.check_response(await self.create(), '/api/v1/tasks')
                generation = self.store.get_task('B001', 'T001')['generationId']
                if status in ('PROCESSING', 'FAILED', 'DONE'):
                    self.store.claim_next()
                if status == 'FAILED':
                    self.store.finish_failed(generation, '受控失败')
                if status == 'DONE':
                    self.store.finish_done(generation, self.done['data'])
                if status == 'TERMINATED':
                    self.store.accept_task({**self.ids, 'taskId': 'replacement'})
                body = self.check_response(await self.query(), path)
                expected = self.done if status == 'DONE' else {**self.ids, 'status': status}
                if status == 'FAILED':
                    expected['reason'] = '受控失败'
                self.assertEqual(body, expected)

    async def test_runtime_error_schemas_match_real_errors(self):
        path = '/api/v1/tasks/query'
        for response, status in [(await self.client.post(path, content='{', headers={'content-type': 'application/json'}), 400),
                                 (await self.client.post(path, json={}), 422)]:
            self.check_response(response, path, status)
        for error, status in [(sqlite3.OperationalError('private'), 503), (RuntimeError('private'), 500)]:
            with patch.object(self.store, 'get_task', side_effect=error):
                self.check_response(await self.query(), path, status)

    async def test_creation_errors_and_optional_null_business_fields(self):
        path = '/api/v1/tasks'
        for data, raw, status in [({}, b'%PDF-1.7\n', 422), (self.ids, b'', 422),
                                  (self.ids, b'not pdf', 415), (self.ids, b'x' * (10485760 + 1), 413)]:
            self.check_response(await self.create(data, raw), path, status)
        self.check_response(await self.client.post(path, content='{}'), path, 400)
        for error, status in [(OSError('private'), 503), (RuntimeError('private'), 500)]:
            with patch.object(self.store, 'accept_task', side_effect=error):
                self.check_response(await self.create(), path, status)
        data = {**self.ids, 'contractNum': '', 'cstNm': None, 'subcontracts': [
            {'subcontractNum': '', 'gnrNm': None, 'SubcontractCollateralRels': [{'cltlTp': None}]}],
            'extension': {'arbitrary': None}}
        self.check_response(await self.create(data), path)
        saved = self.store.get_task('B001', 'T001')['businessData']
        self.assertNotIn('cstNm', saved)
        self.assertEqual(saved['subcontracts'], [{'subcontractNum': '', 'SubcontractCollateralRels': [{}]}])
        self.assertEqual(saved['extension'], data['extension'])

    async def test_openapi_field_type_enum_and_json_semantics(self):
        for path in self.api['paths']:
            documented = self.api['paths'][path]['post']
            runtime = self.runtime['paths'][path]['post']
            self.assertEqual(set(documented['responses']), set(runtime['responses']))
            self.assertEqual(documented['operationId'], runtime['operationId'])
            for media, body in documented['requestBody']['content'].items():
                declared = body['schema']
                if '$ref' in declared:
                    declared = self.api['components']['schemas'][declared['$ref'].split('/')[-1]]
                actual = runtime['requestBody']['content'][media]['schema']
                if '$ref' in actual:
                    actual = self.runtime['components']['schemas'][actual['$ref'].split('/')[-1]]
                self.assertEqual(shape(declared), shape(actual))
        for name, documented in self.api['components']['schemas'].items():
            model = getattr(task_models, name, None)
            if model is not None:
                actual = model.model_json_schema(ref_template='#/components/schemas/{model}')
                if '$ref' in actual:
                    actual = actual['$defs'][name]
                self.assertEqual(shape(documented), shape(actual), name)
        self.assertEqual((await self.client.post('/retrieve', json={'param': '测试'})).text, '完整正文\n第二段')
        self.assertEqual(self.runtime['paths']['/retrieve']['post']['responses']['200']['content'].keys(), {'text/plain'})
        self.assertFalse(any('generate' in path for path in self.runtime['paths']))

    async def test_examples_and_mutually_exclusive_payloads(self):
        schema = self.api['paths']['/api/v1/tasks/query']['post']['responses']['200']['content']['application/json']
        schemas = self.api['components']['schemas']
        for example in schema['examples'].values():
            body = example['value']
            self.assertTrue(accepts(body, schema['schema'], schemas))
            invalid = copy.deepcopy(body)
            invalid['obsolete'] = True
            self.assertFalse(accepts(invalid, schema['schema'], schemas))
        for filename, name in [('create.response.json', 'CreateResponse'), ('query.request.json', 'TaskIdentity'),
                               ('generate.request.json', 'BusinessData'), ('generate.response.json', 'TaskDoneResponse')]:
            body = json.loads((ROOT / 'docs/sdd/examples' / filename).read_text('utf-8'))
            self.assertTrue(accepts(body, schemas[name], schemas), filename)
        for patch_data in [{'status': 'COMPLETED'}, {'data': {'status': 'DONE'}}, {'reason': 'extra'}]:
            self.assertFalse(accepts({**self.done, **patch_data}, schema['schema'], schemas))
