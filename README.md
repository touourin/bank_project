# bank_project

前两步数据工作台：① Excel / CSV 上传或只读 MySQL 接入，后台分批解析到独立 MySQL 暂存库；② 使用表结构及样例对齐本体，人工确认实体、属性、身份和关系模板，再分批生成业务图谱。

## 启动

首次部署先准备 Python 开发环境（见 [开发说明](docs/development.md)），复制 `.env.example` 为 `.env` 并配置模型、retrieve、本体和业务图连接，然后执行：

```bash
make staging-up                  # 启动 MySQL，创建独立暂存库与账号
make build
make run                         # API、接入 worker、前端
# 需要生成图谱时：
docker compose --profile graph up -d --wait neo4j
```

日常启动用 `make run`；代码更新后 `make build && make run`。只运行 API 而不启动 `intake-worker`，新接入任务会留在队列中。

- 前端：<http://127.0.0.1:5173>
- 接口文档：<http://127.0.0.1:8000/docs>
- `/health`：API 存活；`/ready`：API 及内部暂存库可用，不检查模型、外部源和 Neo4j。

默认单文件上限 512 MiB；上传后返回任务，页面显示解析进度、取消和失败重试。模型按表解释字段，retrieve 按表/列匹配节点，均不逐行调用。确定性实例生成依照人工确认的图模板，跨表同身份可以补充属性；不确定或冲突的事实不会自动覆盖。具体边界见 [数据接入](docs/intake.md)、[第二步](docs/alignment.md)、[容量验证](docs/large-data.md)。

## 结构

| 路径 | 职责 |
| --- | --- |
| `src/bank_project/intake/` | 通用文件解析、只读源适配、后台接入 |
| `src/bank_project/staging/` | 内部 MySQL 批次、任务、磁盘关联索引与模板编译 |
| `src/bank_project/alignment/` | 本体匹配、过程记录、人工修改、模板与图谱发布 |
| `frontend/` | React、TypeScript、Ant Design 数据工作台 |
| `compose.yaml` | API、worker、前端、MySQL及可选业务 Neo4j |
| `compose.ontology.yaml` | 独立本地本体数据库 |
| `examples/mock/` | 最终 mock 文件及字段说明 |
| `scripts/` | mock 工具、暂存初始化、旧批次迁移、接口导出 |

外部源与内部暂存分别使用 `BANK_MYSQL_*` / `BANK_STAGING_MYSQL_*`，不共享账号。密码不提交到 Git。MySQL 数据在 `mysql-data` 卷；上传原件、加密凭据密钥及分析记录在 `intake-data` 卷。备份需保留两者；请勿通过 `docker compose down -v` 清空持久数据。旧批次与旧图谱均保留。

## 验证

```bash
make check
make frontend-check
# 可选：先将 BANK_STAGING_MYSQL_* 指向以 _test 结尾的独立测试库
BANK_TEST_MYSQL=1 .venv/bin/python -m pytest tests/test_streaming_intake.py
```

[开发说明](docs/development.md) · [架构](docs/architecture.md) · [数据库配置](docs/database.md) · [前端组件](docs/frontend.md) · [本体环境](docs/ontology.md)
