# T17：三部分上下文与分批生成

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线测试（2026-10-23）；真实模型业务验收待 T28。
**依赖：** [T10](../t10-model-transport/task.md)、[T13](../t13-approval-extraction/task.md)、[T15](../t15-rule-inventory/task.md)、[T16](../t16-fact-resolution/task.md)
**规格映射：** `FR-11`、`FR-13`、`AC-17`、`AC-18`，见 [spec](../../spec.md)。
**目标：** 第二模型对每个原规则返回显式决定及必要的对象材料展开。

## 一个 diff 的边界

- 新建 src/material_generator.py
- 新建 tests/test_material_generation.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 每批输入业务 JSON、完整提取全文、全部产品清单全文；批次只限制要求生成的 ruleKey，不暗中裁去三部分上下文。
2. 内部决定包含 productName/ruleKey、KEEP/EXCLUDE/CHECK、支持来源和对象实例；未知适用性 KEEP，不新增原索引之外的规则。
3. 按用户开发澄清，不设置上下文/输出容量上限，不做调用前容量检查，不静默截断。拆批和有限结构修复均可取消；任一批失败整体抛错，不先发布部分材料。

## 执行步骤

- [x] 先新增/补充 tests/test_material_generation.py 中的 test_batches_keep_all_three_contexts 等行为检查，覆盖下列失败路径。
- [x] 实现前运行下面的定向命令，确认新行为因明确缺失而失败；区分环境/依赖错误与行为失败，不把环境损坏记成有效 red。
- [x] 只实现上述最小范围，使用 unittest 和受控替身，异步测试采用 IsolatedAsyncioTestCase；不新增测试框架。
- [x] 再运行定向验收，检查至少运行一项测试且全部通过；全量回归包含旧检索测试。
- [x] 在当前任务自查文件、边界与密钥/运行产物排除；记录实际命令、结果及未完成事项。当前目录非 Git 仓库，无 Git 初始化或提交。

## 验收场景

- [x] 检查实际发送消息确实含三部分全文；73 条规则及真实最长清单拆批仍覆盖全部 ruleKey。
- [x] 无效 JSON、截断、丢批、重试耗尽及取消准确失败/停止，未知条件不被默认为排除；长上下文不被本地限制或裁剪，供应商容量错误按 T10 失败。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_material_generation.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_material_generation.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 用户开发澄清（2026-10-23）

- 上下文和输出容量不设限，调用前不检查容量；模型截断或服务容量错误仍须失败，不能发布部分结果。
- JSON、规则覆盖或对象展开校验失败允许有限结构修复；额外次数复用 `MODEL_MAX_RETRIES`，默认 2 次。网络与 HTTP 重试由 T10 负责，截断/transport 错误不叠加结构修复。
- 对象范围由模型逐规则声明，程序核对声明范围内的对象覆盖和来源关联；范围判断和内容的业务准确性留给 T28。

## 实施与验证记录（2026-10-23）

- 接口：`await generate_material_decisions(business_data, approval, snapshots, rules, resolved_context, model_client, cancel_check=lambda: False, *, batch_size=20, max_retries=None)`。输入依次是业务 JSON、T13 完整提取、T14 快照、T15 索引、T16 有效事实对象及 T10 客户端。
- 每批完整携带业务 JSON、批复全文（含全部页/表格/事实/授信记录）、全部匹配产品清单全文、完整规则索引和有效事实对象；只限制本批 requestedRuleKeys。快照字节/哈希不作为模型正文发送。全部输入复制为固定上下文，不修改调用方数据。
- 返回完整内部决定列表；批次和最终集合均调用 T18 校验。任一批失败抛错，不返回部分结果。每次修复替换整批，不拼接不完整回复；保留最近一次失败回复，原始完整上下文始终存在。
- 支持调用前、批次间、修复间及校验循环取消；在途 HTTP 通过取消 asyncio Task 中断。没有新增 HTTP 参数、容量配置或依赖。
- red：初始 10 项因缺少生成模块明确失败。最终定向 `.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_material_generation.py' -v`，`Ran 12 tests in 11.198s` / `OK`，退出码 0。覆盖完整三部分上下文、73 条规则、多产品、真实最长清单及超过 60 条材料、长正文、有限修复、后批失败、取消与截断。
- 全量 `.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q`，`Ran 183 tests in 37.526s` / `OK`，退出码 0。新增三个 Python 文件 AST 解析通过，pip check 无依赖冲突。
- 当前目录完成开发和自查，无子代理、工作树、Git 初始化、清单原件修改、真实 Key/PDF/模型请求、运行数据库、打包或部署。T19 的公开结果投影与零材料检查、T20 的流水线接入尚未实现；T28 真实业务准确率未验收。

