# 交接契约

ExtractionBatch 的新输出为 schema_version 0.2；保留 0.1 的输入兼容标识。0.2 增加了来源原文件引用及事件 occurred_on 日期字段。

## 前两步接口

| 接口 | 交接形式 |
| --- | --- |
| SourceReader | ImportRequest → RawInput（只读，不解析） |
| SourceParser | RawInput + ImportRequest → ParsedSource（通用行或文本块） |
| RawStore | 原文件内容 ↔ SourceArtifact（摘要、内部地址） |
| PreparationStore | 按 dataset_id、batch_id 加锁并保存导入、状态和完整转换结果 |
| Ingestor | ImportRequest + 可选上传内容 → IngestionReceipt；读取回执 |
| Extractor | ExtractionRequest → ExtractionSummary；读取状态、结果、映射清单 |
| DocumentModel | 文本 → 类型化、带逐字证据的 DocumentExtraction |
| DocumentCache | 按来源、模型和提示词配置摘要复用已经校验的块结果 |

ImportRequest 的 table、sheet 为可选信息，没有银行表名枚举。
ExtractionRequest 的 mapping 为可选配置名称；自动匹配、显式匹配或 generic_record 回退模式见 preparation.md。

ExtractionBatch 包含 sources、evidence、entities、events、relations。
每个候选必须引用实际存在的证据；证据引用来源记录；关系端点和事件参与方必须存在。输出发布前统一校验，缺失引用或重复标识不作为成功结果交接。

EntityCandidate 保留来源外部键；仅配置 identity_scope=key 时按明确标识复用对象，不按姓名或名称消歧。事件保留 event_id、类型、参与方角色、发生日期及属性，关系保留方向、谓词和可选事件引用。

日期精度只有天时使用 occurred_on，occurred_at 保持空。银行快照日期 DT 与发生日期 OCCUR_DT 分开保留。金额等十进制字段输出为字符串，原始字段在 Evidence.fields 和原文件中留存。

文档实体和事件 properties、文档证据 fields 带 review_required。逐字证据与结构校验并不等价于语义准确性验证，后续仍需消歧、校验和复核。

## HTTP 入口

| 路径 | 行为 |
| --- | --- |
| GET /health | 进程状态、版本、preparation 模式 |
| GET /ready | 已启用的 Neo4j 健康检查，失败 503 |
| GET /api/v1/mappings | 配置清单 |
| POST /api/v1/imports | 收件目录文件或已启用 MySQL 来源导入 |
| POST /api/v1/imports/upload | multipart 文件上传 |
| GET /api/v1/imports/{batch_id}?dataset_id=... | 导入回执 |
| POST /api/v1/extractions | 同步转换并返回摘要 |
| GET /api/v1/extractions/{batch_id}/status?dataset_id=... | not_started/running/failed/completed |
| GET /api/v1/extractions/{batch_id}?dataset_id=... | 完整候选批次 |
| POST /api/v1/batches | 后续批次处理，501 |
| GET /api/v1/runs/{run_id} | 后续流水线运行记录，501；前两步请用 extraction status |
| POST /api/v1/graph/query | 图谱查询，501 |
| GET /api/v1/evidence/{evidence_id} | 图谱侧证据查询，501；前两步的证据在 ExtractionBatch 内 |

输入错误返回 422，数据缺失 404，冲突或同批并发 409，超限 413，依赖不可用 503。业务错误使用 ErrorResponse；FastAPI 参数绑定错误使用其标准 detail 格式。配置 API token 后业务路径需 Bearer 鉴权；健康与文档页不包含数据内容。

后续 Resolver、GraphBuilder、SemanticValidator、GraphWriter、GraphReader、PipelineHandler 保持接口边界，并未在本次实现。Neo4j 适配器仍只负责连接和就绪检查。
