# 本地持久化适配

FileStore 实现 RawStore、PreparationStore、DocumentCache。原文件按 SHA-256 保存；导入、转换和状态使用 dataset_id/batch_id 的哈希键。文件原子写入、同批次文件锁、读取原文件时检查摘要。

运行目录由 BANK_DATA_DIR 配置，本地默认 data/preparation，Docker 使用命名卷。服务独占此目录，不能与不可信用户共享写权限。当前适用于单节点/单副本；多副本需替换为共享事务状态和锁。

MySQL 当前是 adapters/sources 中的只读来源连接器，不负责保存转换结果。
