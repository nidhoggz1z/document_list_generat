# T28：真实模型及业务样例验收入口

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 开发及离线验证完成；真实模型/业务签收待联调（用户确认本次不执行）。
**依赖：** [T24](../t24-integration-races/task.md)、[T26](../t26-packaged-http/task.md)、[T27](../t27-handoff-docs/task.md)
**规格映射：** `AC-10`、`AC-11`、`AC-12`、`AC-13`、`AC-14`、`AC-15`、`AC-16`、`AC-17`、`AC-18`，见 [spec](../spec.md)。
**目标：** 提供显式启用的真实联调入口，形成业务人员可签收的报告。

## 一个 diff 的边界

- 新建 src/verify_task_live.py
- 新建 tests/test_live_validation_cli.py
- 新建 docs/sdd/业务验收记录.md

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 前置外部资料：真实双模型配置、非敏感/获准批复 PDF、业务 JSON、业务人员确认的期望材料；未提供时标待联调，不能填已通过。
2. 实现独立验收 CLI，参数 --base-url、--pdf、--business-data、--expected、--report；服务端提前配置真实模型并启动，CLI 校验输入文件和服务可达，再通过现有创建/查询接口执行，不读取/输出服务器 Key。
3. 报告含状态、覆盖、展开、误排/漏排及业务人工结论；CLI 结构成功不等于语义验收通过，不自动修改期望清单。

## 执行步骤

- [x] 先新增/补充 tests/test_live_validation_cli.py 中的 test_invalid_inputs_do_not_submit_task 等行为检查，覆盖下列失败路径。
- [x] 实现前运行下面的定向命令，确认新行为因明确缺失而失败；区分环境/依赖错误与行为失败，不把环境损坏记成有效 red。
- [x] 只实现上述最小范围，使用 unittest 和临时目录/受控替身，异步测试采用 IsolatedAsyncioTestCase；不新增测试框架。
- [x] 再运行定向验收，检查至少运行一项测试且全部通过；涉及主应用/依赖/打包时追加旧检索回归。
- [x] 自查 diff、边界与密钥/运行产物排除；记录实际命令、结果及未完成事项，准备一次 review。若当前使用 Git，可作为独立提交；不为此自动 git init。

## 验收场景

- [x] 缺 fixture、无效期望文件或服务不可达时报告清楚的失败；单元检查用假 transport，不调用真实模型。
- [ ] 真实验收必须记录实际调用和业务确认；OCR 可读性及材料适用性由输入/期望对照，不依据模型自述判定。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_live_validation_cli.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_live_validation_cli.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 默认仅执行离线 CLI 检查；外部资料齐备并显式运行末节命令时，才执行真实模型验收，不自动部署。
- 不因本任务测试通过就标记整个 spec 已实现。

## 真实业务验收（外部资料齐备后，显式运行）

先由业务方提供实际 PDF、业务 JSON、人工期望清单，模型配置从环境变量读取。CLI 用现有创建/查询业务入口执行，不能把 expected 当生成输入，也不自动将模型输出写成 expected。

```powershell
if (-not (Test-Path -LiteralPath 'src/verify_task_live.py')) { throw '真实验收入口尚未实现' }
.\.venv\Scripts\python.exe -B src/verify_task_live.py --base-url http://127.0.0.1:8000 --pdf acceptance/approval.pdf --business-data acceptance/business.json --expected acceptance/expected.json --report task_storage/acceptance-report.json
if ($LASTEXITCODE -ne 0) { throw '真实联调或业务核对未通过' }
```

**阻塞说明：** acceptance/ 下文件是待业务方提供的外部输入，不是当前仓库已有文件。缺少资料时标记“待业务联调”，不虚构成功；保留自动比较结果和业务签收人/日期。


## 本次用户决定与实际结果

用户确认只自动核对产品、原规则与条目数量，文字由人工核对；真实验收本次待联调。expected 格式及手工签收栏见 ../../业务验收记录.md。定向 10 项通过；最终全量 248 项通过。缺少真实资料不标记业务通过。
