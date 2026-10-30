# T06：重启失败恢复规则

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线验收（2026-10-23）；生命周期调用与单实例保护待 T22。
**依赖：** [T05](../t05-state-cas/task.md)
**规格映射：** `FR-08`、`AC-09`，见 [spec](../../spec.md)。
**目标：** 启动恢复将未完成记录判为 FAILED 而不重跑。

## 一个 diff 的边界

- 修改 src/task_store.py
- 新建 tests/test_task_store_recovery.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 增加 recover_interrupted，只把 PENDING/PROCESSING 改 FAILED 并写服务中断原因。
2. 不修改 DONE/FAILED/TERMINATED 及历史内容；不返回待重排队列表。
3. 在持久测试中覆盖进程遗留记录；生命周期调用与单实例保护留到 T22。

## 执行步骤

- [x] 新增行为测试，覆盖下列失败路径。
- [x] 实现前定向运行，确认因缺少 recover_interrupted 明确失败（4 项）。
- [x] 最小实现，使用 unittest、临时 SQLite 和受控替身，未新增测试框架。
- [x] 定向验收 4 项及包含旧检索的全量回归 71 项均通过。
- [x] 在当前任务自查事务及终态保护，记录命令与结果；当前目录无 Git，未初始化或提交。

## 验收场景

- [x] 重开临时数据库恢复后无未完成记录，原终态及 DONE JSON 不变。
- [x] 恢复幂等，恢复前旧 generationId 的后续写回被 T05 条件拒绝。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_task_store_recovery.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_store_recovery.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 实施与验收记录（2026-10-23）

- 修改 `src/task_store.py`，新增 `tests/test_task_store_recovery.py`。对应 FR-08、AC-09 的存储层范围。
- `recover_interrupted()` 在单一写事务中，仅将 PENDING/PROCESSING 置 FAILED，原因“服务中断，任务未完成”，更新时间，提交后返回修改条数。不会返回重排队列表或自动执行。构造 TaskStore 不会自动恢复；T22 取得独占服务所有权后调用。
- 定向命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_store_recovery.py' -v`。先确认 4 项因缺少实现失败，再实现；最终退出码 0，`Ran 4 tests in 0.241s` / `OK`。
- 重开临时持久数据库验证全部五种状态：只改变未完成记录的 status/reason/updatedAt；其余输入、序号、产物保持，终态记录及 DONE 原始 JSON 字节保持。恢复后无可领取任务，重复恢复返回 0 且整库不变；旧代次产物/成功/异常写回被拒绝，新提交可正常领取。另覆盖空库及恢复提交失败完整回滚。
- 全量命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`，退出码 0，`Ran 71 tests in 8.521s` / `OK`。未接入生命周期、单实例保护、真实模型或部署，未新增依赖或运行数据库。

