# GraphRAG 文档图谱模块

本模块迁移自用户指定的 `graphrag-main/unified-search-app`，保留同目录 MIT 许可证。
运行时固定使用该应用已验证的 `graphrag==2.5.0`，不导入原项目路径、复制原数据或继承原项目 `.env`。

| 原实现 | 本项目位置 | 保留与适配 |
| --- | --- | --- |
| `app/ingestion/parsers.py` | `parsers.py` | 完整解析与校验器；TXT 额外支持 GB18030 |
| `app/ingestion/service.py` | `jobs.py` | 文档去重与来源、原子写入、文件锁、持久化任务、后台进程、故障恢复与重试；模型模板继承改为本项目配置 |
| `app/ingestion/worker.py` | `worker.py` | 严格 CSV 读取、原生 `build_index`、阶段进度、原始文档与六张 Parquet 表及三张 LanceDB 表校验、成功后发布 |
| `app/app_logic.py` 的三种问答、`knowledge_loader` | `service.py` | 原生 Local / Global / DRIFT 全量 DataFrame 参数；移除 Streamlit 会话和渲染耦合 |
| 初始化与模型配置 | `runtime.py` | 生成 GraphRAG 默认提示词、独立存储路径、仅内存注入 BANK 模型密钥 |

应用 HTTP 接口只将 `.txt` 接入该路径；表格保持原数据接入流程。TXT 保存为独立数据集，开始索引后后台进程运行完整原生 GraphRAG 标准流程：分块、实体/关系抽取、描述总结、社区发现、社区报告和向量化。上传不触发模型请求，点击开始索引才调用配置的模型。

`GraphRagService.graph()` 输出完整图，实体与关系的全部原字段均保存在 `properties`，仅将关系名称端点映射为节点 ID；实体顶层 `source_context` 与 `source_text_unit_ids` 提供原始 TXT 分块证据。消歧及 BO 匹配由图治理模块管理，不在这里覆盖原始 Parquet 或向量表。

问答使用原生索引和原生证据，响应中的 `index_basis=original_graphrag_index` 明确这一点。图上显示的合并/匹配结果不冒充重新生成的社区报告或向量索引。

模型名称与服务地址随索引配置保存，不保存密钥。修改模型名称、向量维度或文档正文时应创建新数据集建立完整索引；重试只用于尚未成功的原数据集。

独立后台进程运行 `python -m bank_project.graphrag.worker --data-root ... --key ... --run-id ...`，该命令由任务服务创建。重启后遗留活动任务通过进程存活及运行锁识别为失败，可以安全重试；只在完整产物校验后将任务置为 `succeeded`。

验证：

```bash
.venv/bin/python -m pytest tests/test_graphrag.py tests/test_graphrag_pipeline.py -q
```

测试包含真实 GraphRAG 的文档读取、分块与文档 Parquet 工作流，真实 Parquet/LanceDB 验证，以及三个原生问答方法的签名契约。模型抽取、社区报告和在线答案在测试中使用确定性替身，没有请求外部模型；这些测试不等于真实业务语料的回答质量验收。
