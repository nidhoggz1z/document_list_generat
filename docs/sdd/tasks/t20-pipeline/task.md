# T20：完整处理流水线与阶段产物

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成开发与离线测试（2026-10-23）；后台工作单元与生命周期接入分别待 T21/T22，真实业务验收待 T28。
**依赖：** [T05](../t05-state-cas/task.md)、[T13](../t13-approval-extraction/task.md)、[T14](../t14-checklist-snapshot/task.md)、[T15](../t15-rule-inventory/task.md)、[T16](../t16-fact-resolution/task.md)、[T17](../t17-generation/task.md)、[T18](../t18-decision-validation/task.md)、[T19](../t19-result-projection/task.md)
**规格映射：** `FR-08`、`FR-09`、`FR-13`、`AC-10`、`AC-11`、`AC-18`，见 [spec](../../spec.md)。
**目标：** 串接已完成组件，提供异步 run_task(record) 返回完整 data。

## 一个 diff 的边界

- 新建 src/task_pipeline.py
- 新建 tests/test_task_pipeline.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 依次页面读取/提取→一致快照/索引→事实合并→分批生成/校验→结果投影；通过注入函数复用组件，不新建通用工作流框架。
2. 每阶段及等待前后检查取消；以代次隔离保存提取内容、清单快照/哈希、内部决定，绝不保存 Key。
3. 任一产品/批次失败抛出可展示处理错误，不发布结果；完成状态仍由工作单元通过 T05 条件提交。

## 执行步骤

- [x] 新增 tests/test_task_pipeline.py 的全链路与失败、取消测试。
- [x] 实现前定向运行，10 项因缺少 T20 实现明确失败。
- [x] 仅实现本任务范围，沿用 unittest、IsolatedAsyncioTestCase 和临时目录，无新增依赖。
- [x] 定向验收 16 项通过，全量回归记录见任务索引。
- [x] 在当前目录自查实现、代次隔离和产物边界；没有 Git 初始化或提交。

## 验收场景

- [x] 假模型两产品全链路可返回完整 data 和阶段文件；其中一产品缺清单、页面/批次失败或零结果全部失败。
- [x] 阶段间替换取消不触发后续模型；流水线不自行创建第二个 worker。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_task_pipeline.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_task_pipeline.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 实施与验证记录（2026-10-23）

- 接口：`TaskPipeline(store, documents, model_client, *, page_reader=read_pdf, extractor=extract_approval, generator=generate_material_decisions, batch_size=20)`；`await pipeline.run_task(record, cancel_check=lambda: False)` 接收 TaskStore 领取的 PROCESSING 记录并返回完整 data。模型客户端由调用者持有及关闭。
- 顺序：核对服务生成的 PDF 路径及实际字节哈希→完整页面读取与提取→一次性全部产品快照及索引→事实合并→分批生成及最终 T18 校验→T19 投影。阻塞操作在线程执行；每阶段和等待前后核对取消、当前 generationId 及 PROCESSING 状态。
- 用户确认：阶段文件存放于 `generations/<generationId>/stages/`。approval/checklists/rules/facts/decisions JSON 均封装 generationId，原始 DOCX 使用内部顺序文件名；清单 manifest 保留全文、原文件名及内容哈希。TaskStore.save_artifact 以代次条件登记路径、generationId、文件 SHA-256；拒绝登记即取消。文件通过临时文件、fsync 和原子替换发布，不归档客户端配置、Key 或模型请求头。
- 旧代次至多留下其自身目录的未登记文件，不能写入新代次；无自动清理。阶段写入或处理失败抛出仅含阶段名的 TaskPipelineError，避免泄露模型正文、路径或密钥。流水线不调用 finish_done/finish_failed，不消费队列、不创建 worker。
- 活跃 HTTP 由调用者取消 asyncio Task 中断；线程通过 Event 协作退出，取消返回前等待线程结束。新增重复取消测试先复现“第二次取消使线程未退出就返回”，再修正为重复取消期间仍等待线程收尾。
- 测试使用合成文本/扫描 PDF、临时两产品 DOCX、真实 DocumentStore/TaskStore 和本地假模型。覆盖两产品生成及阶段哈希、清单更新后快照保持、缺清单、实际 PDF 解析失败、不可读页、输入哈希不符、后批次失败、零材料、最终覆盖校验、文件/登记失败、阶段间及最终投影替换、HTTP/线程与重复取消。
- 定向命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_task_pipeline.py -v`，最终 `Ran 16 tests in 2.223s` / `OK`，退出码 0。
- T21/T22 尚未实施，当前上传接口不会因本次改动自动运行流水线。无真实模型、部署、打包或子代理。

