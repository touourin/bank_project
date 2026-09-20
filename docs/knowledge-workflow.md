# 文档图谱、实体消歧与 BOID 挂载

## 迁移范围

源代码来自用户指定的 `graphrag-main` 项目，运行时不再依赖该项目目录。

| 已有实现 | 当前路径及适配 |
| --- | --- |
| `unified-search-app/app/ingestion/parsers.py` | `src/bank_project/graphrag/parsers.py`：完整文档解析能力，当前工作台提供 TXT 接口 |
| `unified-search-app/app/ingestion/service.py` | `graphrag/jobs.py`：原子写入、文档来源、持久任务、进程锁、后台启动及重试 |
| `unified-search-app/app/ingestion/worker.py` | `graphrag/worker.py`：原生索引、进度、Parquet 和 LanceDB 校验后才发布 |
| 原应用问答和图谱读取 | `graphrag/service.py`：原生 Local / Global / DRIFT 调用与完整产物，替换 Streamlit 状态/缓存为 FastAPI 服务 |
| `packages/graphrag/graphrag/entity_resolution/` | `resolution/engine/`：候选、契约、同义词、向量检索、证据判决及模型判决；外层新增图适配、人工审核、快照和可撤销合并 |
| 当前项目 `alignment/retrieval.py`、`matching.py`、`catalog.py` | `knowledge/service.py` 直接复用传输、版本检查、候选评分门槛与本体目录 |

相关 MIT 许可证保存在迁入包内。源项目数据、密钥、虚拟环境、Streamlit 页面与离线评测报告不作为运行依赖搬入。React 页面使用本项目公共组件，接入完整后端流程；原项目的离线对比查看器不能执行实际图合并，因此新增了审核与派生图生成逻辑。

## TXT 与 GraphRAG

数据接入支持 TXT（20 MiB / 文件），按 UTF-8、带 BOM 的 UTF-8 或 GB18030 解码。上传生成独立文档数据集，原始来源名和正文摘要进入 manifest。接入不要求已安装 GraphRAG；启动索引时检查依赖和模型配置。

运行时固定 GraphRAG 2.5.0，与原应用相同，同时固定关键 pandas、numpy、pyarrow、LanceDB 和 OpenAI 客户端版本。Docker 使用 Python 3.12 并安装完整依赖，本地按 README 的 extra 安装。执行路径为文档 → 文本块 → 实体/关系 → 社区/社区报告 → 向量索引 → GraphRAG 查询，使用原生 `build_index` 和 `local_search` / `global_search` / `drift_search`。[官方索引流程](https://microsoft.github.io/graphrag/index/overview/)、[官方查询说明](https://microsoft.github.io/graphrag/query/overview/)。

聊天配置默认继承 `BANK_MODEL_*`，可用 `BANK_GRAPHRAG_CHAT_MODEL`、`BANK_GRAPHRAG_API_BASE`、`BANK_GRAPHRAG_API_KEY` 单独指定。向量配置为 `BANK_GRAPHRAG_EMBEDDING_MODEL`、`BANK_GRAPHRAG_EMBEDDING_API_BASE`、`BANK_GRAPHRAG_EMBEDDING_API_KEY`；地址和密钥省略时继承聊天服务，但模型名必须与供应方兼容，例如示例 DashScope 配置使用 `text-embedding-v4`。模型密钥从运行环境注入，不进入图谱或公开任务记录。上传正文会发送给所配置模型。

后台索引不依赖页面保持打开；进度、尝试次数、错误和产物保存到 `BANK_DATA_DIR/graphrag/`。意外中断显示可重试，只有通过产物校验后才提供图谱/问答。Local 使用实体关系和原文块，Global 使用社区报告，DRIFT 使用社区及局部检索；回答依据由原生 GraphRAG 查询上下文返回。

## 两类图谱消歧

GraphRAG 从完整 entities / relationships 读取；DB 固定到已发布 `BankAlignmentVersion`，分别分页读取所有节点及所有边，与发布计数核对。不会将旧的 50 节点预览或页内关系当作全量输入。

统一图记录保留节点和关系的 `properties` 全部原值；DB 额外保留存储属性。消歧检索结合名称、别名、字段身份线索、描述与关系上下文。模型可用时复用项目 JSON 模型适配器进行证据判决；候选相似度是筛选线索，不是同一实体概率。

每个候选展示原节点、原因、分数、证据、属性冲突。所有实际合并均须在页面接受，支持指定保留节点、拒绝、撤销和人工补充候选。修订号防止多人/多窗口覆盖；已拒绝的实体对不能通过第三节点间接合并。输出保留完整成员记录、冲突值和成员映射；关系端点改指合并节点，同时保存原端点。平行边、自环及原边属性不丢弃。

结果存在 `BANK_DATA_DIR/resolution/runs.sqlite3`，原始快照不变。审核记录包含操作者标签、备注、动作、时间及修订号；标签由审核人员填写，并非独立身份认证。后台分析中断可重新发起。候选预算及未覆盖提醒会显式返回，未被召回的节点仍保留，支持手工指定节点核验。

## 本体匹配

第三步的“节点与边匹配”固定本体 revision 和快照 SHA256，使用现有 retrieve 客户端及 `matching.decide`。高置信度节点追加 `boid`，低置信度候选待人工核对。关系类型只能来自匹配端点在本体中的显式有向关系；原边已有类型与其精确一致时自动采用，否则人工选择，不因只有一条本体关系就制造事实。

过程、原始检索 trace 与审核分开保存到 `BANK_DATA_DIR/knowledge/matches.sqlite3`。人工修改节点 BOID 后重新校验关联边类型。结果只在节点包裹层追加 `boid`、边包裹层追加 `edge_type`，不改名称、ID、描述、权重、原类型、端点或证据；原生记录全部留在 `properties`。

## 接口与结果边界

接口沿用项目 Bearer / Origin 防护：

| 路径（前缀 `/api/v1`） | 用途 |
| --- | --- |
| `/graphrag/uploads`、`/graphrag/datasets` | TXT 上传、数据集/任务列表 |
| `/graphrag/datasets/{key}/index` | 开始/重试后台索引 |
| `/graphrag/datasets/{key}/graph`、`/query` | 完整图、带上下文的 GraphRAG 问答 |
| `/knowledge/sources` | 可用 GraphRAG 数据集及已发布 DB 版本 |
| `/resolution/runs`、`/{id}/decisions`、`/{id}/manual` | 分析、候选审核、人工指定节点 |
| `/resolution/runs/{id}/original`、`/graph` | 原始快照、完整消歧派生图 |
| `/knowledge/matches`、`/{id}/concepts`、`/{id}/decisions` | BOID/边类型匹配、候选查询和人工审核 |
| `/knowledge/matches/{id}/graph` | 完整追加标注的图谱 |

消歧/匹配结果是持久化的独立派生图，可浏览并导出 JSON；不会就地改写来源 Neo4j 或 Parquet。问答明确使用原 GraphRAG 索引，消歧图没有重建社区报告/向量。界面为性能限制绘图规模时，使用完整 JSON 导出取得全部节点和关系。

两种派生结果会进入版本选择器，可按“原图 → 消歧 → 匹配”或“原图 → 匹配 → 消歧”继续处理。来源标识包含任务与修订号；父任务已修改时旧选择返回 409，避免静默换图。已创建的子任务保存独立完整输入快照，父任务的后续审核不会更改子任务证据。再次消歧会包含此前被合并成员的别名、原文与身份线索。

## 验证

`make check` 包含原属性保留、DB 跨页关系、低置信度门槛、审核修订冲突、拒绝/撤销合并及鉴权回归。安装 GraphRAG extra 后，测试实际执行无外部模型调用的原生读入/分块/Parquet 流程，并验证原生三种问答方法的调用契约。`make frontend-check` 检查格式、编译和浏览器流程。

真实抽取、社区报告、向量服务和模型回答需要部署环境模型服务；MySQL / Neo4j 实库回归按开发说明使用独立测试库。模拟服务和无模型流水线测试不能替代这些服务的真实联调。
