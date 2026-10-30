"""T26: real sockets, isolated model server, source and unpacked deployment."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest

import httpx
from pdf_fixtures import make_pdf
from test_checklist_rule_inventory import snapshot, HEADER
from test_material_generation import decision
from test_phrase_materials import workbook, FILENAME
from material_tree import iter_materials

ROOT = Path(__file__).resolve().parents[1]


class PackagedHTTPTests(unittest.TestCase):
    def test_packaged_create_query_restart_over_http(self):
        for packaged in (True, False):
            with self.subTest(packaged=packaged), tempfile.TemporaryDirectory() as directory:
                self.exercise(Path(directory), packaged)

    def exercise(self, root, packaged):
        release, started, hold = threading.Event(), threading.Event(), threading.Event()
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path != '/approval.pdf':
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/pdf')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(payload['model'])
                if payload['model'] == 'extract':
                    if hold.is_set():
                        started.set()
                        if not release.wait(25):
                            return
                    data = dict(pages=[dict(page_number=i, text='甲 乙 完整批复', tables=[], readable=True)
                                       for i in (1, 2)], facts=[], saleable_products=[
                        dict(name=n, page_number=1, source=n, conditions='') for n in ('甲', '乙')])
                else:
                    context = json.loads(payload['messages'][1]['content'])
                    rules = {r['ruleKey']: r for r in context['rules']}
                    if 'requestedRowKeys' in context:
                        data = {'decisions': [dict(rowKey=k, scope='NONE', matches=[], products=[])
                                             for k in context['requestedRowKeys']]}
                    else:
                        data = {'decisions': [decision(rules[k], context['resolvedContext'])
                                              for k in context['requestedRuleKeys']]}
                body = json.dumps({'choices': [dict(finish_reason='stop',
                    message={'content': json.dumps(data, ensure_ascii=False)})]}).encode()
                try:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # Replaced/crashed service has closed its model socket.

        model = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=model.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(model.server_close)
        self.addCleanup(model.shutdown)
        self.addCleanup(release.set)
        if packaged:
            with tarfile.open(ROOT / 'src.tar') as archive:
                archive.extractall(root, filter='data')
            app_dir = root
        else:
            app_dir = ROOT / 'src'
        documents = root / 'fixtures'
        documents.mkdir()
        (documents / FILENAME).write_bytes(workbook())
        for name in ('甲', '乙'):
            (documents / (name + '材料清单.docx')).write_bytes(
                snapshot([HEADER, ['1', '基本资料'], ['1.1', '借款人基本资料'],
                    ['1', '身份证明', '必备', '', ''], ['9', '最后一类']], name)['raw_bytes'])
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        env = {**os.environ, 'DOCUMENT_DIR': str(documents), 'TASK_STORAGE_ROOT': str(root / 'storage'),
               'MODEL_MAX_RETRIES': '0', 'MODEL_REQUEST_TIMEOUT_SECONDS': '20'}
        model_env = {}
        for stage in ('EXTRACT', 'GENERATE'):
            model_env.update({stage + '_MODEL_API_URL': f'http://127.0.0.1:{model.server_port}/chat',
                              stage + '_MODEL_API_KEY': 'offline-only-key', stage + '_MODEL_NAME': stage.lower()})
        if packaged:
            # Real unpacked service must load the separately supplied file at startup.
            for name in model_env:
                env.pop(name, None)
            (app_dir / 'model-api.env').write_text(''.join(
                f'{name}={value}\n' for name, value in model_env.items()), encoding='utf-8')
        else:
            env.update(model_env)
        raw = make_pdf(('text', 'scan'))
        log = (root / 'server.log').open('wb')
        self.addCleanup(log.close)
        process = None

        def stop():
            nonlocal process
            if process is not None:
                process.kill()
                process.wait(timeout=10)
                self.assertIsNotNone(process.returncode)
                process = None

        def start(client):
            nonlocal process
            process = subprocess.Popen([sys.executable, '-B', '-m', 'uvicorn', 'main:app',
                '--app-dir', str(app_dir), '--host', '127.0.0.1', '--port', str(port),
                '--workers', '1', '--log-level', 'warning'], cwd=root, env=env,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    if client.get('/health').status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                if process.poll() is not None:
                    break
                time.sleep(.05)
            self.fail('Service failed to start: ' + (root / 'server.log').read_text(errors='replace'))

        with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=3) as client:
            def query(b, t):
                response = client.post('/api/v1/tasks/query', json=dict(bsnEvtId=b, taskId=t, userNo='Q'))
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()

            def post(b, t):
                response = client.post('/api/v1/tasks', json=dict(
                    bsnEvtId=b, taskId=t, userNo='U',
                    replyImageUrl=f'http://127.0.0.1:{model.server_port}/approval.pdf'))
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['autoReply'], 'task created')

            def wait(b, t, status):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    result = query(b, t)
                    if result.get('status') == status:
                        return result
                    if result.get('status') == 'FAILED':
                        self.fail(str(result))
                    time.sleep(.03)
                self.fail(f'Task did not reach {status}: {result}')

            try:
                start(client)
                self.assertEqual(query('missing', '0'), {'reply': '任务未创建'})
                post('history', '1')
                history = wait('history', '1', 'DONE')
                self.assertEqual(history['data']['total'], 2)
                self.assertNotIn('sourceDocumentHash', history['data'])
                self.assertEqual([p['productName'] for p in history['data']['products']], ['甲', '乙'])
                self.assertEqual([i['itemId'] for i in iter_materials(history['data']['items'])], ['R0001', 'R0002'])
                self.assertEqual(next(iter_materials(history['data']['items']))['sourceSystem'], '上银办')
                self.assertEqual(history['data']['items'][0]['categories'][0]['categoryName'], '基本资料')
                calls_before = len(calls)
                rejected = client.post('/api/v1/tasks', json=dict(bsnEvtId='history', taskId='1',
                    userNo='U', replyImageUrl=f'http://127.0.0.1:{model.server_port}/missing.pdf'))
                self.assertEqual(rejected.status_code, 502, rejected.text)
                self.assertEqual(set(rejected.json()), {'reply'})
                self.assertEqual(query('history', '1'), history)
                self.assertEqual(len(calls), calls_before)
                hold.set()
                post('replace', '1')
                self.assertTrue(started.wait(5))
                post('replace', '2')
                self.assertEqual(query('replace', '1')['status'], 'TERMINATED')
                hold.clear()
                release.set()
                wait('replace', '2', 'DONE')
                started.clear()
                release.clear()
                hold.set()
                post('unfinished', '1')
                self.assertTrue(started.wait(5))
                post('queued', '1')
                self.assertEqual(query('queued', '1')['status'], 'PENDING')
                stop()
                release.set()
                before = len(calls)
                start(client)
                for business in ('unfinished', 'queued'):
                    result = query(business, '1')
                    self.assertEqual(result['status'], 'FAILED')
                    self.assertTrue(result['reason'])
                    self.assertNotIn('data', result)
                self.assertEqual(query('history', '1'), history)
                self.assertEqual(query('replace', '1')['status'], 'TERMINATED')
                self.assertEqual(len(calls), before, 'restart/query must not rerun models')
                daily_files = list((app_dir / 'log').glob('????-??-??.txt'))
                self.assertTrue(daily_files, 'running service must create a daily TXT log')
                daily_text = '\n'.join(path.read_text(encoding='utf-8') for path in daily_files)
                for expected in ('服务启动完成', 'HTTP请求', 'PDF下载完成', '任务处理完成', '重启恢复未完成任务'):
                    self.assertIn(expected, daily_text)
                self.assertNotIn('offline-only-key', daily_text)
            finally:
                release.set()
                stop()
                log.close()


if __name__ == '__main__':
    unittest.main()
