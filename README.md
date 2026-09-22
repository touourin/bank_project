# bank_project

数据与图谱工作台：① Excel / CSV、TXT 或只读 MySQL 接入；② 统一数据转换工作区，按来源建图并对齐本体；③ 实体消歧与人工校验；④ 统一图谱版本浏览及文档问答分析；⑤ 从本体 WHY 生成风险规则，经审核后查询交易实例。

## 启动

查看转换、消歧与分析的前端演示：在 `frontend` 目录运行 `npm install && npm run dev`，打开 [前端演示](http://127.0.0.1:5173/?demo=1)。内置示例图谱、问答、本体匹配和消歧候选，无需启动后端；合并、保留和撤销仅更新当前页面中的示例数据，刷新即可重置。直接打开转换或消歧可使用 `?demo=1&workspace=conversion` / `?demo=1&workspace=resolution`（旧的 `step=4` 消歧链接仍兼容），页面也可退出演示回到正常流程。

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
2. **数据转换**：在同一工作区选择“表格 / MySQL”或“TXT 文本”。表格按结构与样例分析、核对模板后批量生成；TXT 按既有 GraphRAG 流程分块、抽取及索引，随后核对节点与边的本体匹配。切换来源类型保留草稿和任务状态。转换页提供结果预览，问答集中在第四步。
3. **实体消歧**：选择文档原图、聚合前记录、已发布表格图或前序派生版本，检查模型证据与身份限定，确认、拒绝或撤销合并。现有候选过滤、审核过程和原始证据继续保留。
4. **图谱浏览与分析**：统一选择原始图谱、匹配或消歧修订。表格原图按所选版本分页浏览和全量搜索；文档原始索引提供四种问答方式与社区报告。派生图和聚合前记录只提供浏览、来源与导出，不使用原索引冒充其问答结果，可明确切换回原始文档索引。
5. **风险规则**：选择固定本体版本的 BO 节点，沿 IS_A 和明确方向的业务关系寻找 WHY，生成包含来源、路径与实例作用域的 RulePack。人工核对并批准后，选择同本体版本的 DB 图谱、映射原始交易字段及分析时间，执行现金累计阈值、对手地区或概念范围聚合规则。通过统一的 `BANK_ONTOLOGY_BASE_URL` 与表格、TXT、本体浏览共用远端目录及 WHY，任务依据自动归档；[风险规则说明](docs/risk.md) 包含接入、导入和审核执行契约。

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
| `src/bank_project/risk/` | 版本化 WHY 传导、RulePack 校验、人工审核、当前 DB 图谱风险查询 |
| `frontend/` | React、TypeScript、Ant Design 数据工作台 |
| `compose.yaml` | API、worker、前端、MySQL及可选业务 Neo4j |
| `compose.ontology.yaml` | 可选的本地本体开发库；正式本体来源见 [共享本体](docs/ontology.md) |
| `examples/mock/` | 最终 mock 文件及字段说明 |
| `scripts/` | mock 工具、暂存初始化、旧批次迁移、接口导出 |

外部源与内部暂存分别使用 `BANK_MYSQL_*` / `BANK_STAGING_MYSQL_*`，不共享账号。密码不提交到 Git。MySQL 数据在 `mysql-data` 卷；上传原件、加密凭据密钥、分析记录、GraphRAG 索引、消歧/匹配快照、风险规则及证据与审计在 `intake-data` 卷。备份需保留两者；请勿通过 `docker compose down -v` 清空持久数据。旧批次与旧图谱均保留。

## 验证

```bash
make check
make frontend-check
# 可选：先将 BANK_STAGING_MYSQL_* 指向以 _test 结尾的独立测试库
BANK_TEST_MYSQL=1 .venv/bin/python -m pytest tests/test_streaming_intake.py
```

[开发说明](docs/development.md) · [架构](docs/architecture.md) · [数据库配置](docs/database.md) · [前端组件](docs/frontend.md) · [本体环境](docs/ontology.md)
