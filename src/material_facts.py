"""T16: PDF-first facts and source-linked objects; no material generation."""
import asyncio
from copy import deepcopy

from task_models import BusinessData


class MaterialFactError(Exception):
    """Invalid extraction or business context."""


ALIASES = {'担保': '担保方式', '额度': '金额', '借款人名称': '客户名称',
           'cstNm': '客户名称', 'gnrNm': '担保人姓名', 'subcontractNum': '从合同编号',
           'cltlTp': '押品类型', 'cltlNm': '押品名称', 'ctrTamt': '金额',
           'ccy': '币种', 'contractNum': '主合同编号', 'grtstlRcmbNm': '担保方式',
           'primGrtstlNm': '担保方式'}
IDENTITIES = {'客户名称': 'customer', '担保人姓名': 'guarantor',
              '共同担保人姓名': 'jointGuarantor', '从合同编号': 'subcontract',
              '押品存在': 'collateral', '押品名称': 'collateral', '押品类型': 'collateral'}
NESTED = {'subcontracts', 'jntGnrInfs', 'SubcontractCollateralRels'}
ROLES = {'guarantor': '担保人', 'jointGuarantor': '共同担保人',
         'subcontract': '从合同', 'collateral': '押品'}


def resolve_material_facts(approval, business_data, cancel_check=lambda: False):
    """Return products, effective facts and ordered objects with parentKey links.

    JSON paths identify objects even without names/IDs. Unscoped JSON objects
    apply to every PDF product (user decision 2026-10-23). PDF product scopes
    stay separate; empty PDF product applies globally. Within a scope, later
    present facts win in page/record order; absent facts never erase a value.
    Raw code/extension fields remain opaque. Inputs are never mutated.
    """
    def check():
        if cancel_check():
            raise asyncio.CancelledError()

    check()
    try:
        business = BusinessData.model_validate(business_data).model_dump()
        products = deepcopy(approval['products'])
        names = [p['productName'] for p in products]
        if not names or any(not isinstance(n, str) or not n.strip() for n in names) or len(set(names)) != len(names):
            raise ValueError()
        pdf = deepcopy(approval['facts'])
        for f in pdf:
            if (f['productName'] and f['productName'] not in names
                    or type(f['present']) is not bool or type(f['negated']) is not bool
                    or not isinstance(f['subject'], str) or not isinstance(f['name'], str)
                    or not f['name'].strip() or not isinstance(f['value'], str)
                    or type(f['page_number']) is not int or f['page_number'] < 1
                    or not isinstance(f['source'], str)
                    or f['present'] and (not f['value'].strip() or not f['source'].strip())
                    or not f['present'] and (f['negated'] or f['value'] or f['source'])):
                raise ValueError()
            f['name'] = ALIASES.get(f['name'], f['name'])
        pdf.sort(key=lambda f: f['page_number'])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise MaterialFactError('批复事实或业务上下文无效') from None
    all_facts, all_objects = [], []
    for product in names:
        check()
        objects, resolved, scopes = [], {}, {}

        def new_object(kind, path, name='', parent='', aliases=()):
            obj = dict(objectKey=f'{names.index(product)}:{path}', productName=product,
                       kind=kind, name=name, parentKey=parent, source=path, present=True,
                       aliases=[a for a in aliases if a])
            if kind == 'collateral':
                obj['typeState'] = 'unknown'
            objects.append(obj)
            return obj

        def put(name, value, obj, source, origin='JSON', present=True, negated=False, page=None):
            name = ALIASES.get(name, name)
            key = (obj['objectKey'], name)
            if not present and key in resolved and resolved[key]['present']:
                return
            fact = dict(name=name, value=deepcopy(value), present=present, negated=negated,
                        state='unknown' if not present else 'explicit-negative' if negated else 'present',
                        origin=origin, source=source, page_number=page, productName=product,
                        subject=obj['name'], objectKey=obj['objectKey'])
            resolved[key] = fact
            if name in ('客户名称', '担保人姓名', '共同担保人姓名', '押品名称', '从合同编号') and present and not negated:
                obj['name'] = value
            if name == '押品类型' and obj['kind'] == 'collateral':
                obj['typeState'] = fact['state']

        def json_fields(data, obj, path, renames=None):
            for key, value in data.items():
                check()
                if (key in NESTED or value is None or isinstance(value, str) and not value.strip()
                        or value == [] or value == {}):
                    continue
                put((renames or {}).get(key, key), value, obj, f'{path}.{key}')

        customer = new_object('customer', '$', business.get('cstNm', ''), aliases=[business.get('cstId', ''), '借款人', '客户', ''])
        json_fields(business, customer, '$')
        for i, sub in enumerate(business.get('subcontracts', [])):
            check()
            path = f'$.subcontracts[{i}]'
            parent = new_object('subcontract', path, sub.get('subcontractNum', ''), customer['objectKey'])
            json_fields({k: v for k, v in sub.items() if k not in ('gnrNm', 'gnrId')}, parent, path)
            if sub.get('gnrNm', '').strip() or sub.get('gnrId', '').strip():
                obj = new_object('guarantor', path + '.guarantor', sub.get('gnrNm', ''), parent['objectKey'], [sub.get('gnrId', '')])
                json_fields({k: sub[k] for k in ('gnrNm', 'gnrId') if k in sub}, obj, path)
            for j, joint in enumerate(sub.get('jntGnrInfs', [])):
                if not any(isinstance(v, str) and v.strip() for v in joint.values()):
                    continue
                joint_path = f'{path}.jntGnrInfs[{j}]'
                obj = new_object('jointGuarantor', joint_path, joint.get('cstNm', ''), parent['objectKey'], [joint.get('cstId', '')])
                json_fields(joint, obj, joint_path, {'cstNm': '共同担保人姓名'})
            for j, collateral in enumerate(sub.get('SubcontractCollateralRels', [])):
                if not any(collateral.get(k, '').strip() for k in ('cltlId', 'cltlTp', 'cltlNm')):
                    continue
                cl_path = f'{path}.SubcontractCollateralRels[{j}]'
                obj = new_object('collateral', cl_path, collateral.get('cltlNm', ''), parent['objectKey'], [collateral.get('cltlId', '')])
                json_fields(collateral, obj, cl_path)
                if not collateral.get('cltlTp', '').strip():
                    put('押品类型', '', obj, cl_path + '.cltlTp', present=False)

        # Exact source identities only; no fuzzy person/product matching.
        for index, fact in enumerate(pdf):
            check()
            if fact['productName'] and fact['productName'] != product:
                continue
            subject, name = fact['subject'], fact['name']
            kind = IDENTITIES.get(name)
            candidates = [o for o in objects if subject and (subject == o['name'] or subject in o['aliases'])
                          and (kind is None or o['kind'] == kind)]
            if kind and not candidates and name != '客户名称':
                candidates = [o for o in objects if o['kind'] == kind and fact['value'] and
                              fact['value'] in [o['name'], *o['aliases']]]
            general = subject in ('', '借款人', '客户', customer['name'], *customer['aliases'])
            if kind and subject == ROLES.get(kind):
                candidates = [o for o in objects if o['kind'] == kind]
            if (subject, kind) in scopes:
                candidates = [scopes[(subject, kind)]]
            if name == '客户名称' or general and (fact['negated'] or name == '押品存在'):
                obj = customer
            elif len(candidates) == 1:
                obj = candidates[0]
            elif len(candidates) > 1:
                obj = new_object('subject', f'pdf:{index}', subject, aliases=[subject])
                obj['relatedObjectKeys'] = [o['objectKey'] for o in candidates]
            elif not fact['present']:
                obj = customer if general else new_object('subject', f'pdf:{index}', subject)
            elif kind:
                obj = new_object(kind, f'pdf:{index}', subject if subject not in ('借款人', '客户') else '',
                                 aliases=[subject])
            elif general:
                obj = customer
            else:
                obj = new_object('subject', f'pdf:{index}', subject, aliases=[subject])
            if obj['kind'] == 'subject' and subject:
                scopes[(subject, kind)] = obj
            put(name, fact['value'], obj, fact['source'], 'PDF', fact['present'], fact['negated'], fact['page_number'])

        presence = resolved.get((customer['objectKey'], '押品存在'))
        if (presence and presence['present'] and not presence['negated']
                and not any(o['kind'] == 'collateral' for o in objects)):
            new_object('collateral', 'pdf:collateral', parent=customer['objectKey'])['source'] = presence['source']

        # Explicit absence blocks JSON supplementation within precisely this scope.
        removed = set()
        for fact in resolved.values():
            if fact['origin'] != 'PDF' or not fact['negated']:
                continue
            obj = next(o for o in objects if o['objectKey'] == fact['objectKey'])
            if fact['name'] == '担保方式' and obj is customer:
                removed.update(o['objectKey'] for o in objects if o['kind'] in ('subcontract', 'guarantor', 'jointGuarantor', 'collateral'))
            elif fact['name'] == '担保方式' and obj['kind'] in ('guarantor', 'jointGuarantor'):
                removed.add(obj['objectKey'])
            elif obj is customer and fact['name'] in ('押品存在', '担保人姓名', '共同担保人姓名'):
                role = IDENTITIES[fact['name']]
                removed.update(o['objectKey'] for o in objects if o['kind'] == role)
            elif fact['name'] in ('押品存在', '担保人姓名', '共同担保人姓名'):
                removed.add(obj['objectKey'])
        for obj in objects:
            if obj['objectKey'] in removed and any(f['objectKey'] == obj['objectKey'] and f['origin'] == 'PDF' and f['negated'] for f in resolved.values()):
                # Keep a source scope for explicit denial, without an applicable object.
                obj.update(kind='subject', present=False)
                obj.pop('typeState', None)
            if obj['objectKey'] not in removed or not obj['present']:
                if obj['parentKey'] in removed:
                    obj['parentKey'] = ''
                if 'relatedObjectKeys' in obj:
                    obj['relatedObjectKeys'] = [k for k in obj['relatedObjectKeys'] if k not in removed]
                obj.pop('aliases')
                all_objects.append(obj)
        all_facts.extend(f for f in resolved.values() if f['objectKey'] not in removed or f['origin'] == 'PDF' and f['negated'])
    check()
    return dict(products=products, facts=all_facts, objects=all_objects)
