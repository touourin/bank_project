# bank_project

Python + FastAPI 的模块化数据准备与知识图谱工程，供邓王璘、晏子怡共同开发。

**已实现前两步：通用数据导入、可配置数据转换。** 导入层不绑定业务表；转换层使用映射配置或文档模型，输出带来源证据的对象、事件和关系候选。客户标签、客户旅程是两份内置示例配置。
消歧、建图、语义推理、图谱查询及后续流水线仍是占位接口，返回 HTTP 501。

## 启动

```bash
cp .env.example .env  # 已有 .env 时保留原配置，补充需要的新项
make build
make run
```

默认只启动 API，前两步不依赖 Neo4j、MySQL 或模型。
接口文档：<http://127.0.0.1:8000/docs>，支持选择文件上传和调用转换接口。
`/health` 检查进程，`/ready` 检查已启用的 Neo4j 连接；来源数据库、模型按请求连接，导入/转换失败会明确报错。

需要图数据库时，配置 `BANK_NEO4J_ENABLED=true` 和自己的 `BANK_NEO4J_PASSWORD`，再运行：

```bash
docker compose --profile graph up -d --wait
```

Neo4j Browser：<http://127.0.0.1:7477>。原有数据库卷保留；前两步不会写入图谱。

```bash
make status
make logs
make restart
make down     # 保留持久化数据卷
```

Docker 的原文件、导入清单、转换结果、失败状态和模型缓存保存在 `preparation-data` 卷。
`examples/mock` 只读挂载为收件目录，`configs` 只读挂载为配置目录；也可以直接通过接口上传文件。已有镜像使用 `make run`，代码更新后执行 `make build` 再 `make run`。本机 Docker Hub 元数据查询超时时可尝试已有基础镜像的 `make build-cached`。

## 两步独立调用

第一步导入文件，记录来源和快照，不调用模型、不生成图谱：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/imports \
  -H 'Content-Type: application/json' \
  -d '{"dataset_id":"demo","batch_id":"tags-001","source_system":"bank","source_uri":"file:CCM_C_CUST_FLAG_INFO.csv"}'
```

第二步转换同一个批次：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/extractions \
  -H 'Content-Type: application/json' \
  -d '{"dataset_id":"demo","batch_id":"tags-001"}'
```

读取结果：`GET /api/v1/extractions/tags-001?dataset_id=demo`。
状态与失败原因：`GET /api/v1/extractions/tags-001/status?dataset_id=demo`。
可用映射：`GET /api/v1/mappings`。启用 `BANK_API_TOKEN` 后需添加 `Authorization: Bearer ...`。

未知表默认每行转为一个通用 `record`，保留字段，不猜测业务含义。新业务通过添加 `configs/mappings/*.json` 来定义实体、主键、事件、参与方和有方向的关系；不需要修改导入或转换服务。修改配置后重启 API。

详细用法、配置格式与边界：[数据准备指南](docs/preparation.md)。

## 当前能力

| 类型 | 当前实现 |
| --- | --- |
| 表格 | 通用 CSV、XLSX、JSON 对象/数组、JSONL；保留来源位置及原文件 |
| 文档 | TXT、Markdown、文本 PDF、DOCX；支持按页/段落追溯 |
| 数据库来源 | 显式启用后的 MySQL 白名单表，只读快照；不接受用户 SQL |
| 表格转换 | 配置字段类型、对象标识、独立事件、参与角色、方向关系；无配置时通用记录转换 |
| 文档转换 | 可配置的 OpenAI 兼容接口、few-shot、原文证据校验、结果引用检查、缓存与有限重试 |
| 可靠性 | 原文件摘要校验、批次幂等、冲突检测、跨进程批次锁、原子发布、失败状态与中断恢复 |

扫描 PDF 需要先完成 OCR；图片、音频、实时 Kafka/CDC、跨行聚合及复杂业务推理尚未实现。JSONL 是批次导入，不是持续消费任务。文档模型候选需复核，不自动写入图谱。

## 模块与分工

| 模块 | 内容 |
| --- | --- |
| `ingestion` | 邓：通用接入服务，依赖读取、解析、存储接口 |
| `extraction` | 邓：通用映射引擎、文档候选抽取、结果校验与交接 |
| `configs/mappings` | 业务映射配置；银行两张表是示例 |
| `adapters` | 文件/MySQL 读取、格式解析、模型 HTTP 接入、本地持久化、Neo4j 连接 |
| `contracts`、`ports` | 共享类型及可替换接口 |
| `resolution`、`graph`、`query` | 晏：后续消歧、建图、查询，仍为占位 |
| `semantics`、`review`、`pipeline` | 后续语义校验、人工校准和完整流水线，仍为预留 |

业务模块不直接调用其他业务模块或外部 SDK；所有依赖由 `bootstrap.py` 装配，边界由测试保护。

## 本地开发与验证

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -c requirements.lock -e '.[dev]'
make local-run
make check
```

应用要求 Python 3.12+，Docker 使用 Python 3.13。默认本地收件目录为 `examples/mock`，工作数据写入已被 Git 忽略的 `data/preparation`。实际运行配置在 `.env`，模板为 `.env.example`。

`make check` 检查模块依赖、通用订单映射、银行全部 CSV/XLSX、HTTP 接口、错误输入、模型协议、重试和持久化，并检查 OpenAPI 与离线 mock 契约。
模型协议及 MySQL 连接行为使用替身测试；真实模型效果和银行数据库接入需要配置对应服务后联调。

离线 mock 位于 [examples/mock](examples/mock/README.md)：1,000 个合成客户、16,700 条旅程。`make mock` 重新生成最终文件，`make mock-check` 校验；生成脚本仍不依赖服务和数据库。

`make mock-db` 可启动项目自己的 Docker MySQL，并将最终 mock 导入 `customer_tags` 与 `customer_journey_events` 两张表。连接方式、字段说明和查询示例见 [数据库说明](docs/database.md)。业务数据装载使用独立脚本，不写进通用导入、转换服务。

架构：[模块边界](docs/architecture.md)；接口：[交接契约](docs/contracts.md)；协作：[开发约定](docs/development.md)。
