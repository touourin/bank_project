# WHY 风险规则

第五步“风险规则”将已有 BO / BFO 节点的 WHY 转为可审核、可执行的 RulePack。只读取本体和已发布的业务图谱，不修改来源节点、交易或已发布版本。

```text
固定本体版本与快照 → 选择 BO 节点 → 搜索适用 WHY
  → LLM 映射谓词及参数 → 服务端校验、固定作用域
  → 人工确认依据 → 选择同本体版本的 DB 图谱、字段与时间 → 查询命中实例
```

## 按当前项目架构落地

| 文件 | 职责 |
| --- | --- |
| `src/bank_project/risk/catalog.py` | 复用 alignment Catalog 的版本选择、搜索与文件读取限制，读取原始 WHY 和版本内关系 |
| `src/bank_project/risk/propagation.py` | 无 I/O 的方向策略、多父 IS_A 遍历、路径强度与完整实例作用域 |
| `src/bank_project/risk/compiler.py`、`sources.py` | 白名单 RulePack、参数类型、WHY 逐字引用校验 |
| `src/bank_project/risk/service.py` | 复用 JsonModel，编排后台生成、固定证据、审核与执行 |
| `src/bank_project/risk/store.py` | SQLite 持久任务、规则、审核历史及执行记录；审核使用版本及内容哈希比较更新 |
| `src/bank_project/risk/execution.py` | 使用现有 VersionedGraph 读取已发布实例，固定参数化查询、确定性计算 |
| `src/bank_project/api/risk.py` | 复用 Bearer、Origin 和请求体限额的 HTTP 边界 |
| `frontend/src/features/risk/` | 复用公共 Panel、Feedback、请求层、Ant Design 和主题的第五步页面 |

`main.py` 集中装配与关闭服务。生成复用现有模型配置，使用 API 内后台任务及 SQLite 持久化，与当前单 API 部署一致；不迁入原平台的 JobManager、ProviderRegistry 或独立数据库。重启后未完成任务标记失败，已保存规则与审核记录保留。数据在 `BANK_DATA_DIR/risk/`，随现有数据卷备份。

## WHY 快照前提

当前仓库的 `data/ontology/snapshot.json` 只包含概念目录与关系，没有 WHY 原文。目录可以搜索，但无可达 WHY 的锚点不会调用模型、不会生成规则。页面明确显示无候选；不能用名称推测 WHY。

风险目录支持同一版本 Concept 属性中的 `why`、`dimensions.why` 或 `description.why`，容器和 WHY 可使用原始 JSON 对象/数组或其 JSON 字符串。每个 WHY 的内容哈希按 UTF-8、键排序、紧凑 JSON 计算 SHA-256；已有 `dimension_hashes.why` 必须一致。跨版本或缺失端点的关系拒绝载入。

原项目的 WHY 权威内容位于版本化维度服务，Neo4j 目录导出本身不会包含它。`scripts/import_risk_why.py` 可从同版本维度导出文件或 `/concept/dimensions` 服务补齐，先核对每个响应的 `node_id`、`dataset_revision`、WHY 哈希，再原子写入独立输出。使用说明见脚本 `--help`。输入快照及已有输出不会被覆盖；只有全部选定节点校验完成才发布输出。可用多个 `--node-id` 明确选择部分节点，输出记录覆盖数量，不能将部分补齐宣称为全部覆盖。

离线导入示例，`responses.json` 为原维度 API 响应对象的数组：

```bash
.venv/bin/python scripts/import_risk_why.py \
  --snapshot data/ontology/snapshot.json \
  --revision f32572317cda4b3b9c4357246b3417a3e9c54054c112bb75142e0345a7a21766 \
  --dimensions-file responses.json \
  --output data/ontology/with-why.json
```

改用 `--base-url <本体API根路径>` 可直接读取同版本服务；不传两个来源参数时读取 `.env` 的 `BANK_RETRIEVE_BASE_URL`。导入不会自动启用输出文件。

将验证过的新快照配置到 `BANK_ONTOLOGY_SNAPSHOT`，保持 `BANK_ONTOLOGY_REVISION` 与 retrieve 服务一致。Docker 默认只读挂载 `./data/ontology` 到 `/app/ontology`，因此新快照宜放在此目录，并使用容器内路径配置。原有生成任务保存独立的快照副本，后续配置或快照更新不会改变已保存的审核依据。第二步已有分析记录仍按其快照哈希校验，切换快照后需要重新分析才能编辑旧匹配。

## 传导与作用域

默认最多 5 个业务关系跳、50 个 WHY 来源，每次最多 20 个锚点。IS_A 向上寻找祖先规则，不占业务关系跳预算；支持当前目录的多父节点，环不会导致无限遍历。候选按路径强度、关系跳数、IS_A 跳数及稳定标识排序。为了避免深度预算遗漏来源，搜索同时保留节点在不同强度和关系深度下的最优状态。

方向描述均以存储边 `source → target` 为准，表示规则流向：

| 关系 | 正向规则流 | 反向规则流 |
| --- | --- | --- |
| inheres_in | strong | 禁止 |
| has_participant | strong | weak |
| preceded_by | 禁止 | weak |
| continuant_part_of / occurrent_part_of | strong | 禁止 |
| located_in / derives_from | 禁止 | strong |
| adjacent_to、未登记关系 | 禁止 | 禁止 |

IS_A 继承是 `deductive`；路径采用最弱一跳的强度。WHY 优先级每经过一个 weak 跳降低一级，最低为“低”，不编造数值置信度。

`bo_scope` 只包含锚点及其全部 IS_A 子孙，最多 1,000 个概念。超限跳过整个锚点，不能截断成可执行的部分作用域。跨业务关系的 WHY 来源不因此成为实例范围，模型给出的 `bo_scope` 一律由服务端覆盖。候选深度、候选数量、提示词和输出预算不足均留下覆盖标记。

例如：

```text
现金存入限额（WHY） ──inheres_in──→ 现金存入
                                    ↑ IS_A
                                 大额现金存入
```

选择“现金存入”，WHY 来源为“现金存入限额”，实例范围为“现金存入、大额现金存入”。参数必须引用 WHY 的原文；“现金存入限额”自身的实例不进入交易范围。

## 审核与执行

模型只返回 `name / predicate / params / source_node_id / parameter_sources` 等候选字段，不能提供查询语句或分析日期。服务端保存原始 WHY、维度哈希、快照哈希、本体版本、完整传导路径、作用域哈希及模型输入输出。

RulePack 保留迁入结构：

```json
{
  "head": {"risk_label": "现金累计", "semantics": "pattern_not_intent"},
  "body": [{
    "predicate": "cash_aggregate_threshold",
    "params": {
      "threshold": {"value": 10000, "unit": "CNY_MINOR"},
      "n_min": 2,
      "max_single_le_threshold": true,
      "timezone": "+08:00",
      "subject_dimension": "Account",
      "cash_scope": "现金存入",
      "status_filter": "成功",
      "bo_scope": ["cash", "large"]
    }
  }]
}
```

该例是假设 WHY 明确给出“同一账户、+08:00 自然日、至少两笔、成功现金存入、累计超过 100 元而单笔不超过 100 元”，不是系统默认阈值。每个业务参数和谓词需要 `parameter_sources` 的逐字引文。引用校验只证明文字存在，是否支持参数和跨关系适用性由审核人确认。

初始状态固定为 `pending_review / blocked`。缺参数或不支持的谓词保留为不可执行候选；引用不在原文、伪造来源或查询字段直接拒绝。批准要求勾选确认依据、无校验问题、版本与内容哈希匹配，并重新校验保存的快照、路径和作用域。驳回会阻止后续执行，历史运行仍保留当时批准的版本与内容。

规则还固定编译、来源校验和查询策略版本；策略升级后需重新生成并审核。执行记录保留固定查询模板、参数、字段映射、图谱版本和时间窗口共同计算的查询哈希。执行失败或请求中断也保存状态，不会以空命中冒充成功结果。

支持三个原项目已有的可执行谓词：

| 谓词 | 判断 |
| --- | --- |
| cash_aggregate_threshold | 同账户 +08:00 自然日内笔数达标、累计金额严格超过阈值、最大单笔不超过阈值 |
| counterparty_region | 交易状态、对手地区及可选交易类型精确匹配 |
| bo_scoped_aggregate | 窗口内同账户概念范围聚合，检查笔数及可选单笔、累计金额下限 |

执行适配当前 `BankAlignedInstance`，用 `concept_id` 承接原 `boid` 语义，不引入原项目专用 Account/Transaction 节点模式。固定 Cypher 按已发布图谱版本和概念范围分页读取原始 `n.data.fields`，随后在服务端计算。模型不参与逐交易判断，不能生成或拼接 Cypher。执行前核对图谱 `GraphSummary.revision` 等于规则的本体版本。

用户必须明确映射账户、时间、金额、状态，以及谓词要求的类型或地区字段。来源金额为**人民币元**的十进制值，计算使用 Decimal 转整数分；规则金额对象使用 `CNY_MINOR`。时间字段和分析起止必须是含时区 ISO 8601，窗口为 `[start,end)`，不超过 366 天。字段不同名、时间缺时区或单位不同的数据需先提供符合契约的来源；不会自动猜测单位或时区。

每次完整读取最多 50,000 个范围内实例、32 MiB 原始数据；超过任一上限或发现不完整实例则运行失败，不用部分输入推断风险。字段选择仅预览最多 200 个实例并标识抽样。命中统计基于完整输入，页面最多展示 200 个命中、每项 20 个交易证据，明确标记展示截断。

执行当前仅支持第二步发布的 DB 图谱。GraphRAG 或消歧派生图没有同一套交易字段与已发布 DB 版本契约，暂不作为风险执行来源。命中是规则模式线索，不是对违法行为或主观意图的结论。

## API 与验证

所有端点在 `/api/v1/risk`：`GET /catalog`、`GET /sources`、`POST/GET /propagations`、`GET /propagations/{id}`、`GET /cases`、`GET /cases/{id}`、`POST /cases/{id}/review`、`GET /cases/{id}/fields`、`POST/GET /cases/{id}/executions`。请求结构见同步生成的 `docs/openapi.json`；审核及执行都要求 `expected_version` 和 `expected_hash`。

```bash
.venv/bin/python -m pytest tests/test_risk*.py
npm --prefix frontend test -- risk.spec.ts
```

后端使用确定性模型和图存储替身覆盖来源传导、版本隔离、作用域不可扩大、参数与引用校验、审核门禁、查询分页与金额边界。浏览器替身验证真实界面流程；这些检查不消耗真实模型调用，也不写业务 Neo4j。
