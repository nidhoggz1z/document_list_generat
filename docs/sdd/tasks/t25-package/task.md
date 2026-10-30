# T25：打包新模块且排除运行数据

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 开发与工程验收完成（2026-10-23）。
**依赖：** [T22](../t22-app-lifecycle/task.md)、[T23](../t23-contract-gate/task.md)
**规格映射：** `FR-08`、`AC-20`，见 [spec](../spec.md)。
**目标：** 让 src.tar 纳入实际业务模块及依赖，同时保留旧检索打包行为。

## 一个 diff 的边界

- 修改 package.py
- 新建 tests/test_task_package.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 调整现有固定四文件打包清单以纳入实际 src 根业务模块；不递归打入旧 src/data、测试数据或运行存储。
2. 保留根 list 当前有效清单；旧 openapi-hiagent.json 仍只含 retrieve，不混入新任务操作。
3. 打包器测试在临时目录执行，预置假 Key/.env/SQLite/上传文件验证被排除；不把模型配置检查或 worker 启动作为打包副作用。

## 执行步骤

- [x] 先新增/补充 tests/test_task_package.py 中的 test_archive_includes_modules_excludes_runtime 等行为检查，覆盖下列失败路径。
- [x] 实现前运行下面的定向命令，确认新行为因明确缺失而失败；区分环境/依赖错误与行为失败，不把环境损坏记成有效 red。
- [x] 只实现上述最小范围，使用 unittest 和临时目录/受控替身，异步测试采用 IsolatedAsyncioTestCase；不新增测试框架。
- [x] 再运行定向验收，检查至少运行一项测试且全部通过；涉及主应用/依赖/打包时追加旧检索回归。
- [x] 自查 diff、边界与密钥/运行产物排除；记录实际命令、结果及未完成事项，准备一次 review。若当前使用 Git，可作为独立提交；不为此自动 git init。

## 验收场景

- [x] 解包模块 import 及依赖清单齐全，无 src 外层、旧 data、缓存、密钥、数据库或上传 PDF。
- [x] list 原始字节完整；HiAgent 纯文本契约保留。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_task_package.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_package.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_service.py' -v
if ($LASTEXITCODE -ne 0) { throw '旧检索回归失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。


## 实施记录

2026-10-23 当前目录完成；最终全量 unittest 248 项通过（74.164 秒），最终部署包 75 份真实清单 HTTP 冒烟通过，退出码均为 0。T25 定向 1 项、T26 定向 1 项（含源码和部署包两种运行）通过。具体 spec 对照、命令及边界见根目录 VERIFICATION.md。未启用子代理、Git、部署或真实模型。
