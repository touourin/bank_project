# bank_project

数据与图谱工作台：① Excel / CSV、TXT 或只读 MySQL 接入；② 表格数据对齐本体并生成业务图谱；③ TXT 按 GraphRAG 路径建立索引、浏览图谱、问答及 BOID / 边类型匹配；④ 对 GraphRAG / DB 图谱进行实体消歧及人工校验。

## 启动

仅查看第三、四步的前端演示：在 `frontend` 目录运行 `npm install && npm run dev`，打开 [前端演示](http://127.0.0.1:5173/?demo=1)。内置示例图谱、问答、本体匹配和消歧候选，无需启动后端；合并、保留和撤销仅更新当前页面中的示例数据，刷新即可重置。直接打开第四步可使用 `?demo=1&step=4`，页面也可退出演示回到正常流程。

首次部署先准备 Python 开发环境（见 [开发说明](docs/development.md)），复制 `.env.example` 为 `.env` 并配置模型、retrieve、本体和业务图连接，然后执行：

```bash
make staging-up                  # 启动 MySQL，创建独立暂存库与账号
make build
make run                         # API、接入 worker、前端
# 需要生成图谱时：
docker compose --profile graph up -d --wait neo4j
```

日常启动用 `make run`；代码更新后 `make build && make run`。只运行 API 而不启动 `intake-worker`，表格/MySQL 接入任务会留在队列中；GraphRAG 使用 API 启动的独立索引进程。

- 前端：<http://127.0.0.1:5173>
- 接口文档：<http://127.0.0.1:8000/docs>
- `/health`：API 存活；`/ready`：API 及内部暂存库可用，不检查模型、外部源和 Neo4j。

默认单文件上限 512 MiB；上传后返回任务，页面显示解析进度、取消和失败重试。模型按表解释字段，retrieve 按表/列匹配节点，均不逐行调用。确定性实例生成依照人工确认的图模板，跨表同身份可以补充属性；不确定或冲突的事实不会自动覆盖。具体边界见 [数据接入](docs/intake.md)、[第二步](docs/alignment.md)、[容量验证](docs/large-data.md)。

TXT 使用独立的文档接入通道（单文件 20 MiB，UTF-8 / GB18030），在后台执行原生 GraphRAG 2.5.0 分块、实体/关系抽取、社区报告及向量索引，完成后支持 Local / Global / DRIFT / Basic 问答。Docker 镜像包含完整依赖；本地安装使用 `.venv/bin/python -m pip install -c requirements.lock -e '.[dev,mock,graphrag]'`。聊天与向量模型配置见 `.env.example`；仅配置聊天模型不足以完成索引。

## 使用步骤

1. **数据接入**：表格沿用 Excel / CSV / MySQL 流程；文档选择 TXT 和建图方案（原企业中文方案 / 通用文档），保存后开始索引，可查看进度和重试。
2. **本体对齐与图谱生成**：现有表格匹配、人工修改、模板确认和 DB 建图流程保持可用。
3. **GraphRAG 图谱与问答**：选择索引完成的数据集，按中心实体、深度、类型或核心网络浏览；流式问答、四方法对比与答案依据子图联动，支持社区报告和推荐问题。“节点与边匹配”复用现有 retrieve、本体快照及置信度逻辑，支持人工调整 BOID / 边类型和导出完整图。
4. **实体消歧**：选择 GraphRAG 数据集、聚合前记录或已发布的 DB 图谱版本，配置原消歧方法、模型预算和人工/自动应用策略。先检查身份限定、再由大模型分析；默认“合并建议”仅展示模型建议同一实体且证据校验通过的节点对，附模型理由与原文引用，供用户确认或拒绝、指定保留节点。其他结果保存在“分析记录”，不进入待确认队列。确认后显示多个原节点到合并节点的过程，支持撤销和导出派生图。

消歧后的版本可继续本体匹配，匹配后的版本也可继续消歧；版本选择会保留前一步结果及来源记录。

消歧和匹配会保存独立结果与审核记录；原 Neo4j 版本、GraphRAG Parquet、原始节点/边属性均保留。匹配只追加 `boid` / `edge_type`。问答继续依据原 GraphRAG 索引；派生图不会自动重建社区报告和向量索引。操作、数据契约与迁移范围见 [文档图谱与实体消歧](docs/knowledge-workflow.md)。

## 结构

| 路径 | 职责 |
| --- | --- |
| `src/bank_project/intake/` | 通用文件解析、只读源适配、后台接入 |
| `src/bank_project/staging/` | 内部 MySQL 批次、任务、磁盘关联索引与模板编译 |
| `src/bank_project/alignment/` | 本体匹配、过程记录、人工修改、模板与图谱发布 |
| `src/bank_project/graphrag/` | 迁入的文档解析、后台索引、原生 GraphRAG 查询与完整图适配 |
| `src/bank_project/resolution/` | 迁入的消歧引擎、图谱证据适配、人工审核、可撤销合并与审计 |
| `src/bank_project/knowledge/` | DB 全量图读取、复用 retrieve 的 BOID/边类型挂载 |
| `frontend/` | React、TypeScript、Ant Design 数据工作台 |
| `compose.yaml` | API、worker、前端、MySQL及可选业务 Neo4j |
| `compose.ontology.yaml` | 独立本地本体数据库 |
| `examples/mock/` | 最终 mock 文件及字段说明 |
| `scripts/` | mock 工具、暂存初始化、旧批次迁移、接口导出 |

外部源与内部暂存分别使用 `BANK_MYSQL_*` / `BANK_STAGING_MYSQL_*`，不共享账号。密码不提交到 Git。MySQL 数据在 `mysql-data` 卷；上传原件、加密凭据密钥、分析记录、GraphRAG 索引、消歧/匹配快照及审计在 `intake-data` 卷。备份需保留两者；请勿通过 `docker compose down -v` 清空持久数据。旧批次与旧图谱均保留。

## 验证

```bash
make check
make frontend-check
# 可选：先将 BANK_STAGING_MYSQL_* 指向以 _test 结尾的独立测试库
BANK_TEST_MYSQL=1 .venv/bin/python -m pytest tests/test_streaming_intake.py
```

[开发说明](docs/development.md) · [架构](docs/architecture.md) · [数据库配置](docs/database.md) · [前端组件](docs/frontend.md) · [本体环境](docs/ontology.md)
