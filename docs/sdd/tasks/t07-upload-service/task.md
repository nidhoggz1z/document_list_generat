# T07：PDF 接收与原子任务创建服务

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线验收（2026-10-23），范围仅限本任务。
**依赖：** [T01](../t01-contracts/task.md)、[T02](../t02-settings/task.md)、[T04](../t04-queue-transactions/task.md)
**规格映射：** `FR-01`、`FR-02`、`FR-03`、`FR-04`、`AC-01`、`AC-02`、`AC-03`，见 [spec](../../spec.md)。
**目标：** 在文件保存成功后提交任务，任何接收失败均不影响旧任务。

## 一个 diff 的边界

- 新建 src/task_files.py
- 新建 tests/test_task_submission.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 提供 submit_task 供后续 API 调用；按实际字节计数拒绝空文件和超过 10 MiB，检查实际 PDF 类型而非只信扩展名。
2. 生成独立代次目录并保存原字节及 SHA-256；基础检查不承诺 PDF 全文可读。
3. 先准备文件后调用 T04 事务，失败清理仅本次临时文件；提交成功后才通知取消旧代次，通知钩子可先注入测试函数。

## 执行步骤

- [x] 先新增 tests/test_task_submission.py 行为检查，确认缺失 T07 实现导致失败。
- [x] 使用 unittest、临时 SQLite/目录及受控替身，异步采用 IsolatedAsyncioTestCase。
- [x] 实现最小范围，运行定向验收与全量回归。
- [x] 自查文件边界与运行产物排除，记录实际结果；当前目录没有 Git，不初始化或提交。

## 验收场景

- [x] 文件写失败、数据库提交失败、空/非 PDF/超限均不触发旧任务终止或覆盖。
- [x] 10 MiB 边界、伪造 MIME/文件名、路径穿越名字被正确处理；成功文件与任务指针一致。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_task_submission.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_submission.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 实施与验收记录（2026-10-23）

- 新增 `src/task_files.py`、`tests/test_task_submission.py`，覆盖 FR-01～FR-04、AC-01～AC-03 的接收服务范围；HTTP 上传路由仍属于 T09。
- 用户确认：要求从文件首字节开始有 `%PDF-1.x` 或 `%PDF-2.0` 版本头，随后须有空白分隔；忽略文件名、MIME、声明大小。基础检查不承诺全文可读。
- `await submit_task(store, business_data, upload, notify_cancel=...)` 接收支持异步 `read(size)` 的上传对象；调用方负责关闭上传流。按块累计实际字节，最大 10 MiB；独立 UUID 代次路径保存原文并 flush/fsync，随后调用 accept_task 原子提交。失败只清理本次文件，不改旧记录和文件。
- 提交成功后才通知被替换代次列表；支持同步/异步钩子。按用户确认，钩子异常仅记安全日志，仍返回创建成功，已提交记录和文件保留。
- 定向命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_submission.py' -v`。先确认缺失模块导致 8 项明确断言失败；完成后扩充边界检查，最终退出码 0，`Ran 11 tests in 0.778s` / `OK`。
- 覆盖空/伪 PDF、版本头、10 MiB 与多 1 字节、路径穿越名、未知业务字段、原字节/哈希/路径一致性、分块读取失败、打开/部分写入/fsync 失败、真实 SQLite 提交失败、旧 DONE 及文件保护、成功覆盖隔离、通知顺序及异常。
- 全量命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`，退出码 0，`Ran 89 tests in 8.376s` / `OK`。无新增依赖、运行数据库、用户 PDF、生成包或真实模型调用。

