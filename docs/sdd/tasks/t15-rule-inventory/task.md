# T15：原规则清单明细索引

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线测试（2026-10-23）；真实模型业务验收待 T28。
**依赖：** [T14](../t14-checklist-snapshot/task.md)
**规格映射：** `FR-09`、`FR-11`、`FR-13`、`AC-14`、`AC-17`，见 [spec](../../spec.md)。
**目标：** 构建可证明覆盖的明细索引，不把模型输出条数当原规则全集。

## 一个 diff 的边界

- 新建 src/checklist_rules.py
- 新建 tests/test_checklist_rule_inventory.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 基于快照原始 DOCX 与现有读取器建立 productName、内部唯一 ruleKey、sourceRuleId、sourcePosition、原行内容及分组上下文。
2. 先检查全部实际 75 份清单的表格变体；不按纯文本换行简单拆行，保留单元格内换行/合并格上下文。
3. 稳定区分分组与明细，材料/核查原始标记作为上下文保留；仅建立索引，不实现适用性判断。

## 执行步骤

- [x] 先新增/补充 tests/test_checklist_rule_inventory.py 中的 test_all_real_checklists_have_complete_inventory 等行为检查，覆盖下列失败路径。
- [x] 实现前运行下面的定向命令，确认新行为因明确缺失而失败；区分环境/依赖错误与行为失败，不把环境损坏记成有效 red。
- [x] 只实现上述最小范围，使用 unittest 和临时目录/受控替身，异步测试采用 IsolatedAsyncioTestCase；不新增测试框架。
- [x] 再运行定向验收，检查至少运行一项测试且全部通过；涉及主应用/依赖/打包时追加旧检索回归。
- [x] 自查 diff、边界与密钥/运行产物排除；记录实际命令、结果及未完成事项，准备一次 review。若当前使用 Git，可作为独立提交；不为此自动 git init。

## 验收场景

- [x] 全部当前真实文档可索引，抽样逐项对照表格，编号/多行单元格不丢失。
- [x] 重复展示编号仍有唯一内部 ruleKey；任何无法完整建索引的文件必须报错而非跳过。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_checklist_rule_inventory.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_checklist_rule_inventory.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 实施与验证记录（2026-10-23）

- 接口：`build_rule_inventory(snapshots, cancel_check=lambda: False)`，输入 T14 快照，返回按产品/原表格行排序的明细列表。每项包含 productName、ruleKey、sourceRuleId、sourcePosition、cells、groups、rawMarker、kind 和 documentContext。
- 复用 docx_reader 的编号和文本读取规则，核对快照字节哈希及全文；不从纯文本换行推断表格行。ruleKey 为产品和表格行定位的 JSON 元组编码，展示编号重复不冲突，无编号时回退表格行定位。
- 全部真实 75 份文档均为五列明细、两格分组（第二格 gridSpan=4）；共 1,373 个合并分组，9,512 条明细，最长 204 条。测试逐份逐行比较全部单元格文本，包含个人和企业清单；分组、材料及核查原标记保留。
- 不支持的表头、列布局、嵌套表格、竖向/旧式横向合并等整体报 RuleInventoryError，不跳过未知内容。取消在产品、正文块及各行检查；不实现适用性判断。
- red：初始定向 3 项均因模块缺失明确失败；随后完成实现。最终定向命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_checklist_rule_inventory.py' -v`，4 项、OK、退出码 0。
- 最终全量命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q`，`Ran 154 tests in 37.508s` / `OK`，退出码 0，包含旧检索 26 项。6 个新增/修改 Python 文件 AST 检查通过，pip check 无冲突。
- 当前任务自查完成；无新依赖、子代理、Git 初始化、清单原件修改、真实模型调用或部署产物。流水线接入由 T20 完成。

