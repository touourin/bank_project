# 本地本体环境

本地快照 `data/ontology/snapshot.json` 已纳入 Git，克隆或拉取项目时会一并获取；其他 `data/` 运行数据和 `.env` 凭据仍不提交。Docker Compose 将 `./data/ontology` 只读挂载到 API 的 `/app/ontology`，无需单独复制快照。

快照包含两个 ready 版本，必须在 `.env` 设置 `BANK_ONTOLOGY_REVISION`，并与 `BANK_RETRIEVE_BASE_URL` 指向的本体版本一致：

| 版本 | 概念数 |
| --- | --- |
| `f32572317cda4b3b9c4357246b3417a3e9c54054c112bb75142e0345a7a21766` | 3375（`.env.example` 默认值） |
| `c20988318748bb7be2f1e0ca5d9a1bee3be179f92c4e0408e2d13ea9974f4f6f` | 86 |

已有部署拉取代码后，检查 `.env` 中的版本配置，然后执行 `docker compose up -d api` 并刷新页面。若仍提示无法读取快照，检查项目目录下该文件是否存在、API 是否有读取权限以及目录挂载是否正确。这里的“本地”指 API 运行环境。

独立数据库配置 `.env.ontology`、Compose 文件 `compose.ontology.yaml` 和已有数据均保留。

```bash
make ontology-up
make ontology-down
```

浏览器端口 7478，Bolt 端口 7691。此环境与业务 Neo4j 独立。

第二步通过 `alignment/catalog.py` 读取本地快照，不混合不同版本的概念或连接。分析时必须调用配置的 retrieve 服务匹配节点，本地快照提供目录和版本校验；不会写入本体库，业务实例使用独立业务 Neo4j。检索配置与限制见 [第二步说明](alignment.md)。
