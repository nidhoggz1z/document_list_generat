import asyncio
import importlib.util
import json
import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from task_settings import TaskSettings

if importlib.util.find_spec('model_client'):
    import model_client
else:
    model_client = None


def response(content='完整内容', reason='stop'):
    return httpx.Response(200, json={'choices': [{'message': {'content': content}, 'finish_reason': reason}]})


class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(model_client, 'T10 model_client is missing')
        self.settings = TaskSettings.from_env({
            'EXTRACT_MODEL_API_URL': 'https://extract.test/custom/completions?version=1',
            'EXTRACT_MODEL_NAME': 'vision-model', 'EXTRACT_MODEL_API_KEY': 'fixture-extract-secret',
            'GENERATE_MODEL_API_URL': 'https://generate.test/other/endpoint',
            'GENERATE_MODEL_NAME': 'text-model', 'GENERATE_MODEL_API_KEY': 'fixture-generate-secret',
            'MODEL_REQUEST_TIMEOUT_SECONDS': '7', 'MODEL_MAX_RETRIES': '2'})
        self.messages = [{'role': 'user', 'content': '请读取全文'}]

    async def make_client(self, handler, settings=None):
        client = model_client.ModelClient(settings or self.settings,
                                           transport=httpx.MockTransport(handler))
        await client.__aenter__()
        self.addAsyncCleanup(client.aclose)
        return client

    async def test_two_endpoints_text_vision_auth_and_timeout(self):
        requests = []
        async def handler(request):
            requests.append(request)
            return response()
        client = await self.make_client(handler)
        vision = [{'role': 'user', 'content': [{'type': 'text', 'text': '扫描页'},
                  {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,fixture'}}]}]
        for stage, messages in [('extract', vision), ('generate', self.messages)]:
            result = await client.complete(stage, messages)
            self.assertEqual(result.content, '完整内容')
            self.assertEqual(result.finish_reason, 'stop')
            config = getattr(self.settings, stage)
            request = requests[-1]
            self.assertEqual(str(request.url), config.api_url)
            self.assertEqual(request.method, 'POST')
            self.assertEqual(request.headers['Authorization'], 'Bearer ' + config.api_key.get_secret_value())
            self.assertEqual(json.loads(request.content), {'model': config.model_name, 'messages': messages})
            self.assertNotIn(config.api_key.get_secret_value(), request.content.decode())
            self.assertTrue(all(value == 7 for value in request.extensions['timeout'].values()))

    async def test_recoverable_http_failures_exhaust_budget(self):
        for status in [408, 429, 500, 502, 503, 599]:
            calls, waits = [], []
            async def handler(request):
                calls.append(request)
                return httpx.Response(status, text='fixture-extract-secret')
            async def sleep(delay):
                waits.append(delay)
            client = await self.make_client(handler)
            with self.subTest(status=status), patch('model_client.asyncio.sleep', side_effect=sleep):
                with self.assertRaises(model_client.ModelCallError) as caught:
                    await client.complete('extract', self.messages)
                self.assertEqual(caught.exception.status_code, status)
                self.assertEqual(len(calls), 3)
                self.assertEqual(waits, [1, 2])
                self.assertNotIn('secret', str(caught.exception))

    async def test_network_timeout_and_connection_errors_retry_then_succeed(self):
        for error_type in [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError]:
            calls = []
            async def handler(request):
                calls.append(request)
                if len(calls) < 3:
                    raise error_type('fixture-extract-secret', request=request)
                return response('恢复成功')
            client = await self.make_client(handler)
            with self.subTest(error=error_type), patch('model_client.asyncio.sleep', return_value=None):
                self.assertEqual((await client.complete('extract', self.messages)).content, '恢复成功')
                self.assertEqual(len(calls), 3)

    async def test_timeout_exhaustion_zero_retries_and_permanent_status_do_not_retry(self):
        for status in [400, 401, 403, 404, 422, 302]:
            calls = []
            async def handler(request):
                calls.append(request)
                return httpx.Response(status, headers={'Location': 'https://other.test'}, text='secret-body')
            client = await self.make_client(handler)
            with self.subTest(status=status), self.assertRaises(model_client.ModelCallError):
                await client.complete('extract', self.messages)
            self.assertEqual(len(calls), 1)
        for retries in [0, 2]:
            calls = []
            async def timeout(request):
                calls.append(request)
                raise httpx.ReadTimeout('fixture-extract-secret', request=request)
            from dataclasses import replace
            client = await self.make_client(timeout, replace(self.settings, max_retries=retries))
            with patch('model_client.asyncio.sleep', return_value=None):
                with self.assertRaises(model_client.ModelCallError):
                    await client.complete('extract', self.messages)
            self.assertEqual(len(calls), retries + 1)

    async def test_incomplete_or_invalid_outputs_are_never_retried(self):
        payloads = [response('partial', reason) for reason in ['length', 'content_filter', None, 'tool_calls']]
        payloads += [response(value) for value in ['', '  ', None, [], {}]]
        payloads += [httpx.Response(200, json=value) for value in [None, [], {}, {'choices': []},
                     {'choices': [None]}, {'choices': [{'message': None, 'finish_reason': 'stop'}]}]]
        payloads += [httpx.Response(200, text='{bad json')]
        for payload in payloads:
            calls = []
            async def handler(request):
                calls.append(request)
                return payload
            client = await self.make_client(handler)
            with self.subTest(body=payload.content), self.assertRaises(model_client.ModelCallError):
                await client.complete('generate', self.messages)
            self.assertEqual(len(calls), 1)

    async def test_cancel_interrupts_request_and_retry_wait(self):
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def blocking(request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        client = await self.make_client(blocking)
        task = asyncio.create_task(client.complete('extract', self.messages))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        self.assertTrue(cancelled.is_set())
        waiting = asyncio.Event()
        calls = []
        async def retryable(request):
            calls.append(request)
            return httpx.Response(429)
        async def wait(delay):
            waiting.set()
            await asyncio.Event().wait()
        client = await self.make_client(retryable)
        with patch('model_client.asyncio.sleep', side_effect=wait):
            task = asyncio.create_task(client.complete('generate', self.messages))
            await asyncio.wait_for(waiting.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        self.assertEqual(len(calls), 1)

    async def test_keys_not_in_logs_errors_or_exception_chains(self):
        import traceback
        async def handler(request):
            raise httpx.ConnectError('fixture-extract-secret', request=request)
        client = await self.make_client(handler)
        with self.assertNoLogs(level=logging.INFO), patch('model_client.asyncio.sleep', return_value=None):
            try:
                await client.complete('extract', self.messages)
            except model_client.ModelCallError as error:
                rendered = ''.join(traceback.format_exception(error))
                self.assertNotIn('fixture-extract-secret', rendered)
            else:
                self.fail('network error was accepted')
        client = await self.make_client(lambda request: response())
        with self.assertLogs('httpx', level=logging.INFO) as logs:
            await client.complete('extract', self.messages)
        self.assertNotIn('fixture-extract-secret', '\n'.join(logs.output))

    async def test_missing_config_is_safe_and_context_closes_http_client(self):
        calls = []
        client = await self.make_client(lambda request: calls.append(request), TaskSettings.from_env({}))
        with self.assertRaises(Exception) as caught:
            await client.complete('extract', self.messages)
        self.assertIn('配置', str(caught.exception))
        self.assertEqual(calls, [])
        with self.assertRaises(ValueError):
            await client.complete('unknown', self.messages)
        async with model_client.ModelClient(self.settings, transport=httpx.MockTransport(lambda r: response())) as owned:
            self.assertEqual((await owned.complete('extract', self.messages)).content, '完整内容')
        with self.assertRaises(RuntimeError):
            await owned.complete('extract', self.messages)


if __name__ == '__main__':
    unittest.main()
