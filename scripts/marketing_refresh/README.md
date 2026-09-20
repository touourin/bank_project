# 五表合成分析数据更新

本目录保存 2026-09-20 五表更新的可复现过程。数据快照固定为 2026-09-17；用户授权修改桌面原 Excel、移除表名中的 `mock`、加入少量风险，并删除 `shanghai_proj` 其他表。外部分析程序不在本次范围内。

输入为与原 Excel 哈希对应的 `data/mock-sources/` 数据、`examples/mock/E_CRM_C_CUST_TOUR_EVT_SUM.csv` 和桌面原工作簿模板。客户与来源字段保留，技术编号的 `MOCK_` 前缀在全部引用中一致移除。

中间产物与验证证据位于 `data/marketing-refresh-20260920/`。更新前完整备份的位置写入 `backup.json`，包含原 Excel 和数据库全部表；数据库密码只从 `MARKETING_REFRESH_PASSWORD` 环境变量或交互输入读取。

执行顺序（仓库根目录，以 `PYTHONPATH=scripts` 调用模块）：

1. `marketing_refresh.database backup` — 在任何修改前完成备份。
2. `marketing_refresh.prepare` — 生成稀疏 JSONL 中间数据。
3. `marketing_refresh.validate` — 全量逐笔、跨表和来源语义核对；提取独立风险证据。
4. `marketing_refresh.excel_parts metadata` — 从原模板提取样式及类型。
5. `marketing_refresh.build_workbooks` — 用 Artifact Tool 分块写出工作簿；可用 `--label 征信` 单独重建。
6. `marketing_refresh.notes prepare`，然后对五个中文标签逐一运行 `notes apply <标签>` — 同步记录数、空值数、测试码和说明。
7. `marketing_refresh.readback verify <标签>` — 从实际 Excel 全量回读，逐单元格比较，生成真正的数据库导入文件。`preview <标签>` 提供裁剪预览。
8. `marketing_refresh.readback install` — 检查桌面文件未被其他人修改后替换原文件。
9. `marketing_refresh.database stage` — 从 Excel 回读文件导入独立暂存表，并流式回读数据库全部字段，比较逐行 SHA-256 多重集合指纹。
10. `marketing_refresh.database publish` — 短暂获取全部相关表的 WRITE 锁，为持续写入的辅助表补充最新备份；将五张已核对的暂存表切换到正式名称，按外键依赖顺序删除辅助表及五张旧表，确认最终表集合恰为目标五张，再释放锁。
11. `marketing_refresh.report` — 生成独立的风险答案、对照客户和分析说明。

Excel 导出/修改使用 bundled Node 的 `@oai/artifact-tool`。Python 仅进行数据准备、分析、XML 流式读取与导出片段封装，防止 75 万行宽表一次载入内存。`node_modules` 是本机 bundled runtime 的链接，不提交依赖。数据库模块使用已有含 PyMySQL 的 `.venv/bin/python`；工作簿流水线使用 bundled Python 的 lxml。

大表暂存导入使用 MySQL Connector/Python 的压缩协议，避免重复文本拖慢网络。客户端 26.7.0 隔离安装在 `data/marketing-refresh-20260920/mysql_client`，未修改项目原有依赖。重建该环境可执行 `.venv/bin/python -m pip install --target data/marketing-refresh-20260920/mysql_client mysql-connector-python==26.7.0`。参数遵循[官方连接参数说明](https://dev.mysql.com/doc/connector-python/en/connector-python-connectargs.html)，开启 `compress=True`，关闭本地文件加载。

不要在已更新的桌面工作簿上重新提取“原模板”元数据，也不要在没有新备份的情况下再次发布。首次执行的状态文件包含原哈希、已安装哈希与暂存结果，防止覆盖运行中发生的人工修改。

锁定期间的改名和删除遵循 MySQL 8.4 的 [RENAME TABLE](https://dev.mysql.com/doc/refman/8.4/en/rename-table.html) 和 [LOCK TABLES](https://dev.mysql.com/doc/refman/8.4/en/lock-tables.html) 文档。锁仅用于切换阶段，不覆盖长时间 Excel 生成和暂存导入。

风险为 5 类各 8 个客户、合计 40/1000。阈值附近的 CUST_0816 回款集中度为 79.33%，特意保留，不为通过 80% 规则而强行调整。离线示例规则的 39/40 命中不能当作外部程序准确率。


## 主体汇总整理（后续文件修订）

2026-09-20的后续用户请求将征信与工商整理为主体汇总，其他三张表全量审查后保持不变。此步骤只改本地文件，不调用上述数据库stage/publish流程。现有主目录已是汇总文件，不要把它再次当作拆表前的输入。

- `audit_tables.py`：流式审查标签、旅程和流水的主键、字段内容与事件JSON。
- `summary_common.py`：读取来源明细及保守聚合辅助函数，未知金额/币种不转0。
- `summary_credit.py` / `summary_business.py`：各自拥有来源字段映射、汇总口径及字段说明。
- `summarize.py`：以每套修改前文件生成JSON矩阵，遇到来源哈希变化停止，避免覆写人工修改。
- `summary.mjs`：使用bundled Node Artifact Tool写出“数据＋字段说明”，并渲染检查。
- `verify_summaries.py`：逐单元格回读，再独立核对业务合计、财务冲突及客户关联。

中间输入、原文件备份、导出结果、审查和安装证据位于`data/subject-summary-20260920/`。已安装的完整来源文件另在对应交付目录的`来源明细/`。需刷新时应基于明确的新来源快照和独立输出目录重新核验，不绕过脚本的来源变更检查。说明与最新字段规模见`docs/five-table-summary.md`。


### 后续数据库同步

用户随后授权更新数据库。`summary_database.py` 将已验证的**桌面版**征信/工商同步到既有 `shanghai_proj`：只替换两张变更表，保留其余业务表和历史接入批次；其他三张表全量流式回读，与已核验的桌面来源指纹比较。密码来自环境或应用加密保存的源连接，解密仅在内存完成，不写入脚本、日志或证据文件。

流程为源工作簿哈希及内容校验 → 一致性快照备份旧表 → 新表分批入库及全量回读 → 短期写锁内校验旧表未变化 → 原子更名 → 更新两条衍生字段目录、清空旧结构向量缓存 → 独立连接回读 → 清理仅本次生成的旧副本。旧表SQL及目录记录保存在`data/subject-summary-20260920/database/`。已发布状态会拒绝重复执行；不要绕过状态文件重跑。需要恢复时先在独立库验证压缩SQL，再协调正式表切换，不能直接覆盖后续业务写入。
