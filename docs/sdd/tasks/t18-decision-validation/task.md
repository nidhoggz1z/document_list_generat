# T18：规则覆盖与展开结果校验

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线测试（2026-10-23）；对象范围及内容的业务准确性待 T28。
**依赖：** [T15](../t15-rule-inventory/task.md)、[T16](../t16-fact-resolution/task.md)、[T17](../t17-generation/task.md)
**规格映射：** `FR-11`、`FR-13`、`AC-14`、`AC-17`、`AC-18`，见 [spec](../../spec.md)。
**目标：** 拒绝漏规则、伪造规则与错误主体展开，防止部分输出被当成功。

## 一个 diff 的边界

- 修改 src/material_generator.py：独立校验函数
- 新建 tests/test_material_decisions.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 对比决定集合与原 ruleKey 集合，拒绝缺失、多出、重复及跨产品错配。
2. 校验 KEEP/EXCLUDE/CHECK 处理决定和实例支持来源，未知条件不得无依据排除；只有材料可以进入对外结果。
3. 按关联对象核对展开，拒绝编造主体或把 A 资料给 B；结构可核验不等于语义准确率，保留业务验收限制。

## 执行步骤

- [x] 先新增/补充 tests/test_material_decisions.py 中的 test_missing_rule_or_subject_is_rejected 等行为检查，覆盖下列失败路径。
- [x] 实现前运行下面的定向命令，确认新行为因明确缺失而失败；区分环境/依赖错误与行为失败，不把环境损坏记成有效 red。
- [x] 只实现上述最小范围，使用 unittest 和受控替身，异步测试采用 IsolatedAsyncioTestCase；不新增测试框架。
- [x] 再运行定向验收，检查至少运行一项测试且全部通过；全量回归包含旧检索测试。
- [x] 在当前任务自查文件、边界与密钥/运行产物排除；记录实际命令、结果及未完成事项。当前目录非 Git 仓库，无 Git 初始化或提交。

## 验收场景

- [x] 遗漏一规则、重复一决定、伪造主体、核查项伪装材料以及漏掉一个声明范围内应展开对象均失败。
- [x] 完整处理后合法排除导致条数变少可通过；保留内部决定，不向 API 增加 evidence。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_material_decisions.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_material_decisions.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 实施与验证记录（2026-10-23）

- 独立接口：`validate_material_decisions(decisions, rules, resolved_context, cancel_check=lambda: False)`。返回按原规则及对象顺序排列的副本，保留内部决定、支持来源及对象排除记录，不修改输入。
- 决定字段：productName、ruleKey、action、objectKinds、scopeObjectKey、reason、evidence、instances、excludedObjects。对象范围由模型声明类型及可选父对象键；核对 T16 同产品、有效对象及父链，不另造业务分类规则。无可知对象时保留一条无姓名的通用材料。
- 原核查项只能 CHECK 且无材料实例；原材料只能 KEEP/EXCLUDE。KEEP 必须逐对象生成材料实例或给出有明确事实支持的对象排除，不能重复或遗漏。整规则 EXCLUDE 对范围内每个对象分别核对事实支持；未知事实或单纯对象存在不能支持排除。
- evidence 支持 RULE（本 ruleKey 和原单元格连续引文）、OBJECT（objectKey 和 source）、FACT（objectKey、事实名称和 source）引用；核对原始来源及产品。实例 subject 必须与 objectKey 的姓名相同，实例引用只能来自本对象及其父链；不接受 A 的独立来源支持 B。
- 每个材料实例必需 objectKey、subject、materialName、requirement、reason、evidence；可选 sourceSystem、policyBasis、publishDate、publishDepartment。未知展示字段用空字符串，不接受 null、额外 itemType 或对外编号。T19 另负责 API 投影和默认值。
- red：初始 14 项因缺少校验实现明确失败；增补“整规则排除可分别引用 A/B 明确事实”的用例复现误拒绝后修正。最终定向 `.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_material_decisions.py' -v`，`Ran 17 tests in 0.012s` / `OK`，退出码 0。
- 全量 `.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q`，`Ran 183 tests in 37.526s` / `OK`，退出码 0。AST 和 pip check 均通过。
- 按用户确认，程序不独立解释自然语言来判断 objectKinds/scopeObjectKey 是否在业务上正确，也不能凭引文存在证明排除理由或材料文本语义准确；这些检查留给 T28 真实业务样例验收。当前实现只验证结构、引用与声明范围完整性，不宣称真实模型效果已通过。
- 没有修改 API、增加下游 evidence 或接入流水线；当前目录完成自查，无子代理、真实模型、打包或部署。

