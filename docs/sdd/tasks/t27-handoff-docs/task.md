# T27：运行与离线交付说明

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 开发与工程验收完成（2026-10-23）。
**依赖：** [T23](../t23-contract-gate/task.md)、[T25](../t25-package/task.md)、[T26](../t26-packaged-http/task.md)
**规格映射：** `FR-08`、`FR-13`、`AC-20`，见 [spec](../spec.md)。
**目标：** 把实际配置和已验证能力写成可执行交付说明，不部署到目标机器。

## 一个 diff 的边界

- 修改 README.md、VERIFICATION.md
- 修改 docs/sdd/model-api.env.example

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 写清 Python 3.12、单 worker、任务存储目录持久化、双模型端点/Key/模型名、超时/重试和 PDF 依赖。
2. 提供离线 wheel 准备/安装、src.tar 生成、规则目录与任务目录区分、multipart 创建/轮询命令；版本与 T10/T11 锁定一致。
3. VERIFICATION 区分假模型离线验证、真实供应商未测和业务待签收；不声称目标 Windows/Docker 已部署。

## 执行步骤

- [x] 确认前置打包及全量检查已通过，列出实际配置名、依赖版本和已验证环境。
- [x] 对照实现修订错误路径或遗漏，不新增镜像/文案重复测试，也不要求文档改动制造 red。
- [x] 只实现上述最小范围，使用 unittest 和临时目录/受控替身，异步测试采用 IsolatedAsyncioTestCase；不新增测试框架。
- [x] 再运行定向验收，检查至少运行一项测试且全部通过；涉及主应用/依赖/打包时追加旧检索回归。
- [x] 自查 diff、边界与密钥/运行产物排除；记录实际命令、结果及未完成事项，准备一次 review。若当前使用 Git，可作为独立提交；不为此自动 git init。

## 验收场景

- [x] 文档命令使用仓库真实路径，示例不含 Key；配置模板与实现变量一一对应。
- [x] 重复运行包测试和全部 unittest，均成功；仅文档变化不新增镜像快照类测试。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_task_package.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_package.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw '全量检查失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。


## 实施记录

2026-10-23 当前目录完成；最终全量 unittest 248 项通过（74.164 秒），最终部署包 75 份真实清单 HTTP 冒烟通过，退出码均为 0。T25 定向 1 项、T26 定向 1 项（含源码和部署包两种运行）通过。具体 spec 对照、命令及边界见根目录 VERIFICATION.md。未启用子代理、Git、部署或真实模型。
