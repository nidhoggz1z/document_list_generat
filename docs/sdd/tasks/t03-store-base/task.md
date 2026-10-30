# T03：SQLite 任务记录与身份

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线验收（2026-10-23）；仅代表本任务范围。
**依赖：** [T01](../t01-contracts/task.md)、[T02](../t02-settings/task.md)
**规格映射：** `FR-04`、`FR-08`、`AC-08`，见 [spec](../../spec.md)。
**目标：** 实现本地记录和同键覆盖所需的最小持久结构。

## 一个 diff 的边界

- 新建 src/task_store.py
- 新建 tests/test_task_store_base.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 用标准库 sqlite3，唯一键 (bsnEvtId,taskId)，内部 generationId、持久顺序号、状态、输入/文件指针、结果及原因。
2. 提供初始化、读取单条和基础插入；userNo 保存但不参与唯一键。
3. 每代文件路径按 generationId 隔离；不引入 ORM、数据库服务或对外执行批次接口。

## 执行步骤

- [x] 先新增/补充 tests/test_task_store_base.py 中的 test_identity_survives_reopen 等行为检查，覆盖下列失败路径。
- [x] 实现前运行下面的定向命令，确认新行为因明确缺失而失败；区分环境/依赖错误与行为失败，不把环境损坏记成有效 red。
- [x] 只实现上述最小范围，使用 unittest 和临时目录/受控替身，异步测试采用 IsolatedAsyncioTestCase；不新增测试框架。
- [x] 再运行定向验收，检查至少运行一项测试且全部通过；涉及主应用/依赖/打包时追加旧检索回归。
- [x] 自查 diff、边界与密钥/运行产物排除；记录实际命令、结果及未完成事项，准备一次 review。若当前使用 Git，可作为独立提交；不为此自动 git init。

## 验收场景

- [x] 关闭并重开连接后记录仍在；同业务不同 taskId 可同时保存历史。
- [x] 同 taskId 不同业务互不覆盖；查询的不同 userNo 不改变唯一键和创建时保存的 userNo。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_task_store_base.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_store_base.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 实施与验收记录（2026-10-23）

- 新增 `src/task_store.py`、`tests/test_task_store_base.py`；标准库 SQLite，复合主键、UUID 代次、持久序号、业务输入、PDF 指针、结果/原因及阶段产物指针保存。每次操作显式关闭连接。
- 先运行定向测试，4 项因 T03 模块缺失而失败；实现后通过。最终补充持久字段检查，定向命令 `.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_store_base.py' -v`：退出码 0，`Ran 5 tests`、`OK`。
- 覆盖重开读取、不同业务/任务互不覆盖、userNo 不参与唯一键、路径隔离、持久字段及无效输入。`get_task(bsn_evt_id, task_id)` 不接收查询者 userNo，返回创建时保存的值。
- T03 与 T04 最终全量回归：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`，退出码 0，`Ran 57 tests in 7.797s`、`OK`，包含原检索 26 项。
- 当前目录不是 Git 仓库，未初始化 Git 或提交。测试数据库均在临时目录；项目内未生成数据库、上传 PDF 或新包。只完成本地存储，状态领取/完成写回、恢复、HTTP 与文件接收按后续任务实施。

