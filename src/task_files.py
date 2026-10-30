"""Download/validate PDF bytes before atomically accepting a task."""
import asyncio
import hashlib
import inspect
import logging
import os
import re
import sqlite3
from uuid import uuid4
from tempfile import SpooledTemporaryFile
from urllib.parse import urlsplit
from time import perf_counter

import httpx
from httpx import AsyncClient as PDFHTTPClient
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from task_models import BusinessData
from service_log import event

MAX_PDF_BYTES = 10 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 60
logger = logging.getLogger(__name__)


class TaskSubmissionError(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def validate_pdf_url(value):
    """Intranet hosts/IPs are supported; only direct, credential-free HTTP(S)."""
    try:
        if not isinstance(value, str) or not value.strip() or any(c.isspace() for c in value):
            raise ValueError()
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None or parsed.fragment):
            raise ValueError()
        parsed.port
        httpx.URL(value)
    except (ValueError, httpx.InvalidURL):
        raise TaskSubmissionError('replyImageUrl 必须为可直接下载 PDF 的 HTTP(S) URL', 422) from None
    return value


async def submit_task_from_url(store, business_data, *, notify_cancel=None):
    """Fully download before acceptance; network failures never replace old tasks."""
    try:
        data = BusinessData.model_validate(business_data).model_dump(mode='json')
    except ValidationError:
        raise TaskSubmissionError('业务字段缺失或类型错误', 422) from None
    url = validate_pdf_url(data.get('replyImageUrl'))
    started = perf_counter()
    event('PDF下载开始', data)
    # Spill above 1 MiB to disk. Close the response before committing a task.
    with SpooledTemporaryFile(max_size=1024 * 1024) as temporary:
        try:
            async with asyncio.timeout(DOWNLOAD_TIMEOUT_SECONDS):
                async with PDFHTTPClient(timeout=30, follow_redirects=True, max_redirects=5,
                                         trust_env=False) as client:
                    async with client.stream('GET', url, headers={'Accept-Encoding': 'identity'}) as response:
                        if response.status_code != 200:
                            raise TaskSubmissionError('批复 PDF 下载失败，请检查链接是否可访问', 502)
                        size = 0
                        async for chunk in response.aiter_bytes(CHUNK_BYTES):
                            size += len(chunk)
                            if size > MAX_PDF_BYTES:
                                raise TaskSubmissionError('PDF 超过 10 MiB', 413)
                            temporary.write(chunk)
        except (TimeoutError, httpx.TimeoutException):
            event('PDF下载超时', data, level=logging.WARNING,
                  duration_ms=round((perf_counter() - started) * 1000, 2))
            raise TaskSubmissionError('批复 PDF 下载超时，请稍后重试', 504) from None
        except (httpx.RequestError, httpx.InvalidURL):
            event('PDF下载失败', data, level=logging.WARNING, reason='连接、传输或重定向失败')
            raise TaskSubmissionError('批复 PDF 下载失败，请检查链接是否可访问', 502) from None
        except OSError:
            event('PDF暂存失败', data, level=logging.ERROR)
            raise TaskSubmissionError('无法暂存批复 PDF，请稍后重试', 503) from None
        except TaskSubmissionError as error:
            event('PDF下载失败', data, level=logging.WARNING, reason=str(error), status=error.status_code)
            raise
        except asyncio.CancelledError:
            event('PDF下载取消', data)
            raise
        event('PDF下载完成', data, bytes=size, duration_ms=round((perf_counter() - started) * 1000, 2))
        temporary.seek(0)
        return await submit_task(store, data, UploadFile(temporary), notify_cancel=notify_cancel)


async def submit_task(store, business_data, upload, *, notify_cancel=None):
    """Accept an async read(size) upload, returning the public creation response.

    Filename, MIME and declared size are untrusted and ignored. The caller owns
    the upload and closes it. notify_cancel receives replaced generation IDs,
    after commit, and may be a synchronous or asynchronous callable.
    """
    try:
        data = BusinessData.model_validate(business_data).model_dump(mode='json')
    except ValidationError:
        raise TaskSubmissionError('业务字段缺失或类型错误', 422) from None
    generation = uuid4().hex
    path = store.generation_path(generation)
    owned = committed = False
    try:
        path.parent.mkdir(parents=True, exist_ok=False)
        owned = True
        digest = hashlib.sha256()
        size = 0
        header = b''
        with path.open('xb') as target:
            while True:
                chunk = await upload.read(CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    raise TaskSubmissionError('PDF 超过 10 MiB', 413)
                header = (header + chunk)[:9]
                digest.update(chunk)
                target.write(chunk)
            if size == 0:
                raise TaskSubmissionError('PDF 文件不能为空', 422)
            # Only a type check: full readability/encryption is checked at execution.
            if not re.match(rb'%PDF-(?:1\.[0-9]|2\.0)[\r\n\t ]', header):
                raise TaskSubmissionError('文件内容不是 PDF', 415)
            target.flush()
            os.fsync(target.fileno())
        _, cancelled = store.accept_task(data, generation_id=generation,
                                         source_document_hash=digest.hexdigest())
        committed = True
        event('任务已创建', data, generationId=generation, status='PENDING')
        if cancelled:
            event('旧任务已替换', data, replaced_generations=cancelled, status='TERMINATED')
    except (OSError, sqlite3.Error):
        raise TaskSubmissionError('无法保存文件或任务，请稍后重试', 503) from None
    finally:
        if owned and not committed:
            # Delete only this request's service-created file and empty directory.
            try:
                path.unlink(missing_ok=True)
                path.parent.rmdir()
            except OSError:
                logger.error('无法清理未提交的上传文件，generationId=%s', generation)
    if cancelled and notify_cancel is not None:
        try:
            notification = notify_cancel(cancelled)
            if inspect.isawaitable(notification):
                await notification
        except Exception:
            # No exception text: injected hooks may contain sensitive request data.
            logger.error('任务已提交，但旧代次取消通知失败，generationId=%s', generation)
    return {key: data[key] for key in ('bsnEvtId', 'userNo', 'taskId')} | {
        'autoReply': 'task created'}
