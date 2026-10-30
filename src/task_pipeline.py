"""T20: complete processing, generation-scoped artifacts, no status publication.

Create TaskPipeline(store, documents, client); await run_task(claimed_record).
The caller owns the model client and cancels the asyncio Task for active HTTP.
Blocking work observes a thread Event and is joined before cancellation returns.
Only the worker may publish DONE/FAILED using the store's generation CAS.
"""
import asyncio
import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from uuid import uuid4
from time import perf_counter

from approval_extractor import extract_approval
from checklist_rules import build_rule_inventory
from material_facts import resolve_material_facts
from material_generator import generate_material_decisions, validate_material_decisions
from material_result import project_material_result
from pdf_reader import read_pdf
from phrase_rules import snapshot_phrase_library, PhraseLibraryError
from phrase_materials import generate_phrase_materials, validate_phrase_materials, merge_phrase_materials
from material_tree import build_category_catalog
from service_log import event
from product_matching import match_product_checklists


class TaskPipelineError(Exception):
    """Safe stage failure, without provider output, paths or credentials."""


def _write(path, raw):
    """Publish complete files atomically inside a service-owned generation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class TaskPipeline:
    def __init__(self, store, documents, model_client, *, page_reader=read_pdf,
                 extractor=extract_approval, generator=generate_material_decisions, batch_size=20):
        self.store, self.documents, self.model_client = store, documents, model_client
        self.page_reader, self.extractor, self.generator = page_reader, extractor, generator
        self.batch_size = batch_size

    async def run_task(self, record, cancel_check=lambda: False):
        stopped = threading.Event()
        stage = '输入核对'
        stage_started = perf_counter()

        def finish_stage(message='阶段完成', level=logging.INFO, **details):
            event(message, record, level=level, stage=stage,
                  duration_ms=round((perf_counter() - stage_started) * 1000, 2), **details)

        def start_stage(name):
            nonlocal stage, stage_started
            finish_stage()
            stage, stage_started = name, perf_counter()
            event('阶段开始', record, stage=stage)

        def check():
            if stopped.is_set() or cancel_check():
                raise asyncio.CancelledError()

        async def blocking(function, *args):
            check()
            job = asyncio.create_task(asyncio.to_thread(function, *args))
            try:
                value = await asyncio.shield(job)
            except asyncio.CancelledError:
                stopped.set()
                # Shield keeps the thread observable; do not let a cancelled
                # reader continue into the next worker's execution window.
                while not job.done():
                    try:
                        await asyncio.shield(job)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not job.cancelled():
                    job.exception()
                raise
            check()
            return value

        async def checkpoint():
            await asyncio.sleep(0)
            check()
            current = await blocking(self.store.get_task, record['bsnEvtId'], record['taskId'])
            if (not current or current['generationId'] != record['generationId']
                    or current['status'] != 'PROCESSING'):
                raise asyncio.CancelledError()

        async def sync(function, *args):
            await checkpoint()
            value = await blocking(function, *args)
            await checkpoint()
            return value

        def save(name, value):
            check()
            raw = json.dumps(dict(generationId=generation, data=value), ensure_ascii=False,
                             allow_nan=False).encode('utf-8')
            path = directory / (name + '.json')
            _write(path, raw)
            check()
            if not self.store.save_artifact(generation, name, dict(generationId=generation,
                    path=str(path), sha256=hashlib.sha256(raw).hexdigest())):
                raise asyncio.CancelledError()

        def save_snapshots(snapshots):
            manifest = []
            for index, snap in enumerate(snapshots, 1):
                check()
                path = directory / f'checklist-{index:04d}.docx'
                _write(path, snap['raw_bytes'])
                manifest.append({k: v for k, v in snap.items() if k != 'raw_bytes'} | {'path': str(path)})
            save('checklists', manifest)

        def save_phrase_snapshot(snapshot):
            path = directory / 'phrase-library.xlsx'
            _write(path, snapshot['raw_bytes'])
            save('phrase-library', {k: v for k, v in snapshot.items() if k != 'raw_bytes'} | {'path': str(path)})

        def catalog():
            self.documents.refresh()
            with self.documents._lock:
                return list(self.documents.documents)

        try:
            event('阶段开始', record, stage=stage)
            await checkpoint()
            generation = record['generationId']
            source = self.store.generation_path(generation)
            if Path(record['pdfPath']).resolve() != source:
                raise ValueError()
            directory = source.parent / 'stages'
            digest = await sync(lambda: hashlib.sha256(source.read_bytes()).hexdigest())
            if digest != record['sourceDocumentHash']:
                raise ValueError()
            start_stage('PDF 页面读取')
            pages = await sync(self.page_reader, source, check)
            names = await sync(catalog)
            start_stage('批复提取')
            await checkpoint()
            approval = await self.extractor(pages, self.model_client, names, check)
            await checkpoint()
            await sync(save, 'approval', approval)
            # Release page images before generating material batches.
            del pages
            start_stage('产品清单匹配')
            matches = await match_product_checklists([p['productName'] for p in approval['products']],
                                                     names, self.model_client, check)
            await sync(save, 'product-matches', matches)
            for match in matches:
                event('产品清单匹配完成', record, **match)
            start_stage('清单快照及索引')
            selected = await sync(self.documents.snapshot, [m['checklistProductName'] for m in matches])
            by_name = {s['name']: s for s in selected}
            # Preserve PDF product identities in rules/facts/results; retain actual Word filenames/bytes.
            snapshots = [dict(by_name[m['checklistProductName']], name=m['extractedProductName']) for m in matches]
            rules = await sync(build_rule_inventory, snapshots, check)
            categories = await sync(build_category_catalog, snapshots, check)
            await sync(save_snapshots, snapshots)
            await sync(save, 'rules', rules)
            await sync(save, 'directories', categories)
            start_stage('话术库快照')
            phrase_snapshot = await sync(snapshot_phrase_library, self.documents.root, check)
            await sync(save_phrase_snapshot, phrase_snapshot)
            start_stage('有效事实合并')
            facts = await sync(resolve_material_facts, approval, record['businessData'], check)
            await sync(save, 'facts', facts)
            start_stage('材料生成及校验')
            await checkpoint()
            decisions = await self.generator(record['businessData'], approval, snapshots, rules,
                facts, self.model_client, check, batch_size=self.batch_size)
            await checkpoint()
            decisions = await sync(validate_material_decisions, decisions, rules, facts, check,
                                   record['businessData'])
            await sync(save, 'decisions', decisions)
            start_stage('话术匹配及整包材料补充')
            await checkpoint()
            supplements = await generate_phrase_materials(record['businessData'], approval, snapshots,
                rules, facts, phrase_snapshot, self.model_client, check, category_catalog=categories)
            await checkpoint()
            supplements = await sync(validate_phrase_materials, supplements, phrase_snapshot['rules'],
                approval, rules, facts, check, categories)
            await sync(save, 'phrase-decisions', supplements)
            rules, decisions = await sync(merge_phrase_materials, rules, decisions,
                supplements, phrase_snapshot, check, categories, record['businessData'])
            await sync(save, 'merged-materials', dict(rules=rules, decisions=decisions))
            start_stage('结果投影')
            result = await sync(project_material_result, approval, snapshots, rules, decisions, digest, check, categories)
            finish_stage()
            return result
        except asyncio.CancelledError:
            finish_stage('阶段取消')
            stopped.set()
            raise
        except PhraseLibraryError:
            finish_stage('阶段失败', logging.ERROR, reason='话术库缺失、损坏、更新中或格式不完整')
            raise TaskPipelineError('任务处理失败：话术库快照（话术库对应材料.xlsx 缺失、损坏、更新中或格式不完整）') from None
        except Exception as error:
            finish_stage('阶段失败', logging.ERROR, error_type=type(error).__name__)
            raise TaskPipelineError(f'任务处理失败：{stage}') from None
