"""T19: project T18 validated, ordered instances into the public DONE data.

Object order comes from T18; this module restores product/rule order, assigns
IDs and fills display defaults. Metadata meaning remains a T28 business check.
"""
import asyncio
import re
from datetime import datetime, timezone

from task_models import TaskResult
from material_tree import catalog_from_rules, group_materials, rule_category_path


class MaterialResultError(Exception):
    """The complete public result cannot be constructed."""


def _split_display_metadata(text, default_field=None):
    """Split labeled regulation cells; publication references are not display fields."""
    fields = {'来源系统': 'sourceSystem', '制度依据': 'policyBasis',
              '发布部门': 'publishDepartment', '发布日期': 'publishDate'}
    parts = re.split(r'[ \t]*(来源系统|制度依据|发布依据|发布部门|发布日期)[ \t]*[：:][ \t]*', text)
    if len(parts) == 1:
        return None
    values = {fields[label]: value.strip() for label, value in zip(parts[1::2], parts[2::2])
              if label in fields and value.strip()}
    if default_field and parts[0].strip():
        values.setdefault(default_field, parts[0].strip())
    return values


def display_metadata(instance, regulation=''):
    """Normalize inline/multiline labels without changing source data."""
    defaults = dict(sourceSystem='上银办', policyBasis='', publishDate='2026年', publishDepartment='上海银行')
    metadata, unpacked = dict(instance), {}
    for field in defaults:
        packed = _split_display_metadata(instance.get(field, ''), field)
        if packed is not None:
            metadata[field] = ''
            unpacked.update(packed)
    metadata.update(unpacked)
    metadata.update(_split_display_metadata(regulation) or {})
    return {field: metadata.get(field, '') if metadata.get(field, '').strip() else default
            for field, default in defaults.items()}


def material_name(product, instance, *, legacy=False):
    name = instance['materialName']
    prefix = f'[{product}]'
    if name.startswith(prefix):
        name = name[len(prefix):]
    subject, requirement = instance['subject'], instance['requirement']
    if not name.strip():
        return ''
    if legacy:
        # Historical category matching must use the title stored by the old formatter.
        if subject.strip() and subject not in name:
            name = subject + name
        if requirement.strip() and requirement not in name:
            name += f'（{requirement}）'
        return prefix + name
    if requirement.strip():
        name = name.removesuffix(f'（{requirement}）')
    if subject.strip():
        if name.endswith(f'（{subject}）'):
            name = name.removesuffix(f'（{subject}）')
        elif name.startswith(subject):
            name = name[len(subject):]
        name += f'（{subject}）'
    if requirement.strip() and requirement not in name:
        name += f'（{requirement}）'
    return prefix + name


def project_material_result(approval, snapshots, rules, decisions, source_document_hash,
                            cancel_check=lambda: False, category_catalog=None):
    def check():
        if cancel_check():
            raise asyncio.CancelledError()

    check()
    try:
        if not isinstance(source_document_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', source_document_hash):
            raise ValueError()
        names = [p['productName'] for p in approval['products']]
        snaps = {s['name']: s for s in snapshots}
        by_key = {d['ruleKey']: d for d in decisions}
        if (not names or len(set(names)) != len(names) or set(names) != set(snaps)
                or len(snaps) != len(snapshots) or len(by_key) != len(decisions)
                or len({r['ruleKey'] for r in rules}) != len(rules)
                or set(by_key) != {r['ruleKey'] for r in rules}
                or set(names) != {r['productName'] for r in rules}):
            raise ValueError()
        products = [dict(extractedProductName=p['extractedProductName'], productName=p['productName'],
                         checklistFileName=snaps[p['productName']]['filename']) for p in approval['products']]
        items = []
        for product in names:
            for rule in rules:
                check()
                if rule['productName'] != product:
                    continue
                d = by_key[rule['ruleKey']]
                if d['productName'] != product or d['action'] not in ('KEEP', 'CHECK', 'EXCLUDE'):
                    raise ValueError()
                if d['action'] != 'KEEP':
                    continue
                for instance in d['instances']:
                    check()
                    name = material_name(product, instance)
                    item = dict(itemId=f'R{len(items) + 1:04d}',
                        itemType='MATERIAL', productName=product, materialName=name,
                        applicability='APPLICABLE', requirement=instance['requirement'])
                    # All generation paths converge here, including restored and Excel-merged items.
                    item.update(display_metadata(instance, rule['cells'][4]))
                    items.append((item, rule_category_path(rule)))
        check()
        grouped = group_materials(products, items, category_catalog if category_catalog is not None
            else catalog_from_rules(rules, names), cancel_check)
        return TaskResult.model_validate(dict(generatedAt=datetime.now(timezone.utc).isoformat(),
            total=len(items), products=products,
            items=grouped)).model_dump(mode='json')
    except (ValueError, TypeError, KeyError, AttributeError):
        raise MaterialResultError('材料结果无效：产品、规则、展示字段或材料总数校验失败') from None
