# T01：业务输入与任务响应模型

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线验收（2026-10-23）；不代表后续任务及真实模型联调完成。
**依赖：** 无功能任务依赖；先满足总索引的本地环境前提。
**规格映射：** `FR-01`、`FR-07`、`FR-14`、`FR-15`、`AC-02`、`AC-19`，见 [spec](../spec.md)。
**目标：** 把已定稿的输入、五种任务状态和互斥响应变成可复用的数据契约。

## 一个 diff 的边界

- 新建 src/task_models.py
- 新建 tests/test_task_models.py
- 用户本轮同意同步修正 docs/sdd/personalized-checklist.openapi.json，允许空 contractNum。

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 定义业务 JSON、身份、创建响应、状态响应、FAILED、DONE、reply 模型；保留所有业务字段及 SubcontractCollateralRels 大小写。
2. 标识去首尾空白且禁止空/null；已知可选字段 null 忽略；未知字段按用户本轮决定原样保留，包括数字、布尔值、null 和嵌套结构；已知非 null 标量沿用字符串。
3. DONE 使用 data.products 元数据和 data.items 平铺；常量 MATERIAL/APPLICABLE；日期是普通字符串。

## 执行步骤

- [x] 先新增/补充 tests/test_task_models.py 中的 test_optional_null_and_status_variants 等行为检查，覆盖下列失败路径。
- [x] 实现前运行下面的定向命令，确认新行为因明确缺失而失败；区分环境/依赖错误与行为失败，不把环境损坏记成有效 red。
- [x] 只实现上述最小范围，使用 unittest 和临时目录/受控替身，异步测试采用 IsolatedAsyncioTestCase；不新增测试框架。
- [x] 再运行定向验收，检查至少运行一项测试且全部通过；涉及主应用/依赖/打包时追加旧检索回归。
- [x] 自查 diff、边界与密钥/运行产物排除；记录实际命令、结果及未完成事项，准备一次 review。若当前使用 Git，可作为独立提交；不为此自动 git init。

## 验收场景

- [x] 三标识缺失/null/空白和金额数字被拒绝；可选 null/空合同号及未知业务字段可接受。
- [x] 五种状态的字段集合与 OpenAPI 相符；旧状态、输出 null、额外 data.status 被拒绝。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_task_models.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_models.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 实施与验收记录（2026-10-23）

- 首次定向运行 8 项因实现缺失而断言失败（退出码 1），随后实现并修正 Pydantic 继承配置覆盖问题。
- 入口：BusinessData、TaskIdentity、CreateResponse；TaskQueryResponse 可用 Pydantic TypeAdapter 验证。输出字段必需且禁止额外字段；展示缺省值生成留给 T19。
- 覆盖标识、严格类型、金额、可选 null、空合同号、未知字段原样保留、嵌套大小写、OpenAPI 查询示例及状态互斥、DONE 完整性。
- 最终定向命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_models.py' -v`，9 项通过、退出码 0。
- 最终全量回归：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`，42 项通过、退出码 0（包含原检索 26 项）。
- 自查：未改动主应用、依赖、清单或打包产物；未保存真实 Key、业务 PDF 或运行数据库。当前目录非 Git 仓库，未初始化或提交 Git；按用户要求在当前任务自查，未启用子代理。
