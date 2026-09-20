"""Prepare coherent synthetic records without writing Excel or touching MySQL.

The desktop workbooks are the input contract. Their hashes must match the
audited source package before its lossless CSV representation can be reused.
Risk truth is kept outside the five input datasets.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from source_mock.usable import reconcile_credit

from .common import (
    ASOF,
    DESKTOP,
    OUTPUT,
    RISK_NAMES,
    ROOT,
    SEED,
    SOURCE,
    START,
    TABLES,
    RowsWriter,
    amount,
    compact,
    day,
    dec,
    dump,
    load,
    money,
    read_csv,
    rng,
    stable,
)

EXPECTED_HASHES = {
    "客户标签": "b873153e2afa6ea1443abc214dfc16e32befab8bfb7ae3f0bcabdc01b685e6ef",
    "客户旅程": "17ccf78c23eb919e91d268beb86c359b7cb5cda9b8a240fe5d65180b24766f3c",
    "征信": "01478328c1fe01fdc150f2cabc3083138e1fdc1a5601ea21ad4dcc04bf5edb05",
    "工商": "a8fe124011c184bdec7a0cc04265422bc07a53286a77bbfb50634fe1225b7741",
    "交易流水": "eb007f7397e84a72bd41623607987120d11880718a1547b92e663527e5ad49af",
}
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def source_contract():
    result = {}
    for label, table in TABLES.items():
        p = DESKTOP / (label + ".xlsx")
        with p.open("rb") as f:
            digest = hashlib.file_digest(f, "sha256").hexdigest()
        if digest != EXPECTED_HASHES[label]:
            raise ValueError(f"Desktop input changed: {p}")
        with ZipFile(p) as z, z.open("xl/worksheets/sheet1.xml") as f:
            for _, element in ET.iterparse(f, events=("end",)):
                if element.tag == NS + "row":
                    columns = [
                        "".join(t.text or "" for t in c.iter(NS + "t")) or c.findtext(NS + "v", "")
                        for c in element
                    ]
                    break
        result[label] = {"table": table, "path": str(p), "sha256": digest, "columns": columns}
    return result


def clean(row):
    return {k: str(v).replace("MOCK_", "") for k, v in row.items()}


def distribute(total, weights):
    """Allocate integer cents, conserving the batch total exactly."""
    cents = int(money(total) * 100)
    if not weights or cents < 0:
        raise ValueError("Invalid allocation")
    scale = sum(weights)
    raw = [cents * w / scale for w in weights]
    vals = [int(v) for v in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - vals[i], reverse=True)[
        : cents - sum(vals)
    ]:
        vals[i] += 1
    return [Decimal(v) / 100 for v in vals]


def select_scenarios(tags, data, events):
    selected, used = {}, set()

    def choose(kind, eligible):
        ids = sorted(set(eligible) - used, key=lambda x: stable(kind, x))[:8]
        if len(ids) != 8:
            raise ValueError(f"Not enough eligible subjects for {kind}")
        selected[kind] = ids
        used.update(ids)

    choose("legal_execution", [cid for cid in tags if int(cid.rsplit("_", 1)[1]) <= 100])
    candidates = []
    for r in data["PBCEC_ED01B_REPAYMENT"]:
        cid = "MOCK_CUST_" + r["EA01AI01"].zfill(4)
        if (
            cid in tags
            and r["ED01AI01"].endswith("01")
            and dec(r["ED01BJ01"]) > 0
            and day(r["ED01BR04"]) <= ASOF - timedelta(days=20)
        ):
            candidates.append(cid)
    choose("credit_overdue", candidates)
    old = [cid for cid, t in tags.items() if day(t["open_acct_dt"]) <= START]
    choose("cashflow_decline", [cid for cid in old if int(tags[cid]["PYRL_MON_NUM_12M"]) >= 8])
    choose("counterparty_concentration", old)
    choose(
        "activity_decline",
        [
            cid
            for cid in old
            if dec(tags[cid]["our_bank_credit_bal"]) == 0
            and all(
                day(e["OCCUR_DT"]) < ASOF - timedelta(days=100)
                for e in events[cid]
                if e["EVT_TYPE"] in {"YWJC0009-1", "YWJC0005", "YWJC0006-1", "YWJC0006-2"}
            )
        ],
    )
    controls = sorted(set(old) - used, key=lambda x: stable("control", x))[:20]
    return selected, controls


def normalize_credit(data, schema, tags, selected, owners, people):
    overdue, legal = set(selected["credit_overdue"]), set(selected["legal_execution"])
    # Preserve nulls in unrelated fields. Existing current-risk facts are made
    # explicit and rare rather than keeping one identical risk per coverage row.
    for t in schema["tables"]:
        if t["domain"] != "credit":
            continue
        name = t["sheet"]
        for n, row in enumerate(data[name], 1):
            cid = owners[(name, n)]
            for f in t["columns"]:
                k, desc = f["name"], f["source_description"]
                if row.get(k) and any(
                    word in desc
                    for word in (
                        "当前逾期",
                        "逾期总额",
                        "逾期本金",
                        "逾期月数",
                        "逾期期数",
                        "逾期31",
                        "逾期61",
                        "逾期91",
                        "逾期180",
                        "欠税总额",
                        "欠税金额",
                        "欠费金额",
                        "欠息余额",
                    )
                ):
                    row[k] = "0.00" if "J" in k else "0"
                if row.get(k) and desc in {"五级分类", "资产质量分类"}:
                    row[k] = "1"
                if row.get(k) and desc in {"还款状态", "债权转移时的还款状态"}:
                    row[k] = "N"
                if row.get(k) and desc in {"当前缴费状态", "缴费状态"}:
                    row[k] = "1"
            if name == "PBCEC_ED01B_REPAYMENT":
                row.update(ED01BJ04="0.00", ED01BJ05="0.00", ED01BS02="0", ED01BD01="1")
                if cid in overdue and row["ED01AI01"].endswith("01"):
                    value = money(
                        dec(row["ED01BJ01"]) * dec(rng(cid, "arrears").uniform(0.018, 0.047))
                    )
                    due = max(day(row["ED01BR04"]) + timedelta(days=10), ASOF - timedelta(days=10))
                    row.update(
                        ED01BJ04=amount(value),
                        ED01BJ05=amount(value),
                        ED01BS02="1",
                        ED01BD01="2",
                        ED01BR05=due.isoformat(),
                        ED01BJ03=amount(value),
                        ED01BR03=ASOF.isoformat(),
                    )
            # Ensure personal current-period paid amounts reconcile after the
            # removal of unrelated coverage-only overdue examples.
            if name == "PBCPC_PD01ABC_PERFORMANC":
                row["PD01CJ05"] = row.get("PD01CJ04", "0.00")
                row["PD01BD04"] = "N"
            if name == "PBCPC_PC03_TELPAYMENTSUM" and dec(row.get("PC030J01")) == 0:
                row["PC030S02"] = "0"
            if name in {"PBCEC_EF03_FORCEEXECUTION", "PBCPC_PF03AZ_FORCEEXECUTION"}:
                p = "EF030" if name.startswith("PBCEC") else "PF03A"
                target = dec(row.get(p + "J01"))
                paid = money(target * dec(".35")) if cid in legal else target
                row[p + "J02"] = amount(paid)
                row[p + "Q05"] = f"已执行人民币{amount(paid)}元"
                row[p + "Q04"] = "执行中" if cid in legal else "执行完毕"
                row[p + "D01"] = "0" if cid in legal else "3"
                if p + "R02" in row:
                    row[p + "R02"] = (
                        ""
                        if cid in legal
                        else (ASOF - timedelta(days=45 + stable(cid) % 120)).isoformat()
                    )
                    if row.get(p + "R01") and row[p + "R02"] and row[p + "R02"] < row[p + "R01"]:
                        row[p + "R02"] = (day(row[p + "R01"]) + timedelta(days=20)).isoformat()
    for cid, t in tags.items():
        t.update(
            risk_cust_ind=str(int(cid in overdue or cid in legal)),
            loan_overdue_cust_ind=str(int(cid in overdue)),
            badloan_cust_ind="0",
            abnormity_cust_ind=str(int(cid in legal)),
            aml_high_lvl_cust_ind="0",
            wire_fraud_risk_ind="0",
        )
    reconcile_credit(data, owners, tags, people)


def normalize_business(data, selected, owners):
    legal = set(selected["legal_execution"])
    for name, records in data.items():
        if not (
            name.startswith("T_SAIC")
            or name.startswith("VW_GSGR")
            or name == "ENT_RISK_WARNING_SIGNAL"
        ):
            continue
        for n, r in enumerate(records, 1):
            cid = owners[(name, n)]
            active = cid in legal
            resolved = (ASOF - timedelta(days=30 + stable(cid, "resolved") % 80)).isoformat()
            if "PUNISHBREAK" in name:
                total = dec(r.get("PERFORMEDPART")) + dec(r.get("UNPERFORMPART"))
                paid = money(total * dec(".35")) if active else total
                for k, v in {
                    "PERFORMEDPART": amount(paid),
                    "UNPERFORMPART": amount(total - paid),
                    "PERFORMANCE": "部分履行" if active else "全部履行",
                    "CASESTATE": "执行中" if active else "执行完毕",
                    "EXITDATE": "" if active else resolved,
                }.items():
                    if k in r:
                        r[k] = v
            if name in {"T_SAIC_PUNISHED", "VW_GSGR_PUNISHED", "VW_GSGR_PERSONCASEINFO"}:
                for k in ("CASESTATE", "CASERESULT"):
                    if k in r:
                        r[k] = "执行中" if active else "已结案"
            if name in {"T_SAIC_EXCEPTIONLIST", "T_SAIC_BREAKLAW"}:
                if "OUTDATE" in r:
                    r["OUTDATE"] = "" if active else resolved
                if "OUTREASON" in r:
                    r["OUTREASON"] = "" if active else "已完成整改并公示，依法移出"
            if "SHARESFROST" in name or "JUDICIALAID" in name:
                if "THAWDATE" in r:
                    r["THAWDATE"] = "" if active else resolved
                if "FREEZE_FLAG" in r:
                    r["FREEZE_FLAG"] = "冻结" if active else "解除冻结"
                if "FREEZE_DATE" in r:
                    r["FREEZE_DATE"] = "" if active else resolved
            if name == "ENT_RISK_WARNING_SIGNAL":
                r["SIGNAL_NOTES"] = (
                    "司法执行未结，经营异常名录尚未移出"
                    if active
                    else "历史经营异常已完成整改并移出"
                )


def random_day(r, left, right):
    d = left + timedelta(days=r.randrange((right - left).days + 1))
    if d.weekday() >= 5:
        d = max(left, d - timedelta(days=d.weekday() - 4))
    return d


def clock(r):
    return r.randrange(8 * 3600 + 30 * 60, 18 * 3600 + 30 * 60)


def counterparty(cid, i, incoming):
    number = stable(cid, "buyer" if incoming else "supplier", i)
    account = "62" + f"{number % 10**18:018d}"
    city = ("上海", "苏州", "杭州", "宁波", "无锡")[number % 5]
    a = ("恒", "嘉", "启", "瑞", "远", "泽", "裕", "诚")[number // 5 % 8]
    b = ("达", "成", "和", "晟", "安", "隆", "泰", "信")[number // 40 % 8]
    sector = ("供应链", "机电", "商贸", "实业", "新材料", "信息技术")[number // 320 % 6]
    return account, city + a + b + sector + "有限公司"


def make_flows(cid, tag, events, scenario):
    r = rng(cid, "flows")
    opened = day(tag["open_acct_dt"])
    left = max(START, opened)
    flows, term_accounts = [], []

    def add(d, signed, kind, cp=None, seconds=None, token="", reversal=""):
        if left <= d <= ASOF:
            flows.append(
                {
                    "day": d,
                    "signed": money(signed),
                    "kind": kind,
                    "cp": cp,
                    "seconds": clock(r) if seconds is None else seconds,
                    "token": token,
                    "reversal": reversal,
                }
            )

    payroll = defaultdict(Decimal)
    for e in events:
        if e["EVT_TYPE"] == "YWJC0012":
            payroll[e["OCCUR_DT"][:6]] += dec(json.loads(e["PROPERTIES"])["代发金额"])
    average_pay = sum(payroll.values(), Decimal(0)) / max(len(payroll), 1)
    monthly_base = max(
        average_pay * dec(r.uniform(2.5, 4.5)),
        dec(tag["cert_capt_amt"]) * dec(r.uniform(0.018, 0.065)),
        dec(r.uniform(45000, 130000)),
    )
    ym = left.year * 12 + left.month - 1
    end_ym = ASOF.year * 12 + ASOF.month - 1
    for index in range(ym, end_ym + 1):
        year, m0 = divmod(index, 12)
        first = max(left, date(year, m0 + 1, 1))
        last = min(ASOF, date(year, m0 + 1, calendar.monthrange(year, m0 + 1)[1]))
        fraction = dec((last - first).days + 1) / calendar.monthrange(year, m0 + 1)[1]
        season = (1.02, 0.82, 1.03, 1.04, 1.0, 1.06, 1.0, 0.98, 1.08, 1.02, 1.12, 1.14)[m0]
        base = money(monthly_base * dec(season * r.uniform(0.91, 1.09)) * fraction)
        revenue = base
        if scenario == "cashflow_decline" and first >= date(2026, 6, 1):
            revenue = money(
                base * dec(0.54 if first.month == 6 else 0.36 if first.month == 7 else 0.29)
            )
        if scenario == "activity_decline" and first >= date(2026, 6, 1):
            continue
        count = max(2, round(r.randint(10, 18) * float(fraction)))
        receipts = distribute(revenue, [r.lognormvariate(0, 0.5) for _ in range(count)])
        concentrated = scenario == "counterparty_concentration" and first >= date(2026, 6, 1)
        for i, value in enumerate(receipts):
            k = 0 if concentrated and i < count - 1 else (i + stable(cid, index) % 8) % 8
            add(random_day(r, first, last), value, "SETTLE_IN", counterparty(cid, k, True))
        # Operating expenses are distinct from payroll and repayments. The
        # decline cohort keeps its cost base while sales receipts deteriorate.
        wages = payroll.get(f"{year:04d}{m0 + 1:02d}", Decimal(0))
        expense = max(Decimal(0), money(base * dec(r.uniform(0.88, 0.95)) - wages))
        n = max(2, round(r.randint(7, 13) * float(fraction)))
        for i, value in enumerate(
            distribute(expense, [r.lognormvariate(0, 0.6) for _ in range(n)])
        ):
            if value:
                add(
                    random_day(r, first, last),
                    -value,
                    "SETTLE_OUT",
                    counterparty(cid, i % 10, False),
                )

    for e in events:
        kind, d = e["EVT_TYPE"], day(e["OCCUR_DT"])
        p = json.loads(e["PROPERTIES"])
        if kind == "YWJC0012":
            n, total = int(p["代发人数"]), dec(p["代发金额"])
            weights = [
                rng(cid, "salary", k).lognormvariate(0, 0.37)
                * rng(cid, compact(d), k).uniform(0.93, 1.08)
                for k in range(n)
            ]
            start_seconds = 9 * 3600 + stable(cid, compact(d), "batch") % (6 * 3600)
            for k, value in enumerate(distribute(total, weights)):
                account = "62" + f"{stable(cid, 'employee', k) % 10**18:018d}"
                person = (
                    "赵钱孙李周吴郑王陈杨黄徐"[stable(account) % 12]
                    + (
                        "明远",
                        "思宁",
                        "文昕",
                        "瑞安",
                        "嘉涵",
                        "宇泽",
                        "雨桐",
                        "佳怡",
                        "子轩",
                        "梓涵",
                    )[stable(account) // 12 % 10]
                )
                add(d, -value, "PAYROLL", (account, person), start_seconds + k * 2)
        elif kind == "YWJC0005":
            add(d, dec(p["放款金额"]), "LOAN_DRAW")
        elif kind in {"YWJC0006-1", "YWJC0006-2"}:
            add(d, -dec(p["还款金额"]), "LOAN_REPAY")
        elif kind == "YWJC0009-1":
            balance = dec(p["存款金额"])
            account = (
                f"{2000000000 + int(cid.rsplit('_', 1)[1]) * 100 + len(term_accounts) + 1:020d}"
            )
            p["账户号"] = account
            e["PROPERTIES"] = json.dumps(p, ensure_ascii=False, separators=(",", ":"))
            term_accounts.append({"account": account, "day": d, "balance": balance})
            if d >= left:
                add(d, -balance, "TERM_TRANSFER", (account, tag["cust_nm"]))
                term_accounts[-1]["seconds"] = flows[-1]["seconds"] + 1
    # Reversals occur for a small fraction of customers, with varied amounts.
    if stable(cid, "reversal-rate") % 100 < 4 and scenario != "activity_decline":
        d = max(left, ASOF - timedelta(days=7 + stable(cid, "reversal-day") % 100))
        value = money(dec(r.uniform(80, 5600)))
        sec = clock(r)
        cp = counterparty(cid, 9, False)
        add(d, -value, "REV_ORIGINAL", cp, sec, token="r1")
        add(d, value, "REVERSAL", cp, sec + 80, reversal="r1")
    flows.sort(key=lambda f: (f["day"], f["seconds"], f["signed"] < 0))
    running = minimum = Decimal(0)
    for f in flows:
        running += f["signed"]
        minimum = min(minimum, running)
    cushion = money(
        monthly_base
        * dec(r.uniform(0.03, 0.07) if scenario == "cashflow_decline" else r.uniform(0.2, 1.1))
    )
    opening = max(-minimum, Decimal(0)) + cushion
    if opened >= START:
        flows.insert(
            0,
            {
                "day": opened,
                "signed": opening,
                "kind": "OPEN_DEPOSIT",
                "cp": None,
                "seconds": 8 * 3600,
                "token": "",
                "reversal": "",
            },
        )
        opening = Decimal(0)
    return flows, opening, term_accounts


def transaction_row(cid, t, identity, account, f, seq, balance, reverse_seq, product="DEMAND"):
    kind, d, value = f["kind"], f["day"], abs(f["signed"])
    incoming = f["signed"] >= 0
    labels = {
        "SETTLE_IN": ("TRF_IN", "TRANSFER", "销售货款收款"),
        "SETTLE_OUT": ("TRF_OUT", "TRANSFER", "采购货款支付"),
        "PAYROLL": ("PAYROLL", "PAYROLL", "代发工资"),
        "LOAN_DRAW": ("LN_DRAW", "LOAN", "贷款发放"),
        "LOAN_REPAY": ("LN_REPAY", "LOAN", "贷款还款"),
        "TERM_TRANSFER": ("TERM_OUT", "DEPOSIT", "转存单位定期"),
        "TERM_DEPOSIT": ("TERM_IN", "DEPOSIT", "单位定期存入"),
        "OPEN_DEPOSIT": ("OPEN_DEP", "DEPOSIT", "开户资金存入"),
        "REV_ORIGINAL": ("TRF_OUT", "TRANSFER", "采购货款支付"),
        "REVERSAL": ("REVERSAL", "CORRECT", "原交易冲正"),
    }
    code, category, label = labels[kind]
    channels = [
        ch
        for ch, field in (("WEB", "corp_ebnkg_sign_dt"), ("MOBILE", "mb_sign_dt"))
        if t[field] and day(t[field]) <= d
    ]
    channel = channels[stable(cid, seq) % len(channels)] if channels else "COUNTER"
    branch = t["cert_district"] + "001"
    cp = f["cp"]
    if cp is None:
        cp = (
            "9001" + f"{stable(cid, kind) % 10**16:016d}",
            "浦江银行贷款清算专户" if kind.startswith("LOAN") else t["legal_rep_nm"],
        )
    seconds = f["seconds"]
    time = f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
    serial = f"TX{compact(d)}{int(cid.rsplit('_', 1)[1]):04d}{product[:1]}{seq:06d}"
    row = dict(
        ev_ecd=kind,
        accno=account,
        acc_tpcd="01",
        acc_multi_medm_id="0",
        cst_acc=account,
        cst_acc_tpcd="01",
        acct_nm=t["cust_nm"],
        acc_dtl_sn=str(seq),
        acc_dtl_seq_no=str(seq),
        acc_dpbkinno=branch,
        cust_ind=cid,
        cust_nm=t["cust_nm"],
        txn_dt=d.isoformat(),
        txn_tm=time,
        txn_insid=branch,
        act_dt=d.isoformat(),
        valdt=d.isoformat(),
        trdpt_dt=d.isoformat(),
        inpt_tm=time,
        cshex_cd="1",
        cash_tfr_ind="T",
        ccycd="CNY",
        prod_cd=product,
        smy_cd=kind,
        txn_dsc=label,
        txn_smy_dsc=label,
        long_txn_smy=label + "，" + cp[1],
        txn_use="工资薪金"
        if kind == "PAYROLL"
        else "货款结算"
        if kind.startswith("SETTLE")
        else "资金划转",
        txn_pstcrpt=label,
        txn_rmrk="",
        txn_cgycd=category,
        bill_ctcd="EVOUCHER",
        bill_no=serial,
        txn_medm_id=account,
        txn_medm_tpcd="ACCOUNT",
        chnl_tpcd=channel,
        lcl_txn_tpcd=kind,
        fb_id=serial,
        trdpt_txn_cd=kind,
        txn_insid_nm="浦江银行" + t["cert_district"] + "支行",
        cntrprt_txn_py_brno="0002",
        cntrprt_txn_accno_nm=cp[1],
        cntrprt_txn_accno=cp[0],
        cntrprt_trdbrh_nm="城商银行结算中心",
        cntrprtbookentracnonm=cp[1],
        cntrprtbookentr_accno=cp[0],
        cntpr_dep_accno=cp[0],
        cntpr_cst_id="CP" + cp[0][-12:],
        cntpr_amt=amount(value),
        cntpr_ccy_cd="CNY",
        cntrprt_wthr_ccb_cst="1" if kind.startswith(("LOAN", "TERM")) else "0",
        txn_empid="E" + branch + "01" if channel == "COUNTER" else "",
        fst_ahn_empid="E" + branch + "02" if channel == "COUNTER" else "",
        snd_ahn_empid="E" + branch + "03" if channel == "COUNTER" and value >= 1000000 else "",
        dbtcrdrccd="C" if incoming else "D",
        txnamt=amount(value),
        dhamt=amount(0 if incoming else value),
        cr_hpnam=amount(value if incoming else 0),
        acct_bal=amount(balance),
        frncy_amt="0.00",
        cny_amt=amount(value),
        ovrlsttn_ev_trck_no=serial,
        sys_tx_type=category,
        sys_tx_code=code,
        txn_cardno="",
        mrch_cgy_cd="",
        mrch_id="",
        mrch_nm="",
        xbrdr_txn_ind="0",
        iwrd_out_indcd="I" if incoming else "O",
        rate="1.000000000000",
        exgsetl_stat_cd="NONE",
        invfrcty_icmepd_cd="",
        ip_adr=f"192.0.2.{stable(cid) % 250 + 1}" if channel != "COUNTER" else "",
        mac_adr="",
        tmnl_no="",
        txn_py_stlmd_cd="TRANSFER",
        agnc_psn_nm=t["legal_rep_nm"] if channel == "COUNTER" else "",
        agnc_psn_crdt_tpcd="01" if channel == "COUNTER" else "",
        agnc_psn_crdt_no=identity["person_certificate"] if channel == "COUNTER" else "",
        agnc_psn_ctc_tel="",
        agnc_psn_nat="CHN" if channel == "COUNTER" else "",
        dtljrnlentr_ntl_sbjid="2001",
        virt_acct_num=account,
        vrtl_wide_ind="0",
        txn_trgr_way="BATCH" if kind == "PAYROLL" else "MANUAL",
        if_ext_mrg_bookentr="0",
        inr_txn_cd=kind,
        rvrs_acc_dtl_sn=str(reverse_seq) if reverse_seq else "",
        rvrs_cd=str(int(kind == "REVERSAL")),
        be_rvrs_ind=str(int(kind == "REV_ORIGINAL")),
        wrngacc_ori_tlr_srl_no=f"TX{compact(d)}{int(cid.rsplit('_', 1)[1]):04d}{product[:1]}{reverse_seq:06d}"
        if reverse_seq
        else "",
        tlr_srl_no=serial,
        cnsmr_sys_ind="CORE",
        cnsmr_srl_no=serial,
        trdpt_srl_no=serial,
        comm_srl_no=serial,
        src_table="ACCOUNT_TXN",
        etl_job="LOAD_ACCOUNT_TXN",
        etl_first_dt=ASOF.isoformat(),
        etl_proc_dt=ASOF.isoformat() + " 23:59:00",
        dt=compact(ASOF),
        src_sys="CORE",
    )
    return clean(row)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    contract = source_contract()
    schema = load(SOURCE / "schema.json")
    load(SOURCE / "manifest.json")
    tags = {r["cust_ind"]: r for r in read_csv(SOURCE / "expected/customer_tags.csv")}
    identities = {r["cust_ind"]: r for r in read_csv(SOURCE / "reference/customer_identity.csv")}
    owners = {
        (r["table"], int(r["csv_row"]) - 1): r["cust_ind"]
        for r in read_csv(SOURCE / "reference/source_row_links.csv")
    }
    data = {
        t["sheet"]: read_csv(SOURCE / t["filename"])
        for t in schema["tables"]
        if t["domain"] != "transactions"
    }
    events = defaultdict(list)
    for e in read_csv(ROOT / "examples/mock/E_CRM_C_CUST_TOUR_EVT_SUM.csv"):
        events[e["CUST_ID"]].append(e)
    selected, controls = select_scenarios(tags, data, events)
    scenarios = {cid: kind for kind, ids in selected.items() for cid in ids}
    normalize_credit(data, schema, tags, selected, owners, identities)
    normalize_business(data, selected, owners)
    summaries, accounts = [], []
    out = {
        label: RowsWriter(OUTPUT / (table + ".jsonl.gz"), contract[label]["columns"])
        for label, table in TABLES.items()
    }
    for position, (cid, t) in enumerate(tags.items(), 1):
        scenario = scenarios.get(cid, "normal")
        if scenario == "activity_decline":
            events[cid] = [
                e
                for e in events[cid]
                if not (
                    day(e["OCCUR_DT"]) >= date(2026, 6, 1)
                    and e["EVT_TYPE"].startswith(("YWJC", "MOCK_"))
                    and e["EVT_TYPE"] not in {"YWJC0009-2"}
                )
            ]
        flows, opening, terms = make_flows(cid, t, events[cid], scenario)
        account = f"{int(cid.rsplit('_', 1)[1]):020d}"
        balance = opening
        changes, daily, tokens = defaultdict(Decimal), {}, {}
        payroll_total = payroll_count = settle_total = settle_count = 0
        employees, payroll_months, settle_months, latest = set(), set(), set(), ""
        payroll_current = Decimal(0)
        channel_flags = {
            "corp_ebnkg_trans_y_ind": "0",
            "corp_mb_trans_y_ind": "0",
            "CORP_EBNKG_M_IND": "0",
            "CORP_MB_M_IND": "0",
        }
        for seq, f in enumerate(flows, 1):
            balance += f["signed"]
            assert balance >= 0
            if f["token"]:
                tokens[f["token"]] = seq
            row = transaction_row(
                cid, t, identities[cid], account, f, seq, balance, tokens.get(f["reversal"], 0)
            )
            out["交易流水"].add(row)
            changes[f["day"]] += f["signed"]
            if f["kind"] == "PAYROLL":
                payroll_count += 1
                payroll_total += -f["signed"]
                employees.add(f["cp"][0])
                payroll_months.add(f["day"].strftime("%Y%m"))
                latest = max(latest, compact(f["day"]))
                if f["day"].strftime("%Y%m") == "202609":
                    payroll_current += -f["signed"]
            if f["kind"] in {"SETTLE_IN", "SETTLE_OUT"}:
                settle_total += abs(f["signed"])
                settle_count += 1
                settle_months.add(f["day"].strftime("%Y%m"))
            if f["day"].year == 2026 and f["kind"] not in {"REVERSAL", "REV_ORIGINAL"}:
                if row["chnl_tpcd"] == "WEB":
                    channel_flags["corp_ebnkg_trans_y_ind"] = "1"
                    if f["day"].month == 9:
                        channel_flags["CORP_EBNKG_M_IND"] = "1"
                if row["chnl_tpcd"] == "MOBILE":
                    channel_flags["corp_mb_trans_y_ind"] = "1"
                    if f["day"].month == 9:
                        channel_flags["CORP_MB_M_IND"] = "1"
        demand_final = balance
        accounts.append(
            dict(
                cust_ind=clean({"x": cid})["x"],
                account=account,
                opening=amount(opening),
                closing=amount(balance),
                product="DEMAND",
                opened=t["open_acct_dt"],
            )
        )
        running = opening
        for offset in range((ASOF - START).days + 1):
            d = START + timedelta(days=offset)
            running += changes[d]
            daily[d] = running
        term_daily = defaultdict(Decimal)
        for term in terms:
            if term["day"] >= START:
                f = dict(
                    day=term["day"],
                    signed=term["balance"],
                    kind="TERM_DEPOSIT",
                    cp=(account, t["cust_nm"]),
                    seconds=term["seconds"],
                    token="",
                    reversal="",
                )
                out["交易流水"].add(
                    transaction_row(
                        cid, t, identities[cid], term["account"], f, 1, term["balance"], 0, "TERM"
                    )
                )
            accounts.append(
                dict(
                    cust_ind=clean({"x": cid})["x"],
                    account=term["account"],
                    opening=amount(term["balance"] if term["day"] < START else 0),
                    closing=amount(term["balance"]),
                    product="TERM",
                    opened=compact(term["day"]),
                )
            )
            for d in daily:
                if d >= term["day"]:
                    term_daily[d] += term["balance"]
        total_final = demand_final + sum((v["balance"] for v in terms), Decimal(0))
        days = len(daily)
        ydays = (ASOF - date(2026, 1, 1)).days + 1
        t.update(
            agt_cnt_12m=str(payroll_count),
            agt_amt_12m=amount(payroll_total),
            agt_num_12m=str(len(employees)),
            agt_vld_ind_12m=str(int(payroll_count > 0)),
            PYRL_MON_NUM_12M=str(len(payroll_months)),
            PYRL_AMT_M=amount(payroll_current),
            NUM_PYRL_AMT_12M_AVG=amount(dec(payroll_total) / payroll_count if payroll_count else 0),
            LATEST_PYRL_DT=latest,
            manu_settle_amt=amount(settle_total),
            manu_settle_amt_avg_cnt=str(
                money(dec(settle_count) / 12).quantize(Decimal(1), rounding="ROUND_HALF_UP")
            ),
            MANU_SETTLE_AMT_MON_NUM=str(min(12, len(settle_months))),
            SETL_ACTV_ACCT_IND=str(int(len(settle_months) >= 6)),
            exchg_c_bal=amount(total_final),
            zero_dep_cust_ind=str(int(total_final == 0)),
            exchg_c_bal_yavg=amount(
                sum((v + term_daily[d] for d, v in daily.items() if d.year == 2026), Decimal(0))
                / ydays
            ),
            c_exchg_cny_12m_yaug=amount(
                sum((v + term_daily[d] for d, v in daily.items()), Decimal(0)) / days
            ),
            exchg_cny_dmd_12m_yaug=amount(sum(daily.values()) / days),
            sleep_cust_ind="0",
            forex_cust_y_ind="0",
            **channel_flags,
        )
        # Snapshot events must describe the new balance at the snapshot date.
        for e in events[cid]:
            if e["EVT_TYPE"] == "YWJC0009-2":
                p = json.loads(e["PROPERTIES"])
                p["存款金额"] = float(demand_final)
                e["PROPERTIES"] = json.dumps(p, ensure_ascii=False, separators=(",", ":"))
                e["OCCUR_DT"] = compact(ASOF)
            out["客户旅程"].add(clean(e))
        out["客户标签"].add(clean(t))
        summaries.append(
            dict(
                customer=clean({"x": cid})["x"],
                name=t["cust_nm"],
                closing_balance=amount(total_final),
                payroll_total=amount(payroll_total),
            )
        )
        if position % 100 == 0:
            print(
                f"Prepared {position}/1000 customers; {out['交易流水'].count:,} transactions",
                flush=True,
            )
    for t in schema["tables"]:
        if t["domain"] not in {"credit", "business"}:
            continue
        label = "征信" if t["domain"] == "credit" else "工商"
        headers = set(contract[label]["columns"])
        for n, row in enumerate(data[t["sheet"]], 1):
            merged = {"source_table": t["sheet"], "source_row": str(n)}
            for f in t["columns"]:
                k = f["name"]
                target = (
                    k if k in headers else k + "__" + re.match(r"[A-Za-z]+", f["type"])[0].lower()
                )
                if target not in headers:
                    raise ValueError((t["sheet"], k, target))
                merged[target] = row[k]
            out[label].add(clean(merged))
    result = {label: dict(table=TABLES[label], **writer.close()) for label, writer in out.items()}
    dump(OUTPUT / "sources.json", contract)
    dump(
        OUTPUT / "manifest.json",
        dict(synthetic=True, as_of=compact(ASOF), seed=SEED, customers=1000, tables=result),
    )
    dump(OUTPUT / "accounts.json", accounts)
    dump(
        OUTPUT / "risk_truth.json",
        dict(
            as_of=compact(ASOF),
            risk_customer_count=40,
            risk_customer_ratio=0.04,
            scenarios={
                kind: [clean({"x": cid})["x"] for cid in ids] for kind, ids in selected.items()
            },
            controls=[clean({"x": cid})["x"] for cid in controls],
            names=RISK_NAMES,
        ),
    )
    dump(OUTPUT / "customer_summary.json", summaries)
    print(
        json.dumps({label: v["rows"] for label, v in result.items()}, ensure_ascii=False),
        flush=True,
    )


if __name__ == "__main__":
    main()
