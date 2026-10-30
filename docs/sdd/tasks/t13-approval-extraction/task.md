# T13：批复全文与可售产品提取

> 执行约束：在当前任务中完成一个可独立 review 的 diff；按依赖执行，不自动创建其他任务、工作树或启用子代理。可参考 executing-plans 技能；用户授权始终优先。

**状态：** 已完成本地开发与测试（2026-10-23）；真实模型与业务 PDF 的识别准确率验收仍属 T28。
**依赖：** [T01](../t01-contracts/task.md)、[T10](../t10-model-transport/task.md)、[T12](../t12-pdf-pages/task.md)
**规格映射：** `FR-09`、`FR-10`、`FR-13`、`AC-10`、`AC-11`，见 [spec](../../spec.md)。
**目标：** 把所有页面转为带来源的批复全文、产品与业务事实。

## 一个 diff 的边界

- 新建 src/approval_extractor.py
- 新建 tests/test_approval_extractor.py

以上是本任务允许的功能修改范围；仅为本任务添加必要 fixture。不得夹带后续任务实现、无关重构、真实 Key、用户 PDF、运行数据库或生成包。预计超过约 300 行新增/修改逻辑代码，或出现第二个独立目标时，先拆成子任务并更新依赖再实施；不能用“补完顺手功能”扩大 diff。

## 稳定接口与实施要点

1. 输入 T12 页记录，文本与视觉页通过第一节点读取；内部输出页号/text/tables/readable、可售产品及各授信记录、带 present/否定信息的事实。
2. 按可售产品而非授信品种识别，首次出现顺序去重，同产品保留各条授信条件，不随意合并金额。
3. 产品目录只用于约束名称，不能替代原文或使用 JSON 产品回退；分批读取必须校验页集合和取消。

## 执行步骤

- [ ] 先新增/补充 tests/test_approval_extractor.py 中的 test_saleable_products_and_page_coverage 等行为检查，覆盖下列失败路径。
- [ ] 实现前运行下面的定向命令，确认新行为因明确缺失而失败；区分环境/依赖错误与行为失败，不把环境损坏记成有效 red。
- [ ] 只实现上述最小范围，使用 unittest 和临时目录/受控替身，异步测试采用 IsolatedAsyncioTestCase；不新增测试框架。
- [ ] 再运行定向验收，检查至少运行一项测试且全部通过；涉及主应用/依赖/打包时追加旧检索回归。
- [ ] 自查 diff、边界与密钥/运行产物排除；记录实际命令、结果及未完成事项，准备一次 review。若当前使用 Git，可作为独立提交；不为此自动 git init。

## 验收场景

- [ ] 授信品种与可售产品不同、同产品多行、两产品、视觉页与显式无担保均正确保留。
- [ ] 缺页、模型未读、零产品、无效结构及截断全部报错；不对外发布部分提取结果。

## 验收命令

从项目根目录以 PowerShell 执行。以下测试文件/模块在本任务实现时创建，**当前尚不存在则不能运行或宣称通过**。

```powershell
if (-not (Test-Path -LiteralPath 'tests/test_approval_extractor.py')) { throw '验收文件尚未创建，不能判为通过' }
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_approval_extractor.py' -v
if ($LASTEXITCODE -ne 0) { throw '任务验收失败' }
```

**通过标准：** 最终命令退出码为 0，输出 Ran N tests（N>0）及 OK；所有本任务场景已覆盖。文件不存在、零项测试、外部服务未提供、仅 fixture 通过均不能替代功能验收。

## 不包含

- 不修改已确认业务规则，不默默扩大支持格式、产品匹配或历史保留语义。
- 不执行真实模型调用或部署；T28 的真实验收另按显式命令和外部前提运行。
- 不因本任务测试通过就标记整个 spec 已实现。

## 实施与验证记录（2026-10-23）

- 新增 `src/approval_extractor.py`、`tests/test_approval_extractor.py`，直接在当前目录完成，无子代理或新任务。
- 接口：`await extract_approval(pages, model_client, product_names, cancel_check, batch_size=None)`；默认完整页面一次请求，可显式按页分批，后批携带前批完整上下文。返回 `pages/products/facts`，各产品的 `creditRecords` 保留逐条授信记录。
- 所有页传入全文与完整 PNG 图像；校验页集合、可读状态、表格结构、来源引文、事实标志、产品关联和结束标记；明确空白页保留。首次产品识别需原文产品名，续行可沿用此前已确认的产品，目录不补产品。
- red：新增测试运行 7 项，因提取模块缺失明确失败；后续跨页续行及空白页检查亦先复现失败再修正。
- 定向命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_approval_extractor.py' -v`，最终 **10 项，OK，退出码 0**。
- 全量命令：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q`，最终 **133 项，OK，退出码 0**。
- 覆盖两产品、同产品多行、扫描/混合页传图、跨页续行、明确无担保与未提及、缺页/未读/零产品/错误结构/截断、取消和后批失败；使用真实 ModelClient 配合受控 HTTP transport 验证请求内容，无外部模型调用。
- 已在当前任务自查修改范围。当前目录无 Git，不自动初始化；未修改密钥、用户 PDF、运行数据库或生成部署包。模型语义识别准确率、全流程接入和真实业务验收不在本任务中宣称完成。

