# 材料清单检索与个性化任务服务

输入完整项目名称，严格匹配项目根目录 list 中的对应材料清单，直接返回文档的完整正文纯文本，不附加文件名、链接或其他元数据。当前共 75 个项目。

行内 Docker 离线交付使用根目录 `huanban_tar.sh`，直接从 `src` 和 `list` 构建镜像，不依赖 `src.tar`，不调用 `package.py`。默认端口为 `18008`，部署目录为 `/data/deploy_file-retrieval`，详见 [离线部署说明](DEPLOYMENT.md)。下文源码包及本地运行说明保留供对应场景使用。

为兼容行内 Docker 19.03.15 默认 seccomp，基础镜像固定为 `python:3.12-slim-bullseye`（glibc 2.31），保留现有线程机制。

## 匹配规则

| param 项目名称 | 唯一返回文件 |
| --- | --- |
| 流动资金贷款 | 流动资金贷款材料清单.docx |
| 流动资金循环贷款 | 流动资金循环贷款材料清单.docx |
| 外汇流动资金贷款 | 外汇流动资金贷款材料清单.docx |
| 外汇流动资金循环贷款 | 外汇流动资金循环贷款材料清单.docx |
| 个人委托贷款业务 | 个人委托贷款业务_材料清单.docx |

- 从文件名末尾去掉“材料清单”和其前面可选的一个下划线，得到可输入的项目名称；项目名称必须完整相等。
- 仅去除输入前后空白；名称内部的文字、空格、标点、括号、引号均须与项目名称一致。
- param 只填项目名称，不带“材料清单”或 .docx 后缀；不支持简称、关键词、包含匹配或语义推测。
- “流动资金贷款”不会匹配“流动资金循环贷款”或“外汇流动资金贷款”。即使正确清单缺失，也返回 404，不改用相似项目。
- 每次只返回对应的一份材料清单，不追加旧版共性审批要求。旧 src/data 目录不再参与检索或打包。
- 非字符串、空输入、路径输入或超过 100 字符返回 422；未知项目返回 404。启动时存在重复项目名称或损坏清单会使服务启动失败；运行中发现重复项目返回 409，损坏文件或更新未完成返回 503，修复后下次请求自动恢复。
- 仅加载以“材料清单.docx”结尾的文件，忽略 Word 临时文件（~$ 开头）。

## 项目结构

~~~text
文件检索/
  list/                      # 75份Word清单及话术库对应材料.xlsx
  src/
    main.py
    docx_reader.py
    task_*.py                # 持久化、API、流水线与串行工作单元
    model_client.py          # 可取消双模型 HTTP 调用
    pdf_reader.py            # PDFium 全页读取/渲染
    approval_extractor.py
    checklist_rules.py
    material_*.py
    phrase_*.py              # Excel话术快照、匹配、整包补充与合并
    verify_task_live.py       # 显式业务验收客户端
    requirements.txt
    start.sh
  package.py                 # 生成部署包和 HiAgent 导入文件
  src.tar                    # 包内直接为 main.py、list/ 等，无 src 外层
  openapi-hiagent.json
  Dockerfile
  smoke_check.py
  tests/test_service.py
~~~

检索接口在启动时读取全部清单正文，每次请求前检查 list 中的文件变化，有变化时重新校验并更新正文缓存，无变化时复用已解析的正文。检索全文不截断、不总结，不调用外部模型；个性化任务通过配置的双模型处理。开发时读取项目根目录 list，部署包读取 main.py 同级的 list；可用 DOCUMENT_DIR 环境变量显式指定其他清单目录。

## 本地运行

使用 Python 3.12，在项目根目录执行：

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\src\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --app-dir src --host 0.0.0.0 --port 8000 --workers 1
~~~

公司环境可为 pip 命令添加内部源参数：

~~~text
-i http://10.240.99.210:35001/simple --trusted-host 10.240.99.210
~~~

- GET /health：健康检查及材料清单数量。
- GET /projects：列出可用项目名称及对应文件名；name 字段即 param 可输入值。
- GET /classes：保留原路径和 classes 响应键，内容改为项目名称清单。
- POST /retrieve：按项目名称严格获取材料清单正文，成功响应为 UTF-8 纯文本。
- GET /files/{document_id}：下载原始 DOCX。
- GET /docs：接口调试页面。

## HiAgent 接入

保留请求字段 param 和工具名 retrieveDocuments，param 填完整项目名称。成功响应已改为纯文本，需同步调整原先解析 JSON 的后续节点。

1. 部署新服务，确认 http://服务器IP:8000/health 可访问，document_count 与当前有效清单数量一致（初始为 75）。
2. HTTP 请求节点使用 POST http://服务器IP:8000/retrieve，请求头 Content-Type: application/json。
3. 请求体中 param 类型为字符串，绑定开始节点的项目名称变量；实际发送的 JSON 为：

~~~json
{"param":"流动资金贷款"}
~~~

4. 若使用自定义插件，将 openapi-hiagent.json 中 YOUR_SERVER_IP 替换为部署服务器地址后重新导入，param 绑定项目名称。
5. 后续大模型节点直接引用 HTTP 节点的响应正文（通常为 body 字符串）作为材料内容，无需 JSON 解析，也不再提取 combined_content、documents 等旧字段。自定义插件请使用更新后的导入文件，其成功响应声明为 text/plain 字符串。
6. 节点响应长度建议至少 100 KB，并检查平台及模型的输入长度限制。服务自身返回完整正文。

param 应来自已确定的完整项目名称；不要让模型用“流贷”等简称发起查询。完整有效名称可在 /projects 查看。

## 响应正文

成功响应为 HTTP 200，Content-Type 为 text/plain; charset=utf-8。响应体直接是匹配文档的完整正文，不使用 JSON 包装，不附加文件名标题或下载链接。保留原文自带的标题、段落、换行和表格内容。

错误仍返回对应 HTTP 状态码及 JSON detail：未匹配为 404，无效入参为 422，重复项目为 409，文件损坏、目录不可用或更新未完成为 503。异常期间不会把缓存中的旧材料当作更新成功的结果返回；修复后重试即可。

正文按原顺序保留段落、表格和自动列表编号。表格以显式标记和制表符表示，单元格内换行保留；纯文本不复刻 Word 排版和合并格视觉结构，原始版式可通过 DOCX 下载保留。新加入的扫描件、图片及公式对象需要额外解析能力。

## 日常更新材料清单

服务运行后，直接维护它实际读取的 list 文件夹即可：

- 新增项目：放入“项目名称材料清单.docx”，下次查询即可使用新项目名称。
- 更新现有项目：以同名新文档覆盖原文件，下次查询直接返回新版正文。不要同时保留同一项目的多个清单。
- 移除项目：删除对应文件，下次查询返回 404；改名时，项目名称随文件名同步改变。

无需重启服务、重新打包、重建镜像或修改 HiAgent 流程。文件复制未完成或损坏时，接口暂时返回 503；文件写完或修复后自动恢复。可以先上传为 .tmp 等临时扩展名，完成后再改成正式 .docx 文件名。

## 首次部署与代码更新

首次启用自动更新需要部署此版本；以后只有程序代码或依赖变更才需重建镜像。日常更新清单使用上面的文件维护方式。

本地开发在项目根目录运行时，直接维护项目 list。Docker 应将宿主机 list 目录挂载至容器 /home/app/list，确保用户放入的文档对容器可见。在项目根目录的 PowerShell 中执行：

~~~powershell
.\.venv\Scripts\python.exe -B .\package.py
docker build -t file-retrieval:2.2 .
docker run -d --name file-retrieval -p 8000:8000 --restart unless-stopped --mount "type=bind,source=$((Get-Location).Path)\list,target=/home/app/list,readonly" file-retrieval:2.2
~~~

package.py 直接使用项目根目录 list 生成 src.tar，包含当前 Word 清单及话术库 Excel 的原始字节，不打入其他 Excel、旧 src/data 文档、虚拟环境或缓存。话术库缺失或格式错误时打包失败。打包结果同时更新 openapi-hiagent.json。

Linux 部署服务器可使用如下运行命令，将 /opt/file-retrieval/list 替换为服务器上实际的清单目录：

~~~sh
docker run -d --name file-retrieval -p 8000:8000 --restart unless-stopped --mount type=bind,source=/opt/file-retrieval/list,target=/home/app/list,readonly file-retrieval:2.2
~~~

以后只需更新挂载源目录中的文件，容器内会同步可见并在下次请求时生效。只读挂载限制的是容器对文件的写入，不影响在宿主机更新文档。未挂载目录的旧容器只包含镜像中的静态副本，修改宿主机 list 不会同步到容器，需先按上述方式重新创建一次容器。

已有同名容器时，请按部署服务器现有流程替换旧容器。宿主机 8000 端口被占用时可使用 -p 8001:8000，并同步修改 HiAgent 地址。

Dockerfile 沿用 Python 3.12 基础镜像和内部 pip 源。服务器无法访问公共镜像仓库时应使用公司的 Python 3.12 镜像。服务沿用内网 HTTP 接口和现有网关访问控制。

## 个性化任务的产品清单匹配（2026-10-30）

批复提取出的可售产品会先在当前清单目录中查找同名项；没有同名项时，使用现有生成模型，从完整候选目录中判断唯一对应的清单，允许同一业务的名称变体，例如“法人房产按揭贷款”对应“法人按揭贷款材料清单.docx”。模型不能创造目录之外的文件；无法确定或存在歧义时任务失败，不按相似字符随意选取。匹配纠错沿用 `MODEL_MAX_RETRIES`。

批复中的产品名、事实和引用保持原样，结果 `productName` 与 `extractedProductName` 保留批复产品名，`checklistFileName` 返回实际采用的 Word 文件名。规则及目录按原产品名关联，保存 Word 原始字节与哈希，内部新增 `product-matches` 阶段记录和匹配日志。此调整用于个性化任务选清单；原 `/retrieve` 仍按其现有接口约定匹配。

实测中模型提取的结构或原文引用偶有不合规，提取阶段现沿用 `MODEL_MAX_RETRIES` 进行有限纠错：每次仍携带原始完整页面图像和上下文，记录安全校验项代码；页覆盖、可读性和逐字原文引用的校验条件不变，纠正失败仍整次 FAILED。

## 运行日志（2026-10-30）

服务启动后自动创建并写入程序同级的 `log` 目录。开发时为 `D:\10月重点项目\文件检索\src\log`，按北京时间每天一个 UTF-8 TXT，例如 `2026-10-30.txt`。同一天及重启后继续追加；跨天后的第一条记录自动写入新日期文件，旧日志一直保留，不自动删除。日志时间固定为北京时间（`+08:00`），不依赖部署机器的时区。

记录服务启停、接口方法/路由/状态码/耗时、PDF 下载、任务各阶段及耗时、完成/失败/取消/替换和错误原因。使用 `bsnEvtId`、`taskId` 及内部 `generationId` 关联；编号过长时日志最多展示前 512 字符，换行被转义。日志不保存完整业务 JSON、PDF 正文、模型输入输出、密钥、下载链接或 URL 查询参数；不采集第三方 HTTP 客户端的详细日志。日志写入失败时转至控制台，业务流程继续执行。

Docker 部署时目录为 `/home/app/log`，建议在原运行命令中增加持久卷，例如 `--mount type=volume,source=file-retrieval-logs,target=/home/app/log`，容器替换后继续保留日志。`src.tar`、Docker 构建上下文及版本管理不收录运行日志；日志目录由服务启动时自动创建，不需要修改接口或业务 JSON。

## 验证

~~~powershell
.\.venv\Scripts\python.exe -m pip install httpx==0.28.1
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B package.py
.\.venv\Scripts\python.exe -B smoke_check.py
~~~

测试覆盖严格匹配、相似名称隔离、非法输入、表格编号、正文完整性，以及运行中新加、覆盖、改名、删除、重复或损坏清单后的恢复、并发请求和更新中途检测。真实文档数量从 list 动态读取，新增清单无需修改测试中的固定数量。smoke_check.py 从部署包解压并临时启动真实服务，逐一核对全部项目的纯文本响应，并在该服务持续运行期间验证清单自动更新，结束后自动停止服务。实际结果见 VERIFICATION.md。

## 个性化任务运行与交付（T25–T28）

规格见 [spec](docs/sdd/tasks/spec.md)，接口见 [接口文档](docs/sdd/接口文档.md)；新接口契约为 docs/sdd/personalized-checklist.openapi.json。旧 openapi-hiagent.json 只导入 /retrieve，不含任务接口。src 根目录的全部 Python 模块、requirements.txt、start.sh、有效 list 清单及话术库 Excel 进入部署包；不递归收录 src/data、.env、Key 文件、数据库、上传 PDF 或缓存。规则 list 原件不被开发与测试修改。

### 话术库补充材料

个性化生成必须读取 `list/话术库对应材料.xlsx`，工作表名保持“审批决议话术库对应材料”，前三列表头保持“模块话术序号 / 标准话术 / 对应的放款影像材料”。可同名覆盖或增加规则行；材料延续当前的编号、分组及子项格式，不使用合并单元格或公式，非空行的三列均须完整。当前包含 19 条话术、94 个材料子项（跨话术可重复），重复话术序号按 Excel 行号区分。

先按 Word 清单生成，再使用完整批复及表格进行话术语义匹配。主体名、金额、比例及语序允许变化；只有实际出现且有页号和连续原文支持的话术才补材料，未命中或不能确定命中不补。命中后对全部子材料逐项判断，只有明确适用的子项才加入；“如需”、上市或配偶等条件不明确时排除。Excel 的材料要求在明确适用后优先，不能覆盖严格排除和一级目录限制。“/”表示“或”，分组标题不单独输出。

明确归属的条件只补相应产品，共性条件补覆盖产品，归属不明一律不补；合同名称按实际产品调整。不同产品、不同主体分别保留，同产品同主体的等效材料合并：沿用 Word 名称、编号和已有展示信息，补充具体要求及话术命中理由。Word 通用材料可细化为已知主体材料，避免重复。Excel 独有材料编号为 `话术库:行15:材料2` 等定位形式，归入所选 Word 目录，同目录内排在 Word 材料之后。材料叶子字段不变，2026-10-27 起外层结构改为下述目录树。

每个新处理任务固定保存所用 Excel 原件、哈希、规则及匹配记录；生成模型配置与 Word 阶段共用，按话术拆批时仍携带完整上下文。文件更新对下次处理生效，已完成历史不变。文件缺失、损坏、复制未完成或内容不完整使个性化任务 FAILED，不静默跳过补充；修复后创建新任务重试。原 `/retrieve`、项目目录及 DOCX 下载保持原行为，健康检查数量仍只统计 Word 项目。

详细决策见 [实施说明](docs/sdd/话术库补充材料实施说明.md)。离线测试验证引用、整包覆盖、合并及任务流程；语义匹配、产品主体关联和材料准确性需结合真实批复人工验收。

### 严格排除与一级目录过滤（2026-10-27，替代此前整项保留口径）

只有适用性明确的材料才保留。不确定是否应排除、适用条件未知、对象关联不完整、对象类材料没有有效对象时，一律排除，不再重建整项待确认材料。明确适用的对象可以单独保留，不恢复其他已排除对象。内部模型决定必须明确给出 `applicability=APPLICABLE` 才能保留，缺失或 UNKNOWN 都按排除处理；对外字段不增加。

JSON 中的 `grtstlRcmbNm` 同时接收当前阶段或担保方式，例如 `"放贷"`、`"签约"`、`"抵押、保证"`。识别放贷/放款（同义）、签约、抵押、质押、保证；多个词按任一匹配。只核对 Word 一级目录标题，不能用子目录或材料名称中的词通过筛选。一级目录明确属于其他阶段或类型时，其全部材料在模型筛选前排除，话术补充也不能重新加入。一级目录没有这些词时仍参与正常适用性判断；字段缺失、为空或无已识别词时不启用目录过滤。

例如收到 `"放贷"` 时，一级目录“合同签约资料”全部排除，“放款资料”参与筛选，“基本资料”“产品专项及补充材料”继续参与筛选。收到 `"抵押、保证"` 时，明确标记“质押”的一级目录排除，没有阶段或类型词的目录继续参与筛选。不从抵押、保证推测额外的放款或签约阶段。

真实但不足或无法核对的排除引用不再触发恢复材料，改为记录严格审核排除；规则遗漏、保留来源造假、跨产品或对象串用及格式错误继续拒绝。批复优先、JSON 补缺的事实合并逻辑保持原规则，不推测未知押品类型。新处理任务采用本口径，已完成历史保持原结果。所有材料均被排除时，沿用现有“结果必须非空”接口约束，任务返回 FAILED，不为满足条数而保留材料。详细说明见 [实施说明](docs/sdd/严格排除与一级目录过滤实施说明.md)。

### 材料清单目录树（2026-10-27）

新生成材料的标题统一为 `[产品名]材料名称（主体名称）（具体要求）`，无主体或无要求时省略对应括号。JSON 和 TXT 均移除“适用理由（reason）”“来源规则（sourceRuleId）”；内部判断记录和来源关联仍保留。展示字段会拆分 Word“规程依据”单元格中的“来源系统、制度依据、发布日期、发布部门”，支持同一行或分行标注，同名字段优先使用 Word 原文；模型把这些内容整段放入任一元数据字段时也会拆分。“发布依据”不展示、不混入其他字段；未标注字段沿用现有模型值及缺省规则。TXT 顺序为“具体要求、来源系统、制度依据、发布日期、发布部门”。历史 JSON 保留原字段和内容，后续 TXT 导出使用新的展示字段和顺序；已有存档不改写。

2026-10-28：新生成结果的 `data` 只包含 `generatedAt`、`total`、`products`、`items`，不再返回 `sourceDocumentHash`，TXT 不再输出 PDF SHA-256。内部任务记录继续保存文件哈希，并在处理前验证文件一致性；已有历史 JSON 中的该字段原样保留。2026-10-29 起创建入口改为直接接收原业务 JSON，从必填的 `replyImageUrl` 下载单个 PDF。`grtstlRcmbNm` 继续用于一级目录过滤，后续状态处理不变。

查询 DONE 的 `data.items` 现在是产品目录树：每项 `productName`、`categories`；目录递归包含 `categoryId`、`categoryName`、`children`、`materials`，材料展示字段保留在 `materials` 的叶子对象中。`data.products` 继续保留产品元数据。上游须按新结构遍历，不能再直接读取 `data.items[0].materialName`。完整五条材料示例见 [generate.tree.response.json](docs/sdd/examples/generate.tree.response.json)，字段说明见 [接口文档](docs/sdd/接口文档.md)。

目录编号、名称及顺序遵循各产品 Word，保留原名称及多层关系；标题作为目录返回，不计入材料条数。不按担保人新增目录，不合并不同主体/产品的材料。空目录不返回，无材料的已识别产品保留 `categories=[]`。`total` 统计全部材料叶子，`itemId` 按最终展示顺序全局从 R0001 连续编号：产品顺序、Word目录顺序；目录内先子目录再直接材料，同目录Word材料先于Excel独有材料。

合并到 Word 的材料沿用 Word 原目录。Excel 独有材料由生成模型在该产品完整 Word 目录中选择，程序校验目录路径；无法确认归属时按严格口径排除，不再默认放入最后一个一级目录，不新增“话术库补充材料”类。已知目录原本没有材料时，仍可作为明确选择的落点。

历史任务查询也统一返回树：从该代次保存的规则、合并结果或 Word 原件快照恢复目录；旧版 Excel 独有材料不能恢复时归当时 Word 最后一级目录，其他缺少可恢复信息的材料放“未分类材料”。不读取当前 `list`、不调用模型、不改写历史数据库；原材料文字、PDF哈希和生成时间保留，编号随树的展示顺序重排。完整备份时须一并保留代次产物，目录恢复需要这些原始资料。

创建/查询地址、任务状态及原 Word 检索接口不变。本版本同时兼容参考文档的“材料名称”和旧版“材料名称或核查事项”表头，不修改参考原件。实现决策见 [目录树输出实施说明](docs/sdd/目录树输出实施说明.md)。

### 模型配置与运行

Python 3.12；锁定 httpx==0.28.1（异步模型 HTTP）及 pypdfium2==5.14.0（PDFium 页面读取和渲染）。完整直接依赖见 src/requirements.txt。扫描和混合 PDF 的每页图像由提取模型实际读取，因此提取端必须支持 Chat Completions 的 image_url 数据图像输入。生成端使用同协议文本输入。不需要供应商 SDK。

项目根目录已提供 [model-api.env](model-api.env)，两阶段默认使用 DeepSeek `deepseek-flash`。将 EXTRACT_MODEL_API_KEY 和 GENERATE_MODEL_API_KEY 两处占位符替换为自己的 Key（可以相同），保存为 UTF-8 后启动服务即可。程序在启动时自动读取，无需手动执行环境变量加载命令。以后切换 Key、接口或模型只需修改此文件并重启；更新代码时保留原文件，不用重新填写。

本地开发读取项目根目录 model-api.env；src.tar 解压部署读取 main.py 同级的 model-api.env，路径不随启动命令所在目录改变。该文件独立于代码交付，不进入 src.tar、Git 提交或 Docker 构建上下文，部署时单独复制并保留。只有空白示例 docs/sdd/model-api.env.example 可随文档分享。文件包含明文 Key，请限制可读取它的人员。

优先级为“进程环境变量 > model-api.env > 默认值”。如以前用 PowerShell 或 Docker --env-file 设置过同名变量，需先移除旧变量，或开一个未设置这些变量的新终端，文件中的修改才会生效。即使环境变量为空也具有优先级。只有这一个固定名称的文件会自动读取，.env 和 deployment.env 不会自动加载。

支持两阶段的 API_URL/API_KEY/NAME 六项及 MODEL_REQUEST_TIMEOUT_SECONDS、MODEL_MAX_RETRIES 两项，每行 KEY=value，注释独占一行，可用成对英文单/双引号包围值。不支持变量引用、命令执行、行尾注释、未知或重复配置项。URL 必须填写完整端点，不自动追加路径。PDF 提取模型必须支持图片输入。配置文件缺失时仍可使用环境变量；格式、编码或权限错误会禁用任务创建并返回不含 Key 的提示，检索和历史查询保持可用。

在项目根目录 PowerShell 中启动：

~~~powershell
$env:DOCUMENT_DIR = (Resolve-Path .\list).Path
$env:TASK_STORAGE_ROOT = Join-Path (Get-Location) 'task_storage'
.\.venv\Scripts\python.exe -B -m uvicorn main:app --app-dir src --host 0.0.0.0 --port 8000 --workers 1
~~~

DOCUMENT_DIR 是只读规则来源；TASK_STORAGE_ROOT 是可写且持久保存的数据库、上传、阶段产物与历史结果目录。默认存储为 task_settings.py 同级 task_storage。无自动清理。只允许单进程、单 worker 使用同一存储，禁止 --reload 或多副本共享；第二实例会被锁拒绝。网络超时 MODEL_REQUEST_TIMEOUT_SECONDS 默认 180 秒，仅针对每次模型请求；MODEL_MAX_RETRIES 默认 2，为额外重试/结构修复预算，不是总任务 SLA。不设置模型 token 上限，不静默截断。配置缺失时任务创建返回 503，检索与历史查询仍可用。

同业务新任务会终止先前未完成任务，同键覆盖重跑；不同业务按成功提交顺序串行。新任务不会插队。服务重启后原 PENDING/PROCESSING 全部 FAILED，不自动恢复；终态及历史结果保持。停服后完整备份任务存储目录，恢复时配套恢复数据库与代次目录，避免只复制 SQLite 索引。

联网构建机先准备 wheels，复制至相同操作系统、CPU 架构、Python 3.12 的离线目标机安装（跨平台不能直接复用本机原生 wheel）：

~~~powershell
.\.venv\Scripts\python.exe -m pip download --only-binary=:all: -r .\src\requirements.txt -d .\wheelhouse
.\.venv\Scripts\python.exe -B .\package.py
# 将 src.tar、wheelhouse 复制至目标目录，然后在目标目录执行：
python -m venv .venv
# Windows 命令；Linux 将解释器替换为 .venv/bin/python
tar -xf src.tar
.\.venv\Scripts\python.exe -m pip install --no-index --find-links .\wheelhouse -r .\requirements.txt
.\.venv\Scripts\python.exe -B -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
~~~

wheelhouse 还包含 pip 解析出的间接依赖；本次未实际下载或验证目标平台 wheel 安装。Dockerfile 仍需要构建环境可用的基础镜像和内部源。上述旧 Docker 示例仅用于检索；启用任务需只读挂载 model-api.env 并配置持久卷，例如（先在宿主机创建并填写 /opt/file-retrieval/model-api.env，模型 Key 不放入镜像）：

~~~sh
docker run -d --name file-retrieval -p 8000:8000 --restart unless-stopped -e TASK_STORAGE_ROOT=/home/app/task_storage --mount type=bind,source=/opt/file-retrieval/model-api.env,target=/home/app/model-api.env,readonly --mount type=bind,source=/opt/file-retrieval/list,target=/home/app/list,readonly --mount type=volume,source=file-retrieval-tasks,target=/home/app/task_storage file-retrieval:2.2
~~~

修改宿主机 model-api.env 后执行 `docker restart file-retrieval` 生效，无需重建镜像。升级程序时重新构建镜像并沿用同一配置文件挂载。已有容器如果仍使用 --env-file 注入模型变量，先按上例重新创建容器以移除旧变量；仅重启不能清除旧的容器环境变量。

从项目根目录直接提交业务 JSON 并轮询。先将示例中的 `replyImageUrl` 替换成部署服务可访问的真实 PDF 链接：

~~~powershell
curl.exe -sS --max-time 75 http://127.0.0.1:8000/api/v1/tasks -H 'Content-Type: application/json' --data-binary '@docs/sdd/examples/generate.request.json'
curl.exe -sS http://127.0.0.1:8000/api/v1/tasks/query -H 'Content-Type: application/json' --data-binary '@docs/sdd/examples/query.request.json'
~~~

文件由部署服务通过 `replyImageUrl` 直接 GET 下载，支持内网 HTTP/HTTPS 地址和最多 5 次重定向，无需鉴权。单文件上限仍为 10 MiB；单次网络操作超时 30 秒，整个下载最长 60 秒，不自动重试；上游请求超时建议至少 75 秒。开发机不在内网时可用本地 HTTP 文件服务测试。

下载或校验失败直接返回错误，不创建任务、不影响旧任务。下载成功并持久接收后才返回创建成功，不保证后续处理成功。轮询到 DONE 获取完整 data；FAILED 含 reason，TERMINATED 表示已被替换。不要重复提交创建来查询状态。

真实业务验收入口（显式运行，会通过现有服务发起模型任务）：工具只发送 JSON，不上传文件，业务 JSON 必须含 `replyImageUrl`。可选 `--pdf acceptance/approval.pdf` 仅用于核对本地原件与历史结果哈希。

~~~powershell
.\.venv\Scripts\python.exe -B src/verify_task_live.py --base-url http://127.0.0.1:8000 --business-data acceptance/business.json --expected acceptance/expected.json --report task_storage/acceptance-report.json
~~~

新结果的 expected.json 格式为 `{"products":["产品名"],"items":[{"productName":"产品名","materialName":"[产品名]材料名称（主体名称）（具体要求）"}]}`，按产品及完整材料标题统计数量；标题须与期望输出完全一致，可附 requirement 供人工核对。每个期望主体实例单独一条；产品允许无保留条目，但全局 items 不能为空。旧结果仍支持以 sourceRuleId 替代 materialName 进行原规则数量核对，同一份期望清单应统一使用一种标识。程序不验证语义，也不自动修改 expected。客户端默认等待 600 秒、每秒查询，可用 --wait-seconds/--poll-seconds 修改；等待超时不终止服务任务。退出码 0 仅表示自动数量匹配，报告人工签收仍待填写，不能作为业务通过证明。缺资料、输入错误、服务不可达、任务失败或数量差异均非零退出。报告包含完整输出，请保存在本地受控目录。

本次真实模型及业务验收待联调；准备获准 PDF、业务 JSON、人工期望及真实双模型服务后，由业务人员逐项核对 OCR、适用性、文字、主体展开、误排/漏排并签收。见 [业务验收记录](docs/sdd/业务验收记录.md)。测试无需这些外部资料，默认全部使用临时存储和本地假模型。
