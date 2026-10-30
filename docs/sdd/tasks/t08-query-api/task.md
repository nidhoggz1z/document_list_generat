# T08：只读任务查询接口

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线验收（2026-10-23），范围仅限本任务。
**依赖：** [T01](../t01-contracts/task.md)、[T03](../t03-store-base/task.md)
**规格映射：** `FR-14`、`FR-15`、`AC-19`，见 [spec](../../spec.md)。
**目标：** 提供 /api/v1/tasks/query，严格返回已有状态/结果。

## 一个 diff 的边界

- 新建 src/task_api.py
- 修改 src/main.py：注册查询路由
- 新建 tests/test_task_query_api.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 以注入 store 的路由注册函数接入 create_app，避免 import 时创建新全局工作线程。
2. 身份以 bsnEvtId+taskId 定位；不存在返回 200 reply=任务未创建；已有记录回显创建时 userNo。
3. PENDING/PROCESSING/TERMINATED 四字段；FAILED 加 reason；DONE 返回保存的完整 JSON；新接口参数错误为 reply，不改旧 /retrieve 错误格式。

## 执行步骤

- [x] 先新增 tests/test_task_query_api.py 行为检查，确认缺失存储注入导致失败。
- [x] 使用 unittest、TestClient、临时 SQLite/文档及受控替身，不新增测试框架。
- [x] 实现最小范围，完成定向验收、旧检索及全量回归。
- [x] 自查文件边界与运行产物排除，记录实际结果；当前目录没有 Git，不初始化或提交。

## 验收场景

- [x] 全部五种状态、未创建、空/null 标识及无效 JSON 分支符合 HTTP/字段约定。
- [x] 将模型配置入口、生成写回和规则读取替身设置为一调用就报错，历史查询仍能成功，证明只读。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_task_query_api.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_query_api.py' -v
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

- 新增 `src/task_api.py`、`tests/test_task_query_api.py`，仅修改 `src/main.py` 的导入、create_app 注入参数及路由注册，覆盖 FR-14、FR-15、AC-19 的查询范围。
- `create_app(data_dir=None, *, task_store=None)` 支持注入存储；`register_task_routes(service, store)` 注册 POST `/api/v1/tasks/query`。按用户确认，未注入存储返回 HTTP 503 `reply`，默认存储初始化及工作单元仍由 T22 接入。没有新增全局数据库或线程。
- 复用 TaskIdentity 和 TaskQueryResponse；以 bsnEvtId/taskId 定位，回显保存的 userNo。未创建返回 200 reply；各状态字段互斥，DONE 返回已保存完整 data。查询只读一次存储快照，不刷新规则或生成结果。
- 新路由局部处理参数/解析异常：非法 JSON 及无效编码 400，缺失/null/空白/错误类型/额外字段 422，存储不可用 503，未预期异常 500，统一 reply 且不泄露内部异常。旧 retrieve 保留 detail 格式。
- 定向命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_query_api.py' -v`。先确认缺少 create_app 注入导致 7 项断言失败；另复现无效 UTF-8 误报 500 后修复。定向 7 项通过，最终重开存储的历史查询检查亦在全量运行中通过。
- 旧检索命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_service.py' -v`，退出码 0，`Ran 26 tests in 5.947s` / `OK`。
- 全量命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`，退出码 0，`Ran 89 tests in 8.376s` / `OK`。未实现 T09/T22 或真实模型联调。

