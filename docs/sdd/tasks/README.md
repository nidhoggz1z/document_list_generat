# 个性化材料任务服务：开发任务索引

> 执行者：使用 writing-plans 形成的分步计划；后续可在当前任务参考 executing-plans 执行。未经用户单独授权，不启用子代理、另建 Codex 任务或工作树。

**日期：** 2026-10-23（Asia/Shanghai）。**状态：** T01～T27 已完成开发与离线工程验收；T28 验收入口及离线检查完成，真实模型业务验收待联调（用户确认）。最终全量 248 项通过，详见根目录 VERIFICATION.md。
**Goal：** 按已确认 spec 实现上传创建、串行可替换任务、本地历史、多产品个性化生成及轮询查询。
**Architecture：** 复用现有 FastAPI/DocumentStore/docx_reader；SQLite 保存任务与执行代次，单个异步工作单元消费持久 FIFO。模型调用与 PDF/材料处理按单一职责模块拆开，不引入 Redis、Celery、ORM 或供应商 SDK。
**Tech Stack：** Python 3.12、FastAPI/Pydantic、sqlite3、asyncio、unittest；异步 HTTP/PDF 依赖分别在 T10/T11 核实并固定版本。

依据：[spec](spec.md)、[接口契约](../接口文档.md)、[OpenAPI](../personalized-checklist.openapi.json)。任务只细化实施，不改变用户业务决定。每个目录只有一个 task.md，可独立作为 review 清单。

## 环境与验收前提

- 工作目录为项目根目录。沿用 `.venv/Scripts/python.exe` 与现有 unittest；当前基线 test_service.py 已实跑，26 项通过（2026-10-23）。
- 新测试沿用 tests/test_service.py 的 src 导入方式：`sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))`；不要依赖其他测试先替自己设置导入路径。
- 新功能测试和模块尚不存在；每个任务要求先创建再运行，禁止把 discover 的零测试当成功。下面单项命令都是未来验收计划，不代表本轮已执行。
- 安装基础依赖后按各任务追加已锁定依赖；现有 FastAPI TestClient 所需 httpx 依照当前 README 配置。不要提前安装尚未核实的 PDF 或模型 SDK。
- 所有离线自动检查用临时数据库、生成/非敏感 PDF 和本地假模型，不访问真实 API；真实 Key 与业务 fixture 只在 T28 外部准备。
- 任务可按数字顺序执行；依赖是硬前置。无依赖的不同模块可独立 review，但共享 src/main.py 等文件的任务仍顺序完成；本计划不授权并行代理。

## 变更边界及共同完成标准

- 每任务一个独立行为目标及一次可 review diff；通常 1～3 个业务文件加对应测试，建议约 300 行以内逻辑变更。超过边界先拆分并更新所有下游依赖，不能把多项任务打成一个大 diff。
- 所有业务模块在 src/，测试在 tests/；不重新组织现有目录，不修改用户 list 原件，不恢复旧 src/data。
- 不为写计划提前创建空实现、占位测试或伪模型业务结果。先行为失败、再最小实现、再定向通过与必要旧功能回归。
- 用 Event/Barrier/受控 transport 验证竞争，不依赖随机 sleep 或真实模型速度；阶段产物及所有写回带 generationId。
- Review 时附真实验收输出、涉及的 FR/AC、变更边界；真实联调未完成必须直说，不能用结构检查代替业务正确性。
- 不自动提交 Git 或部署。本轮仅 docs/sdd/tasks/ 有新文档；task.md 的状态不能提前勾为完成。

## Chunk 1：数据、持久化与 HTTP 接收（T01～T09）

| 任务 | 硬依赖 | 验收测试文件 |
| --- | --- | --- |
| [T01 业务输入与任务响应模型](t01-contracts/task.md) | 无 | tests/test_task_models.py |
| [T02 模型和本地存储配置](t02-settings/task.md) | 无 | tests/test_task_settings.py |
| [T03 SQLite 任务记录与身份](t03-store-base/task.md) | T01、T02 | tests/test_task_store_base.py |
| [T04 原子替换与 FIFO 顺序](t04-queue-transactions/task.md) | T03 | tests/test_task_store_queue.py |
| [T05 任务领取与迟到结果隔离](t05-state-cas/task.md) | T04 | tests/test_task_store_transitions.py |
| [T06 重启失败恢复规则](t06-restart-recovery/task.md) | T05 | tests/test_task_store_recovery.py |
| [T07 PDF 接收与原子任务创建服务](t07-upload-service/task.md) | T01、T02、T04 | tests/test_task_submission.py |
| [T08 只读任务查询接口](t08-query-api/task.md) | T01、T03 | tests/test_task_query_api.py |
| [T09 上传创建任务接口](t09-create-api/task.md) | T07、T08 | tests/test_task_create_api.py |

## Chunk 2：模型、PDF 和材料生成（T10～T20）

| 任务 | 硬依赖 | 验收测试文件 |
| --- | --- | --- |
| [T10 可取消模型 HTTP 调用](t10-model-transport/task.md) | T02 | tests/test_model_client.py |
| [T11 PDF 依赖选择与兼容性锁定](t11-pdf-backend/task.md) | 无 | tests/test_pdf_backend_compatibility.py |
| [T12 完整 PDF 页面读取](t12-pdf-pages/task.md) | T11 | tests/test_pdf_reader.py |
| [T13 批复全文与可售产品提取](t13-approval-extraction/task.md) | T01、T10、T12 | tests/test_approval_extractor.py |
| [T14 多产品一致清单快照](t14-checklist-snapshot/task.md) | 无 | tests/test_checklist_snapshot.py |
| [T15 原规则清单明细索引](t15-rule-inventory/task.md) | T14 | tests/test_checklist_rule_inventory.py |
| [T16 批复优先事实与对象范围](t16-fact-resolution/task.md) | T13 | tests/test_material_facts.py |
| [T17 三部分上下文与分批生成](t17-generation/task.md) | T10、T13、T15、T16 | tests/test_material_generation.py |
| [T18 规则覆盖与展开结果校验](t18-decision-validation/task.md) | T15、T16、T17 | tests/test_material_decisions.py |
| [T19 平铺响应、默认值与编号](t19-result-projection/task.md) | T01、T18 | tests/test_material_result.py |
| [T20 完整处理流水线与阶段产物](t20-pipeline/task.md) | T05、T13、T14、T15、T16、T17、T18、T19 | tests/test_task_pipeline.py |

## Chunk 3：接入、竞争、打包与业务验收（T21～T28）

| 任务 | 硬依赖 | 验收测试文件 |
| --- | --- | --- |
| [T21 单工作单元调度与抢占取消](t21-serial-worker/task.md) | T05、T20 | tests/test_task_worker.py |
| [T22 生命周期接入、恢复与单实例](t22-app-lifecycle/task.md) | T02、T06、T08、T09、T21 | tests/test_task_app_lifecycle.py |
| [T23 运行接口与 OpenAPI 契约核对](t23-contract-gate/task.md) | T01、T09、T19、T22 | tests/test_task_api_contract.py |
| [T24 多任务竞争与持久化集成验收](t24-integration-races/task.md) | T22、T23 | tests/test_task_integration.py |
| [T25 打包新模块且排除运行数据](t25-package/task.md) | T22、T23 | tests/test_task_package.py |
| [T26 部署包真实 HTTP 离线冒烟](t26-packaged-http/task.md) | T24、T25 | tests/test_task_packaged_http.py |
| [T27 运行与离线交付说明](t27-handoff-docs/task.md) | T23、T25、T26 | tests/test_task_package.py |
| [T28 真实模型及业务样例验收入口](t28-business-acceptance/task.md) | T24、T26、T27 | tests/test_live_validation_cli.py |

## 模块边界

| 文件 | 修改任务 | 职责 |
| --- | --- | --- |
| src/task_models.py | T01 | 见对应任务的稳定接口及 diff 边界 |
| src/task_settings.py | T02 | 见对应任务的稳定接口及 diff 边界 |
| src/task_store.py | T03、T04、T05、T06 | 见对应任务的稳定接口及 diff 边界 |
| src/task_files.py | T07 | 见对应任务的稳定接口及 diff 边界 |
| src/task_api.py | T08、T09、T22 | 见对应任务的稳定接口及 diff 边界 |
| src/main.py | T08、T09、T14、T22 | 见对应任务的稳定接口及 diff 边界 |
| src/requirements.txt | T09、T10、T11 | 见对应任务的稳定接口及 diff 边界 |
| src/model_client.py | T10 | 见对应任务的稳定接口及 diff 边界 |
| src/pdf_reader.py | T12 | 见对应任务的稳定接口及 diff 边界 |
| src/approval_extractor.py | T13 | 见对应任务的稳定接口及 diff 边界 |
| src/checklist_rules.py | T15 | 见对应任务的稳定接口及 diff 边界 |
| src/material_facts.py | T16 | 见对应任务的稳定接口及 diff 边界 |
| src/material_generator.py | T17、T18 | 见对应任务的稳定接口及 diff 边界 |
| src/material_result.py | T19 | 见对应任务的稳定接口及 diff 边界 |
| src/task_pipeline.py | T20 | 见对应任务的稳定接口及 diff 边界 |
| src/task_worker.py | T21、T22 | 见对应任务的稳定接口及 diff 边界 |
| package.py | T25 | 见对应任务的稳定接口及 diff 边界 |
| smoke_check.py | T26 | 见对应任务的稳定接口及 diff 边界 |
| src/verify_task_live.py | T28 | 见对应任务的稳定接口及 diff 边界 |

新模块间只用直接函数/明确字典或数据模型，不建单实现接口工厂。推荐内部接口约定：

- TaskStore：accept_task → generationId/被替换代次；claim_next；get_task；save_artifact；finish_done/failed；recover_interrupted。覆盖、领取及终态写回均事务化。
- 规则索引：productName、内部唯一 ruleKey、sourceRuleId、sourcePosition、原行与分组上下文。sourceRuleId 可重复，ruleKey 不可重复。
- 提取：页记录保留页号/可读状态/全文及表格，事实保留 present/明确否定/来源/主体/产品；缺值不能伪装成否定。
- 生成：逐 ruleKey 返回 KEEP/EXCLUDE/CHECK 及对象实例和来源支持；完整决定集合先校验，再投影平铺 data.items。
- Pipeline 返回完整 data；只有 worker 经 TaskStore 当前代次条件检查才能发布 DONE，不能在模型回调直接改状态。

## 验收覆盖矩阵

| Spec 验收 | 负责任务 |
| --- | --- |
| AC-01 | [T07](t07-upload-service/task.md)、[T09](t09-create-api/task.md)、[T22](t22-app-lifecycle/task.md)、[T24](t24-integration-races/task.md)、[T26](t26-packaged-http/task.md) |
| AC-02 | [T01](t01-contracts/task.md)、[T07](t07-upload-service/task.md)、[T09](t09-create-api/task.md) |
| AC-03 | [T04](t04-queue-transactions/task.md)、[T07](t07-upload-service/task.md)、[T09](t09-create-api/task.md)、[T24](t24-integration-races/task.md) |
| AC-04 | [T05](t05-state-cas/task.md)、[T21](t21-serial-worker/task.md) |
| AC-05 | [T04](t04-queue-transactions/task.md)、[T21](t21-serial-worker/task.md)、[T24](t24-integration-races/task.md) |
| AC-06 | [T04](t04-queue-transactions/task.md)、[T21](t21-serial-worker/task.md)、[T24](t24-integration-races/task.md) |
| AC-07 | [T05](t05-state-cas/task.md)、[T21](t21-serial-worker/task.md)、[T22](t22-app-lifecycle/task.md)、[T24](t24-integration-races/task.md) |
| AC-08 | [T03](t03-store-base/task.md)、[T04](t04-queue-transactions/task.md)、[T24](t24-integration-races/task.md) |
| AC-09 | [T06](t06-restart-recovery/task.md)、[T22](t22-app-lifecycle/task.md)、[T24](t24-integration-races/task.md)、[T26](t26-packaged-http/task.md) |
| AC-10 | [T11](t11-pdf-backend/task.md)、[T12](t12-pdf-pages/task.md)、[T13](t13-approval-extraction/task.md)、[T20](t20-pipeline/task.md)、[T28](t28-business-acceptance/task.md) |
| AC-11 | [T13](t13-approval-extraction/task.md)、[T14](t14-checklist-snapshot/task.md)、[T20](t20-pipeline/task.md)、[T28](t28-business-acceptance/task.md) |
| AC-12 | [T16](t16-fact-resolution/task.md)、[T28](t28-business-acceptance/task.md) |
| AC-13 | [T16](t16-fact-resolution/task.md)、[T28](t28-business-acceptance/task.md) |
| AC-14 | [T15](t15-rule-inventory/task.md)、[T18](t18-decision-validation/task.md)、[T28](t28-business-acceptance/task.md) |
| AC-15 | [T19](t19-result-projection/task.md)、[T28](t28-business-acceptance/task.md) |
| AC-16 | [T19](t19-result-projection/task.md)、[T28](t28-business-acceptance/task.md) |
| AC-17 | [T15](t15-rule-inventory/task.md)、[T17](t17-generation/task.md)、[T18](t18-decision-validation/task.md)、[T24](t24-integration-races/task.md)、[T28](t28-business-acceptance/task.md) |
| AC-18 | [T10](t10-model-transport/task.md)、[T17](t17-generation/task.md)、[T18](t18-decision-validation/task.md)、[T20](t20-pipeline/task.md)、[T24](t24-integration-races/task.md)、[T28](t28-business-acceptance/task.md) |
| AC-19 | [T01](t01-contracts/task.md)、[T08](t08-query-api/task.md)、[T23](t23-contract-gate/task.md)、[T26](t26-packaged-http/task.md) |
| AC-20 | [T14](t14-checklist-snapshot/task.md)、[T25](t25-package/task.md)、[T26](t26-packaged-http/task.md)、[T27](t27-handoff-docs/task.md) |

## 全量交付检查（T26/T27 后）

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw '单元和离线集成验收失败' }
.\.venv\Scripts\python.exe -B package.py
if ($LASTEXITCODE -ne 0) { throw '打包失败' }
.\.venv\Scripts\python.exe -B smoke_check.py
if ($LASTEXITCODE -ne 0) { throw '旧检索部署包冒烟失败' }
```

新任务部署包 HTTP 冒烟由 T26 的 test_task_packaged_http.py 执行；不能只凭旧 smoke_check 通过就认定新功能可用。真实模型、OCR 与材料适用性最终由 T28 和业务人员确认。

## 本轮计划核验

计划文档生成后核对：任务编号/链接/依赖无环、FR/AC 覆盖、每任务有 diff 边界与验收命令。除现有 test_service.py 基线外，未执行尚未创建的未来测试，也未安装新依赖、修改业务实现或调用模型。

本轮结构核验：28 个 task.md、59 条依赖边无环、188 处本地链接可定位，覆盖全部 15 条 FR 和 20 条 AC；28 个定向命令均有测试文件存在检查，30 段 PowerShell 命令已做语法解析。该检查验证任务文档，不代表未来实现或模型业务验收已完成。



## T01 / T02 实施记录（2026-10-23）

- T01 定向 9 项测试通过；T02 定向 7 项测试通过。
- 最终全量命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`，退出码 0，`Ran 42 tests in 6.365s` / `OK`（原检索 26 项、新增 16 项）。四个 Python 文件语法及 OpenAPI JSON 解析通过。
- 用户确认：未知字段原样保留，空合同号接受；存储目录 src/task_storage，180 秒单请求超时，额外重试 2 次，不设 token 预算。
- 只完成模型与配置模块；创建/查询 HTTP、数据库、模型 transport 及生成流水线属于后续任务。真实模型校验按用户决定稍后进行。

## T03 / T04 实施记录（2026-10-23）

- T03 定向 5 项、T04 定向 10 项通过；均先确认缺失实现导致的行为失败，再实现并验证。
- 全量命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`，退出码 0，`Ran 57 tests in 7.797s` / `OK`（原有 42 项及新增 15 项）。
- 新增本地 SQLite TaskStore：复合身份、代次文件指针、持久顺序号、原子同业务替换与同键覆盖。使用真实临时 SQLite 验证 FIFO、并发接收、写入/提交失败完整回滚。
- 验收细节见 T03/T04 文档。仅在当前目录开发和自查；没有新依赖、Git 初始化、其他任务/子代理、运行数据库、打包或部署。T05 及后续功能待实施。

## T07 / T08 实施记录（2026-10-23）

- T07 新增 PDF 接收服务，T08 新增只读查询路由及 create_app 存储注入，功能范围见各自任务文档。
- 用户确认：PDF 必须从起始字节具备合法版本头；T08 未注入存储时返回 503，默认存储留 T22；提交后取消钩子失败记录日志且保留成功。
- T07 定向 11 项、T08 定向 7 项、旧检索定向 26 项通过；新增实现均先确认缺失行为失败。全量命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`，退出码 0，`Ran 89 tests in 8.376s` / `OK`。
- 当前目录完成开发、自查及离线测试，无子代理、工作树、新依赖、Git 初始化、打包、部署或真实模型调用。上传 HTTP 接口与默认生命周期分别待 T09、T22。

## T09 / T10 实施记录（2026-10-23）

- T09 完成 multipart 创建接口、提交和取消钩子注入、局部错误映射及运行时上传 OpenAPI；T10 完成两阶段 Chat Completions 异步 HTTP 调用、有限重试、响应完整性检查与本地取消。
- 用户确认严格 part 校验；网络/408/429/5xx 重试等待 1、2 秒，其他 4xx 不重试；仅 stop 加非空正文视为完整，内容问题不在 T10 修复或重试。
- T09 定向 9 项、T10 定向 8 项、旧检索回归 26 项通过；新增功能均先执行失败测试。最终全量命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`，退出码 0，`Ran 106 tests in 9.446s` / `OK`。
- 固定 python-multipart==0.0.32 和 httpx==0.28.1；pip check 无依赖冲突，五个新增/修改 Python 文件语法检查通过。详情见 T09/T10 验收记录。
- 当前目录完成开发及自查，无子代理、工作树、Git 初始化、用户 PDF、打包、部署或真实模型调用。默认存储及后台工作单元由 T22 接入，真实模型业务验收仍待 T28。


## T11 / T12 实施记录（2026-10-23）

- T11 锁定 pypdfium2==5.14.0；官方来源、许可证、Windows 实测环境与 Linux/离线限制见 [PDF 后端决定](pdf-backend-decision.md)。
- T12 按用户确认的方案 A，逐页保留原文本和整页 RGB PNG；扫描、混合、文字覆盖层均不跳过。读取失败整体报错，逐页及等待锁时响应取消，无固定 100 页上限。视觉和流水线尚未接入。
- T11 定向 3 项、T12 定向 8 项通过。全量命令：` .\.venv\Scripts\python.exe -B -m unittest discover -s tests -v `，退出码 0，Ran 117 tests in 10.891s / OK（既有 106 项及新增 11 项）。pip check 无依赖冲突，4 个新增 Python 文件语法检查通过。
- 当前目录完成开发和自查，无子代理、工作树、Git 初始化、客户 PDF、运行数据库、打包或部署。目标 Docker、内网离线安装及真实模型业务验收未执行。

## T15 / T16 实施记录（2026-10-23）

- T15 完成原规则明细索引，逐行核对全部 75 份真实清单的 9,512 条明细，最长 204 条；保留原编号、单元格换行、合并分组、核查标记及稳定唯一 ruleKey。
- T16 按本次用户澄清完成批复优先事实与对象关联：无产品关联的 JSON 对象套用全部批复产品、全空押品忽略、类型未知保持未知、冲突采用后出现的明确事实；T13 提取提示补充关键事实命名及原文顺序。
- 定向 T15 4 项、T16 16 项、T13 11 项均通过；最终全量 `.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q`，`Ran 154 tests in 37.508s` / `OK`，退出码 0。6 个新增/修改 Python 文件 AST 解析通过，pip check 无依赖冲突。详细接口和限制见各 task.md。
- 在当前目录完成开发、自查及离线验收，无子代理、工作树、新依赖、Git 初始化、清单原件修改、真实模型调用、打包或部署。后续任务状态见各 task.md。

## T17 / T18 实施记录（2026-10-23）

- 新增 material_generator：完整三部分上下文分批生成，有限整批结构修复，取消与整体失败；独立校验原规则覆盖、KEEP/EXCLUDE/CHECK、来源及声明对象范围内的完整展开。没有接入 T19 公开投影或 T20 流水线。
- 用户确认不设上下文/输出容量上限、不做调用前容量检查；结构修复复用 MODEL_MAX_RETRIES；对象范围判断及内容业务准确性留给 T28。
- T17 定向 12 项、T18 定向 17 项通过，先观察缺少实现导致的失败；覆盖 73 条规则及真实最长清单、60+材料、漏规则/漏对象/伪造主体/核查伪装/未知误排、有限修复、截断与取消。
- 全量 `.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q`，`Ran 183 tests in 37.526s` / `OK`，退出码 0。三个新增 Python 文件 AST、pip check 通过；当前目录完成自查，无子代理、新依赖、真实模型调用或部署。


## T19 / T20 实施记录（2026-10-23）

- T19 完成平铺结果、产品/主体不合并、个性化名称补齐、展示默认值、连续编号及最终 TaskResult 校验。T20 完成页面提取到结果投影的完整异步流水线，阶段文件按 generationId 隔离并经 TaskStore 条件登记。
- 用户确认：材料名称只补缺失的主体及要求；模型非空展示字段保留，语义和来源准确性待 T28；提取、原始清单快照/哈希、规则、事实及内部决定保存到代次目录。具体接口与限制见各任务记录。
- 实现前 T19 的 6 项、T20 的 10 项因实现缺失明确失败。最终定向 T19 7 项、T20 16 项全部通过；复核补测复现并修正重复取消导致读取线程提前脱离等待的问题。
- 全量命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q`，退出码 0，`Ran 206 tests in 64.708s` / `OK`（原有 183 项及新增 23 项，包含旧检索回归）。asyncio 调试模式对既有最长清单测试输出一次慢任务提示，没有失败。
- 功能文件仅新增 src/material_result.py、src/task_pipeline.py 及对应测试；没有修改已有业务模块、用户 list、API 或配置依赖。当前目录自查，无 Git 初始化、子代理、真实模型、运行数据库、打包或部署。
- 流水线仅返回完整 data，不发布终态。T21 工作单元和 T22 生命周期待接入；真实识别及材料业务准确性待 T28。
