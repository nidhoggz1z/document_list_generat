# T19：平铺响应、默认值与编号

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线测试（2026-10-23）；模型展示字段内容及来源的业务准确性待 T28。
**依赖：** [T01](../t01-contracts/task.md)、[T18](../t18-decision-validation/task.md)
**规格映射：** `FR-11`、`FR-12`、`FR-15`、`AC-15`、`AC-16`，见 [spec](../../spec.md)。
**目标：** 将完整内部决定投影为最终平铺 DONE data。

## 一个 diff 的边界

- 新建 src/material_result.py
- 新建 tests/test_material_result.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 按产品首次出现→原规则→对象顺序输出；itemId 全局唯一，sourceRuleId 可重复，每条加 productName 和 [产品名]材料前缀。
2. 不合并跨产品/主体同名项；具体要求进 materialName，保留 requirement/reason，固定 MATERIAL/APPLICABLE。
3. 程序补三个指定默认值，其他缺失展示内容为空；建立 products 元数据、PDF 哈希、完成时间和 total，零材料报错。

## 执行步骤

- [x] 新增 tests/test_material_result.py 的行为测试。
- [x] 实现前定向运行，6 项因缺少 T19 实现明确失败。
- [x] 仅实现本任务范围，沿用 unittest，无新增依赖。
- [x] 定向验收 7 项通过，全量回归记录见任务索引。
- [x] 在当前目录自查实现、契约与产物边界；没有 Git 初始化或提交。

## 验收场景

- [x] 两产品共有营业执照保留两项，A/B 身份材料分开；编号、产品引用、顺序及 total 一致。
- [x] 显式元数据压过默认值，缺省为上银办/2026年/上海银行，输出无 null/旧状态或额外包装。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_material_result.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_material_result.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 实施与验证记录（2026-10-23）

- 接口：`project_material_result(approval, snapshots, rules, decisions, source_document_hash, cancel_check=lambda: False)`，输入为 T18 已校验并按对象排序的决定，返回经 TaskResult 校验的完整 data 字典；不修改输入，不发布状态。
- 按批复产品首次出现顺序、原规则顺序、T18 对象顺序输出，保留重复 sourceRuleId，不合并材料；编号从 R0001 连续生成，74 条材料不截断。产品即使没有保留材料仍列在 products；整次零材料失败。
- 用户确认：统一加产品前缀；主体和具体要求未出现在材料名称中才补齐，避免重复。缺失材料展示名保持空字符串，不推断姓名或名称。源文档哈希由 T20 核对原 PDF，完成时间采用带时区的 UTC RFC 3339 字符串。
- 用户确认：保留模型非空展示值，空白统一补默认值；policyBasis 缺失为空。沿用 T18 的字符串检查，不增加原文内容比对，语义和来源准确性留给 T28。
- 定向命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_material_result.py -v`，最终 `Ran 7 tests in 0.005s` / `OK`，退出码 0。
- 无真实模型调用、打包、部署、子代理或其他任务实现。

