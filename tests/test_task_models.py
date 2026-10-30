import copy
import importlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
models = importlib.import_module('task_models') if importlib.util.find_spec('task_models') else None
from material_tree import iter_materials
IDS = dict(bsnEvtId='B001', userNo='U001', taskId='T001')


class TaskModelTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(models, 'T01 task_models implementation is missing')

    def done(self):
        return json.loads((ROOT / 'docs/sdd/examples/generate.response.json').read_text(encoding='utf-8'))

    def test_identity_trims_and_rejects_missing_null_blank_and_non_strings(self):
        self.assertEqual(models.TaskIdentity(**{k: ' ' + v + '\n' for k, v in IDS.items()}).model_dump(), IDS)
        for key in IDS:
            missing = IDS.copy()
            del missing[key]
            with self.subTest(key=key), self.assertRaises(ValidationError):
                models.BusinessData(**missing)
            for value in (None, '', ' \t\n', 1, True, [], {}):
                with self.subTest(key=key, value=value), self.assertRaises(ValidationError):
                    models.BusinessData(**{**IDS, key: value})

    def test_optional_null_and_status_variants(self):
        business = models.BusinessData(**IDS, contractNum='', cstNm=None, subcontracts=[{
            'subcontractNum': '', 'gnrNm': None, 'SubcontractCollateralRels': [{'cltlTp': None}]}])
        self.assertEqual(business.model_dump(), {**IDS, 'contractNum': '', 'subcontracts': [
            {'subcontractNum': '', 'SubcontractCollateralRels': [{}]}]})
        self.assertEqual(json.loads(business.model_dump_json()), business.model_dump())
        adapter = TypeAdapter(models.TaskQueryResponse)
        for status in ('PENDING', 'PROCESSING', 'TERMINATED'):
            payload = {**IDS, 'status': status}
            self.assertEqual(adapter.validate_python(payload).model_dump(), payload)
        for payload in ({**IDS, 'status': 'FAILED', 'reason': '解析失败'}, self.done(), {'reply': '任务未创建'}):
            self.assertEqual(adapter.validate_python(payload).model_dump(), payload)

    def test_unknown_fields_preserve_original_json_at_every_level(self):
        unknown = {'count': 3, 'flag': True, 'empty': None, 'nested': [1, None, {'x': False}]}
        payload = {**IDS, 'model_api_key': unknown, 'subcontracts': [{
            'custom': unknown, 'jntGnrInfs': [{'custom': unknown}],
            'SubcontractCollateralRels': [{'custom': unknown}]}]}
        original = copy.deepcopy(payload)
        self.assertEqual(models.BusinessData.model_validate(payload).model_dump(), original)
        self.assertEqual(json.loads(models.BusinessData.model_validate(payload).model_dump_json()), original)
        self.assertEqual(payload, original)

    def test_known_optional_types_and_decimal_amount(self):
        for amount in ('', '0', '100', '100.12345'):
            self.assertEqual(models.BusinessData(**IDS, ctrTamt=amount).ctrTamt, amount)
        for amount in (100, 1.2, True, '-1', '1e3', '01', '1.', ' 1'):
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                models.BusinessData(**IDS, ctrTamt=amount)
        for patch in ({'ccy': 1}, {'subcontracts': {}}, {'subcontracts': [None]},
                      {'subcontracts': [{'gnrNm': 1}]},
                      {'subcontracts': [{'jntGnrInfs': [{'idCardNo': 123}]}]},
                      {'subcontracts': [{'SubcontractCollateralRels': [{'cltlTp': True}]}]}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                models.BusinessData(**IDS, **patch)

    def test_all_declared_business_fields_and_handwritten_example(self):
        schemas = json.loads((ROOT / 'docs/sdd/personalized-checklist.openapi.json').read_text(encoding='utf-8'))['components']['schemas']
        for name in ('BusinessData', 'Subcontract', 'Collateral', 'JointGuarantor'):
            self.assertEqual(set(getattr(models, name).model_fields), set(schemas[name]['properties']))
        payload = json.loads((ROOT / 'docs/sdd/examples/generate.request.json').read_text(encoding='utf-8'))
        self.assertEqual(models.BusinessData.model_validate(payload).model_dump(), payload)
        self.assertNotIn('minLength', schemas['BusinessData']['properties']['contractNum'])

    def test_response_variants_reject_old_states_null_and_extra_fields(self):
        adapter = TypeAdapter(models.TaskQueryResponse)
        invalid = [{**IDS, 'status': s} for s in ('NEEDS_REVIEW', 'COMPLETED', 'FAILED', 'DONE')]
        invalid += [{**IDS, 'status': 'PENDING', 'data': None}, {'reply': '任务未创建', 'status': 'PENDING'},
                    {**IDS, 'status': 'FAILED', 'reason': None}, {**IDS, 'status': 'FAILED', 'reason': '  '}]
        done = self.done()
        for path in (('data', 'status'), ('data', 'items', 0, 'evidence')):
            payload = copy.deepcopy(done)
            target = payload
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = 'obsolete'
            invalid.append(payload)
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                adapter.validate_python(payload)
        for key in next(iter_materials(done['data']['items'])):
            payload = copy.deepcopy(done)
            next(iter_materials(payload['data']['items']))[key] = None
            with self.subTest(key=key), self.assertRaises(ValidationError):
                adapter.validate_python(payload)

    def test_done_metadata_and_item_constraints(self):
        for patch in ({'total': 1}, {'total': True}, {'products': []}, {'items': []},
                      {'sourceDocumentHash': 'A' * 64}, {'generatedAt': '2026-10-23'},
                      {'generatedAt': '2026-10-23T10:00:00'}):
            payload = self.done()
            payload['data'].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                models.TaskDoneResponse.model_validate(payload)
        for patch in ({'itemType': 'CHECK'}, {'applicability': 'UNKNOWN'}, {'productName': '未知产品'},
                      {'itemId': 'R0002'}, {'materialName': '无产品前缀'}):
            payload = self.done()
            next(iter_materials(payload['data']['items'])).update(patch)
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                models.TaskDoneResponse.model_validate(payload)
        payload = self.done()
        next(iter_materials(payload['data']['items']))['publishDate'] = '2026年'
        models.TaskDoneResponse.model_validate(payload)

    def test_create_reply_and_identity_have_exact_field_sets(self):
        payload = {**IDS, 'autoReply': 'task created'}
        self.assertEqual(models.CreateResponse.model_validate(payload).model_dump(), payload)
        for cls, bad in ((models.CreateResponse, {**payload, 'data': {}}),
                         (models.CreateResponse, {**IDS, 'autoReply': 'created'}),
                         (models.TaskIdentity, {**IDS, 'extra': 1}),
                         (models.ReplyResponse, {'reply': ''}),
                         (models.ReplyResponse, {'reply': 'error', 'data': None})):
            with self.subTest(cls=cls), self.assertRaises(ValidationError):
                cls.model_validate(bad)

    def test_openapi_examples_and_response_required_fields(self):
        api = json.loads((ROOT / 'docs/sdd/personalized-checklist.openapi.json').read_text(encoding='utf-8'))
        adapter = TypeAdapter(models.TaskQueryResponse)
        examples = api['paths']['/api/v1/tasks/query']['post']['responses']['200']['content']['application/json']['examples']
        for example in examples.values():
            payload = example['value']
            parsed = adapter.validate_python(payload)
            self.assertEqual(parsed.model_dump(), payload)
            schema = api['components']['schemas'][type(parsed).__name__]
            self.assertEqual(set(payload), set(schema['required']))
            for key in payload:
                for omit in (True, False):
                    invalid = copy.deepcopy(payload)
                    if omit:
                        del invalid[key]
                    else:
                        invalid[key] = None
                    with self.subTest(key=key, omit=omit), self.assertRaises(ValidationError):
                        adapter.validate_python(invalid)


if __name__ == '__main__':
    unittest.main()
