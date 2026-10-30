# T22：生命周期接入、恢复与单实例

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线验收（2026-10-23）；真实模型、打包与部署不在本任务范围。
**依赖：** [T02](../t02-settings/task.md)、[T06](../t06-restart-recovery/task.md)、[T08](../t08-query-api/task.md)、[T09](../t09-create-api/task.md)、[T21](../t21-serial-worker/task.md)
**规格映射：** `FR-03`、`FR-06`、`FR-08`、`AC-01`、`AC-07`、`AC-09`，见 [spec](../../spec.md)。
**目标：** 把后台调度接入应用生命周期并避免第二实例误恢复任务。

## 一个 diff 的边界

- 修改 src/main.py：lifespan 接入
- 修改 src/task_worker.py：所有权保护/启停
- 修改 src/task_api.py：提交后取消通知
- 新建 tests/test_task_app_lifecycle.py
- 按用户确认更新 tests/test_task_query_api.py 的默认存储过渡测试，并为 tests/test_service.py 配置临时存储。

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 获得同存储单实例所有权后才执行恢复，然后启动一个 worker；先恢复再开放接收。
2. 可用独立 SQLite 所有权锁文件的持有事务实现跨进程互斥，避免锁住任务主数据库或另引锁服务。
3. 注册提交后唤醒/取消钩子；关闭应用结束本地任务并释放资源，未完成记录下次启动 FAILED；原检索 API 保持可用。

## 执行步骤

- [x] 新增 tests/test_task_app_lifecycle.py，覆盖第二实例、恢复、通知、关停及配置失败。
- [x] 实现前定向运行，8 项因缺少生命周期设置入口明确断言失败；补充复现非法超时/重试配置阻断原检索后修复。
- [x] 使用 unittest、TestClient、临时目录、Event 及受控模型替身，不新增框架。
- [x] 定向 10 项、旧检索 26 项、查询 7 项及最终全量 222 项通过。
- [x] 自查修改范围、代次隔离及资源释放，记录结果；当前无 Git，未初始化或提交。

## 验收场景

- [x] 第二实例被拒绝且不能把第一实例的活动任务标失败；所有权释放后可正常启动恢复。
- [x] TestClient 生命周期启动、关停、重启回收本地任务并关闭 HTTP 客户端；配置缺失不破坏原检索接口。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_task_app_lifecycle.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_app_lifecycle.py' -v
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

- 用户确认：模型配置缺失/无效时创建返回 503，不接收新任务；原检索和历史查询可用。允许更新 T08 的临时默认存储行为测试及旧检索临时存储 fixture。
- `create_app(..., task_settings=None)` 在 lifespan 内读取设置和初始化默认 TaskStore，导入及构造应用不创建数据库。支持继续注入 task_store。先取得所有权，再恢复未完成记录为 FAILED，最后启动唯一消费者并开放请求。
- `StorageOwnership(storage_root)` 在独立 `owner.sqlite3` 持有 `BEGIN EXCLUSIVE` 事务，锁保留至 worker 和 HTTP 客户端退出；不占用 tasks.sqlite3。第二实例在恢复前被拒绝，真实子进程检查跨进程互斥及释放。
- 替换事务提交后按代次取消；包括首次提交在内的每次成功接收均唤醒 worker。关停先关闭接收，再取消并等待流水线退出，关闭 HTTP 客户端、释放锁；未完成状态留待下次启动恢复。
- 缺失模型配置及非法模型超时/重试参数均通过安全 503 路径拒绝创建，不写上传文件、不替换历史；查询不依赖模型配置。无效模型预算只禁用创建，使用存储设置继续提供只读接口。
- TestClient 覆盖提交唤醒、提交后取消钩子、第二实例不误恢复、PENDING/PROCESSING 恢复、启动失败释放所有权、关停与同应用重启、HTTP 客户端关闭。复用 T20 合成文本/扫描 PDF、两产品清单及本地假模型，验证真实流水线从上传到 DONE，再重启查询相同已保存结果。
- 定向：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_task_app_lifecycle.py -v`，`Ran 10 tests in 5.614s` / `OK`。旧检索同命令 `-p test_service.py`，`Ran 26 tests in 7.105s` / `OK`；查询 `-p test_task_query_api.py`，7 项通过。退出码均为 0。
- 最终全量：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`，`Ran 222 tests in 48.230s` / `OK`，退出码 0。7 个涉及的 Python 文件语法检查通过；无新增数据库/字节码产物。
- 未运行真实模型、打包、部署或 T23–T28 后续验收；未启用子代理或创建工作树。

