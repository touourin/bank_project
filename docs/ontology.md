# 共享 BFO 本体

表格/MySQL、TXT、本体浏览和风险规则统一由 `ontology/` 服务读取 `BANK_ONTOLOGY_BASE_URL`。默认示例连接 `192.168.130.250` 上的 Legacy BFO：

```dotenv
BANK_ONTOLOGY_BASE_URL=http://192.168.130.250:30080/v2/ontologies/00000000-0000-4000-8000-000000000001
BANK_ONTOLOGY_REVISION=f32572317cda4b3b9c4357246b3417a3e9c54054c112bb75142e0345a7a21766
```

本体 API 只读连接，业务实例继续写入 `BANK_NEO4J_*` 配置的独立业务库。无需给应用配置远端本体库的 Neo4j 密码，也不依赖 SSH 隧道。仅改 Bolt 地址无法获得完整维度内容，WHY 以同版本维度 API 为准。

## 来源与版本

- `/ready` 检查本体 ID 和就绪版本；新任务每次检查，不以旧缓存掩盖断线或版本变化。
- `/evaluation-snapshot` 提供完整概念、语义类型、关系及维度哈希。目录读取不请求全部 WHY。
- `/retrieve` 为表格和 TXT 匹配概念，所有请求绑定当前任务版本。
- `/concept/dimensions` 按需读取 WHAT/WHY；风险生成前补齐全部非空 WHY。逐项核对本体 ID、版本、节点 ID、响应哈希及目录哈希，全部成功才采用。

目录和维度合计不超过 30 MiB；并发及总读取时限由 `BANK_RETRIEVE_CONCURRENCY` 和 `BANK_RETRIEVE_TIMEOUT_SECONDS` 控制。客户端直连内网服务，不读取系统代理。旧 `BANK_RETRIEVE_BASE_URL`、`BANK_RISK_ONTOLOGY_BASE_URL` 是同一入口的兼容别名，多项配置不一致时拒绝启动。

不同本体（如 Legacy BFO、AML BFO）不能混合。切换时同时核对 URL 中的本体 ID 和 revision；已发布业务图谱保留原版本，不会自动重新映射。

## 自动归档与历史兼容

新任务不要求手工复制本体文件。已验证目录及风险 WHY 按 SHA256 保存至 `BANK_DATA_DIR/ontology/snapshots/`，这是任务依据，不是另一套可编辑本体。历史表格/TXT 审核使用当时的归档，即使服务断线、升级或应用重启也不重新解释旧结果。归档缺失或被修改时拒绝审核，不使用当前目录代替。

启动时会把 `BANK_ONTOLOGY_SNAPSHOT` 中的有效旧快照原字节归档，保留已有任务引用的文件哈希，包括其中多个 ready 版本；旧风险任务的 `BANK_DATA_DIR/risk/snapshots/` 仍可读取。因此首次升级应保留原快照和数据卷，不要先删除旧文件。运行库、公共归档和历史风险归档需要一并备份；这些运行数据及 `.env` 不提交 Git。

`data/ontology/snapshot.json` 继续纳入 Git 作为历史迁移来源。Docker 将此目录只读挂载为 `/app/ontology`，公共自动归档写入 `/app/intake-data/ontology/snapshots`，两者路径不同。远端配置存在时不会用旧文件回退新任务。

## 浏览与离线开发

第四步“BFO 本体”展示完整目录和关系，按语义类型区分概念，可筛选 WHY、查看 WHAT/WHY。画布显示有上限的子图，列表和导出保留完整数据。客户和事件记录位于“业务图谱”，分类连线不代表实际交易事实。

没有远端配置时，目录浏览和离线风险生成仍可读取本地快照；表格/TXT 新匹配需要 retrieve 服务。仓库旧快照没有 WHY，离线规则生成需先按 [风险规则说明](risk.md) 补齐原文。

可选开发环境 `compose.ontology.yaml` 和 `.env.ontology` 保留，`make ontology-up` / `make ontology-down` 控制本地本体库（浏览器 7478，Bolt 7691）。它不会自动成为正式本体来源。
