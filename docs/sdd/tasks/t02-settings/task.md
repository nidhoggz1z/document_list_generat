# T02：模型和本地存储配置

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线验收（2026-10-23）；不代表后续任务及真实模型联调完成。
**依赖：** 无功能任务依赖；先满足总索引的本地环境前提。
**规格映射：** `FR-08`、`FR-13`，见 [spec](../spec.md)。
**目标：** 集中读取双模型六项配置和本地目录、网络超时、有限重试预算。

## 一个 diff 的边界

- 新建 src/task_settings.py
- 修改 docs/sdd/model-api.env.example
- 新建 tests/test_task_settings.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 保留 EXTRACT_MODEL_* 与 GENERATE_MODEL_* 六个变量；完整 URL 不自动追加路径。
2. 按用户本轮决定：存储目录默认 src/task_storage（当前项目内），单请求网络超时 180 秒，额外重试 2 次；不设置 token 预算。超时不是总任务 SLA，也不允许静默截断。
3. 真实 Key 不进入 repr、错误消息、配置导出或持久数据；配置错误走明确的服务不可用路径，不影响原检索能力。

## 执行步骤

- [x] 先新增/补充 tests/test_task_settings.py 中的 test_keys_redacted_and_two_endpoints 等行为检查，覆盖下列失败路径。
- [x] 实现前运行下面的定向命令，确认新行为因明确缺失而失败；区分环境/依赖错误与行为失败，不把环境损坏记成有效 red。
- [x] 只实现上述最小范围，使用 unittest 和临时目录/受控替身，异步测试采用 IsolatedAsyncioTestCase；不新增测试框架。
- [x] 再运行定向验收，检查至少运行一项测试且全部通过；涉及主应用/依赖/打包时追加旧检索回归。
- [x] 自查 diff、边界与密钥/运行产物排除；记录实际命令、结果及未完成事项，准备一次 review。若当前使用 Git，可作为独立提交；不为此自动 git init。

## 验收场景

- [x] 两个节点可配置相同/不同端点；占位 Key、无效重试预算不能发出请求。
- [x] 异常和对象展示不泄露 Key；缺失生成配置不阻断原检索及未来读取已有任务所需的配置加载（完整查询集成验收留给 T08/T22）。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_task_settings.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_settings.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 实施与验收记录（2026-10-23）

- 首次定向运行 6 项因实现缺失而断言失败（退出码 1）。
- 入口：TaskSettings.from_env(environ=None)；require_models() 校验生成配置，失败抛 TaskConfigurationError（status_code=503），不暴露原始值；to_dict() 不导出 Key。
- 环境变量：TASK_STORAGE_ROOT、MODEL_REQUEST_TIMEOUT_SECONDS、MODEL_MAX_RETRIES，以及原双模型六项变量。不自动加载 .env、不创建目录、不发送请求。
- Key 用 SecretStr 保存；只有未来 T10 的 Authorization 构造可调用 get_secret_value()。覆盖同/不同端点、逐项缺失、占位值、非法预算、展示及导出脱敏。
- 用户确认真实模型校验稍后进行；未提供真实 Key、未执行真实请求。HTTP 重试执行及已有任务读取集成仍属后续任务。
- 最终定向命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_settings.py' -v`，7 项通过、退出码 0。
- 最终全量回归：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`，42 项通过、退出码 0（包含原检索 26 项）。
- 自查：未改动主应用、依赖、清单或打包产物；未保存真实 Key、业务 PDF 或运行数据库。当前目录非 Git 仓库，未初始化或提交 Git；按用户要求在当前任务自查，未启用子代理。
