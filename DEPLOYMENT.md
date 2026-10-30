# 行内 Docker 离线部署

镜像 `file-retrieval:2.2`，Linux amd64，宿主机端口 `18008` 映射容器 `8000`。
默认部署目录 `/data/deploy_file-retrieval`；目标机只需要 Docker 和 Bash、tar、gzip 等系统工具，无需安装 Python 或联网下载依赖。

## 构建与打包

在有 Docker 的 Linux/WSL 环境，进入项目根目录：

```sh
bash huanban_tar.sh
```

直接从 `src` 和 `list` 构建，不执行 `package.py`，不使用 `src.tar`。
构建默认使用 `python:3.12-slim-bullseye`（glibc 2.31）和既有行内 pip 源；构建机须能访问它们。
可以传入 Docker 构建参数：

```sh
bash huanban_tar.sh \
  --build-arg PIP_INDEX_URL=https://pypi.org/simple \
  --build-arg PIP_TRUSTED_HOST=pypi.org
# 行内基础镜像可增加 --build-arg PYTHON_IMAGE=公司仓库/python:3.12-slim-bullseye
```

产物在 `dist/file-retrieval-2.2/`：完整 `huanban.tar`、每片最多 1 GiB 的 `huanban.tar_000` 等分片、`SHA256SUMS`。
完整包与分片任选一种传输。包内包含压缩后的 Docker 镜像、运维脚本、清单、话术库和空白模型配置模板。
真实 `model-api.env`、运行日志、数据库、上传资料、历史结果和旧数据目录均不进入交付包或构建上下文。

## Docker 19.03.15 兼容处理

基础系统固定为 Debian Bullseye，避免浮动 `slim` 标签切换到新版 glibc 后，线程创建受到旧版 Docker 默认 seccomp 策略影响。
保留 Python 3.12 和现有线程机制；构建关闭 provenance，交付 Linux amd64 单平台镜像。
运行脚本继续使用 Docker 默认安全策略，不添加 `seccomp=unconfined` 或 privileged。
自定义基础镜像也须使用兼容的 glibc，不能换回不指定 Debian 版本的 `python:3.12-slim`。

本地验证包含默认 seccomp，以及基于 Docker 19.03.15 官方策略、显式让 clone3 返回 EPERM 的模拟条件。
后者用于在新 Docker/runc 上复现旧环境的线程问题，并不等同于已在行内 Docker 19.03.15 上验收。
升级后先检查 `http://127.0.0.1:18008/health`，再用真实批复验证完整模型任务。
上游说明：[Moby clone3/glibc 兼容问题](https://github.com/moby/moby/issues/42680)；
策略来源：[Docker 19.03.15 默认 seccomp](https://github.com/moby/moby/blob/v19.03.15/profiles/seccomp/default.json)。

## 目标机部署

在独立的解包目录操作，不要解压覆盖现场部署目录。若只传分片：

```sh
cat huanban.tar_[0-9][0-9][0-9] > huanban.tar
sha256sum -c SHA256SUMS
tar -xf huanban.tar
bash release.sh
```

只传完整包时，使用 `sha256sum huanban.tar` 与源端 `SHA256SUMS` 的对应记录核对，再解压发布。
`release.sh` 加载镜像、复制运维脚本、首次初始化清单和配置、启动容器；不按服务器 IP 自动选择节点。
首次发布后填写 `/data/deploy_file-retrieval/model-api.env` 中的真实模型地址、名称和 Key，然后：

```sh
cd /data/deploy_file-retrieval
bash start.sh
curl --fail http://127.0.0.1:18008/health
bash show.sh
```

提取模型须支持图片输入；目标服务必须能访问模型和业务 PDF 地址。模板未填写时，检索可用，模型任务不可用。
健康检查只证明检索服务可用，模型任务还需真实业务验收。

`list` 和 `model-api.env` 只读挂载，`task_storage` 和 `log` 可写持久化。
升级保留现场清单、配置、任务记录及日志；更新清单请单独维护部署目录的 `list`。
同一任务存储只能运行一个容器、一个 worker。停服后完整备份 `task_storage`，不要只备份 SQLite 文件。

```sh
bash stop.sh       # 停止并移除容器，保留宿主机数据
bash rollback.sh   # 按参考项目，仅调用 stop.sh，不自动切换旧镜像
bash restore.sh    # 按参考项目，仅调用 stop.sh
```

如需调整默认值，可设置 `APP_NAME`、`VERSION`、`DEPLOY_DIR`、`API_PORT`；构建和后续运维使用一致的镜像名与版本。
例如 `API_PORT=18009 bash start.sh`。脚本不会修改系统防火墙；按行内流程开放宿主机对应端口。
