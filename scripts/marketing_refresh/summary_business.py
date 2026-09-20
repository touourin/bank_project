"""Company snapshot summary; personal roles and one-to-many records stay distinct."""

from .summary_common import Summary, cny_total, count, decimal, known_count

BASIC = "T_SAIC_BASIC"
SHARE = "T_SAIC_SHAREHOLDER"
INVEST = "T_SAIC_ENTINV"
DIRECT = [
    ("unify_credit_code", "统一社会信用代码", "text", ""),
    ("cust_nm", "企业名称", "text", ""),
    ("dt", "数据日期", "text", ""),
    ("REGNO", "工商注册号", "text", ""),
    ("ORGCODES", "组织机构代码", "text", ""),
    ("legal_rep_nm", "法定代表人", "text", ""),
    ("survival_status", "企业存续状态", "text", ""),
    ("found_dt", "成立日期", "text", ""),
    ("cert_capt_amt", "注册资本", "number", "元，币种见cert_capt_ccy"),
    ("org_capt_amt", "实收资本", "number", "元，币种见cert_capt_ccy"),
    ("cert_capt_ccy", "资本币种", "text", ""),
    ("INDUSTRYCOCODE", "行业代码", "text", ""),
    ("INDUSTRYCONAME", "行业名称", "text", ""),
    ("ENTTYPE", "企业类型名称", "text", ""),
    ("ENTTYPECODE", "企业类型代码", "text", ""),
    ("REGORGPROVINCE", "登记省份", "text", ""),
    ("REGORGCITY", "登记城市", "text", ""),
    ("REGORGDISTRICT", "登记区县", "text", ""),
    ("DOM", "注册地址", "text", ""),
    ("opscope", "原始经营范围", "text", ""),
    ("mec_num", "从业人数", "number", "人"),
    ("APPRDATE", "核准日期", "text", ""),
    ("CANDATE", "注销日期", "text", ""),
    ("REVDATE", "吊销日期", "text", ""),
    ("TEL", "联系电话", "text", ""),
]
COUNTS = [
    ("branch_count", "分支机构记录数", "T_SAIC_FILIATION"),
    ("manager_count", "主要人员记录数", "T_SAIC_PERSON"),
    ("trademark_count", "商标记录数", "T_SAIC_TRADEMARK"),
    ("change_count", "工商变更记录数", "T_SAIC_ALTER"),
    ("exception_count", "经营异常记录数", "T_SAIC_EXCEPTIONLIST"),
    ("severe_count", "严重违法记录数", "T_SAIC_BREAKLAW"),
    ("execution_count", "企业被执行记录数", "T_SAIC_PUNISHED"),
    ("dishonest_count", "企业失信被执行记录数", "T_SAIC_PUNISHBREAK"),
    ("penalty_count", "企业行政处罚记录数", "T_SAIC_ENTCASEBASEINFO"),
    ("equity_pledge_count", "股权出质登记记录数", "T_SAIC_STOCKPAWN"),
    ("mortgage_count", "动产抵押登记记录数", "T_SAIC_MORTGAGEREG"),
]


def compact_date(value):
    return str(value).replace("-", "").replace("/", "")[:8] if value is not None else None


def still_listed(row, snapshot):
    entered, removed = compact_date(row.get("INDATE")), compact_date(row.get("OUTDATE"))
    return None if not entered else entered <= snapshot and (not removed or removed > snapshot)


def build(source, customers):
    out = Summary(
        "工商",
        "一行 = 一家企业一个数据日期；统一社会信用代码 + dt 唯一",
        "来源明细/工商明细.xlsx",
        source,
    )
    out.field(
        "cust_ind",
        "客户编号",
        "客户标签.unify_credit_code = T_SAIC_BASIC.unify_credit_code",
        "仅精确匹配；未匹配主体保留，客户编号留空",
    )
    for key, name, kind, unit in DIRECT:
        rule = "直接读取原值；缺失保留空白"
        if key in ("cert_capt_amt", "org_capt_amt"):
            rule = "当前来源字段已规范为元，直接读取，不再乘10000"
        out.field(key, name, f"{BASIC}.{key}", rule, kind, unit)
    for key, name, origin, rule, unit in [
        (
            "shareholder_count",
            "股东记录数",
            f"{SHARE}.PALGORITHMID,SHANAME",
            "按企业及股东去重；不累加每行重复的INVAMOUNT",
            "个",
        ),
        (
            "largest_share_ratio",
            "最大股东出资比例",
            f"{SHARE}.FUNDEDRATIO",
            "股东比例取最大；任何比例缺失则留空；0至1为比例",
            "比例",
        ),
        (
            "share_subscribed_cny",
            "股东人民币认缴合计",
            f"{SHARE}.SUBCONAM,REGCAPCUR",
            "逐股东SUBCONAM合计；万元乘10000为元；不累加重复的SUMCONAM；未知币种或金额留空",
            "元",
        ),
        (
            "share_unknown_currency_count",
            "股东出资未知币种数",
            f"{SHARE}.REGCAPCUR",
            "有股东记录才计数；无记录留空",
            "条",
        ),
        (
            "investee_count",
            "对外投资企业数",
            f"{INVEST}.RELATECREDITCODE",
            "按被投资企业信用代码去重；不把被投企业REGCAP算作本企业注册资本",
            "家",
        ),
        (
            "investment_subscribed_cny",
            "对外投资人民币认缴合计",
            f"{INVEST}.SUBCONAM,CONGROCUR",
            "逐被投企业出资合计，万元乘10000为元；未知币种或金额留空",
            "元",
        ),
    ]:
        out.field(
            key, name, origin, rule, "ratio" if key == "largest_share_ratio" else "number", unit
        )
    for key, name, table in COUNTS:
        out.field(
            key,
            name,
            table,
            "仅该企业、该快照已提供的记录数；未提供留空；不混入VW_GSGR个人类记录",
            "number",
            "条",
        )
    out.field(
        "active_exception_count",
        "尚未移出的经营异常记录数",
        "T_SAIC_EXCEPTIONLIST.INDATE,OUTDATE",
        "列入日期<=快照，且移出日期为空或晚于快照；缺失列入日期则汇总留空",
        "number",
        "条",
    )
    out.field(
        "active_severe_count",
        "尚未移出的严重违法记录数",
        "T_SAIC_BREAKLAW.INDATE,OUTDATE",
        "同经营异常日期规则；未提供留空，不默认为0",
        "number",
        "条",
    )
    out.field(
        "active_dishonest_count",
        "执行中的企业失信记录数",
        "T_SAIC_PUNISHBREAK.CASESTATE",
        "仅明确执行中计入；执行完毕不计入；未知状态留空",
        "number",
        "条",
    )
    out.field(
        "detail_coverage",
        "明细覆盖说明",
        "工商各来源表",
        "列出已提供的统计明细类别；未列出不表示不存在",
    )
    out.field("quality_notes", "汇总注意事项", "主体关联、币种及覆盖校验")
    out.field(
        "source_refs",
        "来源记录定位",
        "来源明细/工商明细.xlsx",
        "格式为Sheet名:source_row；包括未放入汇总列的原明细，不是Excel行号",
    )

    seen = set()
    for base in source.rows(BASIC):
        code, snapshot = base["unify_credit_code"], str(base["dt"])
        if not code or (code, snapshot) in seen:
            raise ValueError("Missing or duplicate company key")
        seen.add((code, snapshot))
        row = {key: base.get(key) for key, _, _, _ in DIRECT}
        row["cust_ind"] = customers.get(code)

        def group(table, code=code, snapshot=snapshot):
            return [
                r for r in source.rows(table, "unify_credit_code", code) if str(r["dt"]) == snapshot
            ]

        shares, investments = group(SHARE), group(INVEST)
        share_ids = [(r["PALGORITHMID"], r["SHANAME"]) for r in shares]
        if len(set(share_ids)) != len(shares):
            raise ValueError("Repeated shareholder; cannot safely sum")
        investee_ids = [r["RELATECREDITCODE"] for r in investments]
        if None in investee_ids or len(set(investee_ids)) != len(investments):
            raise ValueError("Missing or repeated investee; cannot safely sum")
        ratios = [decimal(r.get("FUNDEDRATIO")) for r in shares]
        row.update(
            shareholder_count=count(shares),
            largest_share_ratio=float(max(ratios)) if ratios and None not in ratios else None,
            share_subscribed_cny=cny_total(shares, "SUBCONAM", "REGCAPCUR", 10000),
            share_unknown_currency_count=sum(r.get("REGCAPCUR") in (None, "") for r in shares)
            if shares
            else None,
            investee_count=count(investments),
            investment_subscribed_cny=cny_total(investments, "SUBCONAM", "CONGROCUR", 10000),
        )
        for key, _, table in COUNTS:
            row[key] = count(group(table))
        row["active_exception_count"] = known_count(
            group("T_SAIC_EXCEPTIONLIST"), lambda r, snapshot=snapshot: still_listed(r, snapshot)
        )
        row["active_severe_count"] = known_count(
            group("T_SAIC_BREAKLAW"), lambda r, snapshot=snapshot: still_listed(r, snapshot)
        )
        row["active_dishonest_count"] = known_count(
            group("T_SAIC_PUNISHBREAK"),
            lambda r: (
                r["CASESTATE"] == "执行中" if r["CASESTATE"] in ("执行中", "执行完毕") else None
            ),
        )
        row["detail_coverage"] = (
            "；".join(name for key, name, _ in COUNTS if row[key] is not None) or "未提供上述明细"
        )
        notes = []
        if row["cust_ind"] is None:
            notes.append("未关联客户标签，保留独立企业")
        if row["share_unknown_currency_count"] or any(
            r.get("CONGROCUR") in (None, "") for r in investments
        ):
            notes.append("出资币种不明，相应人民币合计留空")
        row["quality_notes"] = "；".join(notes) or None
        row["source_refs"] = source.lineage("unify_credit_code", code)
        out.records.append(row)
    return out
