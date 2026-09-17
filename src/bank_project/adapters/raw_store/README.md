# 原始数据存储预留位置

ports.RawStore 定义读写边界，当前没有文件系统、内存或对象存储实现。
后续在此实现适配器并通过 bootstrap 注入 ingestion / extraction。
