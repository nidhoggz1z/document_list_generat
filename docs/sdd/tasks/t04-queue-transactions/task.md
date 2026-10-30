# T04：原子替换与 FIFO 顺序

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线验收（2026-10-23）；仅代表本任务范围。
**依赖：** [T03](../t03-store-base/task.md)
**规格映射：** `FR-04`、`FR-05`、`AC-03`、`AC-05`、`AC-06`、`AC-08`，见 [spec](../../spec.md)。
**目标：** 以一个事务完成同业务旧任务终止、同键覆盖和新任务排队。

## 一个 diff 的边界

- 修改 src/task_store.py
- 新建 tests/test_task_store_queue.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 实现 accept_task，返回新 generationId 及应取消的旧代次；同业务旧 PENDING/PROCESSING 全部终止，新顺序号追加队尾。
2. 其他 taskId 终态保留；同键新建覆盖，内容改变也允许。
3. 提交失败完整回滚；并发接收以事务顺序确定最后有效代次，不在此任务执行模型或 HTTP。

## 执行步骤

- [x] 先新增/补充 tests/test_task_store_queue.py 中的 test_replacement_moves_to_tail_atomically 等行为检查，覆盖下列失败路径。
- [x] 实现前运行下面的定向命令，确认新行为因明确缺失而失败；区分环境/依赖错误与行为失败，不把环境损坏记成有效 red。
- [x] 只实现上述最小范围，使用 unittest 和临时目录/受控替身，异步测试采用 IsolatedAsyncioTestCase；不新增测试框架。
- [x] 再运行定向验收，检查至少运行一项测试且全部通过；涉及主应用/依赖/打包时追加旧检索回归。
- [x] 自查 diff、边界与密钥/运行产物排除；记录实际命令、结果及未完成事项，准备一次 review。若当前使用 Git，可作为独立提交；不为此自动 git init。

## 验收场景

- [x] A1 执行+B1/C1 排队+A2 到达得到 B1→C1→A2；B2 替换 B1 得到 C1→B2。
- [x] 并发同业务接收仅最后提交的未完成任务保留；注入写入失败旧记录及序号队列均不被部分改变。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_task_store_queue.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_store_queue.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 实施与验收记录（2026-10-23）

- 修改 `src/task_store.py`、新增 `tests/test_task_store_queue.py`。`accept_task(business_data, *, generation_id=None, source_document_hash=None)` 在 `BEGIN IMMEDIATE` 事务内终止同业务未完成记录、覆盖同键、递增持久序号并插入 PENDING；提交后返回 `(generationId, cancelledGenerationIds)`。取消通知由后续调用方在提交成功后处理。
- 先运行定向测试，9 项因缺少 accept_task 而失败；实现后修正测试辅助连接未关闭造成的 Windows 临时目录清理错误，9 项通过。最终增加预分配代次检查后，定向命令 `.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_store_queue.py' -v`：退出码 0，`Ran 10 tests`、`OK`。
- 覆盖 B1→C1→A2、A1 继续执行且队列 C1→B2、全部同业务未完成任务终止、其他 taskId 终态历史完整保留、五种状态下同键同/异输入覆盖和创建者更新。
- 使用 Barrier 同步 5 个线程，分别验证独立存储实例下同业务接收与共享实例下同键接收；不依赖 sleep。SQLite 触发器注入写失败，Connection 替身注入 commit 失败，核对整库任务记录及序号快照不变、后续序号连续。
- 最终全量回归：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`，退出码 0，`Ran 57 tests in 7.797s`、`OK`。已在当前任务自查事务边界、文件隔离与修改范围，未使用子代理。
- 未实现后续 worker、领取/迟到输出条件写入、恢复、PDF 持久化或 HTTP；没有真实模型调用或部署。

