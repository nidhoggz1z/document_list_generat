# T21：单工作单元调度与抢占取消

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线验收（2026-10-23）；范围仅限 T21，生命周期接入见 T22。
**依赖：** [T05](../t05-state-cas/task.md)、[T20](../t20-pipeline/task.md)
**规格映射：** `FR-05`、`FR-06`、`FR-07`、`AC-04`、`AC-05`、`AC-06`、`AC-07`，见 [spec](../../spec.md)。
**目标：** 消费持久 FIFO 队列并连接取消、失败和 DONE 条件提交。

## 一个 diff 的边界

- 新建 src/task_worker.py
- 新建 tests/test_task_worker.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 用一个 asyncio 工作循环原子领取队头，等待一个流水线，完成后再领下一项；无额外内存队列作为真相来源。
2. 替换通知按 generationId 取消本地 asyncio.Task；只在替换事务提交后通知，不能取消别业务任务。
3. 成功/失败都通过 T05 代次检查发布；取消不标 FAILED；失败后继续下一任务，旧任务不复活。

## 执行步骤

- [x] 新增 tests/test_task_worker.py，覆盖 FIFO、抢占、失败及迟到结果。
- [x] 实现前运行定向命令，5 项因缺少 TaskWorker 明确断言失败。
- [x] 使用 unittest、IsolatedAsyncioTestCase、临时 SQLite 和 Event 控制执行顺序；不新增框架。
- [x] 最终定向验收 6 项通过；与 T22 合并全量回归 222 项通过。
- [x] 自查范围及资源释放，记录命令与结果；当前目录无 Git，未初始化或提交。

## 验收场景

- [x] 用 Event 控制 A1/B1/C1/A2、B2、连续 A2/A3 替换顺序，无长 sleep 或真实模型。
- [x] 有效本地任务数峰值为 1；迟到返回不能改状态；一次失败不让整个消费者停掉。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_task_worker.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_worker.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 实施与验收记录（2026-10-23）

- 接口：`TaskWorker(store, pipeline)`，在同一事件循环中调用 `start()`、提交后 `notify(cancelled_generation_ids)`，关停 `await stop()`。启动幂等；通知仅唤醒并按代次取消，不保存另一份内存任务队列。
- 消费者原子领取持久队头，等待当前流水线完全退出后再领下一项。成功/失败使用 TaskStore 的 generationId + PROCESSING 条件提交；取消不写 FAILED，异常原因仅保留安全阶段说明或通用文本。存储暂不可用时记录安全日志并等待通知/重试。
- 测试覆盖同业务抢占后 B1→C1→A2、等待项 B1 被 B2 替换、连续替换跳过 A2、同键旧代次迟到成功/异常、处理失败后继续、空闲唤醒及关停不领取下一项。
- 定向：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_task_worker.py -v`，`Ran 6 tests in 0.283s` / `OK`，退出码 0。
- 全量：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`，`Ran 222 tests in 48.230s` / `OK`，退出码 0。未使用子代理、真实模型、用户 PDF 或生成包。

