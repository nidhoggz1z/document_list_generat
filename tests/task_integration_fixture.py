"""Offline HTTP model and real application lifespan for T24."""
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import main
from model_client import ModelClient
from task_settings import TaskSettings
from task_store import TaskStore
from download_fixture import pdf_download
from task_pipeline import TaskPipeline
from test_checklist_rule_inventory import snapshot, HEADER
from test_material_generation import decision
from pdf_fixtures import make_pdf
from test_phrase_materials import workbook, FILENAME


class IntegrationFixture:
    def __init__(self, root, count=1):
        self.root = Path(root)
        self.documents = self.root / 'list'
        self.documents.mkdir(parents=True)
        (self.documents / FILENAME).write_bytes(workbook())
        for name in ('甲', '乙'):
            rows = [HEADER, ['1', '基本资料'], ['1.1', '借款人基本资料']] + [
                [str(i), f'材料{i}', '必备', '', ''] for i in range(1, count + 1)] + [['9', '最后一类']]
            (self.documents / (name + '材料清单.docx')).write_bytes(snapshot(rows, name)['raw_bytes'])
        self.secret = 'offline-credential-must-not-be-archived'
        self.settings = TaskSettings.from_env({'TASK_STORAGE_ROOT': str(self.root / 'storage'),
            'MODEL_MAX_RETRIES': '2', **{stage + '_MODEL_' + key: value
                for stage in ('EXTRACT', 'GENERATE') for key, value in dict(
                    API_URL='http://offline.test/chat', NAME=stage.lower(), API_KEY=self.secret).items()}})
        self.store = TaskStore(self.settings.storage_root)
        self.raw = make_pdf(('text', 'scan'))
        self.gates, self.started, self.cancelled, self.late_gates = {}, {}, {}, {}
        self.modes, self.calls, self.order = {}, {}, []
        self.active = self.peak = 0
        self.batch_size = 20

    def event(self, mapping, key):
        return mapping.setdefault(key, asyncio.Event())

    async def wait_started(self, marker):
        await asyncio.wait_for(self.event(self.started, marker).wait(), 5)

    def release(self, marker):
        self.event(self.gates, marker).set()

    async def handle(self, request):
        payload = json.loads(request.content)
        generation = self.app.state.task_worker._generation
        with self.store._connection() as conn:
            row = conn.execute('SELECT businessData FROM tasks WHERE generationId=?', (generation,)).fetchone()
        marker = json.loads(row[0])['cstNm']
        stage = payload['model']
        self.calls[marker, stage] = self.calls.get((marker, stage), 0) + 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if stage == 'extract':
                self.order.append(marker)
                self.event(self.started, marker).set()
                try:
                    await self.event(self.gates, marker).wait()
                except asyncio.CancelledError:
                    self.event(self.cancelled, marker).set()
                    if not self.modes.get(marker, '').startswith('late'):
                        raise
                    await self.event(self.late_gates, marker).wait()
                if self.modes.get(marker) == 'late_failure':
                    return httpx.Response(400, text=self.secret)
                data = dict(pages=[dict(page_number=i, text='甲 乙 批复全文', tables=[], readable=True)
                                  for i in (1, 2)], facts=[], saleable_products=[
                    dict(name=n, page_number=1, source=n, conditions='') for n in ('甲', '乙')])
                if self.modes.get(marker, '').startswith('phrase_'):
                    data['pages'][0]['text'] += '，由甲公司提供连带责任保证担保。'
            else:
                context = json.loads(payload['messages'][1]['content'])
                if 'requestedRowKeys' in context:
                    data = {'decisions': [dict(rowKey=k, scope='NONE', matches=[], products=[])
                                         for k in context['requestedRowKeys']]}
                    if self.modes.get(marker, '').startswith('phrase_'):
                        row = context['phraseRules'][0]
                        d = next(d for d in data['decisions'] if d['rowKey'] == row['rowKey'])
                        d.update(scope='EXPLICIT', matches=[dict(page_number=1, quote=row['phrase'])],
                            products=[dict(productName='甲', materials=[dict(materialKey=m['materialKey'], applicability='APPLICABLE', categoryPath=['9'],
                                instances=[dict(objectKey='', subject='', materialName=m['name'],
                                                requirement='', wordRuleKey='')]) for m in row['materials']])])
                        if self.modes[marker] == 'phrase_incomplete':
                            d['products'][0]['materials'].pop()
                    return httpx.Response(200, json={'choices': [dict(finish_reason='stop',
                        message={'content': json.dumps(data)})]})
                rules = {r['ruleKey']: r for r in context['rules']}
                requested = context['requestedRuleKeys']
                mode = self.modes.get(marker)
                if mode == 'http_failure':
                    return httpx.Response(503, text=self.secret)
                if mode == 'bad_second_product' and any(rules[k]['productName'] == '乙' for k in requested):
                    return httpx.Response(200, json={'choices': [dict(finish_reason='stop',
                        message={'content': 'invalid-json'})]})
                data = {'decisions': [decision(rules[k], context['resolvedContext']) for k in requested]}
            return httpx.Response(200, json={'choices': [dict(finish_reason='stop',
                message={'content': json.dumps(data, ensure_ascii=False)})]})
        finally:
            self.active -= 1

    @asynccontextmanager
    async def running(self):
        self.app = main.create_app(self.documents, task_settings=self.settings)
        def factory(settings):
            return ModelClient(settings, transport=httpx.MockTransport(self.handle))
        def pipeline(*args):
            return TaskPipeline(*args, batch_size=self.batch_size)
        with patch.object(main, 'ModelClient', side_effect=factory), patch.object(main, 'TaskPipeline', side_effect=pipeline):
            async with self.app.router.lifespan_context(self.app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test') as client:
                    self.client = client
                    try:
                        yield self
                    finally:
                        # A failed assertion must not leave a deliberately late model hanging.
                        for marker in self.modes:
                            self.event(self.late_gates, marker).set()

    async def post(self, business='A', task='1', marker=None, raw=None):
        data = dict(bsnEvtId=business, taskId=task, userNo='U', cstNm=marker or business + task)
        data['replyImageUrl'] = 'http://10.0.0.8/approval.pdf'
        with pdf_download(self.raw if raw is None else raw):
            return await self.client.post('/api/v1/tasks', json=data)

    async def query(self, business='A', task='1'):
        response = await self.client.post('/api/v1/tasks/query', json=dict(bsnEvtId=business, taskId=task, userNo='Q'))
        assert response.status_code == 200, response.text
        return response.json()

    async def terminal(self, business='A', task='1'):
        async def wait():
            while True:
                body = await self.query(business, task)
                if body.get('status') in ('DONE', 'FAILED', 'TERMINATED'):
                    return body
                await asyncio.sleep(0.005)
        return await asyncio.wait_for(wait(), 10)
