"""Human-readable scenario answers, kept separate from the five inputs."""

from pathlib import Path

from .common import OUTPUT, RISK_NAMES, TABLES, dump, load


def main():
    out = Path.home() / "Desktop/五张表_风险验证说明"
    out.mkdir(exist_ok=True)
    truth = load(OUTPUT / "risk_truth.json")
    evidence = {r["客户号"]: r for r in load(OUTPUT / "risk_evidence.json")}
    manifest = load(OUTPUT / "manifest.json")
    validation = load(OUTPUT / "validation.json")
    backup = load(OUTPUT / "backup.json")
    intro = """# 五张表数据更新与风险验证说明

本批是用于分析验证的合成测试数据，不是真实客户经营记录。快照日期为 **2026-09-17**，交易窗口为 **2025-09-18 至 2026-09-17**。更新制作日期为 2026-09-20。

## 五张输入表

| 桌面文件 | 数据库表名 | 数据行数 |
|---|---|---:|
"""
    for label, table in TABLES.items():
        intro += f"| {label}.xlsx | `{table}` | {manifest['tables'][label]['rows']:,} |\n"
    intro += """
保留原有字段及工作表结构，字段说明同步更新。数据库沿用原有文本字段类型；进行金额、日期统计时应显式转换类型。编号按字符串保存，前导零保留。技术编号与测试事件码统一移除 `MOCK_` 前缀，五表中的引用同步变更。

征信的 98 个来源和工商的 39 个来源仍分别合并在两张宽表中，通过 `source_table` 区分；它们不是新增的数据库表。汇总与明细可能描述同一事实，分析时先筛选来源，避免重复累加。

客户号关系：标签 `cust_ind` = 旅程 `CUST_ID` = 流水 `cust_ind`。工商优先通过 `unify_credit_code` 关联企业；本批企业征信报告号 `EA01AI01` 与客户号后四位对应，例如 `0001` → `CUST_0001`，这是本测试包的编号约定。

## 提高数据自然度的修改

- 经营收付款按企业规模、月份与工作日分别生成，收款方和供应商不再一一配对；保留春节月份回落及月度波动。
- 同一工资批次保留原总金额和人数，但员工工资分布不再相同，并包含稳定的员工差异与小幅月度变化。
- 少量冲正保留完整原流水关联；活期转定期同时记录活期支出与定期入账，重算账户逐笔余额。
- 客户期末存款、日均存款、工资统计、结算统计和旅程汇总随流水同步更新。
- 清理原覆盖样例中广泛分布的“当前逾期/当前未结执行”，将当前目标风险集中到少量客户；已结案、已移出等历史记录仍可用于检验程序是否误判。

全部 1,000 个客户中，设置 40 个互不重叠的目标案例，占 **4%**，每类 8 个。其余 960 个客户没有额外设置这五类目标风险；这不代表对任何可能规则都保证无风险。

## 五类风险及观察点

| 风险 | 人数 | 输入数据中的证据 | 分析时应注意 |
|---|---:|---|---|
| 经营回款下降、资金压力 | 8 | 6–8 月经营回款较 3–5 月下降约 58%–63%，经营支出和工资继续发生 | 从 `SETTLE_IN` / `SETTLE_OUT` 与工资明细计算趋势，不能把贷款入账当销售收入 |
| 回款客户集中度上升 | 8 | 6–8 月最大交易对手的回款占比约 79%–96% | 按交易对手账号聚合；集中度是线索，不直接等于坏账 |
| 贷款逾期 | 8 | 企业征信还款记录存在当前逾期金额与 1 个逾期期数，分类为关注 | 从 `PBCEC_ED01B_REPAYMENT` 明细取证；标签与汇总只是同一事实的反映 |
| 未结司法执行与经营异常 | 8 | 未履行金额大于零、执行状态未结、经营异常尚未移出；征信与工商可交叉核对 | 区分当前有效风险和已经执行完毕、已移出的历史记录 |
| 结算活跃度下降、流失迹象 | 8 | 之前有正常交易，6 月起停止客户主动结算，近期旅程相应减少 | 排除新开户观察期不足；流失迹象不等同经营失败 |

这些风险是分析线索，不是评级结论。现金流下降、客户集中与结算沉寂没有另添“正确答案”字段，程序需自行计算。原有当前逾期、经营异常标签按来源事实保持一致。

## 离线核对结果及阈值边界

已对全部 757,830 笔交易核对借贷金额、账户序号、日期与逐笔余额，并核对客户名称关联、代发工资与旅程汇总、存款标签、企业征信余额，以及来源字段语义规则：校验错误 0。

独立提取原始事实后，用一组简单阈值做冒烟检查：回款比值小于 0.55 且大于 0、最大回款对手占比大于 80%、存在当前逾期、存在未结执行、结算笔数比值低于 0.2 且前期至少 15 笔。40 个目标案例中 39 个命中，未产生额外命中。

`CUST_0816`（苏州璟途精密制造铭有限公司）的最大回款对手占比为 **79.33%**，是保留的阈值附近案例，未通过“严格大于 80%”规则。它仍具有集中度上升的线索，可检验外部程序是否结合变化趋势判断。

以上只是本次数据的离线一致性与示例规则核对。**未运行或测试你的外部分析程序，39/40 不是外部程序的识别率。**

比较窗口使用两个完整的三个月：3 月 1 日–5 月 31 日、6 月 1 日–8 月 31 日，避免将 9 月未完月份直接与完整月份比较。

## 使用方式

将桌面“\u4e94张表_修正版”中的五份 Excel 或数据库五张业务表交给分析程序。此说明目录中的风险答案和对照客户清单用于结果核对，独立于五张输入表。

附带 `40个风险案例.md` 和 `20个对照客户.md`，分别列出客户号、名称和可核对的事实。数据库中不增设风险答案表。
"""
    intro += f"\n更新前完整备份：`{backup['directory']}`。其中包含原 Excel 及原数据库全部 10 张表的 SQL 压缩备份。\n"
    if (OUTPUT / "database_published.json").exists():
        published = load(OUTPUT / "database_published.json")
        intro += f"\n数据库已于 {published['published_at']} 更新，确认只保留上述五张表。导入来源是更新后的 Excel 回读数据；入库后全量内容指纹一致。旧的会话记忆、分块、评分、目录表及五张旧表均已移除。\n"
    (out / "数据更新与分析说明.md").write_text(intro, encoding="utf-8")
    report = "# 40 个风险案例答案\n\n数据快照：2026-09-17。此文件独立于五张分析输入表。\n"
    for kind, ids in truth["scenarios"].items():
        report += f"\n## {RISK_NAMES[kind]}（8 个客户）\n\n| 客户号 | 客户名称 | 关键证据 |\n|---|---|---|\n"
        for cid in ids:
            e = evidence[cid]
            if kind == "cashflow_decline":
                detail = f"3–5 月回款 {float(e['3至5月经营回款']):,.2f} 元，6–8 月 {float(e['6至8月经营回款']):,.2f} 元，下降 {1 - e['回款环比']:.2%}"
            elif kind == "counterparty_concentration":
                detail = f"6–8 月经营回款 {float(e['6至8月经营回款']):,.2f} 元，最大对手占比 {e['最大回款客户占比']:.2%}"
            elif kind == "credit_overdue":
                detail = f"当前逾期金额 {float(e['当前逾期金额']):,.2f} 元，存在 1 个逾期期数"
            elif kind == "legal_execution":
                detail = "存在未结执行，未履行金额大于零；经营异常尚未移出"
            else:
                detail = f"6–8 月经营回款为零，结算笔数为 3–5 月的 {e['活跃度比值']:.0%}"
            report += f"| {cid} | {e['客户名称']} | {detail} |\n"
    (out / "40个风险案例.md").write_text(report, encoding="utf-8")
    controls = "# 20 个对照客户\n\n这些客户未设置目标风险；用于观察程序是否仅凭“出现过历史记录”或规模差异而误报。\n\n| 客户号 | 客户名称 | 回款比值 | 最大回款对手占比 | 当前逾期 | 当前未结执行 |\n|---|---|---:|---:|---:|---|\n"
    for cid in truth["controls"]:
        e = evidence[cid]
        ratio = "观察期不足" if e["回款环比"] is None else f"{e['回款环比']:.2%}"
        controls += f"| {cid} | {e['客户名称']} | {ratio} | {e['最大回款客户占比']:.2%} | {e['当前逾期金额']} | {'有' if e['存在未结执行'] else '无'} |\n"
    (out / "20个对照客户.md").write_text(controls, encoding="utf-8")
    dump(
        out / "风险核对答案.json",
        {
            "synthetic": True,
            "as_of": truth["as_of"],
            "scenarios": truth["scenarios"],
            "controls": truth["controls"],
            "evidence": [evidence[c] for ids in truth["scenarios"].values() for c in ids],
            "external_program_tested": False,
        },
    )
    # Replace the superseded preliminary check with the completed full result.
    dump(
        OUTPUT / "semantic_validation.json",
        {
            "error_count": validation["error_count"],
            "covered_by": "validation.json; all credit/business rows checked",
        },
    )
    print("Risk report ready:", out, flush=True)


if __name__ == "__main__":
    main()
