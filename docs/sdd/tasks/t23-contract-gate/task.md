# T23：运行接口与 OpenAPI 契约核对

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发、自查和离线验收（2026-10-23）；真实模型及部署验收不在本任务范围。
**依赖：** [T01](../t01-contracts/task.md)、[T09](../t09-create-api/task.md)、[T19](../t19-result-projection/task.md)、[T22](../t22-app-lifecycle/task.md)
**规格映射：** `FR-01`、`FR-14`、`FR-15`、`AC-19`，见 [spec](../../spec.md)。
**目标：** 用真实路由响应与既定契约做可执行比对，不只校验手写样例。

## 一个 diff 的边界

- 新建 tests/test_task_api_contract.py
- 按实际生成修正 docs/sdd/personalized-checklist.openapi.json、接口文档及示例
- 用户于本会话确认：允许最小修改 src/task_api.py 的查询 OpenAPI 错误声明，使其与真实 reply 响应一致；不修改业务行为。

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 核对创建 multipart 字段、业务 JSON 空值、查询 JSON、HTTP 分支与全部互斥状态字段。
2. 比较运行 OpenAPI 和已定文档的字段、枚举及类型语义；不能为让测试通过而改业务决定。
3. 允许修正自动 schema 的必要表达差异；不得新增旧 generate 路径或把业务字段重新包一层。

## 执行步骤

- [x] 新增真实 HTTP 路由与静态/运行契约比对。
- [x] 修改声明前运行定向测试：2 项因缺失查询错误 schema 明确失败，3 项通过。
- [x] 仅补齐授权的查询错误声明；使用 unittest、临时存储及受控失败注入。
- [x] 定向验收、旧检索回归和全量回归通过，结果见下。
- [x] 完成文件和边界自查，未初始化 Git 或创建运行产物。

## 验收场景

- [x] DONE/FAILED/另外三状态/未创建/接收错误均有真实路由样本通过。
- [x] 无旧状态、URL 下载输入、data 内第二个 status；业务上下文 replyImageUrl 仍允许保留，原 /retrieve 纯文本保持。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_task_api_contract.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_api_contract.py' -v
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

- 新增 5 项 IsolatedAsyncioTestCase，真实 ASGI 路由连接临时 SQLite，核对创建、五种互斥状态、未创建、创建 400/422/413/415/503/500 与查询 400/422/503/500。查询使用不同 userNo，确认回传已保存值；校验可选 null 忽略、空合同号及未知扩展字段保留。
- 修改前 5 项中 2 项失败：查询运行 OpenAPI 缺少 400/503/500，422 被自动声明为 HTTPValidationError。按用户授权，仅在 src/task_api.py 增补 ReplyResponse 声明；真实错误行为不变。
- 核对 multipart、模型字段/必填项/类型/nullable/枚举、运行及手写 schema、互斥字段、四个 JSON 样例和 OpenAPI 内置示例。测试内校验器只覆盖本契约用到的类型及结构，不是通用 OpenAPI 验证器。手写样例已符合契约，无需修改内容；更新接口文档及机器契约实施状态和严格 multipart 说明。
- 定向：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_task_api_contract.py -v`，`Ran 5 tests in 0.471s` / `OK`。
- 旧检索：同命令 `-p test_service.py`，`Ran 26 tests in 8.613s` / `OK`。
- 最终全量：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q`，`Ran 236 tests in 57.250s` / `OK`。以上退出码均为 0。
- 本轮四个 Python 文件 AST、OpenAPI JSON 解析及 pip check 通过。现有最长清单测试输出一次 asyncio 慢任务提示，没有失败。无新依赖、真实模型调用、Git 初始化、子代理、工作树、打包或部署。

