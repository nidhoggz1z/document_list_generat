# T09：上传创建任务接口

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线验收（2026-10-23）；真实模型联调及应用生命周期不在本任务范围。
**依赖：** [T07](../t07-upload-service/task.md)、[T08](../t08-query-api/task.md)
**规格映射：** `FR-01`、`FR-02`、`FR-03`、`AC-01`、`AC-02`、`AC-03`，见 [spec](../../spec.md)。
**目标：** 将 multipart 请求映射到提交服务并立即响应。

## 一个 diff 的边界

- 修改 src/task_api.py
- 修改 src/main.py：注入提交服务
- 修改 src/requirements.txt：multipart 必需依赖
- 新建 tests/test_task_create_api.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. businessData 作为表单文本 JSON，file 作为上传文件；用已安装风格接入 FastAPI，锁定实际 multipart 依赖版本。
2. 成功保存入队后返回四个确认字段；不等待模型，不下载 replyImageUrl。
3. 400/422/413/415/503/500 按 spec 映射 reply 结构，新接口异常处理不要破坏旧路由。

## 执行步骤

- [x] 先新增/补充 tests/test_task_create_api.py 中的 test_ack_before_model_finishes 等行为检查，覆盖下列失败路径。
- [x] 实现前运行下面的定向命令，确认新行为因明确缺失而失败；区分环境/依赖错误与行为失败，不把环境损坏记成有效 red。
- [x] 只实现上述最小范围，使用 unittest 和临时目录/受控替身，异步测试采用 IsolatedAsyncioTestCase；不新增测试框架。
- [x] 再运行定向验收，检查至少运行一项测试且全部通过；涉及主应用/依赖/打包时追加旧检索回归。
- [x] 自查 diff、边界与密钥/运行产物排除；记录实际命令、结果及未完成事项，准备一次 review。若当前使用 Git，可作为独立提交；不为此自动 git init。

## 验收场景

- [x] JSON+PDF 同请求创建后立即可查询，生成替身被阻塞时创建仍返回。
- [x] 缺 part、坏 JSON、缺标识、超限及持久失败准确映射，旧任务保持原状。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_task_create_api.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_create_api.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_service.py' -v
if ($LASTEXITCODE -ne 0) { throw '旧检索回归失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 实施与验收记录（2026-10-23）

- 用户确认严格 multipart 校验：仅一个 businessData 文本及一个 file 文件；重复、额外 part、错误 part 类型返回 422，语法损坏返回 400。JSON 的 NaN/Infinity 按非法语法拒绝。
- `create_app(..., task_store=..., task_submitter=submit_task, notify_cancel=None)` 注入提交服务和取消通知；使用现有 T07 保存并原子入队后立即返回四字段确认，不请求模型或下载业务 URL。存储未注入返回 503，默认初始化仍待 T22。
- 创建路由沿用局部 reply 错误结构；400/422/413/415/503/500 均有行为验证，旧检索保持 detail。成功、校验失败、损坏 multipart 均关闭上传文件；检查最终 multipart 边界，防止截断请求被误收。运行时 OpenAPI 描述文本 JSON、二进制上传及错误响应。
- 固定并实际安装 `python-multipart==0.0.32`；HTTP 客户端由 T10 固定。修改范围为 src/task_api.py、src/main.py、src/requirements.txt、tests/test_task_create_api.py 及任务验收记录。
- 先运行定向命令，确认缺少创建路由返回 404、缺少提交注入导致明确断言失败；实现后补充 JSON 语法和 OpenAPI 的失败测试再修复。
- 定向命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_create_api.py' -v`，退出码 0，`Ran 9 tests in 0.667s` / `OK`。
- 旧检索命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_service.py' -v`，退出码 0，`Ran 26 tests in 6.777s` / `OK`。
- 最终全量命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`，退出码 0，`Ran 106 tests in 9.446s` / `OK`。已自查范围与资源清理；无 Git 初始化、子代理、用户 PDF、打包、部署或真实模型调用。

