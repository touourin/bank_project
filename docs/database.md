# 数据库与暂存配置

## 两套 MySQL 配置

| 用途 | 配置前缀 | 默认库/账号 | 授权 |
| --- | --- | --- | --- |
| 外部源 | `BANK_MYSQL_*` | `bank_project` / `bank_app`（本地 mock） | 应用只读事务；银行应额外使用数据库只读账号 |
| 内部暂存 | `BANK_STAGING_MYSQL_*` | `bank_staging` / `bank_staging` | 仅自己的库内建表和读写 |

源适配器绝不接收内部写库连接；暂存组件不读取源配置。源账号不能与内部账号同名。源可以是远程银行库，内部依然是本地 Docker MySQL，两者互不覆盖。

```bash
make staging-up  # 准备本地 MySQL 和独立暂存账号，密码写入忽略的 .env
make build
make run         # API + intake-worker + frontend
make mock-db     # 可选：显式装载两张最终 mock 到源库，不是启动必需步骤
```

初始化脚本保留现有暂存密码和已配置接入限额。它只在本地 Docker MySQL 中创建 `bank_staging`；自建其他暂存服务时由管理员配置 schema/账号。`BANK_STAGING_MYSQL_HOST` 是本地 Python 连接地址；Compose 对 API 和 worker 使用服务名 `mysql:3306`。外部源在 Docker 内由 `BANK_MYSQL_DOCKER_HOST/PORT` 决定。

## 持久化与迁移

- `mysql-data`：原有源数据、内部批次、行、接入任务、关联索引和临时图模板实例。
- `intake-data`：上传原件、外部凭据加密密钥、分析运行记录 SQLite。
- `neo4j-data`：业务图谱版本；独立本体库见 [ontology.md](ontology.md)。

迁移旧 `intake/batches.sqlite3` 时先做 SQLite 一致性备份，再执行：

```bash
.venv/bin/python scripts/migrate_staging.py /absolute/path/to/batches-backup.sqlite3
```

保留批次 ID、表 ID、源行号和字段值，已迁移的完整批次跳过；不删除或改写原库。若上一次迁移中断留下不完整批次，需先由 worker 清理失败尝试再重试。迁移期间应停止新增接入，或切换前重新备份并补迁新批次。

分析记录仍用同一 `BANK_DATA_DIR/alignment/runs.sqlite3` 保存小体量配置、过程、版本及租约。新分析只存暂存表引用和少量样本；历史内嵌输入快照仍能读取。不要单独清空分析序号而继续使用原 Neo4j 发布状态。多副本 API / Kubernetes 扩容前还需迁移该任务存储，当前标准部署是单 API 加独立接入 worker。

## 业务 Neo4j

```bash
docker compose --profile graph up -d --wait neo4j
```

浏览器 7477，Bolt 7690。按模板分批写隔离版本，计数/所有权确认后原子切换当前指针，失败不替换已发布图。历史和失败图版本暂不自动删除。模型和 Neo4j 不参与第一步接入；内部 MySQL 是标准模式的启动依赖，`/ready` 会检查它。
