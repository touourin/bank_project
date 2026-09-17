# bank_project

供邓王璘、晏子怡共同开发的 Python + FastAPI + Neo4j 工程框架。

**当前仅搭框架，未实现业务功能。** 保留模块目录、接口契约草案、依赖装配、配置、健康检查和 Docker 启动。数据导入、抽取、消歧、建图、语义校验、查询和流水线均为占位；合法业务请求统一返回 HTTP 501。

## Docker 启动

首次使用，复制 `.env.example` 为 `.env`，填写自己的 `BANK_NEO4J_PASSWORD`（模板不提供默认密码），再构建启动：

```bash
cp .env.example .env
# 编辑 .env，填写 BANK_NEO4J_PASSWORD 后继续
make build
make run
```

当前工作区已有 `.env`，无需覆盖。已有镜像时直接 `make run`。
`make run` 启动 API 和 Neo4j，并等待健康检查。

| 入口 | 地址 |
| --- | --- |
| Swagger 接口文档 | http://127.0.0.1:8000/docs |
| 进程健康检查 | http://127.0.0.1:8000/health |
| 基础设施就绪检查 | http://127.0.0.1:8000/ready |
| Neo4j Browser | http://127.0.0.1:7477 |

```bash
make status       # 容器状态
make logs         # 日志
make restart      # 重启 API
make down         # 停止容器，保留数据库卷
```

代码修改后运行 `make build && make run`。本机若 Docker Hub 元数据查询超时，可使用 `make build-cached` 利用已有基础镜像构建。

Neo4j 适配器目前只管理连接和执行就绪检查，不创建业务节点、索引或约束，也不读写业务数据。之前运行留下的数据卷予以保留；框架不会读取其中的演示数据。

`/health` 只检查进程；`/ready` 使用异步数据库检查，默认整体超时为 2 秒，异常或超时返回 503。
数据库卡住不会占用 HTTP 工作线程等待。启用 Neo4j 时，空密码和无效连接地址会在启动时被拒绝。

## 模块分工

| 模块 | 负责人 | 当前内容 |
| --- | --- | --- |
| ingestion、extraction | 邓王璘 | 数据接入、抽取入口占位 |
| resolution、graph、query | 晏子怡 | 消歧、建图、查询入口占位 |
| semantics | 晏子怡对接本体负责人 | 语义挂载与校验入口占位 |
| review | 晏子怡；邓王璘抽检复测 | 扩展位置说明 |
| contracts、ports、pipeline | 共同维护 | 交接契约草案、接口、编排占位 |
| adapters | 按接入任务分工 | Neo4j 连接；原始存储及模型接入预留 |

业务模块只依赖共享契约和接口；具体实现由 `bootstrap.py` 统一装配。
客户、转账等业务模型、算法和规则由后续需求确定。

## 本地开发

需要 Python 3.12+；容器使用 Python 3.13。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -c requirements.lock -e '.[dev]'
docker compose stop api
make local-run
```

本地默认 `BANK_GRAPH_BACKEND=none`，不连接数据库。设置为 `neo4j` 才启用连接与就绪检查；Compose 已配置此项。

```bash
make check        # 静态检查、测试、离线接口文档一致性
make test         # 单独运行框架测试
make schema       # 修改接口后更新 docs/openapi.json
```

设计说明：[模块边界](docs/architecture.md)、[契约草案](docs/contracts.md)、[开发约定](docs/development.md)。
