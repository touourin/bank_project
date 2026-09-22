# 文档图谱、实体消歧与 BOID 挂载

## 迁移范围

源代码来自用户指定的 `graphrag-main` 项目，运行时不再依赖该项目目录。

本轮已补齐原企业应用的业务配置、图谱交互、流式问答与聚合前消歧。源文件对应关系、真实验证及边界见 [迁移核查与验收](graphrag-migration-audit.md)。页面按本项目四步工作流组织，底层保留原方法；模型输出本身不保证逐字或节点数一致。

| 已有实现 | 当前路径及适配 |
| --- | --- |
| `unified-search-app/app/ingestion/parsers.py` | `src/bank_project/graphrag/parsers.py`：完整文档解析能力，当前工作台提供 TXT 接口 |
| `unified-search-app/app/ingestion/service.py` | `graphrag/jobs.py`：原子写入、文档来源、持久任务、进程锁、后台启动及重试 |
| `unified-search-app/app/ingestion/worker.py` | `graphrag/worker.py`：原生索引、进度、Parquet 和 LanceDB 校验后才发布 |
| 原应用问答和图谱读取 | `graphrag/service.py`：原生 Local / Global / DRIFT / Basic、流式输出、问题生成与完整产物，替换 Streamlit 状态/缓存为 FastAPI 服务 |
| `packages/graphrag/graphrag/entity_resolution/` | `resolution/engine/`：候选、契约、同义词、向量检索、证据判决及模型判决；外层新增图适配、人工审核、快照和可撤销合并 |
| 当前项目 `alignment/retrieval.py`、`matching.py`、`catalog.py` | `knowledge/service.py` 直接复用传输、版本检查、候选评分门槛与本体目录 |
| 公共转换审核 `conversion/` | 表格与 TXT 共用任务就绪、本体快照一致性、审核记录和派生版本检查；源格式解析及生成策略分别保留 |

相关 MIT 许可证保存在迁入包内。源项目路径、密钥、虚拟环境和 Streamlit 不作为运行依赖。13 个企业提示词随 Python 包发布，消歧离线引擎、CLI、评测和报告校验代码完整迁入；本机 5 份已验证历史实验复制到运行数据目录供查看。React 页面使用本项目公共组件，接入完整后端流程；原项目的离线对比查看器不能执行实际图合并，因此新增了审核与派生图生成逻辑。

## TXT 与 GraphRAG

数据接入支持 TXT（20 MiB / 文件），按 UTF-8、带 BOM 的 UTF-8 或 GB18030 解码。上传生成独立文档数据集，原始来源名和正文摘要进入 manifest。接入不要求已安装 GraphRAG；启动索引时检查依赖和模型配置。

运行时固定 GraphRAG 2.5.0，与原应用相同，同时固定关键 pandas、numpy、pyarrow、LanceDB 和 OpenAI 客户端版本。Docker 使用 Python 3.12 并安装完整依赖，本地按 README 的 extra 安装。执行路径为文档 → 文本块 → 实体/关系 → 社区/社区报告 → 向量索引 → GraphRAG 查询，使用原生 `build_index` 和 `local_search` / `global_search` / `drift_search` / `basic_search`。[官方索引流程](https://microsoft.github.io/graphrag/index/overview/)、[官方查询说明](https://microsoft.github.io/graphrag/query/overview/)。

聊天配置默认继承 `BANK_MODEL_*`，可用 `BANK_GRAPHRAG_CHAT_MODEL`、`BANK_GRAPHRAG_API_BASE`、`BANK_GRAPHRAG_API_KEY` 单独指定。向量配置为 `BANK_GRAPHRAG_EMBEDDING_MODEL`、`BANK_GRAPHRAG_EMBEDDING_API_BASE`、`BANK_GRAPHRAG_EMBEDDING_API_KEY`；地址和密钥省略时继承聊天服务，但模型名必须与供应方兼容，例如示例 DashScope 配置使用 `text-embedding-v4`。模型密钥从运行环境注入，不进入图谱或公开任务记录。上传正文会发送给所配置模型。

后台索引不依赖页面保持打开；进度、尝试次数、错误和产物保存到 `BANK_DATA_DIR/graphrag/`。意外中断显示可重试，只有通过产物校验后才提供图谱/问答。Local 使用实体关系和原文块，Global 使用社区报告，DRIFT 使用社区及局部检索；回答依据由原生 GraphRAG 查询上下文返回。

## 两类图谱消歧

GraphRAG 从完整 entities / relationships 读取；DB 固定到已发布 `BankAlignmentVersion`，分别分页读取所有节点及所有边，与发布计数核对。不会将旧的 50 节点预览或页内关系当作全量输入。

统一图记录保留节点和关系的 `properties` 全部原值；DB 额外保留存储属性。消歧检索结合名称、别名、字段身份线索、描述与关系上下文。模型可用时复用项目 JSON 模型适配器进行证据判决；候选相似度是筛选线索，不是同一实体概率。

分析时依次显示别名检查、候选检索、候选比较，逐项更新已处理数量、失败/预算跳过数量及耗时。证据消歧按身份规则及模型判定；同义词＋大模型方法先扩展别名、再执行 model-only 判定。两种方法均与同输入的 legacy_title_v1 基线比较。默认判定预算 2000、别名预算 200，模型阶段默认并发 8，请求较多时可能持续数分钟；等待请求期间每 10 秒更新耗时。失败或超出预算的结果保留在分析记录中，不生成待确认建议，也不会自动合并。

默认打开“合并建议”，仅显示大模型建议同一实体、原文引用校验通过且没有身份限定阻断的结果，展示模型理由、左右原文引用、原节点和属性差异。不同实体、证据不足、调用失败、预算未覆盖或未配置模型的结果归入“未建议合并”，不计入待确认数量；身份冲突或限定不完整的结果为“自动不合并”。所有结果均可在“分析记录”查看，历史记录读取也使用同一规则。默认 review 策略由人工接受合并；可选择 apply 自动应用引擎经 complete-link 检查通过的分组，自动操作同样写入审核记录且支持撤销。支持指定保留节点、拒绝、撤销和人工补充候选。修订号防止多人/多窗口覆盖；已拒绝的实体对不能通过第三节点间接合并。输出保留完整成员记录、冲突值和成员映射；关系端点改指合并节点，同时保存原端点。平行边、自环及原边属性不丢弃。

GraphRAG 的可引用 `context` 仅包含节点关联的文档原文，不再拼入抽取后的 `title`、`description` 等属性；这些属性仅用于检索和定位。缺少原文时不以生成属性替代。数据库图谱的引用依据为来源字段，页面标注为“数据库来源字段”。“大模型引用与来源”区分文档原文、数据库字段、生成节点属性及未核实引用，展示可定位的原文上下文。历史任务读取时对照保存的原始快照重新校验；引用只存在于生成属性中的旧建议退出待确认队列，普通确认接口拒绝接受。历史原判决与既有人工决定不改写。`id`、连接数量等属性值不同统一展示为属性差异，不冒充实体身份冲突。

每组记录在实体对下方直接显示“原文与引用”：左右并排显示模型引用、实际引用来源、完整关联原文和分块 ID，逐字高亮匹配位置；节点 `description` 单独折叠标明为抽取描述。“展开阅读”可在宽窗口中查看同一份原文，无须重新请求。多个分块显示分析时保存的拼接文本，不推测原快照中未保存的分块边界。缺失原文时明确提示，不用描述替代。数据库来源显示完整原始字段。分析完成后，当前页候选自动加载原文；分析预览阶段提示等待快照就绪。原文来自该消歧任务的不可变输入快照，后续索引和人工合并不会改写它。

差异区域按实体名称并排对照，优先显示名称、类型、描述，已知字段使用中文标签；窄屏按实体依次排列。GraphRAG 的 `id`、`human_readable_id`、`text_unit_ids`、`degree`、`frequency` 单独折叠为“系统记录差异”，不计入内容差异数量。数据库字段及未知字段保持可见，不按 `id` 名称或后缀猜测其用途。历史成员及同一节点的多份来源值全部保留，展示调整不修改原始差异和审核结果。

结果存在 `BANK_DATA_DIR/resolution/runs.sqlite3`，原始快照不变。审核记录包含操作者标签、备注、动作、时间及修订号；标签由审核人员填写，并非独立身份认证。后台分析中断可重新发起。候选预算及未覆盖提醒会显式返回，未被召回的节点仍保留，支持手工指定节点核验。

## 本体匹配

第二步 TXT 工作区的“节点与边匹配”固定本体 revision 和快照 SHA256，使用现有 retrieve 客户端及 `matching.decide`。高置信度节点追加 `boid`，低置信度候选作为匹配建议展示。界面复用表格转换的检索依据组件，直接展示候选概念、原始得分与当前挂载，并支持筛选、展开查看候选和人工修改。用户可整体采纳有效的节点建议，随后按已采用的端点重新校验边；唯一有向关系可作为本次人工采纳的类型建议，多种候选仍需逐项选择。整体采纳按修订号原子提交并逐项记录审核，不覆盖已人工修改或清除的结果。关系类型只能来自匹配端点在本体中的显式有向关系；原边已有类型与其精确一致时自动采用，否则人工选择，不因只有一条本体关系就制造事实。

过程、原始检索 trace 与审核分开保存到 `BANK_DATA_DIR/knowledge/matches.sqlite3`。人工修改节点 BOID 后重新校验关联边类型。结果只在节点包裹层追加 `boid`、边包裹层追加 `edge_type`，不改名称、ID、描述、权重、原类型、端点或证据；原生记录全部留在 `properties`。

## 接口与结果边界

接口沿用项目 Bearer / Origin 防护：

| 路径（前缀 `/api/v1`） | 用途 |
| --- | --- |
| `/graphrag/uploads`、`/graphrag/datasets` | TXT 上传、数据集/任务列表 |
| `/graphrag/datasets/{key}/index` | 开始/重试后台索引 |
| `/graphrag/datasets/{key}/graph`、`/query` | 完整图、带上下文的 GraphRAG 问答 |
| `/knowledge/sources` | 可用 GraphRAG 数据集、已发布 DB 版本及派生修订 |
| `/knowledge/graph?source_kind=&source_id=` | 读取文档或派生图谱，强制核对指定修订；原始 DB 图谱使用分页接口 |
| `/alignment/graph/{version}/overview` | 固定表格图谱版本的统计及分类概览，配合该版本节点分页 |
| `/resolution/runs`、`/{id}/decisions`、`/{id}/manual` | 分析、候选审核、人工指定节点 |
| `/resolution/runs/{id}/original`、`/graph` | 原始快照、完整消歧派生图 |
| `/resolution/runs/{id}/candidates/{candidate_id}/sources` | 按需读取候选左右节点的完整来源快照、引用定位与独立描述 |
| `/knowledge/matches`、`/{id}/concepts`、`/{id}/decisions`、`/{id}/accept-proposals` | BOID/边类型匹配、候选查询和人工审核 |
| `/knowledge/matches/{id}/graph` | 完整追加标注的图谱 |

消歧/匹配结果是持久化的独立派生图，可浏览并导出 JSON；不会就地改写来源 Neo4j 或 Parquet。问答明确使用原 GraphRAG 索引，消歧图没有重建社区报告/向量。界面为性能限制绘图规模时，使用完整 JSON 导出取得全部节点和关系。

两种派生结果会进入版本选择器，可按“原图 → 消歧 → 匹配”或“原图 → 匹配 → 消歧”继续处理。来源标识包含任务与修订号；父任务已修改时旧选择返回 409，避免静默换图。已创建的子任务保存独立完整输入快照，父任务的后续审核不会更改子任务证据。再次消歧会包含此前被合并成员的别名、原文与身份线索。

## 验证

`make check` 包含原属性保留、DB 跨页关系、低置信度门槛、审核修订冲突、拒绝/撤销合并及鉴权回归。安装 GraphRAG extra 后，测试实际执行无外部模型调用的原生读入/分块/Parquet 流程，并验证原生四种问答方法的调用契约。`make frontend-check` 检查格式、编译和浏览器流程。

真实抽取、社区报告、向量服务和模型回答需要部署环境模型服务；MySQL / Neo4j 实库回归按开发说明使用独立测试库。模拟服务和无模型流水线测试不能替代这些服务的真实联调。


## 原项目功能入口

- **第一步 / TXT 文本**：选择“企业情报 · 原项目中文方案”或“通用文档 · 跟随原文语言”，可调分块大小和重叠。企业方案使用原 8 类实体、中文业务提示词、1000/150 分块和分步模型（抽取/问答为聊天模型，摘要/社区为 fast 模型）。提示词哈希保存在数据集 `profile.json`，密钥只在内存注入。
- **第二步 / TXT 转换结果，第四步 / 图谱浏览**：核心网络、中心实体邻域、1–3 层深度、节点上限、类型和名称筛选、度数相关大小；点击节点可以重新居中。绘图上限不影响全量表格与导出。
- **第四步 / 图谱问答**：Local / Global / Basic 流式输出，DRIFT 完整返回；保留本次页面会话，四方法对比、生成推荐问题、检索过程、原文及社区证据。回答后切换“答案依据”，红色边框/边标记直接引用。社区报告单独提供层级、成员与报告全文。
- **第三步 / 实体消歧**：可选生成后的 GraphRAG 图、聚合前记录、DB 发布版本及前序派生版本。原图与聚合前记录是不同输入，不把已合并节点冒充原始提及。聚合前导出仍以 chunk 内实体记录为单位，抽取器已经在单块内部混合的信息无法恢复；歧义关系端点原样放入 metadata，不猜连边。
- **第三步 / 消歧方法与参数**：`evidence_v1`、`synonym_llm_v1`，review/apply、预算、并发、超时、候选/簇上限、同义词词表、向量输入及人工金标。输入 JSON 遵循原引擎契约；向量必须覆盖同一 corpus 的所有记录，金标仅用于评分，词表必须带审校来源。

数据库来源的事件使用参与方、事件类型和发生时间筛选疑似重复记录，支持原始英文列名和转换后的中文列名；事件中的客户号仅表示参与方，行号生成的展示名称不用于匹配身份。事件自身编号仍可召回候选，同一客户同一天的同类事件也必须经过证据判断与审核，不能仅凭组合字段自动合并。缺少可用身份线索的节点保留并显示提示；这不代表已证明没有重复。TXT 保留原文名称、别名与上下文的候选逻辑，两种来源共用后续判断和审核流程。

候选对总上限仍默认 20,000。超限会在候选对模型判断前停止，并保存已发现的候选数下界和配置上限；不会自动提高预算或静默截掉超额数据。修改参数后需新建分析，历史失败记录继续保留。

实验默认保存在 `BANK_DATA_DIR/resolution/experiments`，可用 `BANK_RESOLUTION_EXPERIMENTS_DIR` 指定其他目录。可复用的判决缓存按语料命名空间、模型版本/地址、原提示词及完整输入隔离。原 CLI 的 annotate / compare 命令同样可用：

```bash
.venv/bin/python -m bank_project.resolution.engine annotate --corpus corpus.json --output annotations.json
.venv/bin/python -m bank_project.resolution.engine compare --corpus corpus.json --output data/resolution/experiments/new-run --config resolver.json --gold gold.json
# 模型方法另加 --method synonym_llm_v1 --root <GraphRAG-2.5-数据集目录> --model-id default_chat_model --model-revision <部署版本>
```

新增 API：`POST /graphrag/datasets/{key}/query/stream`（NDJSON，status/token/result/error）、`GET .../records`、`GET .../reports`、`POST .../questions`；`GET /resolution/experiments`、`GET /resolution/experiments/{name}`、`GET .../{name}/download`。所有接口继承鉴权和同源约束。

表格与 TXT 的目录、retrieve、人工搜索和风险依据统一配置 `BANK_ONTOLOGY_BASE_URL`。每次新匹配固定本体 ID、revision 和目录哈希；人工修订读取自动归档的原始目录，远端升级不改变历史结果。详见 [共享本体](ontology.md)。
