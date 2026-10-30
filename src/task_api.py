"""Task HTTP routes; services are injected, never created on import."""
import json
import sqlite3
import logging

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

from task_files import TaskSubmissionError, submit_task_from_url
from task_models import CreateTaskRequest, CreateResponse, ReplyResponse, TaskIdentity, TaskQueryResponse
from task_settings import TaskConfigurationError
from material_history import historical_result
from service_log import event


def reject_json_constant(value):
    raise ValueError('JSON 不允许 NaN 或 Infinity')


class TaskQueryRoute(APIRoute):
    """Keep task error envelopes local so legacy retrieval errors stay intact."""
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def task_handler(request):
            try:
                if self.path == '/api/v1/tasks':
                    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
                        raise TaskSubmissionError('请求必须采用 application/json', 400)
                    try:
                        json.loads(await request.body(), parse_constant=reject_json_constant)
                    except ValueError:
                        raise TaskSubmissionError('JSON 语法错误', 400) from None
                return await handler(request)
            except TaskSubmissionError as error:
                event('任务请求拒绝', request.scope.get('log_identity'), level=logging.WARNING,
                      status=error.status_code, reason=str(error))
                return JSONResponse(status_code=error.status_code, content={'reply': str(error)})
            except TaskConfigurationError as error:
                event('任务服务配置不可用', request.scope.get('log_identity'), level=logging.ERROR)
                return JSONResponse(status_code=503, content={'reply': str(error)})
            except RequestValidationError as error:
                event('任务请求校验失败', level=logging.WARNING)
                malformed = any(item['type'] == 'json_invalid' for item in error.errors())
                invalid_fields = ('业务字段或 replyImageUrl 缺失或类型错误' if self.path == '/api/v1/tasks'
                                  else '任务标识缺失或类型错误')
                return JSONResponse(status_code=400 if malformed else 422,
                                    content={'reply': 'JSON 语法错误' if malformed else invalid_fields})
            except HTTPException as error:
                return JSONResponse(status_code=error.status_code, content={'reply': '无法解析查询请求'})
            except (OSError, sqlite3.Error) as error:
                event('任务存储不可用', request.scope.get('log_identity'), level=logging.ERROR,
                      error_type=type(error).__name__)
                return JSONResponse(status_code=503, content={'reply': '任务存储暂不可用，请稍后重试'})
            except Exception as error:
                event('任务请求内部错误', request.scope.get('log_identity'), level=logging.ERROR,
                      error_type=type(error).__name__)
                return JSONResponse(status_code=500, content={'reply': '任务请求内部错误'})
        return task_handler


def register_task_routes(service, store=None, *, task_submitter=submit_task_from_url, notify_cancel=None):
    """Register routes; the application lifespan owns storage and worker resources."""
    service.state.task_store = store
    service.state.task_submitter = task_submitter
    service.state.task_notify_cancel = notify_cancel
    router = APIRouter(route_class=TaskQueryRoute)

    @router.post('/api/v1/tasks', response_model=CreateResponse,
                 operation_id='createChecklistTask', summary='通过 JSON 中的 URL 下载批复 PDF 并创建任务',
                 responses={status: {'model': ReplyResponse} for status in (400, 422, 413, 415, 502, 503, 504, 500)})
    async def create_task(body: CreateTaskRequest, request: Request):
        request.scope['log_identity'] = {key: getattr(body, key) for key in ('bsnEvtId', 'taskId')}
        task_store = service.state.task_store
        if task_store is None:
            raise TaskSubmissionError('任务存储尚未配置', 503)
        settings = getattr(service.state, 'task_settings', None)
        configuration_error = getattr(service.state, 'task_configuration_error', None)
        if configuration_error is not None:
            raise TaskConfigurationError(configuration_error)
        if settings is not None:
            settings.require_models()
        result = await service.state.task_submitter(task_store, body.model_dump(mode='json'),
                                                   notify_cancel=service.state.task_notify_cancel)
        worker = getattr(service.state, 'task_worker', None)
        if worker is not None:
            worker.notify()
        return result

    @router.post('/api/v1/tasks/query', response_model=TaskQueryResponse,
                 operation_id='queryChecklistTask', summary='查询任务状态及已保存结果',
                 responses={status: {'model': ReplyResponse} for status in (400, 422, 503, 500)})
    def query_task(body: TaskIdentity, request: Request):
        request.scope['log_identity'] = {key: getattr(body, key) for key in ('bsnEvtId', 'taskId')}
        task_store = service.state.task_store
        if task_store is None:
            return JSONResponse(status_code=503, content={'reply': '任务存储尚未配置'})
        record = task_store.get_task(body.bsnEvtId, body.taskId)
        if record is None:
            return {'reply': '任务未创建'}
        response = {key: record[key] for key in ('bsnEvtId', 'userNo', 'taskId', 'status')}
        if record['status'] == 'FAILED':
            response['reason'] = record['reason']
        elif record['status'] == 'DONE':
            response.update(message='生成完成', data=historical_result(record, task_store))
        return response

    service.include_router(router)
