"""Cancellable Chat Completions HTTP transport, with no business processing.

Own one client with ``async with ModelClient(settings)`` and await
``complete('extract' or 'generate', messages)``. Cancelling that asyncio Task
interrupts the active request or backoff; CancelledError is never swallowed.
"""
import asyncio
from dataclasses import dataclass

import httpx

from task_settings import TaskConfigurationError


class ModelCallError(Exception):
    """Safe to persist: never contains provider bodies, headers or exceptions."""
    def __init__(self, message, *, status_code=None, finish_reason=None):
        super().__init__(message)
        self.status_code = status_code
        self.finish_reason = finish_reason


@dataclass(frozen=True)
class ModelReply:
    content: str
    finish_reason: str


class ModelClient:
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self._http = httpx.AsyncClient(timeout=settings.request_timeout_seconds,
                                       transport=transport, follow_redirects=False,
                                       trust_env=False)

    async def __aenter__(self):
        await self._http.__aenter__()
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self):
        await self._http.aclose()

    async def complete(self, stage, messages):
        if stage not in ('extract', 'generate'):
            raise ValueError('模型阶段必须为 extract 或 generate')
        config = getattr(self.settings, stage)
        if config is None:
            raise TaskConfigurationError('生成服务不可用：模型配置缺失或无效')
        for attempt in range(self.settings.max_retries + 1):
            try:
                result = await self._http.post(config.api_url,
                    headers={'Authorization': 'Bearer ' + config.api_key.get_secret_value()},
                    json={'model': config.model_name, 'messages': messages})
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == self.settings.max_retries:
                    raise ModelCallError('模型网络调用失败，重试预算已耗尽') from None
            except httpx.HTTPError:
                raise ModelCallError('模型 HTTP 调用失败') from None
            else:
                if result.status_code == 200:
                    return self._parse(result)
                retryable = result.status_code in (408, 429) or 500 <= result.status_code <= 599
                if not retryable or attempt == self.settings.max_retries:
                    raise ModelCallError('模型 HTTP 调用失败，状态码 ' + str(result.status_code),
                                         status_code=result.status_code)
            # First retry waits 1 second, second waits 2; no total task deadline.
            await asyncio.sleep(attempt + 1)

    @staticmethod
    def _parse(response):
        try:
            choice = response.json()['choices'][0]
            reason = choice.get('finish_reason')
            content = choice['message']['content']
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ModelCallError('模型响应结构无效') from None
        if reason != 'stop':
            # Only known protocol labels are safe to retain; arbitrary provider text
            # must not reach diagnostics or persisted failure reasons.
            label = reason if isinstance(reason, str) and reason in (
                'length', 'content_filter', 'tool_calls', 'function_call') else None
            raise ModelCallError('模型输出不完整或缺少有效结束标记', finish_reason=label)
        if not isinstance(content, str) or not content.strip():
            raise ModelCallError('模型输出正文为空或类型错误')
        return ModelReply(content=content, finish_reason=reason)
