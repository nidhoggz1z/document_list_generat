# T14：多产品一致清单快照

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成本地开发与测试（2026-10-23）。
**依赖：** 无功能任务依赖；先满足总索引的本地环境前提。
**规格映射：** `FR-09`、`AC-11`、`AC-20`，见 [spec](../../spec.md)。
**目标：** 复用现有严格检索，一次获取所需产品的相同目录版本内容。

## 一个 diff 的边界

- 修改 src/main.py：DocumentStore 增加只读快照方法
- 新建 tests/test_checklist_snapshot.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 在现有锁/refresh 机制下添加 snapshot(names)，返回每份 name、filename、全文、实际原始字节和内容哈希。
2. 若读取期间版本变化，拒绝发布混合快照；缺任一产品、重复名称、坏文件整体失败。
3. 不改变 retrieve 的纯文本响应和热更新语义，不恢复旧 src/data，也不做与本任务无关的类拆分。

## 执行步骤

- [ ] 先新增/补充 tests/test_checklist_snapshot.py 中的 test_multi_product_snapshot_is_consistent 等行为检查，覆盖下列失败路径。
- [ ] 实现前运行下面的定向命令，确认新行为因明确缺失而失败；区分环境/依赖错误与行为失败，不把环境损坏记成有效 red。
- [ ] 只实现上述最小范围，使用 unittest 和临时目录/受控替身，异步测试采用 IsolatedAsyncioTestCase；不新增测试框架。
- [ ] 再运行定向验收，检查至少运行一项测试且全部通过；涉及主应用/依赖/打包时追加旧检索回归。
- [ ] 自查 diff、边界与密钥/运行产物排除；记录实际命令、结果及未完成事项，准备一次 review。若当前使用 Git，可作为独立提交；不为此自动 git init。

## 验收场景

- [ ] 读取中换文件不能混合新旧产品；快照的字节、哈希和全文一致。
- [ ] 普通/循环/外汇名称严格隔离；原 test_service.py 全部通过。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_checklist_snapshot.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_checklist_snapshot.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_service.py' -v
if ($LASTEXITCODE -ne 0) { throw '旧检索回归失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 实施与验证记录（2026-10-23）

- 仅为 `src/main.py` 中 DocumentStore 增加 `snapshot(names)`，新增 `tests/test_checklist_snapshot.py`。
- 返回按输入顺序排列的 `name/filename/content/raw_bytes/content_hash` 记录；哈希为原始 DOCX 字节 SHA-256，正文从同一份字节经现有读取器解析。
- 复用 refresh 全目录校验与现有锁，读取前后比对目录版本；任一名称缺失、重复、文件损坏或读取中版本变化均整体报错。返回副本不影响检索缓存，原 retrieve 接口保持不变。
- red：新增测试运行 4 项，因 snapshot 方法缺失明确失败。
- 定向命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_checklist_snapshot.py' -v`，最终 **6 项，OK，退出码 0**。
- 回归命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_service.py' -v`，**26 项，OK，退出码 0**。
- 全量命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q`，最终 **133 项，OK，退出码 0**。
- 已核对全部真实 75 份清单的快照字节、哈希和全文；覆盖普通/循环/外汇名称隔离、读取中替换、实际读取损坏及恢复，不修改清单原件。
- 已在当前任务自查修改范围；无新依赖、子代理或部署产物，未将本任务通过视为整个 spec 完成。

