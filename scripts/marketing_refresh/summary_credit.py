"""One subject/report per row; account and financial facts keep their own scope."""

from .summary_common import Summary, cny_total, count, decimal, known_count, latest_accounts

HEADER = "PBCEC_EA01AB_REPORTHEADER"
PERSON = "PBCPC_PA01AB_REPORTHEADER"
IDENTITY = "PBCEC_EA01C_IDENTITYINFO"
PROFILE = "PBCEC_EC01_BASICIDENTITY"
CREDIT = "PBCEC_EB01A_CREDITCUE"
LIMIT = "PBCEC_EB04_FACILITYAGTSUM"
ACCOUNT = "PBCEC_ED01A_BASICLOANACCT"
REPAYMENT = "PBCEC_ED01B_REPAYMENT"
GUARANTEE = "PBCEC_ED04AB_GUARANTEEDETAIL"
PERSON_ACCOUNT = "PBCPC_PD01ABC_PERFORMANC"
QUERY = "PBCPC_PC05AB_QUERYRECOREDSUM"
FINANCIAL = {
    "assets": ("资产总额", "DEBT", "BJ31", "BJ44"),
    "liabilities": ("负债总额", "DEBT", "BJ53", "BJ68"),
    "equity": ("所有者权益", "DEBT", "BJ59", "BJ86"),
    "revenue": ("营业收入", "PROFIT", "BJ01", None),
    "net_profit": ("净利润", "PROFIT", "BJ17", "BJ43"),
    "operating_cash": ("经营活动现金流净额", "CASH", "BJ10", "BJ10"),
}
FIN_TABLES = {
    "DEBT": ("PBCEC_EG02AB_DEBTINFO2007", "PBCEC_EG01AB_DEBTINFO2002"),
    "PROFIT": ("PBCEC_EG04AB_PROFITINFO2007", "PBCEC_EG03AB_PROFITINFO2002"),
    "CASH": ("PBCEC_EG06AB_CASHINFO2007", "PBCEC_EG05AB_CASHINFO2002"),
}


def financials(source, report):
    records = {t: source.one(t, "EA01AI01", report) for pair in FIN_TABLES.values() for t in pair}
    if not any(records.values()):
        return {"financial_status": "未提供企业2002/2007财务记录"}
    scopes = {
        (r[t[6:10] + "AR01"], r[t[6:10] + "AD02"], r[t[6:10] + "AD03"])
        for t, r in records.items()
        if r
    }
    if len(scopes) != 1:
        return {
            "financial_status": "财务期间或报表类型不同，未汇总",
            "financial_conflicts": "scope",
        }
    scope = next(iter(scopes))
    has_2007 = any(records[pair[0]] for pair in FIN_TABLES.values())
    result = {
        "financial_year": str(scope[0]),
        "financial_version": "2007" if has_2007 else "2002",
        "financial_scope": "/".join(map(str, scope[1:])),
    }
    conflicts = []
    for key, (_, family, modern_field, old_field) in FINANCIAL.items():
        modern, old = FIN_TABLES[family]
        m = decimal(records[modern].get(modern[6:10] + modern_field))
        o = decimal(records[old].get(old[6:10] + old_field)) if old_field else None
        if m is not None and o is not None and m != o:
            result[key] = None
            conflicts.append(key)
        else:
            # Never fill one version's gaps using the other version's figures.
            selected = m if has_2007 else o
            result[key] = float(selected) if selected is not None else None
    result["financial_conflicts"] = ", ".join(conflicts) or None
    result["financial_status"] = "版本冲突，相关金额留空" if conflicts else "已提取；缺失金额仍为空"
    return result


def build(source, customers):
    out = Summary(
        "征信",
        "一行 = 一个主体的一份报告；主体类型 + report_id 唯一",
        "来源明细/征信明细.xlsx",
        source,
    )
    for key, name, origin in [
        ("subject_type", "主体类型", "PBCPC=个人；PBCEC=企业及其他组织"),
        (
            "cust_ind",
            "客户编号",
            "企业EA01CI01 = 客户标签.unify_credit_code 精确关联；个人不关联企业客户",
        ),
        ("subject_name", "主体名称", f"{PERSON}.PA01BQ01 / {IDENTITY}.cust_nm"),
        ("id_type", "主体证件类型", f"{PERSON}.PA01BD01 / {IDENTITY}.EA01CD01"),
        ("id_number", "主体证件号码", f"{PERSON}.PA01BI01 / {IDENTITY}.EA01CI01"),
        ("report_id", "报告编号", f"{PERSON}.PA01AI01 / {HEADER}.EA01AI01"),
        ("report_time", "报告时间", f"{PERSON}.PA01AR01 / {HEADER}.EA01AR01"),
        ("dt", "数据日期", f"{PERSON}.dt / {HEADER}.dt"),
    ]:
        out.field(key, name, origin)
    profile_fields = [
        ("industry_code", "行业代码", "EC010D04"),
        ("company_scale_code", "企业规模代码", "EC010D03"),
        ("founding_year", "成立年份", "EC010R01"),
        ("company_status_code", "企业状态代码", "EC010D05"),
    ]
    for key, name, field in profile_fields:
        out.field(key, name, f"{PROFILE}.{field}")
    limits = [
        ("nonrevolving_limit", "非循环授信额度", "EB040J01"),
        ("nonrevolving_used", "非循环已用额度", "EB040J02"),
        ("nonrevolving_available", "非循环可用额度", "EB040J03"),
        ("revolving_limit", "循环授信额度", "EB040J04"),
        ("revolving_used", "循环已用额度", "EB040J05"),
        ("revolving_available", "循环可用额度", "EB040J06"),
    ]
    for key, name, field in limits:
        out.field(
            key,
            name,
            f"{LIMIT}.{field}",
            "原表汇总值，不重复累加，不与借贷余额相加",
            "number",
            "原表单位",
        )
    for key, name, origin, rule, unit in [
        (
            "loan_account_count",
            "借贷账户记录数",
            f"{ACCOUNT}.ED01AI01 / {PERSON_ACCOUNT}.PD01AI01",
            "报告内按账户去重，取最新信息日期；没有明细留空",
            "个",
        ),
        (
            "open_loan_count",
            "企业未结清借贷账户数",
            f"{ACCOUNT}.ED01AD01",
            "仅企业，状态1计入；全部状态已知才计数",
            "个",
        ),
        (
            "closed_loan_count",
            "企业已结清借贷账户数",
            f"{ACCOUNT}.ED01AD01",
            "仅企业，状态2计入；全部状态已知才计数",
            "个",
        ),
        (
            "reported_loan_balance",
            "报告借贷交易余额",
            f"{CREDIT}.EB01AJ01",
            "报告原汇总值，保留口径，不与明细余额叠加",
            "原表单位",
        ),
        (
            "loan_balance_cny",
            "借贷账户人民币余额",
            f"{REPAYMENT}.ED01BJ01 + {ACCOUNT}.ED01AD07 / {PERSON_ACCOUNT}.PD01BJ01,PD01AD04",
            "报告+账户精确关联，按信息日期选最新；仅CNY，缺失币种或金额则留空",
            "原表人民币金额单位",
        ),
        (
            "current_overdue_count",
            "企业当前逾期账户数",
            f"{REPAYMENT}.ED01BJ04",
            "仅企业，金额>0计数；缺失则留空，不与个人历史逾期混合",
            "个",
        ),
        (
            "current_overdue_cny",
            "企业当前逾期人民币金额",
            f"{REPAYMENT}.ED01BJ04 + {ACCOUNT}.ED01AD07",
            "按报告+账户关联，CNY合计；缺失币种或金额留空",
            "原表人民币金额单位",
        ),
        (
            "current_overdue_principal_cny",
            "企业当前逾期人民币本金",
            f"{REPAYMENT}.ED01BJ05 + {ACCOUNT}.ED01AD07",
            "CNY合计；与逾期金额不同，不重复累加",
            "原表人民币金额单位",
        ),
        (
            "max_current_overdue_months",
            "企业当前最大逾期月数",
            f"{REPAYMENT}.ED01BS02",
            "账户最新记录取最大，任一缺失留空",
            "月",
        ),
        (
            "reported_guarantee_balance",
            "报告担保交易余额",
            f"{CREDIT}.EB01AJ05",
            "报告汇总值，不与担保明细合计叠加",
            "原表单位",
        ),
        (
            "guarantee_account_count",
            "担保账户记录数",
            f"{GUARANTEE}.ED04AI01",
            "同报告账户按信息日期取最新；未提供留空",
            "个",
        ),
        (
            "guarantee_balance_cny",
            "担保账户人民币余额",
            f"{GUARANTEE}.ED04BJ01,ED04AD04",
            "仅CNY余额合计；有未知币种或缺失金额时留空",
            "原表人民币金额单位",
        ),
        (
            "unknown_currency_count",
            "明细未知币种记录数",
            f"{ACCOUNT}.ED01AD07 / {GUARANTEE}.ED04AD04 / {PERSON_ACCOUNT}.PD01AD04",
            "统计参与账户汇总的未知币种；无账户明细则留空",
            "条",
        ),
    ]:
        out.field(key, name, origin, rule, "number", unit)
    out.field(
        "latest_repayment_date",
        "最近实际还款日期",
        f"{REPAYMENT}.ED01BR04 / {PERSON_ACCOUNT}.PD01BR02",
        "当前可见账户明细日期取最大；不代表历史数据完整",
    )
    for key, name in [
        ("financial_year", "财务年份"),
        ("financial_version", "财务字段版本"),
        ("financial_scope", "财务报表类型及子类型"),
    ]:
        out.field(
            key,
            name,
            "PBCEC_EG01至EG06.AR01 / AD02 / AD03",
            "仅同期间同类型可提取；2007字段组优先，冲突单独标注；版本号不代表可信度",
        )
    for key, (name, family, m, o) in FINANCIAL.items():
        modern, old = FIN_TABLES[family]
        origins = f"{modern}.{modern[6:10]}{m}" + (f" / {old}.{old[6:10]}{o}" if o else "")
        out.field(
            key,
            name,
            origins,
            "同期间同类型两版本非空值冲突时留空；不跨版本填补缺失；2002主营业务收入不冒充营业收入",
            "number",
            "原表单位（未明确，禁止与元直接相加）",
        )
    out.field(
        "financial_conflicts",
        "财务冲突字段",
        "同期间同类型2002与2007版本对比",
        "列出被留空的冲突字段；对应原数值在来源明细",
    )
    out.field(
        "financial_status",
        "财务数据状态",
        "企业2002/2007财务表覆盖情况",
        "未提供、可提取或冲突；事业单位财务明细保留在来源文件，当前不套企业口径",
    )
    public = [
        ("tax_record_count", "欠税记录数", "PBCEC_EF01_TAXINFO", "PBCPC_PF01AZ_TAXARREAR"),
        (
            "execution_record_count",
            "强制执行记录数",
            "PBCEC_EF03_FORCEEXECUTION",
            "PBCPC_PF03AZ_FORCEEXECUTION",
        ),
        (
            "penalty_record_count",
            "行政处罚记录数",
            "PBCEC_EF04_ADMINPUNISHMENT",
            "PBCPC_PF04AZ_ADMINPUNISHMENT",
        ),
    ]
    for key, name, enterprise, person in public:
        out.field(
            key,
            name,
            f"{enterprise} / {person}",
            "当前提供的报告内记录数；无记录为未知，留空，不能推断无风险",
            "number",
            "条",
        )
    out.field(
        "queries_loan_1m",
        "个人近1月贷款审批查询次数",
        f"{QUERY}.PC05BS03",
        "仅个人，不与企业或近2年查询口径混合",
        "number",
        "次",
    )
    out.field("last_query_date", "个人上次查询日期", f"{QUERY}.PC05AR01")
    out.field(
        "historical_overdue_months",
        "个人历史最长逾期月数",
        "PBCPC_PC02D_OVERDUESUM.PC02DS04",
        "各业务类型取最大；缺失不当0；不同于企业当前逾期",
        "number",
        "月",
    )
    out.field(
        "loan_detail_status",
        "借贷明细覆盖",
        f"{ACCOUNT} / {PERSON_ACCOUNT}",
        "只说明有无提供，不能推断完整性",
    )
    out.field("quality_notes", "汇总注意事项", "按本行缺失、币种和财务冲突生成")
    out.field(
        "source_refs",
        "来源记录定位",
        "来源明细/征信明细.xlsx",
        "格式为Sheet名:source_row；覆盖报告的全部原始记录，非Excel行号",
    )

    for personal, header in [(False, HEADER), (True, PERSON)]:
        report_key = "PA01AI01" if personal else "EA01AI01"
        seen = set()
        for h in source.rows(header):
            report = h[report_key]
            if report in seen:
                raise ValueError("Duplicate report header")
            seen.add(report)
            identity = h if personal else source.one(IDENTITY, report_key, report)
            row = dict(
                subject_type="个人" if personal else "企业及其他组织",
                report_id=report,
                report_time=h["PA01AR01" if personal else "EA01AR01"],
                dt=h["dt"],
                subject_name=identity.get("PA01BQ01" if personal else "cust_nm"),
                id_type=identity.get("PA01BD01" if personal else "EA01CD01"),
                id_number=identity.get("PA01BI01" if personal else "EA01CI01"),
            )
            row["cust_ind"] = (
                customers.get(row["id_number"]) if not personal and row["id_type"] == "10" else None
            )
            notes = []
            if personal:
                accounts = latest_accounts(
                    source.rows(PERSON_ACCOUNT, report_key, report), "PD01AI01", "PD01BR03"
                )
                row["loan_balance_cny"] = cny_total(accounts, "PD01BJ01", "PD01AD04")
                currencies = [r.get("PD01AD04") for r in accounts]
                repayments = [r.get("PD01BR02") for r in accounts]
                query = source.one(QUERY, report_key, report)
                row.update(
                    queries_loan_1m=query.get("PC05BS03"),
                    last_query_date=query.get("PC05AR01"),
                    financial_status="个人不适用企业财务口径",
                )
                overdue = source.rows("PBCPC_PC02D_OVERDUESUM", report_key, report)
                months = [r.get("PC02DS04") for r in overdue]
                row["historical_overdue_months"] = (
                    max(months) if months and None not in months else None
                )
            else:
                profile = source.one(PROFILE, report_key, report)
                row.update({key: profile.get(field) for key, _, field in profile_fields})
                limit = source.one(LIMIT, report_key, report)
                row.update(
                    {
                        key: float(decimal(limit[field])) if limit.get(field) is not None else None
                        for key, _, field in limits
                    }
                )
                credit = source.one(CREDIT, report_key, report)
                row.update(
                    reported_loan_balance=credit.get("EB01AJ01"),
                    reported_guarantee_balance=credit.get("EB01AJ05"),
                )
                accounts = latest_accounts(
                    source.rows(ACCOUNT, report_key, report), "ED01AI01", "ED01AR04"
                )
                repays = latest_accounts(
                    source.rows(REPAYMENT, report_key, report), "ED01AI01", "ED01BR01"
                )
                by_account = {r["ED01AI01"]: r for r in repays}
                if set(by_account) != {a["ED01AI01"] for a in accounts}:
                    raise ValueError("Loan/repayment account keys differ")
                joined = [
                    dict(**a, **{k: v for k, v in by_account[a["ED01AI01"]].items() if k not in a})
                    for a in accounts
                ]
                row["open_loan_count"] = known_count(
                    accounts,
                    lambda r: r["ED01AD01"] == "1" if r["ED01AD01"] in ("1", "2") else None,
                )
                row["closed_loan_count"] = known_count(
                    accounts,
                    lambda r: r["ED01AD01"] == "2" if r["ED01AD01"] in ("1", "2") else None,
                )
                for target, field in [
                    ("loan_balance_cny", "ED01BJ01"),
                    ("current_overdue_cny", "ED01BJ04"),
                    ("current_overdue_principal_cny", "ED01BJ05"),
                ]:
                    row[target] = cny_total(joined, field, "ED01AD07")
                row["current_overdue_count"] = known_count(
                    repays,
                    lambda r: decimal(r["ED01BJ04"]) > 0 if r["ED01BJ04"] is not None else None,
                )
                months = [r["ED01BS02"] for r in repays]
                row["max_current_overdue_months"] = (
                    max(months) if months and None not in months else None
                )
                guarantees = latest_accounts(
                    source.rows(GUARANTEE, report_key, report), "ED04AI01", "ED04BR01"
                )
                row.update(
                    guarantee_account_count=count(guarantees),
                    guarantee_balance_cny=cny_total(guarantees, "ED04BJ01", "ED04AD04"),
                )
                currencies = [r.get("ED01AD07") for r in accounts] + [
                    r.get("ED04AD04") for r in guarantees
                ]
                repayments = [r.get("ED01BR04") for r in repays]
                row.update(financials(source, report))
                if row.get("financial_conflicts"):
                    notes.append("财务版本存在冲突，见financial_conflicts及来源明细")
            row["loan_account_count"] = count(accounts)
            row["unknown_currency_count"] = (
                sum(c in (None, "") for c in currencies) if currencies else None
            )
            row["latest_repayment_date"] = max(
                (d for d in repayments if d is not None), default=None
            )
            row["loan_detail_status"] = (
                "已提供账户记录，未证明全量" if accounts else "未提供账户记录"
            )
            for key, _, enterprise, person in public:
                row[key] = count(
                    source.rows(person if personal else enterprise, report_key, report)
                )
            if row["unknown_currency_count"]:
                notes.append("有未知币种，相应人民币汇总留空")
            if row["cust_ind"] is None:
                notes.append("未关联客户标签，保留独立主体")
            row["quality_notes"] = "；".join(notes) or None
            row["source_refs"] = source.lineage(report_key, report)
            out.records.append(row)
    return out
