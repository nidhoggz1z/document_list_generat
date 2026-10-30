# T05：任务领取与迟到结果隔离

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线验收（2026-10-23）；范围仅限本任务，调度与生命周期接入待 T21/T22。
**依赖：** [T04](../t04-queue-transactions/task.md)
**规格映射：** `FR-06`、`FR-07`、`AC-04`、`AC-07`，见 [spec](../../spec.md)。
**目标：** 让状态和结果只能由当前 PROCESSING 代次原子发布。

## 一个 diff 的边界

- 修改 src/task_store.py
- 新建 tests/test_task_store_transitions.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 原子领取最早 PENDING 为 PROCESSING；提供按代次保存阶段产物与 finish_done/finish_failed。
2. 全部写入检查当前 generationId 和 PROCESSING；DONE 与完整结果同事务提交。
3. 终态禁止被迟到成功或失败改写；不要仅依赖内存取消标志。

## 执行步骤

- [x] 新增行为测试，覆盖下列失败路径。
- [x] 实现前定向运行，确认因缺少 claim_next 明确失败（10 项）。
- [x] 最小实现，使用 unittest、临时 SQLite 和受控替身，未新增测试框架。
- [x] 定向验收 10 项及包含旧检索的全量回归 71 项均通过。
- [x] 在当前任务自查修改范围、事务及代次隔离，记录命令与结果；当前目录无 Git，未初始化或提交。

## 验收场景

- [x] 旧代次在同键覆盖、同业务终止后迟到的成功和异常均不能写回。
- [x] 模拟完成事务失败，不能出现 DONE 无结果；完成先提交与替换先提交分别符合 spec。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_task_store_transitions.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_store_transitions.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 实施与验收记录（2026-10-23）

- 修改 `src/task_store.py`，新增 `tests/test_task_store_transitions.py`。对应 FR-06/FR-07、AC-04/AC-07 的存储层范围。
- `claim_next()` 在 BEGIN IMMEDIATE 事务中领取最早 PENDING，提交后返回 PROCESSING 记录，空队列返回 None；单消费者循环由 T21 实现。
- `save_artifact(generation_id, name, value)` 在同一事务内合并指定名称的 JSON 阶段内容/指针；不会写阶段文件。`finish_done(generation_id, result)` 复用 TaskResult 验证完整 data 后原子提交状态与结果；语义完整性仍由后续流水线检查。`finish_failed(generation_id, reason)` 要求非空原因。三项写回均校验当前 generationId 与 PROCESSING，成功返回 True，过期/非执行态返回 False，存储错误向上传播。
- 定向命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_store_transitions.py' -v`。先确认 10 项因缺少实现失败，再实现；最终退出码 0，`Ran 10 tests in 0.293s` / `OK`。
- 覆盖 FIFO、空队列、并发不重复领取、阶段产物持久保存、PENDING/未知代次拒绝、同键新代次已 PROCESSING 时旧代次拒绝、全部终态不可改写、完整结果持久化及无效结果/原因拒绝。Event 控制两种完成/替换提交顺序；Connection 替身注入领取/产物/完成的提交失败，确认原记录完整回滚。
- T06 完成后全量命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`，退出码 0，`Ran 71 tests in 8.521s` / `OK`（包含原有 57 项及新增 14 项）。未使用子代理、外部模型或部署；未新增依赖或运行数据库。

