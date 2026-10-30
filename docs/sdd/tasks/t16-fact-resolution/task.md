# T16：批复优先事实与对象范围

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线测试（2026-10-23）；真实模型业务验收待 T28。
**依赖：** [T13](../t13-approval-extraction/task.md)
**规格映射：** `FR-10`、`AC-12`、`AC-13`，见 [spec](../../spec.md)。
**目标：** 把批复事实与 JSON 补充合并为可供生成和对象校验的上下文。

## 一个 diff 的边界

- 新建 src/material_facts.py
- 新建 tests/test_material_facts.py
- 用户在开发澄清中授权补充 src/approval_extractor.py 的关键事实名称/顺序提示及 tests/test_approval_extractor.py 的对应测试；不新增模型调用。

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 保持 present/explicit-negative/unknown 的差别；批复明确值优先，JSON 只补缺失，不解释未知业务代码。
2. 为客户、担保人、共同担保人、从合同、押品建立有来源的对象关联；按用户开发澄清，无产品关联的 JSON 对象套用全部批复产品，批复限定范围不跨产品。
3. 显式无担保压过 JSON 的担保数组；明确有押品但 cltlTp 空属于未知类型。

## 执行步骤

- [x] 先新增/补充 tests/test_material_facts.py 中的 test_explicit_pdf_negative_overrides_json 等行为检查，覆盖下列失败路径。
- [x] 实现前运行下面的定向命令，确认新行为因明确缺失而失败；区分环境/依赖错误与行为失败，不把环境损坏记成有效 red。
- [x] 只实现上述最小范围，使用 unittest 和临时目录/受控替身，异步测试采用 IsolatedAsyncioTestCase；不新增测试框架。
- [x] 再运行定向验收，检查至少运行一项测试且全部通过；涉及主应用/依赖/打包时追加旧检索回归。
- [x] 自查 diff、边界与密钥/运行产物排除；记录实际命令、结果及未完成事项，准备一次 review。若当前使用 Git，可作为独立提交；不为此自动 git init。

## 验收场景

- [x] 批复明确无担保+JSON A/B 不输出 JSON 担保事实；批复未列姓名时 JSON A/B 补入。
- [x] 空数组不覆盖批复事实；有押品未知类型不变成无押品，产品绝不从 JSON 补识别。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_material_facts.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_material_facts.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 用户开发澄清（2026-10-23）

- 无产品关联的 JSON 对象：单产品关联该产品，多产品自动套用全部批复产品；不以 JSON 识别新产品。
- 全空押品占位不算明确有押品；有编号、名称、类型或批复明确存在证据才建立对象，类型缺失保持 unknown。
- 批复同范围冲突采用原文后出现的明确事实，不因冲突失败；未提及不覆盖明确事实。
- 允许补充 T13 的提取提示和对应测试，统一关键事实名称，保留其他事实，无额外模型调用。

## 实施与验证记录（2026-10-23）

- 接口：`resolve_material_facts(approval, business_data, cancel_check=lambda: False)`，输入 T13 提取结果和业务 JSON，返回 products、facts、objects，不修改输入。
- facts 保留 present/negated/state、PDF/JSON 来源、引文或 JSON 路径、页号、产品/主体及 objectKey；批复明确值优先，JSON 只补缺失，原始码值与扩展数据保持不解释。
- objects 包含客户、从合同、担保人、共同担保人和押品，具有独立 objectKey、parentKey、产品范围及来源路径。相同姓名对应多个对象时保留 relatedObjectKeys，不随意指定其中一个；无法证实的 PDF 父子关联留空。否定后的主体范围保留为 present=false 的 subject，不作为适用对象。
- 全局无担保屏蔽 JSON 担保从合同及关联人/押品事实；局部否定只影响对应产品/主体，无押品不影响担保人。后出现明确值覆盖先前值，unknown 不擦除明确值；有押品未知类型保持存在。
- T13 提示定义关键事实名称及原文顺序；兼容既有“担保”“额度”等名称，其他事实不丢弃。没有接入 T17 生成、T18 决定校验或 T20 流水线。
- red：初始定向 8 项因模块缺失明确失败，T13 新提示检查失败；增补范围/重复主体等回归先观察失败再修正。最终定向命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_material_facts.py' -v`，16 项、OK、退出码 0；T13 同方式运行 test_approval_extractor.py，11 项、OK、退出码 0。
- 最终全量命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q`，`Ran 154 tests in 37.508s` / `OK`，退出码 0。AST 与 pip check 通过。
- 当前任务内完成自查与测试，无新依赖、子代理、Git 初始化、真实 PDF/模型、运行数据库、打包或部署。模型实际识别效果需 T28 业务样例验收。

