# 数据工作台前端

工作步骤按业务阶段组织：数据接入 → 数据转换 → 实体消歧 → 图谱浏览与分析 → 风险规则。Excel / CSV / MySQL 与 TXT 在第二步共享工作区入口，保留各自的转换策略和任务记录。

`features/conversion/ConversionPage` 负责工作区布局和来源类型切换。表格侧 `TableConversionWorkspace` 编排字段分析、模板草稿与生成任务，`GenerationConfirmation` 展示生成确认内容；`RunOverview`、`MappingResults`、`GenerationRules` 继续负责原有交互。TXT 侧由 `useTextDatasets` 管理列表刷新和任务轮询，`TextDatasetPicker` 选择任务，`TextIndexPanel` 启动或重试索引，`TextConversionWorkspace` 组合转换预览及 `MatchingPanel` 审核。切换来源类型保留已挂载工作区及未保存草稿；重新进入时刷新来源列表。

两类来源共用以下行为，页面仅适配自身的数据结构：

| 公共模块 | 统一的行为 |
| --- | --- |
| `conversion/taskState.ts` | 待处理、运行、可审核、完成、失败的状态解释，表格分析与构图状态合并判断 |
| `hooks/useTaskResource.ts` | 请求完成后再安排轮询，切换任务取消旧请求，刷新保留当前内容，已确认修订不会被旧响应覆盖；表格、TXT、匹配和消歧均使用 |
| `hooks/useAsyncAction.ts` | 防重复提交、失败保留与重试、卸载后忽略完成回调；转换任务与审核弹窗共用 |
| `conversion/useConceptSearch.ts` | 先展示原检索候选，再搜索本体；取消过时搜索及延迟任务 |
| `conversion/ReviewNoteFields.tsx`、`ReviewHistory.tsx` | 审核人、原因、修改前后详情、分页与版本展示；表格和图谱通过适配器读取历史记录 |

表格新记录保留审核人和真正创建该修改的方案版本；后续派生方案不改写旧记录归属。历史记录没有记录作者或版本时明确显示未填写或历史未记录，不用当前版本补造历史。

第三步 `ResolutionPage` 保留候选过滤、来源证据、合并核验、退回和审计；新增跳转按钮携带当前结果修订进入分析页。原图、本体匹配和消歧均保留独立版本。

第四步 `features/analysis/AnalysisPage` 统一选择两类图谱及派生版本。`GraphResults` 按来源能力选择读取方式：表格原始版本通过概览和游标分页读取，复用 `GraphPanel`；文档及派生结果复用 `KnowledgeGraphPanel`。文档原始索引才显示 `GraphChat` 和 `ReportsPanel`，派生结果明确提示没有独立问答索引，可显式跳回原始文档。版本更新返回冲突时提示重新选择，不静默换图。画布显示上限与全量导出继续分离。

React + TypeScript + Vite + Ant Design。公共界面使用 `ui/`，HTTP 使用 `api/request.ts`，来源能力及派生版本标识集中在 `knowledge/graphSources.ts`。公共层负责任务交互、审核和版本保护；表格批量生成、GraphRAG 原生索引及消歧算法仍由各自适配器执行，不自动执行跨来源实体融合。

演示模式支持 `workspace=conversion|resolution|analysis`，兼容旧 `step=4` 消歧链接，全部操作留在浏览器内存，不访问业务 API。

```bash
npm --prefix frontend run dev
npm --prefix frontend run build
npm --prefix frontend test
npm --prefix frontend run format:check
```

开发代理默认指向 `http://127.0.0.1:8000`，可用 `BANK_API_PROXY` 指定其他后端。主题偏好保存在浏览器本地，API token 和自定义数据库密码仅在页面内存中。刷新后需重新填写访问凭证。

## 公共组件

`frontend/src/ui/` 只负责展示和交互，不依赖 `features/`、数据接入 API 或银行字段。后续步骤可以直接使用这一层。

| 文件 | 职责 |
| --- | --- |
| `UIProvider.tsx` | 中文语言包、主题上下文、组件默认配置；跟随系统主题和减少动画偏好 |
| `theme.ts` | 唯一的颜色配置，同时生成 Ant Design 主题和页面 CSS 变量 |
| `Panel.tsx` | 内容面板、标题、说明及操作区 |
| `DataTable.tsx` | 表格尺寸、横向滚动、表头和可访问区域；不做客户端分页 |
| `PagePagination.tsx` | 后端 offset 与页码的换算、加载禁用、统一数量展示 |
| `Feedback.tsx` | 错误与重试、加载、空状态 |
| `ConfirmDialog.tsx` | 异步确认、阻止重复提交、错误保留与重试，操作中禁止关闭 |
| `JsonDetails.tsx` | 按需展开原始证据及修改详情 |
| `ui.css` | 公共组件布局；业务页面布局保留在各自功能目录 |

按钮、输入框、选择框、表单、上传、标签页等直接使用 Ant Design。只在项目有统一行为时封装组件，避免给每个库组件再套一层同名包装。弹窗使用受控组件；如需全局消息，使用 `App.useApp()`，不要使用脱离主题上下文的静态方法。

业务代码位于 `src/features/`。组件负责交互状态，`api.ts` 负责请求，公共展示组件不发请求。新增步骤在 `features/` 下建立功能目录，复用 `ui/` 与全局主题，不要再单独写一套按钮、表单或分页样式。

数据表格按后端返回的字符串显示，保留前导零和大金额精度，区分 NULL 与空文本。上传控件不会自动发请求；用户点击“解析并暂存”后按文件独立处理，部分失败不会阻止后续文件。表单切换标签页后保留输入，修改 MySQL 连接信息会清除旧选表结果。

## 运行与验证

浏览器测试使用临时批次/分析库，覆盖文件上传、类型与原值、分页、同名文件隔离、多文件部分失败、删除取消及失败重试、密码和凭证不持久化、主题、错误恢复及移动端布局。表格转换测试使用替身模型、检索适配器及图存储，验证真实 API 选表分析、结果持久化、取消构图、低分禁止构图、retrieve 候选及原始得分展示、人工选择、节点详情和页面刷新。真实 Neo4j 发布原子性通过独立数据库的 `tests/test_alignment_graph.py` 验证。

组件库在运行时生成样式，Caddy 的 CSP 允许内联样式以支持主题、下拉框和弹窗定位；脚本仍限定同源，不允许内联脚本或 `eval`。修改组件库配置后，应在 Docker 静态构建中检查 CSP 与控制台，不能仅检查 Vite 开发环境。

第五步“风险规则”位于 `src/features/risk/`，复用相同公共组件和请求层。页面提供 BO 搜索、WHY 生成任务轮询、来源与传导路径核验、版本化审核、DB 图谱字段映射和时间窗口执行。未审核或有校验问题的规则不能执行，无 WHY 时明确显示未生成规则。演示模式暂不开放第五步；真实数据前提见 [风险规则](risk.md)。

仅更新前端容器：

```bash
docker compose build frontend
docker compose up -d --no-deps --wait frontend
```

## 共享 BFO 本体浏览

第四步分为“业务图谱”和“BFO 本体”。本体页通过 `/api/v1/ontology/graph` 读取完整概念和关系，显示来源、固定版本、节点类型及 WHY 覆盖数；画布保留子图上限，完整表格可分页搜索，导出包含全部目录数据。可搜索概念或筛选含 WHY 的概念，按需读取 WHAT/WHY，响应绑定当前目录的 revision 和哈希。页面是只读目录浏览，不生成业务实例或风险规则。
