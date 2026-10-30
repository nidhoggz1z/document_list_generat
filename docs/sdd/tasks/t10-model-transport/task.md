# T10：可取消模型 HTTP 调用

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线验收（2026-10-23）；真实模型联调待 T28。
**依赖：** [T02](../t02-settings/task.md)
**规格映射：** `FR-06`、`FR-13`、`AC-18`，见 [spec](../../spec.md)。
**目标：** 实现单个 Chat Completions 兼容调用、有限网络重试及本地取消。

## 一个 diff 的边界

- 新建 src/model_client.py
- 修改 src/requirements.txt：HTTP 客户端
- 新建 tests/test_model_client.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 复用可异步取消的 HTTP 客户端；优先已使用的 httpx，确认兼容后固定版本，不新增供应商 SDK。
2. POST 完整 URL，Bearer 仅放请求头，model/messages 在 JSON；分别支持文本和视觉消息，解析有效内容与截断标记。
3. 超时及可恢复 HTTP 失败有限重试，重试等待可取消；不重试无效凭证等永久错误，不在此实现材料逻辑。

## 执行步骤

- [x] 先新增/补充 tests/test_model_client.py 中的 test_cancel_interrupts_request_and_retry_wait 等行为检查，覆盖下列失败路径。
- [x] 实现前运行下面的定向命令，确认新行为因明确缺失而失败；区分环境/依赖错误与行为失败，不把环境损坏记成有效 red。
- [x] 只实现上述最小范围，使用 unittest 和临时目录/受控替身，异步测试采用 IsolatedAsyncioTestCase；不新增测试框架。
- [x] 再运行定向验收，检查至少运行一项测试且全部通过；涉及主应用/依赖/打包时追加旧检索回归。
- [x] 自查 diff、边界与密钥/运行产物排除；记录实际命令、结果及未完成事项，准备一次 review。若当前使用 Git，可作为独立提交；不为此自动 git init。

## 验收场景

- [x] 本地 MockTransport 校验两节点端点/模型/鉴权，日志异常无 Key。
- [x] 429/5xx/超时达到预算即失败；取消中断请求及等待；finish_reason=length 等不能当完整输出。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_model_client.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_model_client.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 实施与验收记录（2026-10-23）

- 新增 src/model_client.py、tests/test_model_client.py，固定并复用已安装 `httpx==0.28.1`，不引入供应商 SDK。
- 使用 `async with ModelClient(settings)` 管理并复用异步 HTTP 客户端；`await client.complete('extract' 或 'generate', messages)` 发送该阶段完整 URL、独立 Bearer 头与 model/messages JSON，原样支持文本和视觉消息，返回 ModelReply(content, finish_reason)。不跟随重定向，不设置 token 预算。
- 用户确认：网络连接/超时及 HTTP 408/429/5xx 有限重试，依次等待 1、2 秒（更多重试预算时继续按次数递增）；其他 4xx 等永久失败不重试。复用 T02 的超时和额外重试次数配置。取消 asyncio Task 可打断请求或等待，CancelledError 原样传播；客户端由上下文关闭。
- 用户确认：只接受 finish_reason=stop 和非空文本；length/content_filter/缺失结束标记、无效响应结构均报错，不自动修复或重试内容问题。保留已知失败结束标记，不对外透传任意供应商内容。
- 网络异常不带原异常链，HTTP 错误不带响应正文或凭证；用测试专用假密钥检查日志、错误文本及格式化 traceback。真实 Key 不用于测试。
- 先运行定向命令，确认缺少模块导致 8 项明确断言失败；实现后定向命令 `.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_model_client.py' -v`，退出码 0，`Ran 8 tests in 0.095s` / `OK`。MockTransport 与 Event 验证重试预算、恢复、零重试、取消、配置隔离及关闭，不访问真实 API。
- 最终全量命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`，退出码 0，`Ran 106 tests in 9.446s` / `OK`；`python -m pip check` 返回 No broken requirements found。五个新增/修改 Python 文件 AST 语法检查通过。
- 已自查实现边界；未接入后续 PDF/生成流水线、生命周期或真实模型，不表示 AC-18 的后续业务校验全部完成。

