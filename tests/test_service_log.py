"""Daily logs, lifecycle, request summaries, and content exclusion."""
import importlib.util
import io
import logging
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import main
from task_settings import TaskSettings
from test_service import make_docx
from download_fixture import pdf_download
from task_integration_fixture import IntegrationFixture

logs = __import__('service_log') if importlib.util.find_spec('service_log') else None


class DailyLogTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(logs, 'daily TXT logging is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'log'

    def test_beijing_midnight_rotates_with_exact_date_names_and_preserves_history(self):
        handler = logs.DailyLogHandler(self.directory)
        self.addCleanup(handler.close)
        for stamp, message in [('2026-10-30T15:59:59+00:00', '中文第一天'),
                               ('2026-10-30T16:00:00+00:00', '中文第二天')]:
            record = logging.LogRecord('service', logging.INFO, '', 1, message, (), None)
            record.created = datetime.fromisoformat(stamp).timestamp()
            handler.handle(record)
        self.assertEqual({p.name for p in self.directory.iterdir()}, {'2026-10-30.txt', '2026-10-31.txt'})
        first = (self.directory / '2026-10-30.txt').read_text(encoding='utf-8')
        self.assertIn('2026-10-30 23:59:59', first)
        self.assertIn('中文第一天', first)
        second = (self.directory / '2026-10-31.txt').read_text(encoding='utf-8')
        self.assertIn('2026-10-31 00:00:00', second)
        self.assertNotIn('中文第一天', second)

    def test_restarts_append_and_nested_setup_does_not_duplicate_lines(self):
        for _ in range(2):
            with logs.daily_logging(self.directory):
                with logs.daily_logging(self.directory):
                    logs.event('测试追加', {'bsnEvtId': 'B\nspoof', 'taskId': 'T', 'secret': 'never-log'})
        text = next(self.directory.glob('*.txt')).read_text(encoding='utf-8')
        self.assertEqual(text.count('测试追加'), 2)
        self.assertEqual(len(text.splitlines()), 2)
        self.assertIn('B\\nspoof', text)
        self.assertNotIn('never-log', text)

    def test_threads_do_not_lose_or_merge_log_lines(self):
        with logs.daily_logging(self.directory):
            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(lambda i: logs.event('并发记录', task=i), range(40)))
        lines = next(self.directory.glob('*.txt')).read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 40)
        self.assertTrue(all('并发记录' in line for line in lines))

    def test_unwritable_directory_falls_back_without_breaking_business(self):
        self.directory.write_text('not a directory', encoding='utf-8')
        output = io.StringIO()
        with patch('sys.stderr', output), logs.daily_logging(self.directory):
            logs.event('测试降级', {'bsnEvtId': 'B', 'taskId': 'T'})
        self.assertIn('测试降级', output.getvalue())

    def test_disk_flush_failure_does_not_break_shutdown(self):
        output = io.StringIO()
        with patch('sys.stderr', output), patch.object(logs.DailyLogHandler, 'flush', side_effect=OSError('disk full')):
            with logs.daily_logging(self.directory):
                logs.event('磁盘写入失败测试')
        self.assertIn('磁盘写入失败测试', output.getvalue())

    def test_lifecycle_and_http_summaries_omit_body_url_query_and_arbitrary_exceptions(self):
        documents = self.root / 'list'
        make_docx(documents / '测试材料清单.docx', 'private-document-text')
        settings = TaskSettings.from_env({'TASK_STORAGE_ROOT': str(self.root / 'storage'),
            **{stage + '_MODEL_' + key: value for stage in ('EXTRACT', 'GENERATE')
               for key, value in dict(API_URL='http://private-model/chat', NAME='fixture',
                                     API_KEY='private-model-key').items()}})
        app = main.create_app(documents, task_settings=settings, log_dir=self.directory)
        data = dict(bsnEvtId='B', userNo='U', taskId='T', cstNm='private-customer',
                    replyImageUrl='http://private-files/approval?token=private-url-token')
        with TestClient(app) as client:
            self.assertEqual(client.post('/retrieve?token=private-query', json={'param': '测试'}).status_code, 200)
            with pdf_download(b'not a PDF private-pdf-text'):
                self.assertEqual(client.post('/api/v1/tasks', json=data).status_code, 415)
            async def broken(*args, **kwargs):
                raise RuntimeError('private-internal-exception')
            app.state.task_submitter = broken
            self.assertEqual(client.post('/api/v1/tasks', json=data).status_code, 500)
            self.assertEqual(client.post('/api/v1/tasks/query', json=data | {'cstNm': None}).status_code, 422)
        text = next(self.directory.glob('*.txt')).read_text(encoding='utf-8')
        for expected in ('服务启动', '服务停止', 'HTTP请求', '/retrieve', 'status=200',
                         'status=415', 'status=500', 'PDF下载开始', 'PDF下载完成', 'bsnEvtId="B"', 'taskId="T"'):
            self.assertIn(expected, text)
        for private in ('private-document-text', 'private-query', 'private-customer', 'private-model-key',
                        'private-url-token', 'private-files', 'private-pdf-text', 'private-internal-exception'):
            self.assertNotIn(private, text)


class TaskLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_stages_outcomes_replacement_and_model_content_exclusion(self):
        self.assertIsNotNone(logs)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'log'
            fixture = IntegrationFixture(root / 'fixtures')
            with patch.object(logs, 'DEFAULT_LOG_DIR', directory):
                async with fixture.running():
                    fixture.release('success')
                    self.assertEqual((await fixture.post(marker='success')).status_code, 200)
                    self.assertEqual((await fixture.terminal())['status'], 'DONE')
                    fixture.modes['failure'] = 'http_failure'
                    fixture.release('failure')
                    await fixture.post(task='failed', marker='failure')
                    self.assertEqual((await fixture.terminal(task='failed'))['status'], 'FAILED')
                    await fixture.post(task='old', marker='old')
                    await fixture.wait_started('old')
                    await fixture.post(task='new', marker='new')
                    fixture.release('new')
                    self.assertEqual((await fixture.terminal(task='new'))['status'], 'DONE')
                    self.assertEqual((await fixture.query(task='old'))['status'], 'TERMINATED')
            text = next(directory.glob('*.txt')).read_text(encoding='utf-8')
            for expected in ('任务已创建', '任务开始处理', '阶段开始', '阶段完成', 'PDF 页面读取',
                             '批复提取', '材料生成及校验', '阶段失败', '任务处理完成', '任务处理失败',
                             '旧任务已替换', 'duration_ms=', 'status="DONE"', 'status="FAILED"'):
                self.assertIn(expected, text)
            self.assertNotIn(fixture.secret, text)
            self.assertNotIn('甲 乙 批复全文', text)


if __name__ == '__main__':
    unittest.main()
