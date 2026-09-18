# bank_project

供邓王璘、晏子怡共同开发的 Python + FastAPI + Neo4j 工程框架。

**当前仅搭框架，未实现业务功能。** 保留模块目录、接口契约草案、依赖装配、配置、健康检查和 Docker 启动。数据导入、抽取、消歧、建图、语义校验、查询和流水线均为占位；合法业务请求统一返回 HTTP 501。

## 离线 mock 数据

客户标签与客户旅程的合成 CSV 位于 [examples/mock](examples/mock/README.md)。
默认生成 1,000 个虚构客户，保留银行源表列名，并提供同一客户的多条关联事件。当前最终版共 16,700 条旅程，两份 Excel 各自包含数据和字段说明页。
脚本仅使用 Python 标准库，不依赖银行原始 Excel、FastAPI 服务或数据库。

```bash
make mock          # 重新生成两张 CSV 和 manifest.json
make mock-check    # 校验字段类型、客户关联、日期及模拟场景的一致性
```

离线数据工具支持 Python 3.9+，默认使用 `python3`，可通过 `MOCK_PYTHON` 指定解释器。
也可直接运行 `python3 scripts/generate_mock.py` 和 `python3 scripts/validate_mock.py`。
应用服务仍要求 Python 3.12+。
这套工具只准备源数据；HTTP 导入和后续图谱链路仍待业务实现。

## Docker 启动

首次使用，复制 `.env.example` 为 `.env`，填写自己的 `BANK_NEO4J_PASSWORD`（模板不提供默认密码），再构建启动：

```bash
cp .env.example .env
# 编辑 .env，填写 BANK_NEO4J_PASSWORD 后继续
make build
make run
```

如果已经有 `.env`，保留其中的密码，按最新模板补充或更新配置项。已有镜像时直接 `make run`。
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

本地默认 `BANK_NEO4J_ENABLED=false`，不连接图数据库。设置为 `true` 才启用 Neo4j 连接与就绪检查；Compose 中固定为 `true`，连接容器内的 Neo4j。

## 依赖配置

运行参数在项目根目录 `.env` 中填写，由 `settings.py` 统一读取和校验。`.env.example` 提供模板，密码不提交到版本库。

| 配置 | 用途 | 当前状态 |
| --- | --- | --- |
| `BANK_NEO4J_ENABLED` | 本地启动时是否连接 Neo4j：`true` / `false` | 已接入；Compose 固定启用 |
| `BANK_NEO4J_URI/USER/PASSWORD/DATABASE` | Neo4j 连接参数 | 已接入；Compose 使用容器地址，从 `.env` 读取密码 |
| `BANK_MYSQL_HOST/PORT/DATABASE/USER/PASSWORD` | MySQL 连接参数 | 已预留并校验参数；适配器、连接和 Compose 服务尚未实现 |
| `BANK_HEALTH_TIMEOUT_SECONDS` | 已接入依赖的就绪检查超时 | 默认 2 秒 |

MySQL 配置供后续原始数据存储模块使用；填写这些参数不会自动连接数据库、建表或导入 CSV。当前 `/ready` 只检查已装配的 Neo4j 连接。

旧配置需要迁移：删除 `BANK_GRAPH_BACKEND`，原值 `none` 改为 `BANK_NEO4J_ENABLED=false`，原值 `neo4j` 改为 `BANK_NEO4J_ENABLED=true`。遗留旧键时，本地启动会明确提示更新，避免悄悄关闭原有连接。`/ready` 响应中的 `graph_backend` 仍表示当前实际使用的后端。

## 开发检查

```bash
make check        # 静态检查、测试、离线接口文档一致性
make test         # 单独运行框架测试
make schema       # 修改接口后更新 docs/openapi.json
```

设计说明：[模块边界](docs/architecture.md)、[契约草案](docs/contracts.md)、[开发约定](docs/development.md)。
