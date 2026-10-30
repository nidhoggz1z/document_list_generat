"""Exact document retrieval for HiAgent's param input."""
import hashlib
import io
import inspect
import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from docx_reader import read_docx
from task_api import register_task_routes
from task_files import submit_task_from_url
from task_settings import DEFAULT_STORAGE_ROOT, TaskConfigurationError, TaskSettings
from task_store import TaskStore
from task_pipeline import TaskPipeline
from task_worker import StorageOwnership, TaskWorker
from model_client import ModelClient
from service_log import daily_logging, event, RequestLogMiddleware

CHECKLIST_SUFFIX = '材料清单'


class RetrievalError(Exception):
    def __init__(self, message, status_code=503):
        super().__init__(message)
        self.status_code = status_code


class DocumentStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.documents = {}
        self.by_id = {}
        self._signature = None
        self._lock = Lock()
        self.refresh()

    def _scan(self):
        if not self.root.is_dir():
            raise RetrievalError('材料清单目录不存在：' + str(self.root))
        paths = sorted(p for p in self.root.rglob('*.docx')
                       if not p.name.startswith('~$') and p.stem.endswith(CHECKLIST_SUFFIX))
        signature = []
        for path in paths:
            stat = path.stat()
            signature.append((path.relative_to(self.root).as_posix(), stat.st_mtime_ns,
                              stat.st_ctime_ns, stat.st_size, stat.st_ino))
        return paths, tuple(signature)

    def refresh(self):
        # Reuse parsed content while file names, timestamps and sizes stay unchanged.
        with self._lock:
            try:
                paths, signature = self._scan()
                if signature == self._signature:
                    return
                documents, by_id = self._load(paths)
                if self._scan()[1] != signature:
                    raise RetrievalError('材料清单正在更新，请稍后重试')
            except OSError as exc:
                raise RetrievalError('无法检查材料清单目录，请稍后重试') from exc
            # Publish only after every document in the new version validates.
            self.documents = documents
            self.by_id = by_id
            self._signature = signature

    def _load(self, paths):
        documents = {}
        by_id = {}
        for path in paths:
            if not path.resolve().is_relative_to(self.root):
                raise RetrievalError('文档路径超出数据目录')
            # Only remove the fixed filename suffix and its optional underscore separator.
            name = path.stem.removesuffix(CHECKLIST_SUFFIX).removesuffix('_')
            if not name or name != name.strip() or len(name) > 100:
                raise RetrievalError('材料清单文件名中的项目名称无效：' + path.name)
            if name in documents:
                raise RetrievalError('存在重复项目名称，请先消除歧义：' + name, 409)
            try:
                content = read_docx(path)
            except Exception as exc:
                raise RetrievalError('无法完整读取文档：' + path.name) from exc
            source = path.relative_to(self.root).as_posix()
            document_id = hashlib.sha256(source.encode('utf-8')).hexdigest()[:24]
            item = dict(id=document_id, name=name, filename=path.name, category='材料清单',
                        category_name='材料清单', source=source, content=content,
                        character_count=len(content), download_url='/files/' + document_id)
            documents[name] = item
            by_id[document_id] = path
        if not documents:
            raise RetrievalError('数据目录内没有可用材料清单')
        return documents, by_id

    def retrieve(self, param):
        if not isinstance(param, str):
            raise RetrievalError('param 必须是项目名称字符串', 422)
        name = param.strip()
        if not name or len(name) > 100 or any(c in name for c in ('/', '\\', '\x00')):
            raise RetrievalError('param 必须是有效项目名称，不能包含路径', 422)
        self.refresh()
        item = self.documents.get(name)
        if item is None:
            raise RetrievalError('未找到项目对应的材料清单：' + name + '；请通过 /projects 查看有效项目名称', 404)
        return item['content']

    def snapshot(self, names):
        """Return ordered name/filename/content/raw_bytes/content_hash records.

        Refresh validates the entire catalog. Under its lock, bracket reads with
        directory signatures and parse the exact bytes retained in the snapshot.
        Any version change fails the whole call; a later call can refresh/retry.
        """
        if not isinstance(names, (list, tuple)) or not names:
            raise RetrievalError('快照必须包含项目名称列表', 422)
        normalized = []
        for value in names:
            if not isinstance(value, str):
                raise RetrievalError('项目名称必须是字符串', 422)
            name = value.strip()
            if not name or len(name) > 100 or any(c in name for c in ('/', '\\', '\x00')):
                raise RetrievalError('项目名称无效', 422)
            if name in normalized:
                raise RetrievalError('快照项目名称重复', 409)
            normalized.append(name)
        self.refresh()
        with self._lock:
            try:
                signature = self._scan()[1]
                if signature != self._signature:
                    raise RetrievalError('材料清单正在更新，请稍后重试')
                snapshot = []
                for name in normalized:
                    item = self.documents.get(name)
                    if item is None:
                        raise RetrievalError('未找到项目对应的材料清单：' + name, 404)
                    path = self.by_id[item['id']]
                    if not path.resolve().is_relative_to(self.root):
                        raise RetrievalError('文档路径超出数据目录')
                    raw = path.read_bytes()
                    try:
                        content = read_docx(io.BytesIO(raw))
                    except Exception:
                        raise RetrievalError('无法完整读取文档：' + item['filename']) from None
                    snapshot.append(dict(name=name, filename=item['filename'], content=content,
                                         raw_bytes=raw, content_hash=hashlib.sha256(raw).hexdigest()))
                if self._scan()[1] != signature:
                    raise RetrievalError('材料清单正在更新，请稍后重试')
                return snapshot
            except OSError:
                raise RetrievalError('无法读取材料清单快照，请稍后重试') from None


class RetrievalInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    param: str = Field(..., min_length=1, max_length=100, strict=True,
                       description='完整项目名称，例如：流动资金贷款。严格匹配对应材料清单，不含材料清单或 .docx 后缀。')


def create_app(data_dir=None, *, task_store=None, task_submitter=submit_task_from_url, notify_cancel=None,
               task_settings=None, log_dir=None):
    # Packaged service uses ./list; development uses the project-level ../list.
    local_list = Path(__file__).resolve().parent / 'list'
    default_list = local_list if local_list.is_dir() else local_list.parent.parent / 'list'
    root = data_dir or os.environ.get('DOCUMENT_DIR') or default_list
    store = DocumentStore(root)

    @asynccontextmanager
    async def task_lifespan(service):
        configuration_error = None
        try:
            settings = task_settings or TaskSettings.from_env()
        except TaskConfigurationError as error:
            # Invalid model budgets disable creation, without disabling read-only APIs.
            configuration_error = str(error)
            event('模型配置不可用，任务创建已禁用', level=logging.WARNING)
            settings = TaskSettings.from_env({'TASK_STORAGE_ROOT': os.environ.get(
                'TASK_STORAGE_ROOT', str(DEFAULT_STORAGE_ROOT))})
        storage_root = task_store.storage_root if task_store is not None else settings.storage_root
        # Ownership precedes even default store initialization, and always recovery.
        with StorageOwnership(storage_root):
            tasks = task_store if task_store is not None else TaskStore(storage_root)
            recovered = tasks.recover_interrupted()
            if recovered:
                event('重启恢复未完成任务', count=recovered, status='FAILED')
            async with ModelClient(settings) as client:
                worker = TaskWorker(tasks, TaskPipeline(tasks, store, client))

                async def notify(generations):
                    worker.notify(generations)
                    if notify_cancel is not None:
                        result = notify_cancel(generations)
                        if inspect.isawaitable(result):
                            await result

                service.state.task_store = tasks
                service.state.task_settings = settings
                service.state.task_configuration_error = configuration_error
                service.state.task_worker = worker
                service.state.task_client = client
                service.state.task_notify_cancel = notify
                worker.start()
                try:
                    yield
                finally:
                    # Stop acceptance first; join pipeline before closing HTTP and ownership.
                    service.state.task_store = None
                    try:
                        await worker.stop()
                    finally:
                        service.state.task_worker = None
                        service.state.task_settings = None
                        service.state.task_notify_cancel = notify_cancel

    @asynccontextmanager
    async def lifespan(service):
        with daily_logging(log_dir):
            event('服务启动中')
            try:
                async with task_lifespan(service):
                    event('服务启动完成')
                    yield
            except Exception as error:
                event('服务运行或启动失败', level=logging.ERROR, error_type=type(error).__name__)
                raise
            finally:
                event('服务停止')

    service = FastAPI(title='放款材料文件检索服务', version='2.2.0', lifespan=lifespan,
                      description='输入完整项目名称，严格匹配材料清单并返回完整正文纯文本；list 更新后下次查询自动生效。')
    service.openapi_version = '3.0.3'
    service.add_middleware(RequestLogMiddleware)
    service.state.store = store
    register_task_routes(service, task_store, task_submitter=task_submitter, notify_cancel=notify_cancel)

    @service.exception_handler(RetrievalError)
    async def retrieval_error(request, error):
        event('材料检索失败', level=logging.WARNING, status=error.status_code)
        return JSONResponse(status_code=error.status_code, content={'detail': str(error)})

    @service.get('/health', summary='健康检查')
    def health():
        store.refresh()
        return {'status': 'healthy', 'document_count': len(store.documents)}

    @service.get('/projects', summary='可用项目名称与材料清单')
    def projects():
        store.refresh()
        return {'projects': [{key: doc[key] for key in ('name', 'category', 'category_name', 'filename')}
                             for doc in store.documents.values()]}

    @service.get('/classes', summary='可用项目清单（兼容原接口）')
    def classes():
        return {'classes': projects()['projects']}

    @service.post('/retrieve', response_class=PlainTextResponse, operation_id='retrieveDocuments',
                  summary='按项目名称严格获取材料清单正文', responses={
                      404: {'description': '项目对应的材料清单不存在'},
                      409: {'description': '存在重复项目名称'},
                      503: {'description': '清单损坏、正在更新或目录不可用，请修复后重试'}})
    def retrieve(body: RetrievalInput) -> str:
        return store.retrieve(body.param)

    @service.get('/files/{document_id}', name='download_file', summary='下载原始 DOCX 文件')
    def download_file(document_id: str):
        store.refresh()
        path = store.by_id.get(document_id)
        if path is None:
            raise RetrievalError('文件不存在', 404)
        if not path.is_file():
            raise RetrievalError('原文件正在更新，请稍后重试', 503)
        return FileResponse(path, filename=path.name,
                            media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')

    return service


app = create_app()

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('main:app', host='0.0.0.0', port=8000, workers=1)
