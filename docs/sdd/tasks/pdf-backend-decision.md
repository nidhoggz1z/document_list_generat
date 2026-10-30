# T11 / T12 PDF 后端决定

日期：2026-10-23（Asia/Shanghai）。选择 `pypdfium2==5.14.0`，仅一个 PDF 读取/渲染依赖，不增加 OCR 服务、Pillow 或第二 PDF 库。

## 官方依据与候选比较

- [pypdfium2 5.14.0 官方发行](https://pypi.org/project/pypdfium2/5.14.0/)：Python >=3.6，提供 py3-none-win_amd64、manylinux_2_17_x86_64 / aarch64 等轮子。Python 3.12 在声明范围内，Windows x64 和现有 `python:3.12-slim` 的常见 glibc 架构有二进制分发。选择同时具备文本读取、PDFium 页面渲染的单一后端。
- [官方安装和许可证说明](https://pypdfium2.readthedocs.io/en/stable/readme.html)：包装层采用 Apache-2.0 / BSD-3-Clause；PDFium 及其依赖另有随附许可证，交付必须保留 wheel 内的许可证和第三方通知，不能把整个二进制简化描述为仅一种许可证。普通 wheel 捆绑 PDFium，无强制 Pillow/NumPy 运行依赖；不使用 V8/XFA 特制构建。
- [PyMuPDF 官方能力与许可证](https://pymupdf.readthedocs.io/en/latest/about.html)：文本和渲染能力满足需要，但采用 AGPL 或商业授权；当前未选择这一授权路径。
- [pypdf 官方说明](https://pypi.org/project/pypdf/)：列出的主要能力是页面操作和文本/元数据提取，未提供本任务需要的整页渲染能力，故不选择。未安装其依赖。
- [pypdfium2 官方 API](https://pypdfium2.readthedocs.io/en/stable/python_api.html)：使用完整 Unicode 的 `get_text_bounded(errors='strict')`；不使用 UCS-2 限制的范围提取。页面和位图显式 close。PDFium 不支持并发线程调用，因此本模块所有 PDFium 操作使用同一互斥锁。

## 已确认的页接口与处理策略

用户选择方案 A：所有页面都保留原文本和整页图像，不根据少量文本、文字覆盖层、图像对象或表格启发式跳过渲染。

`read_pdf(path, cancel_check)` 是同步函数，返回完整的 `list[PdfPage]`。页号从 1 开始，`text` 保留后端原始抽取结果（含行序/表格文字线索，不伪造已重建的结构化表格），`image_png` 为整页 RGB PNG 字节。默认 2 倍 PDF 画布分辨率（普通页面约 144 dpi）。不截取页数、文本或图像内容。

每页 `needs_vision=True`、`readable=False`，表示尚未由 T13 的视觉阶段确认整页内容可读；它们不表示本地解析失败。页号与图像一一对应，表格布局由整页图像保留。图片只在内存中，不写业务目录。文本页也不得仅凭有文本就跳过视觉内容。

`cancel_check` 为同步无参数回调：返回真或抛出 `asyncio.CancelledError` 均停止；假/None 继续。打开前、每页前、渲染前和每页完成后检查；取消原样传播并释放资源。原生单次调用进行中无法强行打断，在下一个检查点响应。未来异步 worker 应把本同步读取放到线程，并提供线程可见的取消信号；具体接入属于 T20/T21。

不可打开、受密码保护或已加密（包括空用户密码）、零页、中间页不能加载、文本提取或渲染失败均抛出 `PdfReadError`；错误带 `code` 和可用页号，不返回部分清单。错误正文不包含文件内容/密码/原异常文本。不会打开链接、附件或外部 URL。

兼容注意：实测 5.14.0 的便捷加载构造器在原生文档页数为零时先抛错，未注册句柄关闭，Windows 路径输入可能遗留文件占用。T12 因此先读取本地字节，通过同库 raw API 加载，再由文档包装器接管有效句柄；在关闭保护范围内检查零页。原字节保持至文档关闭，失败不会遗留文件锁。

## 环境与离线安装

实际验证范围：本项目 `.venv`，CPython 3.12.9，Windows x64，pypdfium2 5.14.0，捆绑 PDFium 156.0.8076.0。使用临时生成的四类页面和标准 R2 加密 PDF，无客户数据、真实模型或外部 OCR。

现有 Dockerfile 为 `python:3.12-slim`。Linux 轮子可用性依据官方发行文件，**未运行 Docker、目标内网安装或目标机器验收**；内网镜像是否收录此版本未验证。目标架构、基础镜像和其余基础依赖必须在交付阶段共同验证。

联网准备机按目标平台收集二进制轮子，例如 Linux x64：

```powershell
python -m pip download --only-binary=:all: --no-deps --platform manylinux_2_17_x86_64 --python-version 312 --implementation cp --abi cp312 pypdfium2==5.14.0 -d wheelhouse
```

目标机器离线安装：`python -m pip install --no-index --find-links wheelhouse pypdfium2==5.14.0`。Windows 需另取 win_amd64 wheel；此处只说明新依赖，完整 requirements 的离线轮子集合由 T27 准备。以上交付命令是操作说明，不表示本轮已在目标平台运行。只使用 wheel，避免离线回退源码构建。保留 wheel 的哈希与许可证随交付存档。

PDFium 可能容错修复某些格式错误；本模块保证把后端报告的错误升级为整体失败，不能保证检测所有未报告的损坏或识别精度。视觉真实性/表格理解和真实业务验收由 T13/T28 负责。方案 A 的内存占用随总页图像量增长，未引入固定 100 页上限。
