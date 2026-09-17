# 接口契约草案

当前版本为 0.1 草案，仅用于模块交接和 API 文档。业务 schema、必填证据、引用完整性、身份生成、幂等及事务规则均未实现。

## 模块接口

| 接口 | 交接形式 |
| --- | --- |
| Ingestor | ImportRequest → SourceArtifact |
| Extractor | ImportRequest + SourceArtifact → ExtractionBatch |
| Resolver | ExtractionBatch → ResolutionResult |
| GraphBuilder | ExtractionBatch + ResolutionResult → GraphPatch |
| SemanticValidator | ExtractionBatch → SemanticReport |
| GraphWriter | GraphPatch + RunResult → 无返回值 |
| GraphReader | GraphQuery → QueryResult；按标识获取证据 |
| RunReader | 按数据集及运行标识读取 RunResult |
| PipelineHandler | 导入、批次处理及运行查询入口 |
| HealthCheck | 异步基础设施就绪检查与连接关闭 |

这些是 Python Protocol 契约，存储与业务实现均待接入。GraphReader 使用查询请求，不要求调用方加载整份图谱。

## 共享模型

ExtractionBatch 包含 dataset_id、batch_id、producer_version，以及 sources、evidence、entities、events、relations。
候选实体保留来源键；候选事件保留时间及参与角色；关系保留端点和可选事件引用。

GraphPatch 仅描述通用节点、边、来源和证据。节点类型、关系类型、属性字段与 Neo4j 存储形式尚未确定。
GraphQuery 的 query_type 和 parameters 也是预留字段，当前没有已支持的查询类型。

ImportRequest 仅描述数据集、批次、来源系统和 source_uri；当前不会读取此 URI。
RunResult 的状态枚举是未来接口形状，不代表已经存在任务执行或任务持久化。

Pydantic 目前只负责字段类型及基础格式检查。具体字段以 src/bank_project/contracts/models.py 为准；修改草案时同时调整调用方与 docs/openapi.json。

## HTTP 入口

| 方法与路径 | 当前行为 |
| --- | --- |
| GET /health | 返回进程状态、版本、framework 模式 |
| GET /ready | 检查配置的基础设施；不可用或超时返回 503 |
| POST /api/v1/imports | 501 尚未实现 |
| POST /api/v1/batches | 501 尚未实现 |
| GET /api/v1/runs/{run_id} | 501 尚未实现 |
| POST /api/v1/graph/query | 501 尚未实现 |
| GET /api/v1/evidence/{evidence_id} | 501 尚未实现 |

业务请求格式合法时返回 501，格式错误时由 FastAPI 返回 422；不会返回模拟成功结果。
501 响应统一为 ErrorResponse（error、detail），OpenAPI 同步声明此结构。
查询运行或证据需提供 dataset_id 查询参数。接口路径和响应模型是占位草案，后续按确认的需求实现。
