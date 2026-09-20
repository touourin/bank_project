"""Individual payroll entries and an exact, continuous multi-account ledger."""

from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .common import amount, compact, dec, stable


def month_day(as_of: date, offset: int, day: int = 15) -> date:
    year, index = divmod(as_of.year * 12 + as_of.month - 1 - offset, 12)
    return date(year, index + 1, min(day, monthrange(year, index + 1)[1]))


def window_start(as_of: date) -> date:
    previous = as_of.replace(
        year=as_of.year - 1, day=min(as_of.day, monthrange(as_of.year - 1, as_of.month)[1])
    )
    return previous + timedelta(days=1)


def flow(occurred, signed, kind, counterparty="", token="", reverse_of=""):
    return dict(
        date=occurred,
        signed=dec(signed),
        kind=kind,
        counterparty=counterparty,
        token=token,
        reverse_of=reverse_of,
    )


def customer_flows(s):
    flows = []
    start = window_start(s.as_of)
    for offset in range(12):
        occurred = month_day(s.as_of, offset)
        if not max(start, s.opened) <= occurred <= s.as_of:
            continue
        for k in range(1 + stable(s.seed, s.index, offset) % 3):
            value = Decimal(1000 + stable(s.seed, s.index, offset, k) % 90000)
            flows.append(
                flow(occurred, value, "SETTLE_IN", f"{8000000000 + s.index * 100 + k:020d}")
            )
            flows.append(
                flow(occurred, -value, "SETTLE_OUT", f"{8100000000 + s.index * 100 + k:020d}")
            )
    for e in s.events:
        occurred = date.fromisoformat(
            f"{e['OCCUR_DT'][:4]}-{e['OCCUR_DT'][4:6]}-{e['OCCUR_DT'][6:8]}"
        )
        if not start <= occurred <= s.as_of:
            continue
        p = e["properties"]
        if e["EVT_TYPE"] == "YWJC0012":
            total = dec(p["代发金额"])
            n = int(p["代发人数"])
            cents = int(total * 100)
            each, remainder = divmod(cents, n)
            # A documented shareholder funding inflow, excluded from active
            # settlement/payroll aggregates; each employee receives one entry.
            flows.append(flow(occurred, total, "FUNDING"))
            for employee in range(n):
                paid = Decimal(each + (employee < remainder)) / 100
                flows.append(
                    flow(
                        occurred,
                        -paid,
                        "PAYROLL",
                        f"{7000000000 + s.index * 10000 + employee:020d}",
                    )
                )
        elif e["EVT_TYPE"] == "YWJC0005":
            flows.append(flow(occurred, p["放款金额"], "LOAN_DRAW"))
        elif e["EVT_TYPE"] in {"YWJC0006-1", "YWJC0006-2"}:
            flows.append(flow(occurred, -dec(p["还款金额"]), "LOAN_REPAY"))
    # Linked reversal is one original plus one opposite entry. Neither is a
    # successful settlement when the expected tags are aggregated.
    flows.append(
        flow(s.as_of - timedelta(days=1), -Decimal("123.45"), "REV_ORIGINAL", token="original")
    )
    flows.append(
        flow(s.as_of - timedelta(days=1), Decimal("123.45"), "REVERSAL", reverse_of="original")
    )
    flows.sort(
        key=lambda f: (
            f["date"],
            0
            if f["signed"] > 0 and f["kind"] != "REVERSAL"
            else 1
            if f["kind"] != "REVERSAL"
            else 2,
        )
    )
    current = minimum = Decimal(0)
    for f in flows:
        current += f["signed"]
        minimum = min(minimum, current)
    demand = sum(
        (dec(e["properties"]["存款金额"]) for e in s.events if e["EVT_TYPE"] == "YWJC0009-2"),
        Decimal(0),
    )
    opening = max(-minimum, demand - current, Decimal(0))
    adjustment = demand - (opening + current)
    if adjustment:
        flows.append(flow(s.as_of, adjustment, "CASH_SWEEP"))
    if s.opened > start and opening:
        flows.insert(0, flow(s.opened, opening, "OPEN_DEPOSIT"))
        opening = Decimal(0)
    accounts = [
        dict(
            accno=s.account,
            ccy="CNY",
            product="DEMAND",
            opened=s.opened,
            opening=opening,
            final=demand,
            flows=flows,
        )
    ]
    for j, e in enumerate([e for e in s.events if e["EVT_TYPE"] == "YWJC0009-1"], 1):
        value = dec(e["properties"]["存款金额"])
        occurred = date(int(e["OCCUR_DT"][:4]), int(e["OCCUR_DT"][4:6]), int(e["OCCUR_DT"][6:8]))
        accounts.append(
            dict(
                accno=f"{2000000000 + s.index * 10 + j:020d}",
                ccy="CNY",
                product="TERM",
                opened=occurred,
                opening=Decimal(0),
                final=value,
                flows=[flow(occurred, value, "TERM_DEPOSIT")],
            )
        )
    if s.index % 10 == 1:
        accounts.append(
            dict(
                accno=f"{3000000000 + s.index:020d}",
                ccy="USD",
                product="FX",
                opened=s.opened,
                opening=Decimal(0),
                final=Decimal(0),
                flows=[flow(s.as_of, 100, "FX_IN"), flow(s.as_of, -100, "FX_OUT")],
            )
        )
    return accounts


def transaction_row(table, s, account, f, sequence, balance, reverse_sequence):
    ccy = account["ccy"]
    rate = Decimal("7.1") if ccy == "USD" else Decimal(1)
    value = abs(f["signed"])
    incoming = f["signed"] > 0
    event = f"MOCK_TXN_{s.index:04d}_{account['product']}_{sequence:06d}"
    occurred = f["date"].isoformat()
    # Sequence-derived times are monotonic within each account/day (under 86,400
    # entries per account/day in this fixture). They are not random timestamps.
    seconds = 9 * 3600 + sequence
    clock = f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
    counter = f["counterparty"] or f"{6000000000 + s.index:020d}"
    payload = dict(
        ev_ecd="MOCK_" + f["kind"],
        accno=account["accno"],
        acc_tpcd="01",
        acc_multi_medm_id="0",
        cst_acc=account["accno"],
        cst_acc_tpcd="01",
        acct_nm=s.tag["cust_nm"],
        acc_dtl_sn=str(sequence),
        acc_dtl_seq_no=str(sequence),
        acc_dpbkinno="MOCK_BRANCH",
        cust_ind=s.tag["cust_ind"],
        cust_nm=s.tag["cust_nm"],
        txn_dt=occurred,
        txn_tm=clock,
        txn_insid="MOCK_BRANCH",
        act_dt=occurred,
        valdt=occurred,
        trdpt_dt=occurred,
        inpt_tm=clock,
        cshex_cd="1",
        cash_tfr_ind="T",
        ccycd=ccy,
        prod_cd=account["product"],
        smy_cd="MOCK_" + f["kind"],
        txn_dsc="模拟" + f["kind"],
        txn_smy_dsc="模拟" + f["kind"],
        long_txn_smy="合成业务流水 " + f["kind"],
        txn_use="工资" if f["kind"] == "PAYROLL" else "模拟经营往来",
        txn_pstcrpt="合成数据",
        txn_rmrk="合成场景",
        txn_cgycd="PAY" if f["kind"] == "PAYROLL" else "M01",
        bill_ctcd="M01",
        bill_no=event,
        txn_medm_id=f"MOCK_MEDIUM_{s.index:04d}",
        txn_medm_tpcd="M01",
        chnl_tpcd="WEB" if sequence % 2 else "MOBILE",
        lcl_txn_tpcd="MOCK_" + f["kind"],
        fb_id=event,
        trdpt_txn_cd="MOCK_" + f["kind"],
        txn_insid_nm=s.branch,
        cntrprt_txn_py_brno="MOCK_BANK_OTHER",
        cntrprt_txn_accno_nm="模拟员工" if f["kind"] == "PAYROLL" else "模拟交易对手",
        cntrprt_txn_accno=counter,
        cntrprt_trdbrh_nm="模拟外部银行",
        cntrprtbookentracnonm="模拟对手记账户",
        cntrprtbookentr_accno=counter,
        cntpr_dep_accno=counter,
        cntpr_cst_id="MOCK_CP_" + counter[-12:],
        cntpr_amt=amount(value),
        cntpr_ccy_cd=ccy,
        cntrprt_wthr_ccb_cst="0",
        txn_empid="MOCK_EMP_01",
        fst_ahn_empid="MOCK_EMP_02",
        snd_ahn_empid="MOCK_EMP_03",
        dbtcrdrccd="C" if incoming else "D",
        txnamt=amount(value),
        dhamt=amount(0 if incoming else value),
        cr_hpnam=amount(value if incoming else 0),
        acct_bal=amount(balance),
        frncy_amt=amount(value if ccy != "CNY" else 0),
        cny_amt=amount(value * rate),
        ovrlsttn_ev_trck_no=event,
        sys_tx_type="MOCK_TRANSFER",
        sys_tx_code="M01",
        txn_cardno=f"{4000000000 + s.index:020d}",
        mrch_cgy_cd="M01",
        mrch_id=f"MOCK_MERCHANT_{s.index:04d}",
        mrch_nm="模拟收款商户",
        xbrdr_txn_ind="1" if ccy != "CNY" else "0",
        iwrd_out_indcd="I" if incoming else "O",
        rate=amount(rate, 12),
        exgsetl_stat_cd="M01",
        invfrcty_icmepd_cd="M01",
        ip_adr=f"192.0.2.{s.index % 250 + 1}",
        mac_adr=f"02:00:00:00:{s.index // 256:02x}:{s.index % 256:02x}",
        tmnl_no=f"MOCK_TERM_{s.index:04d}",
        txn_py_stlmd_cd="M01",
        agnc_psn_nm=s.person,
        agnc_psn_crdt_tpcd="01",
        agnc_psn_crdt_no=s.certificate,
        agnc_psn_ctc_tel=f"000{s.index:08d}",
        agnc_psn_nat="CHN",
        dtljrnlentr_ntl_sbjid="M001",
        virt_acct_num=account["accno"],
        vrtl_wide_ind="0",
        txn_trgr_way="M01",
        if_ext_mrg_bookentr="0",
        inr_txn_cd="MOCK_" + f["kind"],
        rvrs_acc_dtl_sn=str(reverse_sequence) if reverse_sequence else "",
        rvrs_cd="1" if f["kind"] == "REVERSAL" else "0",
        be_rvrs_ind="1" if f["kind"] == "REV_ORIGINAL" else "0",
        wrngacc_ori_tlr_srl_no=f"MOCK_TXN_{s.index:04d}_{account['product']}_{reverse_sequence:06d}"
        if reverse_sequence
        else "",
        tlr_srl_no=event,
        cnsmr_sys_ind="MOCK",
        cnsmr_srl_no=event,
        trdpt_srl_no=event,
        comm_srl_no=event,
        src_table="MOCK_ACCOUNT_TXN",
        etl_job="MOCK_SOURCE_GENERATOR",
        etl_first_dt=s.as_of.isoformat(),
        etl_proc_dt=s.as_of.isoformat() + " 23:59:00",
        dt=compact(s.as_of),
        src_sys="MOCK",
    )
    # Optional context is populated only for relevant cases; all fields have an
    # applicable nonempty scenario, including one agent/card transaction per client.
    if sequence % 4:
        for key in (
            "agnc_psn_nm",
            "agnc_psn_crdt_tpcd",
            "agnc_psn_crdt_no",
            "agnc_psn_ctc_tel",
            "agnc_psn_nat",
        ):
            payload[key] = ""
    if f["kind"] not in {"SETTLE_OUT", "SETTLE_IN"}:
        for key in ("mrch_cgy_cd", "mrch_id", "mrch_nm"):
            payload[key] = ""
    return {field["name"]: payload[field["name"]] for field in table["columns"]}


def generate_customer(table, s, sink, account_sink):
    start = window_start(s.as_of)
    days = (s.as_of - start).days + 1
    ytd_start = date(s.as_of.year, 1, 1)
    ytd_days = (s.as_of - ytd_start).days + 1
    daily = defaultdict(Decimal)
    demand_daily = defaultdict(Decimal)
    payroll_amount = Decimal(0)
    payroll_count = 0
    employees = set()
    payroll_months = set()
    settlement_months = set()
    latest = ""
    monthly_payroll = Decimal(0)
    settle_amount = Decimal(0)
    settle_count = 0
    final = Decimal(0)
    for account in customer_flows(s):
        balance = account["opening"]
        changes = defaultdict(Decimal)
        tokens = {}
        for seq, f in enumerate(account["flows"], 1):
            if f["token"]:
                tokens[f["token"]] = seq
            reverse = tokens.get(f["reverse_of"], 0)
            balance += f["signed"]
            if balance < 0:
                raise ValueError("negative account balance")
            sink.add(transaction_row(table, s, account, f, seq, balance, reverse))
            changes[f["date"]] += f["signed"]
            if f["kind"] == "PAYROLL":
                value = abs(f["signed"])
                payroll_amount += value
                payroll_count += 1
                employees.add(f["counterparty"])
                payroll_months.add(f["date"].strftime("%Y%m"))
                latest = max(latest, compact(f["date"]))
                if f["date"].month == s.as_of.month and f["date"].year == s.as_of.year:
                    monthly_payroll += value
            elif f["kind"] in {"SETTLE_IN", "SETTLE_OUT"}:
                settle_amount += abs(f["signed"])
                settle_count += 1
                settlement_months.add(f["date"].strftime("%Y%m"))
        if balance != account["final"]:
            raise ValueError("account closing mismatch")
        rate = Decimal("7.1") if account["ccy"] == "USD" else Decimal(1)
        final += balance * rate
        running = account["opening"]
        for offset in range(days):
            d = start + timedelta(days=offset)
            running += changes[d]
            daily[d] += running * rate
            if account["product"] == "DEMAND":
                demand_daily[d] += running * rate
        account_sink.add(
            dict(
                cust_ind=s.tag["cust_ind"],
                accno=account["accno"],
                ccycd=account["ccy"],
                prod_cd=account["product"],
                open_acct_dt=compact(account["opened"]),
                period_start=compact(start),
                dt=compact(s.as_of),
                opening_balance=amount(account["opening"]),
                closing_balance=amount(balance),
            )
        )
    metrics = dict(
        agt_cnt_12m=str(payroll_count),
        agt_amt_12m=amount(payroll_amount),
        agt_num_12m=str(len(employees)),
        agt_vld_ind_12m=str(int(payroll_count > 0)),
        PYRL_MON_NUM_12M=str(len(payroll_months)),
        PYRL_AMT_M=amount(monthly_payroll),
        NUM_PYRL_AMT_12M_AVG=amount(payroll_amount / payroll_count if payroll_count else 0),
        LATEST_PYRL_DT=latest,
        manu_settle_amt=amount(settle_amount),
        manu_settle_amt_avg_cnt=str(
            (Decimal(settle_count) / 12).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        ),
        MANU_SETTLE_AMT_MON_NUM=str(len(settlement_months)),
        SETL_ACTV_ACCT_IND=str(int(len(settlement_months) >= 6)),
        exchg_c_bal=amount(final),
        zero_dep_cust_ind=str(int(final == 0)),
        exchg_c_bal_yavg=amount(
            sum((v for d, v in daily.items() if d >= ytd_start), Decimal(0)) / ytd_days
        ),
        c_exchg_cny_12m_yaug=amount(sum(daily.values()) / days),
        exchg_cny_dmd_12m_yaug=amount(sum(demand_daily.values()) / days),
    )
    # Existing payroll and closing-balance labels must reconcile exactly.
    for key in (
        "agt_cnt_12m",
        "agt_amt_12m",
        "agt_num_12m",
        "PYRL_MON_NUM_12M",
        "PYRL_AMT_M",
        "NUM_PYRL_AMT_12M_AVG",
        "LATEST_PYRL_DT",
        "exchg_c_bal",
    ):
        if key == "LATEST_PYRL_DT":
            equal = metrics[key] == s.tag[key]
        else:
            equal = dec(metrics[key]) == dec(s.tag[key])
        if not equal:
            raise ValueError(f"{s.tag['cust_ind']}.{key}: baseline reconciliation failed")
    return metrics
