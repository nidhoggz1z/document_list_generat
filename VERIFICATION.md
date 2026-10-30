# 验证记录

最新记录为下方“产品清单名称变体匹配与真实完整流程”；其他记录保留为历史证据。

## 产品清单名称变体匹配与真实完整流程（2026-10-30）

用户授权放宽个性化任务的产品清单对应逻辑。实现同名直取、非同名由生成模型从现有完整目录中选择唯一对应项，保存 product-matches；批复原名、事实与证据保留，快照采用实际 Word 原始字节，结果 checklistFileName 返回实际文件。旧 retrieve 行为保持。实测前置提取再次失败后，补充安全校验代码及沿用 MODEL_MAX_RETRIES 的有限纠错，严格引用和页覆盖校验不变。

- 最终代码全量回归 363 项通过，135.875 秒，进程退出码 0，记录为 verification-product-matching-final-tests.log。
- 真实测试使用用户原 JSON、真实 PDF URL、现有模型配置和原 list，未使用模拟、缓存提取、历史响应或重命名清单。最终 attempt4 返回 DONE，689.2 秒，51 条材料。
- 实际匹配“法人房产按揭贷款”到“法人按揭贷款材料清单.docx”，Word 原件哈希核对一致。输入字节、持久化业务对象及下载原件均核对一致。
- 169 条规则已全部形成决定；Word 阶段 43 个材料实例，话术合并后 51 条。最终响应契约、连续编号、非空标题和新输出字段检查通过。
- 存在两组需业务复核的完全同名材料：林斌身份证件 R0009/R0010，婚姻证明 R0011/R0012。保持真实输出，未手工删改；不认定业务去重及适用性已验收。
- src.tar 已更新且变更模块与源码一致；smoke_check.py 通过 75 项目的完整正文及运行中更新检查。部署包不含运行日志与测试数据。

结果目录：manual_test_20261030_json_url/attempt4_matching_with_extraction_repair。查看该目录测试报告、query-response.json、result.json、materials.txt；父目录最新测试结果.md 指向本轮，早期失败记录保留。

## 原 JSON 与 URL 下载真实测试（2026-10-30）

使用用户新提供的瑞奕熙业务 JSON 和 `http://127.0.0.1:18765/ruiyixi-approval.pdf`，保持生产代码、输入、模型配置和原清单不变，通过真实 main:app 的 HTTP 接口进行隔离测试。结果：**完整流程未通过，未生成最终材料 JSON。**

创建、JSON 保存、URL 下载及 PDF 页面读取通过，下载原件与输入均核对一致。第一轮被运行环境禁止外网连接（Windows 10013）；获得网络运行权限后第二轮调用真实模型，89.6 秒后因批复提取校验失败返回 FAILED。独立真实提取诊断随后通过两页校验，识别 1 产品/55 事实；没有将其回填到失败任务。

另外确定清单名称不匹配：JSON/诊断提取的“法人房产按揭贷款”在原 list 中无严格匹配，仅有“法人按揭贷款材料清单.docx”。继续测试前需用户确认是否为同一产品及允许使用该清单。本轮未重命名、添加映射或放宽校验。

输入、接口响应、原件、各轮独立存储及诊断报告保存在 `manual_test_20261030_json_url`，详见该目录 `测试报告.md` 与 `overall-report.json`。生成模型阶段未进入，不能把此前离线回归通过视为本次真实业务通过。

## 按北京时间每日 TXT 日志（2026-10-30）

用户确认采用推荐规则：程序同级 log 目录，每天一个 UTF-8 TXT，文件名 YYYY-MM-DD，按北京时间切换；同日及重启后追加，长期保留，不自动删除。开发路径为 `src/log`，部署后为程序同级 `log`（Docker 为 `/home/app/log`）。

使用 Python 标准 logging 实现，不增加依赖。记录服务启停、接口方法/路由/状态码/耗时、PDF 下载、任务阶段及耗时、完成/失败/取消/替换和安全错误摘要；只选择业务编号、任务编号和代次编号关联，不采集完整业务 JSON、PDF 正文、模型输入输出、密钥、下载链接或查询参数。日志写入失败回退控制台，关闭日志时的磁盘错误不影响服务清理。业务输入、状态及结果规则保持原样。

- 全量回归：354 项通过，进程退出码 0，144.945 秒。完整输出为根目录 `verification-daily-log-tests.log`。
- 日志专项：最终 7 项通过；覆盖北京时间零点、准确日期文件名、中文 UTF-8、同日和重启追加、重复初始化、并发写入、目录不可写、磁盘 flush 失败、HTTP 摘要、下载、完整任务阶段、失败与替换、正文/密钥排除。
- 任务 worker、pipeline、原件接收分别复核 6、20、11 项通过；原来的取消、原子保存及持久化校验保持。
- 全量回归包含源码与部署包真实 HTTP 创建/查询/重启检查，并断言服务实际生成日期 TXT；打包测试明确排除运行日志，验证导入或打包本身不创建日志。
- `src.tar` 已重新生成，全部变更模块与当前源码字节一致，包内无 log 运行数据。README、版本管理及 Docker 构建上下文排除规则已同步。
- 最终部署包 `smoke_check.py` 通过：75 个项目的 HTTP 检索及完整正文一致，运行中新增、覆盖、重复与恢复、改名、损坏与恢复、删除检查通过。

`src/log/2026-10-30.txt` 已由本地验证过程生成，其中业务编号为测试数据；未启动常驻服务，也未调用真实模型或真实内网文件链接。

## JSON 输入与 URL 下载（2026-10-29）

用户确认保持原 JSON 结构，`replyImageUrl` 指向可直接下载的单个 PDF；部署环境为内网，开发机不在内网。创建入口改为 application/json，先下载、校验和保存，再原子创建任务。下载失败不创建任务、不替换旧任务。10 MiB 上限与后续处理规则保持不变。

- 全量回归：`python -B -m unittest discover -s tests -v`，348 项通过，最终进程退出码 0（203.645 秒）。完整输出见根目录 `verification-json-url-tests.log`。
- 接口专项复核：`python -B -m unittest discover -s tests -p 'test_task_*api*.py' -q`，22 项通过。
- 下载测试覆盖内网形式 URL、重定向、无效 URL、HTTP/连接错误、网络及整体超时、传输中断、空文件、非 PDF、大小上限，以及失败和等待期间旧任务保持不变。
- 集成回归覆盖完整 PDF/双模型生成、任务替换、历史查询和重启恢复；源码与 src.tar 解包服务均通过真实本地 HTTP 下载 PDF，404 下载失败不修改已有 DONE 结果。
- `package.py` 已重新生成 src.tar；包内 main.py、task_api.py、task_files.py、task_models.py、verify_task_live.py 与当前源码字节一致。
- `smoke_check.py` 通过：75 个项目 HTTP 检索与完整正文均核对成功，运行中新增、覆盖、重复及恢复、改名、损坏及恢复、删除检查通过。
- 接口文档、OpenAPI、JSON 样例及验收工具已同步。验收工具只发送 JSON，`--pdf` 为可选本地核对原件，不再上传。

未验证真实内网文件链接的连通性，也未进行本轮真实模型业务验收；开发阶段以本地 HTTP 文件服务及离线模型验证流程。部署后需由内网服务访问实际 replyImageUrl 核验网络和文件权限。

## 输入确认与移除对外 PDF 哈希（2026-10-28）

用户确认继续提交业务 JSON＋PDF 文件，`grtstlRcmbNm` 继续过滤一级目录，本次不调整状态处理。33 个顶层输入字段和从合同/共同担保人/押品嵌套字段与当前模型一致，无需改动输入契约。

新生成结果的 `data` 只包含 `generatedAt`、`total`、`products`、`items`，不输出 `sourceDocumentHash`；四个 TXT 导出入口同步移除 PDF SHA-256。内部任务记录和处理前文件哈希校验仍保留。无哈希的新结果与含哈希的历史结果分别校验，历史树状和旧平铺结果沿用只读查询，不改写已有数据。验收客户端支持无公开哈希的新结果，历史响应若提供哈希仍校验其是否匹配。

先验证新增/调整用例在旧实现上失败，再修改实现。最终使用模拟数据和临时文档运行 22 个相关测试模块（排除读取真实最长清单的用例），255 项全部通过，包含源码及解包服务的真实本地 HTTP 创建、查询、替换及重启测试。未调用真实模型，未执行实际批复文件联调。

接口说明、OpenAPI 2.2.0、新结果 JSON 示例和 `src.tar` 已同步；部署包内 21 个 Python 模块与源码逐字节一致，原包内 76 份清单/话术库文件哈希保持一致。

## 输出格式精简（2026-10-28）

按用户最终确认：新材料标题为 `[产品名]材料名称（主体）（具体要求）`；JSON、TXT 移除 `reason`、`sourceRuleId`，不新增“发布依据”；保留原默认值。支持同一行和分行元数据标注，Word 同名标注优先。四个 TXT 导出入口统一按“具体要求、来源系统、制度依据、发布日期、发布部门”输出，历史 JSON 字段、标题和存档不改写。旧平铺结果继续沿用既有只读目录恢复逻辑。新结果验收客户端改用产品及完整材料标题计数，兼容历史原规则编号计数。

本次使用模拟数据、临时构造的文档及离线 HTTP 服务，在当前任务内装载材料生成/校验/投影、目录与历史查询、话术合并、严格审核、响应模型与接口契约、任务管线/集成/存储/worker 和验收客户端等 16 个测试模块；排除真实最长清单用例，206 项测试全部通过。新增格式用例先确认旧实现失败，再验证修改后通过；涵盖中文嵌套括号主体、已有个性化标题去重、同一行字段拆分、混装元数据、原默认值、历史查询原样返回及 TXT 导出不修改输入。未调用真实模型，未执行实际批复文件联调。

同步更新接口说明、OpenAPI、新结果示例及 `src.tar`；包内全部 21 个 Python 模块与当前源码逐字节一致，原包内 76 份清单/话术库文件的 SHA-256 保持一致。

## 上海雅宽严格审核版本真实用例（2026-10-27）

用户提供 TEST_TASK_20261027_002 的原业务 JSON 及两页上海雅宽批复，并在自动审批要求后明确授权发送至 https://api.deepseek.com（deepseek-flash）真实测试。原项目仅有“法人按揭贷款材料清单.docx”，批复产品原名为“法人房产按揭贷款”；本次使用正文明确适用该产品、字节完全相同的更名隔离副本，不改原 list、JSON 或既有任务存储。

全新提取、生成及话术匹配均实时调用，无历史回复回放。727 秒达到 DONE，12 次模型逻辑调用（含 2 次结构修复）。169 条 Word 规则中，79 条按一级目录排除；23 条明确保留，形成 28 个 Word 实例。19 条话术命中 2 条，合并后最终 32 项，R0001-R0032 连续。PDF 哈希、输入、字段、材料总数、编号及一级目录过滤均核对；原样 TXT、接口响应、核验 JSON 及测试报告已保存至用户指定 result 目录，文件名含“严格审核”，未覆盖旧结果。

本次不能认定业务审核通过：同一房产因 JSON/PDF 的不同表述被展开为两个对象，1.4.2、1.4.10、4.1.9、4.1.11 各有两份同一押品对应材料；R0008、R0009 还出现“现房”判断，而输入未明确现房/期房状态。测试报告保留这些问题，没有手工删改结果或改输入掩盖。完整本地运行与核验记录在 manual_test_20261027_002_strict。本次仅测试与导出，没有修改生产模块来消除这些问题。

## 严格排除与一级目录过滤（2026-10-27）

替代此前“未知整项保留”的业务口径，按用户确认使用 `grtstlRcmbNm` 过滤一级目录；无阶段词的目录继续参与筛选。放贷/放款归一，多个词任一匹配。Word 只保留明确适用的实例；话术逐子项审核，产品归属、适用条件或独有材料目录不明确时排除。后续补充不能恢复不匹配目录。

新增 15 项定向用例，先确认旧实现失败；同时更新与旧保留口径冲突的既有测试。覆盖缺失适用性标记、未知事实、对象遗漏/空关联、混合对象排除、不可靠排除引用、全部目录不匹配时不调用模型、一级与子目录冲突、多词匹配、无阶段词目录、话术的 Word 映射及 Excel 独有材料两条恢复路径。伪造保留来源、跨对象/跨产品和结构错误仍拒绝。

最终源码及重新打包后的实际执行均退出码 0：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 package.py
# Generated src.tar and openapi-hiagent.json; documents: 75
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -q
# Ran 332 tests in 89.038s / OK
.\.venv\Scripts\python.exe -B -X utf8 smoke_check.py
# http_project_requests=75, verified_text_contents=75, archive_docx_count=75
# live_updates=add/replace/duplicate_and_recover/rename/corrupt_and_recover/delete
```

另逐字节核对部署包的 material_generator.py、material_tree.py、phrase_materials.py、task_pipeline.py 与最终源码一致；包内没有 model-api.env、task_storage 或 manual_test 资料。未修改参考 Word/Excel 原件、实际模型配置及已完成历史结果。本轮使用离线假模型和本地真实 HTTP 验证，未调用外部模型；材料语义准确性仍需实际业务验收。全部材料排除时沿用非空结果约束返回 FAILED。部署服务需重启，新创建/重跑任务使用新口径。

日期：2026-10-21（Asia/Shanghai）。

## 本次已验证

- Python 3.12.9，26 项 unittest 全部通过。
- /retrieve 成功响应为 text/plain; charset=utf-8，响应体直接等于匹配文档正文，不含额外文件名标题、下载地址或 JSON 包装，也无 Content-Disposition 文件下载响应头。
- “流动资金贷款”“流动资金循环贷款”以及对应外汇项目分别返回各自唯一的材料清单。
- 只有循环贷款清单、缺少普通贷款清单时，查询“流动资金贷款”返回 404，不回退。
- 下划线分隔文件名按完整项目名称准确定位；简称、部分名称、旧类名、额外“材料清单”或 .docx 后缀均不匹配。
- 非字符串、空输入、缺少字段、路径和过长名称返回 422；重复项目、损坏清单、无效目录不静默忽略。
- 表格、段落换行、自动编号和样式编号通过样例验证。
- 全部 75 份真实清单逐一验证：每个原始 Word 文本节点均按原顺序存在于返回正文中。75 份 DOCX 均未发现 word/media 嵌入媒体文件。
- package.py 已重新生成 src.tar 和 openapi-hiagent.json。部署包包含 75 份 list 清单，无旧 data 文档、src 外层或 Python 缓存。
- 从最终 src.tar 解压并临时启动真实 Uvicorn 服务，完成 75 个项目的 HTTP 查询；逐一验证 UTF-8 纯文本响应与 list 对应 DOCX 解析正文完全一致，没有额外文件信息。
- 独立下载接口通过单元测试验证；检索接口不输出下载链接。
- 自动更新通过单元测试验证：新增、同名覆盖、改名、删除立即在下次请求生效；删除后不返回旧缓存或相似项目。
- 运行中发现重复清单返回 409，损坏或写入中的文档返回 503；删除冲突文件、完成写入或修复文件后自动恢复，无需重启。
- 并发请求返回完整新版正文；更新加载过程中再次发生文件变化时拒绝发布中间结果，下次请求可正常恢复。文件未变时复用已解析正文。
- 最终部署包的真实 HTTP 验证在同一 Uvicorn 进程持续运行时完成新增、覆盖、改名、删除、重复冲突及修复、损坏及修复；全部通过。仅修改临时解压目录，用户 list 原件未修改。
- 6 个非完整名称或旧格式请求经真实 HTTP 验证均返回 404。
- HiAgent 导入文件为 OpenAPI 3.0.3，版本 2.2.0；保留 retrieveDocuments 和 param；导入操作仅包含 /retrieve，200 响应仅声明 text/plain 字符串，无旧 DocumentOutput 结构，并描述更新时的 409/503 状态。
- /projects 返回项目名称与对应文件，/classes 保留兼容结构。
- 单元测试、打包和最终部署包验证退出码均为 0。临时验证服务已停止。
- Windows 真实 HTTP 验证结束时，Uvicorn 偶有底层连接重置日志（WinError 10054）；本次全部查询、更新验证和断言均已完成，退出码为 0。

## 尚未执行

- 未进行 Docker 镜像构建、容器部署或公司服务器联调；内部 pip 源和基础镜像的可用性需在部署环境验证。
- Docker 目录挂载的 Windows / Linux 命令已在 README 中提供，实际服务器挂载尚未验证。首次需部署本版本并将宿主机 list 挂载到 /home/app/list；后续文档变动无需重新打包、重建镜像或重启。
- 未修改 HiAgent 工作流。openapi-hiagent.json 中的 YOUR_SERVER_IP 需替换为部署服务器地址后重新导入；param 绑定完整项目名称，后续节点直接读取 HTTP body，不再解析旧 combined_content 等 JSON 字段。

## 复验命令

安装 README 所述依赖后，在项目根目录执行：

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B package.py
.\.venv\Scripts\python.exe -B smoke_check.py
~~~

服务不总结或截断正文；纯文本不复刻 Word 视觉排版，原始版式通过 DOCX 下载保留。

## 2026-10-23：T25–T28 与 spec 全量工程核验

依据 docs/sdd/tasks/spec.md（15 条 FR、20 条 AC），沿用已确认业务行为。T25 打包、T26 源码/解包真实 HTTP、T27 交付说明、T28 显式验收 CLI 已实现。T28 真实模型与业务签收按照用户本次决定仍待联调；自动比较只核对产品、原规则编号及实例数，不比较文字。

本次环境为 Windows、Python 3.12.9、项目 .venv。httpx==0.28.1、pypdfium2==5.14.0 与 src/requirements.txt 锁定一致，pip check 无依赖冲突。使用 unittest、临时 SQLite/文件、生成 PDF 和独立本地假模型；无真实供应商调用，无新增第三方依赖，无 Docker/目标服务器部署。

### 全量规格对照

下表“通过”只表示已运行的工程验证；涉及真实文本理解、OCR准确性及业务适用性的行均另列外部签收限制。

| FR | 验证依据（tests/ 下文件） | 结果/边界 |
| --- | --- | --- |
| FR-01 | test_task_models、test_task_create_api、test_task_packaged_http | JSON/PDF、标识、可选 null、10 MiB 边界和真实 multipart 通过 |
| FR-02 | test_task_create_api、test_task_submission、test_task_integration | 拒绝无效输入，错误响应及失败不替换通过 |
| FR-03 | test_task_submission、test_task_packaged_http | 文件/数据库持久后接收，不等模型通过 |
| FR-04 | test_task_store_queue、test_task_integration | 同键覆盖、代次隔离与历史保留通过 |
| FR-05 | test_task_worker、test_task_integration | FIFO、同业务替换、最多一个有效任务及事务竞争通过 |
| FR-06 | test_model_client、test_task_pipeline、test_task_worker | 各阶段取消、迟到写回隔离及重复取消通过 |
| FR-07 | test_task_store_transitions、test_task_query_api | 状态转换、完整结果原子发布及终态不可改通过 |
| FR-08 | test_task_store_recovery、test_task_app_lifecycle、test_task_packaged_http、test_task_package | 持久化、单实例锁、重启 FAILED、历史保留、打包排除通过 |
| FR-09 | test_pdf_reader、test_approval_extractor、test_checklist_snapshot | 页面/图像传送、严格匹配与快照通过；真实 OCR/产品识别待联调 |
| FR-10 | test_material_facts | 批复优先、未知押品、主体及产品范围通过；真实事实提取待业务核对 |
| FR-11 | test_checklist_rule_inventory、test_material_decisions、test_material_result | 原规则覆盖、核查/排除校验、展开及编号通过；模型声明的适用范围语义待签收 |
| FR-12 | test_material_result | 五展示字段原值保留和统一缺省值通过；原文来源语义待业务核对 |
| FR-13 | test_model_client、test_material_generation、test_material_decisions、test_task_pipeline | 完整上下文、有限重试/修复、截断、失败批次、空结果通过；供应商容量/视觉能力待联调 |
| FR-14 | test_task_query_api、test_task_api_contract、test_task_packaged_http | 各状态互斥响应、快照查询及不调用模型通过 |
| FR-15 | test_task_models、test_material_result、test_task_api_contract | 哈希、时区、产品、平铺条目、顺序及总数通过 |

| AC | 工程证据 | 结论 |
| --- | --- | --- |
| AC-01 | submission/create_api/packaged_http：持久接收即查，模型尚未完成 | 通过 |
| AC-02 | create_api/submission：标识、JSON、PDF和超限拒绝 | 通过 |
| AC-03 | store_queue/submission/integration：文件、事务及提交失败回滚 | 通过 |
| AC-04 | app_lifecycle/packaged_http/query_api：完成并重复只读查询 | 通过 |
| AC-05 | worker/integration：FIFO 三种替换及完成历史保留 | 通过 |
| AC-06 | store_queue/integration：并发成功提交顺序决定存活者 | 通过 |
| AC-07 | transitions/worker/integration：迟到成功及失败不可覆盖 | 通过 |
| AC-08 | store_queue/integration：相同及不同输入均覆盖重跑 | 通过 |
| AC-09 | recovery/app_lifecycle/packaged_http：强制中断后未完成失败、不重跑 | 通过 |
| AC-10 | pdf_reader/approval_extractor：扫描/混合/文本页面传送，损坏加密失败 | 离线通过，真实 OCR 待核对 |
| AC-11 | approval_extractor/checklist_snapshot/pipeline：产品依据、条件与严格匹配 | 离线通过，真实识别待核对 |
| AC-12 | material_facts/material_decisions：批复优先、姓名补缺及对象展开 | 离线通过，真实业务待核对 |
| AC-13 | material_facts/material_decisions：未知类型不判无押品 | 离线通过，真实业务待核对 |
| AC-14 | material_decisions：核查项/确定排除/未知保留 | 离线通过，实际适用性待签收 |
| AC-15 | material_result/models：跨产品及主体、前缀/编号/数量 | 结构通过，主体关联及名称语义待核对 |
| AC-16 | material_result：缺省、非 null及原值 | 结构通过，模型原文来源待核对 |
| AC-17 | material_generation/integration：真实最长清单规则及 60+ 条不截断 | 离线通过，真实模型完整性待核对 |
| AC-18 | model_client/material_generation/pipeline：JSON/漏规则/失败批次/零材料/取消 | 离线故障注入通过，真实供应商待联调 |
| AC-19 | task_api_contract/query_api/packaged_http：运行接口、OpenAPI与样例 | 通过 |
| AC-20 | service/task_package/smoke_check：75 原清单、严格检索、自动更新、排除运行数据 | 通过 |

### 交付检查与限制

- T25 测试先复现旧包遗漏业务模块，再验证全部 src 根 Python 模块可导入、依赖文件及原 DOCX 字节一致；临时 .env、Key、SQLite、PDF、data、缓存均不进入包，打包不启动 worker 或校验真实模型。
- T26 使用真实 Uvicorn 子进程和独立本地模型 HTTP server，源码及解包各跑相同契约，包含 DONE、替换 TERMINATED、PROCESSING/PENDING 强制中断后重启 FAILED、历史保留且查询不重调模型。服务和测试线程均清理；Windows 子进程隐藏启动。
- T28 输入无效/缺文件/服务不可达不提交，数量差异、缺展开、额外产品及规则、失败/替换、轮询超时、无效响应均报告失败；报告不可覆盖输入。expected 不发送给服务或改写。数量匹配仍保持人工待签收。
- 75 个真实项目的最终部署包 HTTP 响应与 DOCX 解析正文逐一一致；同一服务内新增、覆盖、重复冲突及恢复、改名、损坏及恢复、删除全部通过。
- 全量 unittest 的最长清单用例可能输出 asyncio 慢任务诊断，不表示失败；不构成性能 SLA。真实模型 API Key、用户 PDF、运行存储未放入交付包。
- 离线 wheel 准备/安装流程、Docker 持久卷及环境注入已写入 README，尚未在目标离线服务器实测。无 Git 仓库，未初始化/提交；未创建子代理或独立 Codex 任务。

真实样例与签收填写 docs/sdd/业务验收记录.md；不能以此工程全量通过替代业务验收。

### 最终实际命令与结果（2026-10-23）

```powershell
.\.venv\Scripts\python.exe -B package.py
# Generated src.tar and openapi-hiagent.json; documents: 75
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
# Ran 248 tests in 74.164s / OK
.\.venv\Scripts\python.exe -B smoke_check.py
# http_project_requests=75, verified_text_contents=75, archive_docx_count=75
# live_updates=add/replace/duplicate_and_recover/rename/corrupt_and_recover/delete
.\.venv\Scripts\python.exe -B -m pip check
# No broken requirements found.
.\.venv\Scripts\python.exe -B src/verify_task_live.py --help
# 必需参数及客户端等待/轮询参数均正常输出
```

以上命令退出码均为 0。另以 AST 解析 49 个 Python 文件通过；最终 src.tar 包含 17 个根 Python 模块、requirements.txt、start.sh 及 75 份 DOCX，模块及原始清单逐字节一致，无 src 外层、运行数据库、上传 PDF、密钥文件或缓存。新验收 CLI 已纳入最终包。

T25 定向 1 项通过，T26 定向 1 项通过（内部两组 source/packaged 真实 HTTP 子场景），T28 定向 10 项通过；最终 248 项包含全部旧功能及新增检查。测试先观察 T25 旧打包遗漏、T28 入口缺失导致的失败；复核另复现并修正异常健康响应导致 CLI 无报告退出的问题。最终全量回归已在这些修正后执行。

本次以当前任务内自查完成代码审阅，无并行代理；规则原件未修改。全局进程枚举因系统只读权限不足未执行成功；测试通过自行持有的进程句柄完成 kill/terminate + wait，未依赖全局枚举判断清理成功。

## 模型配置文件自动加载（2026-10-26）

- 新增项目根目录 `model-api.env`，默认填写 DeepSeek 接口与 `deepseek-flash`，两处 Key 保留占位符。启动时读取；环境变量优先，显式空值也覆盖文件；不修改进程环境，运行中的配置不随文件编辑变化，重启后重新读取。
- 源码运行从项目根目录读取，解包运行从 `main.py` 同级读取；验证从其他工作目录启动、解包目录本身名为 src 的情况。支持 UTF-8/BOM、空白、独立注释、英文单/双引号；缺失文件兼容原环境变量配置。格式、编码、权限错误均返回不含 Key 的提示，并保持检索与历史查询可用。
- 定向测试先复现缺少文件加载及格式错误检查，再验证实现；新增 6 项配置测试及 1 项生命周期测试。原真实 HTTP 部署包测试改为单独放入配置文件，不通过环境变量提供模型地址、Key 或模型名，完成创建、生成、查询和重启恢复。
- 当前任务内自查配置读取、错误脱敏、路径定位及打包边界，无新增依赖、无子代理。真实 Key 不进入 Git 或 Docker 构建上下文；`src.tar` 白名单打包继续排除根目录及 src 下的配置文件。README 更新了文件维护和 Docker 只读挂载方式。

实际验证（全部退出码 0）：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_task_settings.py -v
# 13 项通过
.\.venv\Scripts\python.exe -B package.py
# Generated src.tar and openapi-hiagent.json; documents: 75
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
# Ran 255 tests in 66.476s / OK
.\.venv\Scripts\python.exe -B smoke_check.py
# 75 份正文及运行中新增、覆盖、重复恢复、改名、损坏恢复、删除全部通过
.\.venv\Scripts\python.exe -B -m pip check
# No broken requirements found.
```

另检查部署包中的 `task_settings.py` 与当前源码逐字节一致，且不包含 `model-api.env`。未填真实 Key，未调用 DeepSeek，未实测 Docker 或目标服务器部署；真实模型和业务准确性仍需单独联调验收。

## 话术库补充材料（2026-10-26）

依据用户对十二项澄清的答复，新增强制 Excel 来源，决策及实施清单见 `docs/sdd/话术库补充材料实施说明.md`。原件解析为19条话术、94个材料子项；重复话术序号按物理行区分，不修改原件。没有新增依赖或接口字段。

离线检查覆盖：逐行完整处理、未命中不补、连续原文页号引用、伪造引用拒绝、命中整包覆盖、Excel优先于Word排除决定、核查项明确要求的材料提升、Word名称编号及元数据保留、通用材料细化主体去重、多主体和产品隔离、归属未知覆盖全部产品、分批完整上下文及重试/截断/取消、Excel缺失/损坏/更新及任务快照固定。创建/查询HTTP验证命中后补齐材料，缺少整包任一材料时FAILED且没有部分结果，已有历史保持。

测试先复现缺少Excel支持、旧包漏收Excel、通用材料与已知主体重复、纯名称重复及来源分组污染材料名称，再实现和修正。全量回归首次发现两处旧夹具缺少Excel或调用次数预期未更新，修正后重新执行全量。当前任务内完成代码自查，未启用子代理。

最终实际执行，以下命令均退出码0：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_phrase_materials.py -q
# Ran 19 tests / OK
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_task_pipeline.py -q
# Ran 19 tests / OK
.\.venv\Scripts\python.exe -B package.py
# Generated src.tar and openapi-hiagent.json; documents: 75
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
# Ran 278 tests in 83.676s / OK
.\.venv\Scripts\python.exe -B smoke_check.py
# http_project_requests=75, verified_text_contents=75, archive_docx_count=75
# live_updates=add/replace/duplicate_and_recover/rename/corrupt_and_recover/delete
.\.venv\Scripts\python.exe -B -m pip check
# No broken requirements found.
```

另以AST解析50个源码/测试Python文件，核对部署包中19个Python模块、75份DOCX及1份XLSX与当前原文件逐字节一致；无运行数据、模型配置/Key或缓存进入部署包。全量测试包含源码及解包服务的真实本地HTTP检查。

本次真实模型调用0次，未实测Docker或目标服务器部署。话术语义、否定/豁免识别、复杂条件完整命中、产品主体归属及材料等效映射仍由模型判断；程序校验连续引用存在及结构完整，不能据此证明判断正确，必须以真实批复人工验收。见 `docs/sdd/业务验收记录.md`。

## 目录树输出与历史只读转换（2026-10-27）

依据用户对十项澄清的答复，DONE的data.items替换为产品目录树；原材料叶子字段和默认值保留，total统计叶子，itemId按目录展示顺序连续编号。保留Word原目录编号/名称和多层结构，剪除空目录，不增加主体目录；Excel合并材料沿用Word目录，独有材料由模型从本产品完整目录中选择，不确定时归最后一个一级目录（包括原本没有明细的最后标题）。目录选择按原件白名单校验，不接受模型编造目录。

历史查询统一返回树，不改写原数据库，不读取当前list或调用模型。优先从原代次的合并规则/规则索引和Word快照恢复目录，验证路径范围、代次和哈希；相同编号存在歧义时用原实例名称辅助，无法唯一恢复则归未分类材料，材料不丢失。旧Excel无目录时使用可恢复的原Word最后一级目录。快照/索引缺失、损坏和不完整回退、历史材料/生成时间/哈希不变均有离线验证。

当前75份参考Word使用“材料名称”表头，旧索引器仅接受“材料名称或核查事项”，实际复现并新增测试后兼容两种表头，其他列、布局和索引完整性仍严格校验。现有Word单元格新增显式换行，原件比对测试相应保留换行；未修改参考原件。目录名称中的段落换行也有专门回归，保持与规则索引一致。

新增目录树7项、历史转换10项、Excel目录选择2项、表头兼容1项；更新原测试的树遍历、任务产物和本地HTTP模型夹具。先观察目录树/历史转换缺失导致的失败，模型目录白名单及兜底测试、显式换行和不完整合并索引恢复测试均完成失败后修正。第一轮全量294项通过，新增后最终重新执行298项。未新增依赖或子代理，当前任务内完成代码自查。

接口文档及personalized-checklist.openapi.json已同步递归模型（契约版本2.0.0），创建及非DONE响应不变。generate.response.json为多产品树示例，generate.tree.response.json为5条材料完整示例，两者均经过实际响应模型校验。验收CLI递归统计实际材料，人工expected文件继续逐条列材料，数量一致仍不证明语义或目录归类准确。

最终实际命令均退出码0：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_material_tree.py -q
# Ran 7 tests / OK
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_material_history.py -q
# Ran 10 tests / OK
.\.venv\Scripts\python.exe -B package.py
# Generated src.tar and openapi-hiagent.json; documents: 75
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
# Ran 298 tests in 77.570s / OK
.\.venv\Scripts\python.exe -B smoke_check.py
# http_project_requests=75, verified_text_contents=75, archive_docx_count=75
# live_updates=add/replace/duplicate_and_recover/rename/corrupt_and_recover/delete
.\.venv\Scripts\python.exe -B -m pip check
# No broken requirements found.
```

另AST解析54个源码/测试Python文件，核对src.tar中21个Python模块、75份DOCX及1份XLSX与当前原文件逐字节一致，包内不含模型配置/Key、缓存或运行存储。全量回归含源码与解包服务的本地真实HTTP检查。此次未调用真实模型、未实测Docker或目标服务器；模型对Excel独有材料的目录分类仍待真实批复业务验收。

## 排除依据不足时整项保留（2026-10-27）

用户确认按整项处理：任何相关对象排除依据不足时，该规则作用范围内全部对象保留；全部排除依据充分时仍可排除。随后确认，排除引文与原文不一致、对象展开遗漏两类也整项保留。共享校验同时规范化 `EXCLUDE` 和 `KEEP.excludedObjects`，生成与流水线最终校验均生效。保留实例的来源真实性、跨对象/产品隔离、规则覆盖、核查项类型和格式校验继续执行。沿用原 Word 的材料名称、提供要求、适用条件和制度依据；已有合法实例细化信息保留，对象展开遗漏时丢弃不完整细化并全部按原要求恢复，理由写明“排除依据不足，整项暂予保留，待人工确认”。

新增 10 项回归，验证整项恢复全部对象、保留原要求及展示字段、父范围、通用材料、幂等性、不可靠排除引文被丢弃、漏对象时原要求恢复、保留实例来源错误仍拒绝、无需模型修复即可投影，以及流水线最终校验后的产物保存。最后补齐“有保留实例时，其他有充分排除证据的对象也必须恢复”，先观察只返回 A 的失败，再修正为 A/B 都保留。旧的无依据排除和缺对象测试按新业务口径改为验证 KEEP，不再期待任务报错或模型修复。

修复两处既有测试样本选择：忽略 `~$` Word 临时文件，避免将临时锁文件计入数量或作为唯一打包清单；未删除用户临时文件，生产清单扫描规则未改。

实际执行：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_material_decisions.py -v
# Ran 26 tests / OK
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
# Ran 308 tests in 82.590s / OK
.\.venv\Scripts\python.exe -B -X utf8 package.py
# Generated src.tar and openapi-hiagent.json; documents: 75
.\.venv\Scripts\python.exe -B -X utf8 smoke_check.py
# http_project_requests=75, verified_text_contents=75, archive_docx_count=75
# live_updates=add/replace/duplicate_and_recover/rename/corrupt_and_recover/delete
```

离线重验留存的真实模型 `call-0009-generate-response.txt`：原第三批 20 条规则整体通过，原失败的 2.3.2、2.3.4、2.3.5 均变为 KEEP，各对原声明范围内 10 个对象保留，重复校验结果一致。完整记录在 `manual_test_20261027/exclusion-fallback-replay.json`。进一步重验第一版修改后新遇到的 2.1.4 对象遗漏和 2.2.3 排除引文不一致，按用户追加口径也通过；三批共 60 条规则的录制回复全部校验通过，回放清单在 `manual_test_20261027/approved-replay-manifest.json`。

已独立核对部署包 `material_generator.py` 与源码字节一致，包内没有模型配置、运行存储或人工测试资料。本节的模型回复重验不代表已完成全流程真实业务验收；后续真实重跑的结论另存 result 测试报告。

中间版本带回放续测达到 DONE：169 条 Word 规则、19 条话术规则全部处理，Word 阶段 484 个材料实例，话术命中 3 条，合并后 498 个材料叶子，R0001–R0498 连续。48 条 Word 规则按程序整项保留，275 个材料理由含该标记。原三项失败规则分别保留 10 个对象实例。

本次复用同一 PDF 已校验的提取快照和前 60 条规则的真实历史模型回复；回放严格核对完整输入上下文和回复哈希。余下 109 条规则与全部话术匹配实时调用 DeepSeek。本轮 15 次生成逻辑调用中 3 次回放、12 次真实模型 HTTP 调用，耗时约 933 秒；不是全新 OCR 到结果的完全实时调用。此前全新 OCR 的引文一致性失败和第一版修改的排除引用失败均保留原记录，最终续测数据在 `manual_test_20261027/after_fallback_final`，交付结果及报告位于用户指定的 `D:\10月重点项目\result`。

最终补齐已有保留对象时的整项恢复后，使用同一 PDF 提取快照及本轮全部成功真实回复，进行严格完整上下文和哈希匹配的全量回放。9 组 Word 回复、4 组话术回复经过真实本地 HTTP 接口和完整流水线后，7 秒达到 DONE，0 次新增外部模型调用。最终 Word 阶段为 509 个材料实例，合并后 523 个叶子，编号 R0001–R0523；66 条 Word 规则经程序整项保留，330 个最终材料带保留标记。带保留实例的 Word 决定全部没有 excludedObjects。原三个失败规则仍分别保留 10 个对象实例。全部文字和条数与 TXT 核对，最终产物位于 `manual_test_20261027/after_fallback_verified`；result 交付文件以这份 523 条结果为准。

## 未关联对象的通用材料兜底（2026-10-27）

上海宝杨茂业用例明确抵押而未列明具体押品，模型对材料 1.4.3 返回空对象编号，原校验在对象覆盖兜底前终止。新增 5 项测试，先观察未知押品、已知对象范围及生成投影 3 项在原代码下报错，再修改共享校验：无主体的空关联保留实例按对象遗漏处理，格式与来源校验通过后丢弃模型细化，按原清单对当前范围全部对象重建；不推测押品名称或类型。非空错误编号、虚构主体、重复实例及错误来源仍拒绝。提示词同步要求名称为空的已知对象仍引用其内部编号。

本次实际执行均退出码 0：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_material_decisions.py -v
# Ran 31 tests / OK
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_material*.py' -v
# Ran 87 tests / OK
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
# Ran 317 tests in 82.757s / OK
.\.venv\Scripts\python.exe -B -X utf8 package.py
# Generated src.tar and openapi-hiagent.json; documents: 75
.\.venv\Scripts\python.exe -B -X utf8 smoke_check.py
# 75 project requests and text contents verified; runtime updates verified
```

另核对部署包 `material_generator.py` 与源码逐字节一致，包内未包含模型配置。直接重验留存的真实回复：原失败项 1.4.3 通过且可投影为含原适用条件的待确认材料，关联对象为原 `0:pdf:collateral`，重复校验不变。原第二批各次回复均有 19/20 条通过；另一条 2.2.3 的保留引用与原文不一致仍被拒绝，未通过放宽来源校验掩盖。此为离线逐规则重验，不代表该批全部通过或完整任务完成，记录见 `manual_test_20261027_003/unknown-object-fix-verification.json`。修复后的实时重跑另存 `manual_test_20261027_003/after_unknown_object_fix`。

修复后的全新真实调用已达到 DONE：原 JSON、原两页 PDF，从提取到 Word 生成和话术匹配全部实时调用，未使用提取快照或历史回复回放。168 条 Word 规则通过，形成 167 个材料实例；19 条话术匹配完成，最终仍为 167 个材料叶子，编号 R0001–R0167。原失败项 1.4.3 使用真实未知押品对象，保留原条件及待人工确认理由。共 16 次模型逻辑调用（含两次材料结构修复），耗时约 995 秒。运行脚本与最终核验脚本均退出码 0，核对 PDF 哈希、业务输入原始字节、任务标识、材料总数、连续编号及 TXT 所有展示字段一致；结果在该目录，TXT、接口响应、修复后报告及核验记录另交付至用户指定 result。材料适用性仍需人工业务验收。
