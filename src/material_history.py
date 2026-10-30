"""Read-only tree views of old flat results, using only original task artifacts."""
import hashlib
import json
from pathlib import Path

from checklist_rules import build_rule_inventory, RuleInventoryError
from material_result import material_name
from material_tree import (
    build_category_catalog, catalog_from_rules, group_materials, rule_category_path, select_category,
)
from task_models import HistoricalTaskResult, LegacyTaskResult, TaskResult


def historical_result(record, store):
    saved = record['result']
    if isinstance(saved, dict) and saved.get('items') and 'categories' in saved['items'][0]:
        model = HistoricalTaskResult if 'sourceDocumentHash' in saved else TaskResult
        return model.model_validate(saved).model_dump(mode='json')
    flat = LegacyTaskResult.model_validate(saved).model_dump(mode='json')
    generation = record['generationId']
    stage_root = (store.generation_path(generation).parent / 'stages').resolve()

    def read_original(path, digest):
        path = Path(path).resolve()
        if not path.is_relative_to(stage_root):
            raise ValueError('Artifact outside original generation')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('Artifact hash mismatch')
        return raw

    def artifact(name):
        try:
            ref = record['artifacts'][name]
            if ref['generationId'] != generation:
                raise ValueError()
            value = json.loads(read_original(ref['path'], ref['sha256']))
            if value['generationId'] != generation:
                raise ValueError()
            return value['data']
        except (OSError, ValueError, TypeError, KeyError):
            return None

    merged = artifact('merged-materials')
    rules = merged.get('rules', []) if isinstance(merged, dict) else artifact('rules')
    decisions = merged.get('decisions', []) if isinstance(merged, dict) else artifact('decisions')
    if not isinstance(rules, list) or not rules:
        rules = artifact('rules')
    if not isinstance(decisions, list) or not decisions:
        decisions = artifact('decisions')
    rules = rules if isinstance(rules, list) else []
    decisions = decisions if isinstance(decisions, list) else []
    names = [p['productName'] for p in flat['products']]
    try:
        catalog = catalog_from_rules(rules, names)
    except (ValueError, TypeError, KeyError, AttributeError):
        rules, catalog = [], {name: [] for name in names}

    # Original snapshots also preserve final headings with no original details.
    manifests = artifact('checklists')
    for manifest in manifests if isinstance(manifests, list) else []:
        try:
            raw = read_original(manifest['path'], manifest['content_hash'])
            snapshot = dict(manifest, raw_bytes=raw)
            recovered = build_rule_inventory([snapshot])
            catalog.update(build_category_catalog([snapshot]))
            existing = {r['ruleKey'] for r in rules}
            rules.extend(r for r in recovered if r['ruleKey'] not in existing)
        except (OSError, ValueError, TypeError, KeyError, RuleInventoryError):
            continue

    by_key = {d['ruleKey']: d for d in decisions if isinstance(d, dict) and isinstance(d.get('ruleKey'), str)}
    located = []
    for item in flat['items']:
        candidates = [r for r in rules if r.get('productName') == item['productName']
                      and r.get('sourceRuleId') == item['sourceRuleId']]
        paths = []
        for rule in candidates:
            path = rule_category_path(rule)
            if path and path not in paths:
                paths.append(path)
        if len(paths) > 1:
            matches = []
            for rule in candidates:
                try:
                    instances = by_key.get(rule['ruleKey'], {}).get('instances', [])
                    if any(material_name(item['productName'], instance, legacy=True) == item['materialName'] for instance in instances):
                        path = rule_category_path(rule)
                        if path and path not in matches:
                            matches.append(path)
                except (ValueError, TypeError, KeyError):
                    continue
            paths = matches
        path = paths[0] if len(paths) == 1 else []
        if not path and item['sourceRuleId'].startswith('话术库:'):
            path = select_category(catalog, item['productName'], [])
        located.append((item, path))
    grouped = group_materials(flat['products'], located, catalog)
    return HistoricalTaskResult.model_validate(dict(flat, items=grouped)).model_dump(mode='json')
