# MySQL 中的客户标签与旅程

数据库：`bank_project`。当前写入的是最终合成数据，不是银行真实数据。

已在本地 Docker MySQL 8.4.11 实际建表并导入：全量逐字段比对通过，没有无法关联客户的旅程；重复运行新增 0 条。还验证了第二张表发生内容冲突时，第一张表本次新增记录一并回滚。通过运行中的 API 完成 MySQL 来源导入与两套业务映射转换，结果保存在 mysql-demo 数据集。完整工程回归为 158 项通过。

| 表名 | 含义 | 行数 | 字段数 | 主键 | 原始表名 |
| --- | --- | ---: | ---: | --- | --- |
| customer_tags | 客户标签快照 | 1,000 | 155 | cust_ind | CCM_C_CUST_FLAG_INFO |
| customer_journey_events | 客户旅程事件 | 16,700 | 8 | DT + ROWKEY | E_CRM_C_CUST_TOUR_EVT_SUM |

客户关联：`customer_journey_events.CUST_ID = customer_tags.cust_ind`。标签是当前一份快照，不是标签历史表。两张表是来源业务数据；原始文件、转换任务和候选结果仍由既有准备服务存储。

## 连接与查看

本机数据库客户端连接 `127.0.0.1:3306`，数据库 `bank_project`，用户 `bank_app`，密码查看本机 `.env` 中的 `BANK_MYSQL_PASSWORD`。端口和账号可通过同一配置文件修改。Docker API 通过 `mysql:3306` 访问；连接外部数据库时配置 BANK_MYSQL_DOCKER_HOST/PORT。

```sql
SELECT cust_ind, cust_nm, dt FROM customer_tags LIMIT 10;

SELECT t.cust_nm, e.EVT_TYPE, e.OCCUR_DT, e.PROPERTIES
FROM customer_journey_events e
JOIN customer_tags t ON t.cust_ind = e.CUST_ID
ORDER BY e.CUST_ID, e.OCCUR_DT, e.ROWKEY
LIMIT 20;

SHOW FULL COLUMNS FROM customer_tags;
SHOW FULL COLUMNS FROM customer_journey_events;
```

每个字段都保留中文 COMMENT。客户号、账号和 YYYYMMDD 日期保留文本；金额使用 DECIMAL(26,8)，不会经 float 转换。CSV 空值写入 SQL NULL；主键和银行已明确非空的字段限制非空，不把 mock 必填假设直接当成银行规则。

PROPERTIES 使用 LONGTEXT 保留 JSON 原文，并加 JSON 对象合法性 CHECK。这样嵌套金额不会因数据库 JSON 数值存储而改变精度；需要精确金额计算时，应将明确字段提取为 DECIMAL，不通过浮点计算。表使用 utf8mb4 和区分大小写的 collation。

## 启动与重复导入

```bash
make mock-db   # 准备本地凭据、启动独立 MySQL、校验并导入两份最终 mock
```

已有数据库且连接参数已配置时，单独执行：

```bash
.venv/bin/python scripts/load_mock_mysql.py
```

同主键且内容相同的记录跳过；同主键但内容不同则停止，本次两张表的写入一起回滚。额外的已有记录保留；不执行 DELETE、TRUNCATE、DROP 或自动修改已有表结构。建表 DDL 会独立提交；若导入失败，可能保留空表，但不会提交半批数据。

字段来自 `configs/bank/schema.json`，可查看 `database/mysql/schema.sql`。更新字段契约后可运行 `scripts/load_mock_mysql.py --write-schema` 重新导出 SQL；此命令不连接数据库。已有表结构变更需要明确的迁移，不能靠重跑导入覆盖。

持久化数据在 Docker 命名卷 `bank-project_mysql-data`。普通停止或重建容器不清除数据；不要使用 `docker compose down -v` 删除数据卷。随机 root 密码保存在已忽略的 `data/mysql/root-password`，通过 Docker secret 只挂载到数据库容器；应用凭据保存在已忽略的 `.env`。已有卷首次创建后，改配置文件不会自动更改数据库账号密码。

## 通过现有接口读取

`.env` 设置 BANK_MYSQL_SOURCE_ENABLED=true，BANK_MYSQL_SOURCE_TABLES 为 `["customer_tags","customer_journey_events"]`，然后重新创建 API 容器。若希望 `make run` 同时启动数据库，设置 COMPOSE_PROFILES=database；同时使用图数据库时为 database,graph。

第一步请求示例：

```json
{
  "dataset_id": "mysql-demo",
  "batch_id": "tags-001",
  "source_system": "bank",
  "source_uri": "mysql:customer_tags"
}
```

旅程使用 `mysql:customer_journey_events` 和新的 batch_id。第二步调用现有 `/api/v1/extractions`；字段映射按列名匹配，改表名不需要修改通用导入、转换代码。

本地 bank_app 是开发用账号，具备本数据库建表和导入权限；API 的来源适配器使用只读事务。部署到真实银行环境时应为 API 单独配置只读账号，建表和数据装载由独立运维账号执行。
