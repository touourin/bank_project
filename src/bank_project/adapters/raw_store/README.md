# 原始数据存储预留位置

ports.RawStore 定义读写边界，当前没有文件系统、内存或对象存储实现。
后续在此实现适配器并通过 bootstrap 注入 ingestion / extraction。

MySQL 接入参数已预留在根目录 `.env.example` 和 `settings.py`：
`BANK_MYSQL_HOST`、`BANK_MYSQL_PORT`、`BANK_MYSQL_DATABASE`、`BANK_MYSQL_USER`、`BANK_MYSQL_PASSWORD`。
目前只读取配置并校验端口等基础格式，没有 MySQL 驱动、数据库连接、建表或数据写入；Compose 也尚未提供 MySQL 服务。
后续由 bootstrap 将这些参数传给存储适配器，业务模块不直接读取环境变量。
