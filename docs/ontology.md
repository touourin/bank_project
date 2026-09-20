# 本地本体环境

独立数据库配置 `.env.ontology`、Compose 文件 `compose.ontology.yaml`、本地快照 `data/ontology/snapshot.json` 和已有数据均保留。

```bash
make ontology-up
make ontology-down
```

浏览器端口 7478，Bolt 端口 7691。此环境与业务 Neo4j 独立。

第二步通过 `alignment/catalog.py` 读取本地快照。多个 ready 数据集必须通过 `BANK_ONTOLOGY_REVISION` 固定其中一个，不混合概念或连接。默认不访问远程服务器，也不写本体库；业务实例使用独立业务 Neo4j。可选外部检索的配置与限制见 [第二步说明](alignment.md)。
