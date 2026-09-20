# 项目结构

```text
HTTP / React 页面
    ├─ intake：上传流 / 外部只读 MySQL → 后台接入任务
    │          CSV / XLSX 流式解析 → 批次写入端口
    ├─ staging：独立 MySQL 批次、行、任务、关联索引、模板编译
    ├─ alignment：结构与样例 → 字段含义解释 → retrieve 匹配 → 人工确认模板 → 分批业务图谱
    ├─ graphrag：TXT → 持久后台索引 → Parquet / LanceDB → 图谱 / 问答
    ├─ resolution：完整 GraphRAG / DB 图 → 候选证据 → 人工审核 → 可撤销派生图
    └─ knowledge：GraphRAG 实体 → 现有 retrieve 决策 → 追加 BOID / 边类型
```

解析器只接收文件和写入端口，不掌握数据库凭据。外部 MySQL 适配器只读来源，通过相同写入端口交付数据。内部存储不调用文件解析器或模型。`main.py` 集中装配依赖，API 路由负责输入、鉴权与响应，不承载匹配算法。

| 包 | 责任 |
| --- | --- |
| `intake/` | 数据契约、值保持、文件/源适配、上传落盘与独立 worker |
| `staging/` | 内部 MySQL 连接、不可变批次、持久队列、关联索引、模板实例编译 |
| `alignment/` | 本体/LLM 适配、可追踪分析、人工修改、模板校验、图谱版本发布 |
| `graphrag/` | 迁入文档接入与索引 worker、原生 GraphRAG 查询及完整产物适配 |
| `resolution/` | 迁入消歧引擎、两类图谱证据适配、快照与审核、合并过程和派生图 |
| `knowledge/` | 已发布 DB 版本全量读取、复用匹配逻辑的追加标注 |
| `api/` | 两步路由、Bearer / Origin 防护、请求限额、健康检查 |
| `frontend/src/ui/` | 公共主题、面板、表格、反馈、分页、确认弹窗 |
| `frontend/src/features/` | 各步骤页面、契约、API、业务组件 |

前端功能依赖公共组件，公共组件不依赖具体步骤。接入页轮询后台任务；第二步只保存结构和样例引用，确认后的模板才驱动全量实例化。数据格式与银行表名无关，不把客户标签或旅程写死在接入层。

第二步中 `planning.py` 约束不含本体 ID 的字段解释，`retrieval.py` 实现独立检索端口及网络/缓存边界，`matching.py` 根据版本、候选和分数作采用决策。`analyzer.py` 编排这些步骤和过程保存。实体分组由模型解释，节点由 retrieve 确定；接口原始结果与人工修改分开记录。

新 MySQL 接入批次不可修改；隐藏批次不破坏历史引用。行值为字符串或 null，推断类型不改变值，不自动推断业务键。图模板明确字段归属、精确身份和关系依据，模型不会逐行参与确定性建图。

运行部署是单 API、独立接入 worker、前端、MySQL、业务 Neo4j，本体检索使用外部 retrieve，目录与版本校验使用独立本地快照。分析版本/租约目前仍在小体量 SQLite 中；MySQL 暂存解决大表内存与关联问题，不代表已经支持多 API 副本。更换编排或任务存储不要求重写文件解析器。

边界与使用见 [数据接入](intake.md)、[第二步](alignment.md)、[数据库](database.md)、[容量验证](large-data.md)。
