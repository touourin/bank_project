# 实体消歧质量评测、标注与验收规范

版本：设计稿 v1，2026-09-18。本文用于后续开发、测试与验收；文中新增字段、命令和阈值均为建议，尚未实现，也不是已经达到的效果。

## 1. 先固定评测对象

消歧质量必须同时回答四个问题：自动合并是否合对、应合并的是否找到、最终实体簇是否纯净完整、多少数据仍需人工处理。不得用一个笼统的“准确率”替代这些指标。银行场景应先控制错合，同时明确漏合和复核成本，避免通过全部拒判得到表面高精度。

本项目当前情况：

- `ResolutionService.resolve()` 尚未实现；`ResolutionResult` 只有映射和版本，没有状态、候选集、分数与决策轨迹。
- 标签映射使用 `cust_ind`，旅程映射使用 `CUST_ID`，均抽取为 `customer_id`。在同一 dataset/source_system 下，其 key 身份可能在抽取阶段已相同。
- `EntityCollector` 已按 `candidate_id` 合并同批实体，并在属性不一致时拒绝；仅测试消歧函数，可能完全看不到上游错误合并或丢失。
- 当前 mock manifest 标记 `synthetic: true`，有 1,000 行客户标签、16,700 行旅程。现有测试预期旅程抽取出 2,098 个实体、16,700 个事件；事件数不能作为独立客户样本量。
- mock 身份码和数据分布是人为构造。它能证明代码满足用例，不能证明真实银行数据达到 99.5% 或 99.9% 精度。

设一个 `mention` 是“一条原始记录中一次可定位的实体出现”；一个原始行可包含企业、法人、账户等多个 mention。`observation` 是消歧输入中可独立追踪的对象观察。必须建立 `mention_id → observation_id → versioned membership → canonical_id` 的可追溯关系。`candidate_id` 是旁路聚合视图，其内部观察可能分别归属多个 canonical，不得强行提供单值兼容映射。评测样本 ID 不得用客户号生成，以免相同错误编号先把两个真实体折叠。

observation 的稳定定位由来源版本、行/文本位置、出现序号或稳定映射 alias、抽取契约版本组成；角色只是属性，不能作为唯一出现标识，同记录同角色可有多个主体。gold mention 未被抽取时 `observation_id` 为空，仍保留在端到端评测全集中。

正式评测分三层，报告分开保存：

| 层级 | 输入与衡量内容 | 目的 |
|---|---|---|
| 抽取与归一层 | 原始 mention、身份字段、角色、来源、归一值 | 发现错误抽取、漏抽取、错误标准化、预合并 |
| 消歧条件评测 | 已抽取 observation 与冻结注册表 | 定位候选召回、打分、判决、聚类问题 |
| 全流程评测 | 原始 mention 到最终实体、事件参与者、关系端点 | 得到用户实际承受的错合与漏合 |

建议 V2 输出每个 observation 一条状态：`matched / new / unresolved / conflict`；`method=exact / model / human / null`，matched 时 method 必须有值；`decision_origin=automatic / human` 独立记录，自动与人工按 origin 区分。仅 `matched/new` 进入本次确认映射；`new` 要求身份信息足够、检索完整完成且索引水位符合读取快照。超时、证据不足、候选截断或未补齐的索引滞后不可伪装为“确认新实体”。

最新决定与当前生效归属分开：`assignment_action=keep / assign / retract / quarantine` 表示归属变更动作，`effective_assignments` 表示对应版本的有效归属。技术失败可 `unresolved + keep` 保留既有有效归属，但不计作本次自动处理成功；可信新冲突应隔离受影响旧归属，纠正通过撤销/重新分配发布补偿。

## 2. 建立可靠 gold，而不是把规则输出当答案

### 2.1 标注口径

标注手册先定义“同一实体”：企业法律主体、银行客户档案、自然人、账户分别是什么。银行客户档案与法律主体不能默认一一对应；母子公司、企业与法人、同一人不同账户应分别建实体并建立关系。名称变更通常不意味着实体变更，法人重组等复杂情形由业务口径确定，保留时间和判断依据。

每个候选关系标签限定为：

| 标签 | 含义 | 使用方式 |
|---|---|---|
| `same` | 有足够独立证据确认同一对象 | 可训练与评测 |
| `different` | 有足够证据确认不同对象 | 可训练与评测；可生成 cannot-link |
| `uncertain` | 证据不足、相互冲突或时点不清 | 不强行转负例；单列占比及影响 |
| `out_of_scope` | 对象类型或来源不在约定范围 | 按事先定义范围剔除，保留数量 |

强标识可作为证据，但不能将“编号相等”本身直接生成最终 gold，再声称编号规则 100% 正确。对自动编号规则也要核验命名空间、是否复用、是否主从档案、历史迁移、来源错误。原文引用存在只证明文本中出现了内容，不能证明字段角色、所属实体和身份判定正确。

### 2.2 六步标注流程

1. 冻结来源文件、数据库快照、映射、登记时点与采样计划；为样本生成与身份字段无关的稳定 ID。
2. 标注员先看原始证据与来源时间，不看算法最终分数和结论；可用独立检索发现同实体其他记录。
3. 两名标注员独立完成 `same/different/uncertain` 和实体成员归属；标注依据必须指向可追溯材料。
4. 分歧交由具备业务知识的第三人裁决；不足以裁决则保留 `uncertain`，禁止为凑样本填确定标签。
5. 对 same 连通分量执行一致性检查：若 A=B、B=C，但 A≠C，回到整簇裁决；不能简单取传递闭包吞掉冲突。
6. 冻结 gold 版本和摘要；后续修订记录原标签、新标签、原因、审批人、影响样本，旧报告不覆盖。

报告双人原始一致率、分标签分歧率、裁决占比；类别极不平衡时，一致率很高也可能掩盖 same 类问题，可补 Cohen's kappa，但不把标注员一致当成客观真值。

### 2.3 三套数据共同使用

| 数据集 | 构建方法 | 可回答的问题 |
|---|---|---|
| 代表性质量集 | 按真实流量随机或按已知概率分层抽样，保持抽样权重 | 预期生产精度、覆盖率、复核量 |
| 完整实体簇集 | 抽取实体后尽量找齐其在评测范围内的所有来源与历史记录，再裁决相邻混淆实体 | 聚类漏合、错合、候选召回 |
| 困难与故障集 | 同名、近名、键冲突、历史更名、OCR、脏字段、链式误合、异常调用 | 稳健性和已知缺陷是否回归 |

困难集故意改变分布，不能直接当生产精度。随机抽一批全空间记录对几乎全是 different，也不能衡量业务效果。需同时覆盖自动匹配、自动建新、拒判、冲突和检索失败。

代表性样本量应由目标置信区间决定。启动阶段可先标注约 1,000–3,000 个独立实体覆盖主要来源，约 3,000–10,000 个有信息量的候选对诊断规则；这些是工作量估算，不自动满足正式验收样本量。

## 3. 防止训练、调参和验收互相泄漏

### 3.1 四种独立评测协议

| 协议 | 划分方式 | 注意事项 |
|---|---|---|
| 未见实体泛化 | 以 gold_entity_id 分组，建议 train/dev/test 约 60/20/20 | 同实体所有别名、记录、衍生扰动进入同组 |
| 时间回放 | 按实际可获知时间分 train、calibration/dev、未来 test | 用 `observed_at` 控制可见性，不能只看业务发生日期 |
| 未见来源泛化 | 留出一个来源系统或机构组合 | 同实体可否在已知来源存在，须明确并分别报告 |
| 生产注册表回放 | 冻结 T0 注册表，按 T0 以后批次回放 | 分别统计既有实体、新实体；注册表中的历史记录是合法输入 |

未见实体评测和生产回放解决不同问题。生产回放允许客户历史档案作为合法索引，但不得把测试期人工答案、未来别名或测试标签提前写入索引。若训练使用了这些客户的标注结果，报告应注明属于“既有实体时间泛化”，不能称为完全未见实体效果。

实体对不是独立分组单位。先按实体分组再生成训练对：训练对两端都只能来自 train；dev/test 同理。存在近重复文档、同一模板衍生记录、转载链时，再按文档家族/来源复制链分组。通用 GroupKFold 可保证同组不跨折，但实体加时间的多重约束需项目自定义划分器并验证。[GroupKFold 官方说明](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupKFold.html)

频率特征、别名字典、停用词、模型、概率校准器都仅使用许可训练窗口拟合。阈值和 top-K 在 dev 上确定后冻结；最终 test 只作验收，不反复看结果调参。时间切分必须保证训练只使用过去；通用工具不能自动解决本项目迟到数据问题。[TimeSeriesSplit 官方说明](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)

### 3.2 必须自动阻断的泄漏

- train/dev/test 的 gold_entity_id 不符合选定协议、同一 document_family 跨集、同一合成原型及变体跨集。
- 测试标签、人工裁决结果、测试期 master 合并表出现在特征、注册表初始化或别名字典。
- 同一测试集反复调门槛后仍声明“独立测试”；应生成新锁定测试版本。
- 默认把客户号、gold_entity_id、canonical_id 作为机器学习普通分类特征，从而记忆实体；标识精确匹配与泛化打分应分开评估。

## 4. 指标定义：分母、拒判和单例都要固定

### 4.1 统一评测全集

在一份冻结评测集合 U 上，`G(i)` 是 mention/observation i 的真实实体簇，`C(i)` 是预测实体簇。簇指标必须对完整已裁决子集计算，gold ID 和预测 ID 只比较成员关系，无需字符串相等。

必须分报两个评测视图。**本次决策视图**只采用当前运行的 matched/new；`unresolved/conflict` 各自分配唯一评测单例 `unresolved:<id>`，不能全放进一个“未知实体”簇，也不能从召回分母中删除。**当前有效分区视图**采用指定 `registry_commit_version`（影子运行使用声明的模拟版本）的 `effective_assignments`；技术失败 keep 的历史有效归属仍在原簇，隔离/撤销且无新有效归属的观察作为单例。每份报告标明视图、快照、原有污染与本次新增错误，不得把保留旧归属算成本次自动成功。

未抽取 mention 在端到端聚类投影中亦保留唯一单例，并另外计入抽取召回；真实单例即使完全未抽取也可能得到完美聚类分数，因此抽取召回不能省略。自动运行与人工处理后快照分别计算，保留每个有效归属的决定来源；混入历史人工裁决的当前分区只能报告整体运营质量，不能冒充纯自动效果。

仅抽样标了几个 pair 时，不能擅自把其他 pair 当 different，不能把不完整标注的簇指标报告为全量 B-cubed。只报告已标 pair 上的条件指标、gold 覆盖范围和已知抽样设计；需要簇指标则先补齐成员裁决。`uncertain` 从确定 gold 指标中分开，但必须报告数量、切片分布和敏感性范围。

### 4.2 候选召回

对一个本应匹配的输入 i，`T(i)` 是在该次回放允许可见、与 i 同一 gold 实体的目标集合，`K(i)` 是算法真正检索并送入判决的候选集合。目标可以是历史 canonical 或本批另一个 observation；按照评测协议处理批内前后顺序，并记录本批临时索引行为。

```text
candidate_hit_recall = Σ_i 1[K(i) ∩ T(i) 非空] / #{i: T(i) 非空}
candidate_pair_recall = Σ_i |K(i) ∩ T(i)| / Σ_i |T(i)|
```

两者都报告 top-1/5/20/K、候选条数 p50/p95/p99、检索耗时、截断率和失败率。`hit_recall` 回答“至少找到一个正确目标”；`pair_recall` 回答“找全多少应比较的目标”。分母来自 gold 和冻结可见范围，不能由检索返回结果定义；检索失败计 miss，真实无目标不进入该分母而进入新实体评测。

批内去重另外检查每个真实实体的候选边是否形成连通图，以及同实体被分成多少候选分量。一个大簇只找到相邻边，pair recall 可能低但仍可连通，因此候选连通率补充解释聚类上限；它不证明这些边足以安全合并。

### 4.3 自动关联精度、覆盖率与拒判

`A` 为本次 `status=matched` 且 `decision_origin=automatic` 的输入决定集合，不能仅凭 method 判断自动；`correct(i)` 要求 i 被关联到同一真实实体且目标簇不存在已知其他 gold 实体。目标簇原已污染时，另报“增量引入污染”和“继承污染”，保守主指标仍视作不正确关联。以下覆盖率和召回均按本次最终决定计数，unresolved + keep 不进入成功分子。

```text
auto_match_precision = Σ_{i∈A} correct(i) / |A|
auto_resolution_coverage = #自动状态为 matched/new 的输入 / #全部范围内输入
auto_match_coverage = #自动 matched 输入 / #全部范围内输入
review_rate = #送人工复核输入 / #全部范围内输入
unresolved_rate = #unresolved 输入 / #全部范围内输入
conflict_rate = #conflict 输入 / #全部范围内输入
existing_match_recall = #被自动正确关联的既有实体输入 / #真实存在可见匹配目标的输入
```

分母为零时记录 `null`、样本量 0、原因，不返回 100%。`new` 不进入合并精度分母，防止新建大量单例稀释错合。精度区间和覆盖率同时呈现，绘制阈值变化的 precision/recall/coverage/review 曲线。人工复核后另存报告，不可用人工修正后的结果宣称自动精度。

### 4.4 新实体识别

每次决策前根据冻结历史与协议允许的批内已处理对象，确定 gold 是否已有可见代表。对尚无代表的实体，第一次可靠建档是 new；同批其余出现应匹配该实体。批处理无固定次序时，在评测规范中指定一个稳定首次代表，并增加与顺序无关的“每个新 gold 实体创建 canonical 数”指标。

```text
new_precision = #正确首次建档 / #自动 new 决策
new_recall = #正确首次建档 / #满足身份充分条件且应首次建档的输入
duplicate_creation_rate = #本已有可见代表却自动 new / #自动 new 决策
new_entity_fragmentation = 每个真实新实体被创建的 canonical 数分布
```

同时对所有真实新实体报告“正确建档/待确认/错配到老实体”的比例，防止通过身份充分条件排除所有困难样本。空姓名、无可靠标识或检索超时触发 `unresolved`，不能被当作正确 new。新增实体被误并入既有实体是严重错合，应独立列出。

“身份充分”采用事先冻结的业务证据口径并由 gold 标注判定，不能依据算法是否输出 new 决定资格。new_recall 的分子只计其分母资格集合中的正确首次建档，避免选择性扩大分子或缩小分母。

### 4.5 Pairwise precision/recall

在 U 内只取 i<j 的无序非自反 pair。`P` 为预测同簇 pair 集，`G` 为真实同簇 pair 集：

```text
TP = |P ∩ G|; FP = |P \ G|; FN = |G \ P|
pair_precision = TP / (TP + FP)
pair_recall = TP / (TP + FN)
pair_F1 = 2TP / (2TP + FP + FN)
```

分母为零的指标记 null 并报告支持数。所有记录预测为单例时，若存在真实多记录实体，pair recall 为 0、precision 未定义，不能因为没有错合报告 100% 精度。不要把海量不同实体 pair 的 true negative 加进普通 accuracy，它会掩盖大量漏合。

大簇在 pair 指标中有平方级权重；同时报普通全量结果、实体大小切片和下述 B-cubed。实现可用 gold×pred 交叉计数及组合数计算 TP，无需枚举全体 O(N²) pair；审计导出错误 pair 时限定数量，保留总计。

### 4.6 B-cubed precision/recall

B-cubed 按对象衡量预测簇的纯度和真实簇的覆盖，在普通互斥分区上定义如下；起源与评测讨论见 [Bagga 与 Baldwin 原论文](https://aclanthology.org/C98-1012/) 和 [Amigó 等聚类指标比较论文](https://doi.org/10.1007/s10791-008-9066-8)。

```text
P_i = |C(i) ∩ G(i)| / |C(i)|
R_i = |C(i) ∩ G(i)| / |G(i)|
B3_P = Σ_i P_i / |U|
B3_R = Σ_i R_i / |U|
B3_F1 = 2 × B3_P × B3_R / (B3_P + B3_R)
```

包含自身，因此真实与预测都为单例时 P_i=R_i=1。`B3_F1` 采用平均 precision/recall 的调和均值，不使用逐对象 F1 再平均，报告固定这一约定。除 mention 加权标准 B-cubed，再报每个 gold 实体等权的宏平均作为补充，明确后者不是上述标准指标。

评测器必须有手算用例：gold=`{a,b,c},{d,e},{f}`，pred=`{a,b,d},{c},{e},{f}`，应得到 TP=1、FP=2、FN=3；pair P=1/3、R=1/4、F1=2/7；B3 P=7/9、R=11/18、F1=154/225。该用例同时含错合、漏合、单例，不与消歧实现绑定。

### 4.7 簇级与业务影响

- **污染簇率**：包含多个 gold 实体的预测簇数 / 预测非空簇数；另报非单例簇版本，防止单例稀释。
- **受错合影响记录率**：位于污染簇中的记录数 / 全部记录数；另报受影响真实实体数、账户数。
- **完整实体恢复率**：恰有一个预测簇与 gold 簇成员完全相等的 gold 实体比例；分别报单例与多记录实体。
- **拆分度**：每个 gold 实体跨多少预测簇，报告均值、p95、最大值。
- **错误关联影响**：错归客户的事件数、账户关联数；金额可作为影响排序字段，不能代替身份正确性。
- **约束违规**：未经授权跨身份隔离域/租户、跨实体类型、明确 cannot-link 被合并、一个生效唯一标识指向多个 active canonical。多个 dataset 可在明确批准的共享身份域中关联，评测验证授权域配置，不能把所有跨 dataset 关联一律判错。

A-B 和 B-C 分数都高，不代表 A-C 无冲突。必须构造桥接节点把两个大簇串起来的测试；一个错误合并两个各 100 条记录的实体簇，会新增 10,000 个错误同实体 pair，不能只计“一条边错了”。

## 5. 怎样证明 99.5% 或 99.9%，而非只报点估计

### 5.1 独立、同分布、随机审计样本的精确二项区间

设 n 个独立自动合并决定中 s 个正确、f=n-s 个错误。报告 `s/n` 和单侧 95% 精确置信下界。小错误数时采用 Clopper–Pearson，而非会在零错误时给出零宽区间的普通正态近似。[NIST 二项比例区间](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm)

零错误时，下界 L 满足 `L^n=α`，所以 `L=α^(1/n)`，95% 单侧取 α=0.05。要使 L≥p0，须 `n≥ceil(log(0.05)/log(p0))`：

| 零错误独立样本数 n | 单侧 95% 精度下界 | 可以支持的结论 |
|---:|---:|---|
| 100 | 97.0487% | 不足以支持 99.5% |
| 500 | 99.4026% | 仍不足以支持 99.5% |
| 598 | 99.5003% | 满足 99.5% 门槛的零错最小样本量 |
| 1,000 | 99.7009% | 不足以支持 99.9% |
| 2,995 | 99.9000% | 满足 99.9% 门槛的零错最小样本量 |

这些是统计抽样条件成立时的公式推导，不代表标了 598 条重复客户旅程就能证明质量。若出现错误，应计算实际区间并增加有效样本或修正算法；不能删除错例。一般下界为 `BetaQuantile(α; s, n-s+1)`（s=0 时为 0）。统计适配器可用 SciPy 的 `binomtest(s,n,alternative="greater").proportion_ci(confidence_level=0.95,method="exact")`，通过 ports 提供服务；evaluation 业务模块不得直接 import SciPy。固定依赖版本并用上述零错结果核对。[SciPy 官方 API](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html)

“95% 置信下界”是重复抽样覆盖意义，不是“此次参数有 95% 概率高于门槛”。若想以较高概率通过验收，还需根据预期真实精度和检验功效另算样本量；零错最小 n 不是功效规划。

### 5.2 相关性、分层与反复查看

同一客户的 100 次事件、同一错误模板产生的 100 个记录、同簇所有 pair 并非 100 个独立试验。应以真实实体、预测污染连通分量或来源家族作为相关组，在统计方案中说明抽样单位；评估两系统差异时按同一组做配对 cluster bootstrap，不能按 pair 独立 bootstrap。

若直接抽簇并判定“整簇是否全对”，二项区间描述的是簇级正确率，不能改名为记录级精度。若需记录级或决策级指标，采用正确的抽样权重和设计型方差，或选择每组独立抽取的代表决定并明确其目标分布；不能只把组数替换进公式而保持原有分子。

零错误数据的普通 bootstrap 往往所有重采样仍零错，无法体现未观察到错误的风险；不能用退化到 100% 的 bootstrap 区间替代精确界或经审定的保守抽样界。实体组很少、某来源无标注或权重极端时，应报告证据不足。

所有验收切片提前登记。同时验收多个切片可使用 Bonferroni 等保守 α 分配，避免把多个 95% 当作整体 95%；不断查看结果直到“刚好通过”会改变误报率，应预定样本量与窗口，或采用专门的序贯检验方案。

## 6. 线上没有全量 gold 时怎样检测

### 6.1 日志能够发现异常，但不能直接计算精度

持续观测 matched/new/unresolved/conflict 分布、命中规则分布、top1-top2 分差、候选为空和截断率、关键字段缺失率、簇尺寸增长、来源分布、人工推翻率、索引滞后与失败率。相比已验证基线的突变用于告警和定位；“没有冲突报警”“模型分数 0.999”均不等于正确率 99.9%。

自动合并抽检侧重错合；对 new、unresolved、冲突和检索未命中也要独立抽检，用更宽检索及业务材料寻找漏合。仅看模型已召回候选或人工复核队列，会系统性漏掉召回错误。历史投诉可以补充错例，不代表总体错误率。

### 6.2 固定周期的概率抽样审计

将每个来源×规则类型×分数段×动作构成互斥、穷尽的审计层 h，冻结总体数 N_h、抽样数 n_h、随机种子与纳入概率。保留覆盖全部总体的随机样本，并对高风险层加抽；定向排查的便利样本单独报告。

若每层内等概率抽样，e_h 为样本错误数，则：

```text
总体错误率估计 q_hat = Σ_h (N_h / Σ_h N_h) × (e_h / n_h)
总体精度估计 p_hat = 1 - q_hat
```

不等概率抽样保留 π_i，可用 `Σ_i(y_i/π_i)/Σ_i(1/π_i)` 的加权比例估计，其中 y_i=1 表示正确；置信区间必须使用与分层、整群、多阶段抽样一致的方法。若某层 n_h=0，不能默认为正确；应补样或明确该层不可估计。

例如 90% 流量来自层 A、10% 来自层 B，各抽 100 条，分别发现 0 和 5 个错误，未加权样本错误率是 2.5%，总体加权估计却是 0.5%。两者回答的问题不同；不能因困难层加抽就把原始样本均值当生产精度。

可实施的保守验收方法：在满足层内独立二项假设时，对 H 个层分别用 α/H 求错误率上界 U_h，再以 `Σ_h W_h U_h` 作为总体错误率上界；存在簇相关时仍须改用相应整群抽样方法。上线初期建议由统计审核确认抽样框与区间实现。

审计存在 uncertain 时，若已查 s 条正确、f 条错误、u 条不确定，样本正确率敏感性区间为 `[s/(s+f+u), (s+u)/(s+f+u)]`；加权样本按权重计算同样上下界，再结合抽样不确定性。不能仅剔除 uncertain 后宣称全部决定达标。记录未回复、证据缺失等审计非响应，并补查其系统偏差。

### 6.3 影子与灰度发布

用相同冻结输入、注册表快照和真实批次顺序，回放当前版本与候选版本；输出改变的决定、受影响簇、证据与阈值，重点复核“原分离→新合并”。独立影子注册表不得被正式人工裁决提前污染。

离线过门后按预先约定来源和流量灰度；低支持或新来源先只给建议。监控重大错合、无法回放、硬约束违反和故障降级。任何暂停自动合并都保留入库证据、拒判状态与人工任务；回滚恢复注册表和图投影的一致版本。

## 7. 建议验收门槛与发布决策

以下数字仅为**建议，待真实数据实测及业务确认**；不是银行已认可标准，也不是本项目实测结果。以同一冻结测试协议比较，绝不能只追某个平均分。

| 项目 | 第一阶段建议门槛 | 说明 |
|---|---|---|
| 自动关联 precision | 单侧 95% 下界 ≥99.5% | 样本设计成立；更高风险可要求 ≥99.9% |
| 候选 hit recall | 点估计 ≥99.5%，报告区间与每来源值 | 单列批内、跨批、弱标识；小样本不足则暂不放量 |
| 多记录实体 B3 recall | ≥98%，同时报告 pair recall | 初始讨论值，必须评估数据缺失与人工成本 |
| hard cannot-link、未经授权跨身份域/租户及跨类型错合 | 固定安全用例 0 违规 | 是软件不变量验收；明确批准共享域另有正例 |
| 已确认图引用一致性、幂等、回滚恢复 | 测试集全部通过 | 见测试矩阵 |
| 自动覆盖率、new precision、复核率 | 先报基线，再确定各来源门槛 | 不凭空要求所有来源相同覆盖率 |
| 复核处理量 | 不超过已确认人工容量，报告 p95 等待时间 | 通过提高拒判量达到精度不可造成任务堆积 |
| 时间/来源外推 | 分别报告，关键来源无明显质量退化 | 未验证来源默认不自动套用既有认证 |

“建议门槛通过”需要指标估计、区间、切片支持量、gold 质量、运行可靠性一起满足。若精度通过但覆盖率接近零、召回明显下降、人工任务不可处理，判定未满足产品验收。缺少足够 gold 时，结论是“尚未证明”，不是“不存在错误”。

## 8. 工程测试矩阵

| 类别 | 必测场景 | 预期/核验方式 |
|---|---|---|
| 基础身份 | 同命名空间同标识、不同命名空间同值、共享域授权、前导零、空值 | 只在授权语义中匹配；空值不产生公共实体 |
| 强标识冲突 | 一个 observation 的多个键指向不同 canonical | conflict；不得按分数最高强并 |
| 类型角色 | 母子公司、企业法人、账户与客户、同一法人管理两企业 | 关系保留，身份分离 |
| 时间 | 旧名称、变更日期、迟到数据、未来信息、合法历史编号映射 | 严格按可见时点；历史映射可追溯 |
| 弱字段 | 同名同址、共享电话、同园区、简称、OCR/缺字 | 无充分证据不自动合；困难集单列 |
| 候选检索 | 正确实体不在 top-K、本批新增/重复、索引水位落后读取版本 | miss 被计数；不能补齐增量时检索不完整，禁止 new/依赖此检索的自动模糊匹配 |
| 聚类 | A-B/B-C 桥接、强 cannot-link、超大簇、成百上千重复事件 | 检查整簇约束与污染影响，不能只有边级断言 |
| 上游错误 | 原始两人同错误编号、属性冲突、错误字段角色、漏抽实体 | mention 级评测能发现；引用存在不自动判抽取正确 |
| 幂等重试 | 同 idempotency_key 重试、同一消息重复投递 | 复用旧决定及提交结果，不重复证据/归属副作用 |
| 新批重导 | 同来源内容以新 batch 重导 | 保留新批证据及新决定，通过身份匹配复用实体；不能把新证据吞掉 |
| 并发 | 两批同时创建同键、同时合并同簇、读取过期版本 | 唯一约束/版本检查；冲突重试后一致 |
| 故障 | 索引/模型/数据库超时、事务提交后进程崩溃、图写失败 | 不静默降级为 new；重试可恢复一致性 |
| 旧归属重评 | 超时/信息不足，或发现可信身份冲突 | 前者 keep 并标未重验；后者 quarantine 受影响归属，禁止继续作自动身份锚点 |
| 图谱发布 | 一次归属更新涉及多个节点、事件与边，投影中途失败 | 对读者按版本原子可见；禁止新旧归属混合，重试不重复发布 |
| 人工回写 | 复核结果重复投递、过期复核、人工 split | 幂等；旧版本决定不能覆盖新状态 |
| 撤销回滚 | 错合后又新增证据/事件，再撤销 merge | 按来源恢复归属；派生图重建；新证据不丢失 |
| 聚合关系纠正 | 多条源事实支持同一聚合边，其中一条归属被纠正 | 按源事实支持重算端点/证据，其他支持仍存在时不能整边删除 |
| 批次顺序 | 固定流的多种批大小、相同时间记录次序 | 比較最终分区与稳定 ID；若在线策略有顺序依赖则量化并声明 |
| 性能 | 不同实体量、脏字段高频桶、热键、最大证据簇、冷/热缓存 | 耗时/候选数/内存/吞吐均有上界与回压 |
| 指标正确性 | 手算用例、全单例、全一簇、空集、缺标签、零错区间 | 与数学预期一致；未定义结果显式 null |

性能压测保存硬件、并发、软件版本、数据库/索引大小、实际候选分布；分别测解析、标准化、召回、打分、事务与图投影。可先测试 1 万/10 万/100 万观察量阶梯，检查候选生成不退化成无界全量两两比较。p95/p99 延迟和吞吐门槛需要部署资源与批处理窗口确定，当前不虚构已实现 TPS。

## 9. 后续开发的数据与命令契约

### 9.1 建议目录

```text
configs/resolution/evaluation.json       # 指标、阈值、切片、置信度和采样协议
evaluation/resolution/README.md          # 操作流程、数据来源和限制
evaluation/resolution/schemas/           # manifest/observations/gold/predictions 的 JSON Schema
evaluation/resolution/fixtures/          # 纯合成、可入库的手算与故障用例
evaluation/resolution/manifests/         # 数据摘要与受控存储引用，不含真实客户敏感数据
src/bank_project/evaluation/resolution/   # 后续新增：纯指标、校验、抽样与评测编排
src/bank_project/adapters/evaluation/    # 后续新增：文件/统计/第三方依赖适配器
tests/resolution/                        # 后续新增：消歧回归/并发/回滚/故障测试
tests/evaluation/                        # 后续新增：指标、区间、采样与防泄漏验证
data/resolution-eval/<run_id>/           # 指标 JSON、报告、差异、错误审计；已受 Git 忽略规则保护
```

evaluation 是业务模块，只依赖自身、标准库、`contracts`、`ports`；不直接 import SciPy、sklearn、其他业务模块实现或数据库/文件适配器。第三方统计与切分工具、输入输出放在 adapters 并通过 ports 注入；与 resolver 的回放协作由应用编排连接端口。上文工具文档说明算法和参考实现，不代表允许打破项目模块边界。

真实 gold、原始银行记录、审计证据存受控数据区；仓库只存 schema、合成用例和受控引用。敏感标识用一致的受控伪名化方法；须验证处理后仍保留任务所需相似度和错误模式，不能使用会把相同实体拆散的逐行随机脱敏来评真实效果。

### 9.2 建议交换字段

| 文件 | 最低字段 |
|---|---|
| `manifest.json` | benchmark_id/version、范围、gold_version、split_protocol、snapshot/as_of、输入摘要、采样框、seed、可见性规则、标注说明 |
| `mentions.jsonl` | mention_id、source_id、record_key、locator/span、occurrence_index、role、entity_type、raw/normalized_identity_refs、observed_at、valid_at、document_family、observation_id（未抽取时为空） |
| `observations.jsonl` | observation_id、source_version、locator/span、occurrence_index或稳定映射alias、extraction_contract_version、role、entity_type、candidate_id（旁路，可空）、evidence_refs、observed_at、valid_at；定位不依赖身份值或仅靠角色 |
| `gold_entities.jsonl` | mention_id/observation_id、gold_entity_id（可空）、label_status、evidence_refs、annotators、adjudicator、gold_version |
| `gold_pairs.jsonl` | left_id/right_id、same/different/uncertain、reason_code、evidence_refs、annotator_ids、adjudicated_at |
| `splits.jsonl` | sample_id、split、group_id、time_window、source_holdout、visibility_snapshot |
| `retrieval.jsonl` | run_id、query_id、target_id、rank、channel、raw_score、available_at、truncated、retrieval_complete、retrieval_status、index_version、indexed_registry_version、registry_read_version |
| `predictions.jsonl` | run_id、observation_id、status、canonical_id（可空）、method（可空）、decision_origin、assignment_action、rule/model/calibration_version、decision_id、reason_codes、raw_score、calibrated_probability、runner_up、registry_read_version、registry_commit_version（影子/未提交时可空）、latency_ms |
| `cluster_membership.jsonl` | membership_id/version、snapshot_id、canonical_id、observation_id、effective_from/to、assignment_state、decision_id、decision_origin、supersedes、registry_commit_version；承载指定版本 effective_assignments |
| `audit_samples.jsonl` | population_window、decision_id、stratum、N_h、n_h、inclusion_probability、sample_group、review_label、evidence_refs、reviewer |
| `metrics.json` | metric_name、estimand、evaluation_view=current_decisions/effective_partition、snapshot_version、value、numerator、denominator、slice、ci_method/lower/upper、gold_coverage、unknown_count、weights、warnings |

标注侧可有比预测输入更多的业务核实信息，但预测时必须严格限制到可见证据。测试 gold 通过独立路径加载，评测命令不把 gold 传给 resolver。`candidate_to_canonical` 无法表达拒判和轨迹，开发评测器前要先补状态与审计输出。

### 9.3 建议 CLI（均为待开发接口）

```bash
bank-project resolution-eval validate --manifest <manifest.json>
bank-project resolution-eval split --manifest <manifest.json> --protocol entity-disjoint
bank-project resolution-eval replay --manifest <manifest.json> --resolver <version> --registry <snapshot>
bank-project resolution-eval score --gold <gold-dir> --predictions <run-dir> --config <evaluation.json>
bank-project resolution-eval compare --baseline <run-id> --candidate <run-id>
bank-project resolution-eval audit-sample --window <window-id> --plan <sampling.json>
bank-project resolution-eval gate --run <run-id> --config <evaluation.json>
```

`validate` 核验 schema、ID 唯一、same/different 一致、成员完整、权重、时间和 split 防泄漏；`replay` 从只读快照运行，不污染生产注册表；`score` 分报本次决策与当前有效分区的总体/切片指标，`compare` 导出行为变化和配对统计；`gate` 返回机器可读 pass/fail/insufficient_evidence，CI 可据此阻止发布。当前 `bank-project` 仅有 `serve`，上述命令尚不存在。

## 10. 可以直接排期的开发任务

| 顺序 | 工作项 | 完成定义 |
|---|---|---|
| E1 | 明确身份口径、标注手册、关键业务切片 | 业务与工程共同确认；含正例、困难负例、uncertain 样例 |
| E2 | 增加 observation/mention 追踪、状态、候选与决策审计契约 | 每个输入有结果，所有预合并可展开，人工自动可分离 |
| E3 | 搭建 gold/schema/防泄漏校验 | 不一致三角、未来证据、跨集实体会被自动发现 |
| E4 | 实现指标与置信区间 | 手算簇、单例/空集、598/2995 零错界全部核对 |
| E5 | 建代表性 gold 与困难集 | 双人标注、裁决、实体完整性检查、版本冻结 |
| E6 | 实现回放与基线比较 | 同快照可重现，报告 exact-only 与候选方案的增益/损失 |
| E7 | 增量/并发/链式错误/回滚测试 | 矩阵关键用例通过；原始证据、注册表、图投影可核对 |
| E8 | 实现概率审计抽样与报告 | 保存抽样概率；加权估计与不确定标签不被隐藏 |
| E9 | 影子与受控灰度验收 | 门槛、支持数、覆盖率、人工容量和回滚演练共同通过 |

每次发布报告应包含：版本与数据摘要、适用范围、gold 质量、抽样和切分、自动与人工两套指标、来源/实体类型/缺失模式/簇大小切片、置信区间、失败案例、候选与聚类错误归因、运行可靠性、未获验证范围。报告中“未测”“无标签”“证据不足”必须与“通过”区别显示。
