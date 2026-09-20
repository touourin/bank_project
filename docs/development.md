# 本地开发

Python 3.12+，Node.js 24。默认推荐使用 Docker Compose 启动。

完整 GraphRAG 运行环境使用 Python 3.12。Docker 固定官方 Python 镜像的 ECR 镜像摘要，并安装 OpenMP 运行库，避免 GraphRAG 数值依赖缺失；可通过构建参数 `PYTHON_IMAGE` 使用其他提供 Python 3.12 的受信镜像。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -c requirements.lock -e '.[dev,mock,graphrag]'
npm --prefix frontend ci
```

使用 MySQL 模式时先运行 `make staging-up`，然后分别在三个终端运行：

```bash
make local-run
make local-worker
make frontend-dev
```

后端为 8000，前端为 5173。若同端口 Docker 服务正在运行，先用 `docker compose stop api intake-worker frontend` 停止对应服务，或为本地服务指定其他端口。

```bash
make check             # Python 格式、测试、OpenAPI 和最终 mock 校验
make frontend-check    # 前端格式、TypeScript 构建、浏览器检查
make schema            # 更新离线接口说明
```

浏览器检查使用独立 8011/5174 端口及本机 Chrome。浏览器测试后端不读取本地 .env；默认不会连接数据库或调用模型。显式设置 `BANK_TEST_MYSQL=1` 的存储集成测试必须将 `BANK_STAGING_MYSQL_*` 指向以 `_test` 结尾的独立测试库；禁止连接实际工作暂存库，避免测试任务被工作 worker 领取。源 mock 测试需要本地 `data/schema-review/normalized-source-schema.json`，缺失时跳过该组测试。

业务逻辑重置前的源码快照位于本机 `/Users/ourin/project/_backups/`。Git 历史未改写，当前改动尚未提交。
