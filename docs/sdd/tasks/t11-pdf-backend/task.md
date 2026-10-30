# T11：PDF 依赖选择与兼容性锁定

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与本地离线验收（2026-10-23）；目标 Docker / 内网安装及真实模型验收未执行。
**依赖：** 无功能任务依赖；先满足总索引的本地环境前提。
**规格映射：** `FR-09`、`FR-13`、`AC-10`，见 [spec](../../spec.md)。
**目标：** 在实现页处理前固定一个满足文本读取和页面渲染的 PDF 库。

## 一个 diff 的边界

- 修改 src/requirements.txt：仅一个 PDF 读取/渲染依赖
- 新建 docs/sdd/tasks/pdf-backend-decision.md
- 新建 tests/test_pdf_backend_compatibility.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 对候选库只依据官方说明核实 Python 3.12、Windows 与 Docker 环境轮子、许可证及文本/图像能力，选择一个，不建立多后端工厂。
2. 锁定实际版本，记录离线依赖与已验证环境；不得把开发机安装成功写成目标机器已验证。
3. 用临时生成的小 PDF 做选中库的打开、文本提取和页面渲染兼容检查，测试不依赖模型或真实客户文件。

## 执行步骤

- [x] 先新增/补充 tests/test_pdf_backend_compatibility.py 中的 test_selected_backend_extracts_and_renders 等行为检查，覆盖下列失败路径。
- [x] 实现前运行下面的定向命令，确认新行为因明确缺失而失败；区分环境/依赖错误与行为失败，不把环境损坏记成有效 red。
- [x] 只实现上述最小范围，使用 unittest 和临时目录/受控替身，异步测试采用 IsolatedAsyncioTestCase；不新增测试框架。
- [x] 再运行定向验收，检查至少运行一项测试且全部通过；涉及主应用/依赖/打包时追加旧检索回归。
- [x] 自查 diff、边界与密钥/运行产物排除；记录实际命令、结果及未完成事项，准备一次 review。若当前使用 Git，可作为独立提交；不为此自动 git init。

## 验收场景

- [x] 至少一页文本和一页图像能通过同一个库读取/渲染；坏/加密文件可区分失败。
- [x] 选型说明含官方来源和环境限制，依赖可复现，未增加独立 OCR 服务。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_pdf_backend_compatibility.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_pdf_backend_compatibility.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

**额外前提：** 依赖兼容检查需先安装本 diff 锁定的库。依赖版本及许可证来自官方资料，不凭旧知识猜版本；目标机器离线安装仍留给交付/部署验证。

## 实施与验收记录（2026-10-23）

- 锁定 pypdfium2==5.14.0；官方来源、许可证、实际环境与离线限制见 [后端决定](../pdf-backend-decision.md)。仅一个 PDF 依赖，无 OCR 服务。
- 定向测试先确认 requirements 缺少版本锁定而失败，再锁定并验收。早期测试误用页面上下文管理器已改为显式 closing，此测试写法错误不计为有效 red。
- 使用临时生成的文本、扫描、混合、文字覆盖层和 R2 加密 PDF，同库提取/渲染及坏文件与密码错误区分通过。tests/pdf_fixtures.py 为两任务共用的必要 fixture，无第二 PDF 库。
- 定向命令：` .\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_pdf_backend_compatibility.py -v `，退出码 0，Ran 3 tests in 0.030s / OK。
- 全量回归：` .\.venv\Scripts\python.exe -B -m unittest discover -s tests -v `，退出码 0，Ran 117 tests in 10.891s / OK，含旧检索 26 项及本轮新增 11 项。pip check 无依赖冲突，4 个新增 Python 文件 AST 语法检查通过。
- 在当前目录完成开发及代码自查，无子代理、工作树、Git 初始化、真实模型调用、用户 PDF、运行数据库、打包或部署。后续视觉识别和流水线仍属 T13/T20/T21/T22，未标记全 spec 完成。
