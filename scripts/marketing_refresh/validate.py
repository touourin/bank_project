"""Independent full-record checks and exploratory risk evidence.

Reads only the five prepared inputs and balance reference for reconciliation.
Ground truth is loaded only after features and rule findings are finalized.
These checks do not represent a test of the external user's application.
"""

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from .common import ASOF, OUTPUT, SOURCE, START, amount, day, dec, dump, load, money, rows


def main():
    manifest = load(OUTPUT / "manifest.json")
    tags = {r["cust_ind"]: r for r in rows(OUTPUT / "marketing_customer_tags.jsonl.gz")}
    accounts = {r["account"]: r for r in load(OUTPUT / "accounts.json")}
    balances = {k: dec(v["opening"]) for k, v in accounts.items()}
    last_sequence, last_time = {}, {}
    deltas = defaultdict(lambda: defaultdict(Decimal))
    features = defaultdict(lambda: defaultdict(Decimal))
    incoming_cp = defaultdict(lambda: defaultdict(Decimal))
    payroll = defaultdict(lambda: defaultdict(Decimal))
    payroll_counts = Counter()
    employees = defaultdict(set)
    ledger_seen = set()
    reverse_pending = {}
    dates, times, kinds = Counter(), Counter(), Counter()
    error_count = 0
    errors = []

    def error(text):
        nonlocal error_count
        error_count += 1
        if len(errors) < 50:
            errors.append(text)

    count = 0
    for r in rows(OUTPUT / "marketing_transactions.jsonl.gz"):
        count += 1
        cid, acc = r["cust_ind"], r["accno"]
        if cid not in tags or acc not in accounts:
            error("Unknown customer/account")
            continue
        d = day(r["txn_dt"])
        v = dec(r["txnamt"])
        seq = int(r["acc_dtl_sn"])
        key = (acc, seq)
        if key in ledger_seen:
            error("Duplicate account sequence")
        ledger_seen.add(key)
        if seq != last_sequence.get(acc, 0) + 1:
            error("Non-contiguous sequence")
        last_sequence[acc] = seq
        tm = (r["txn_dt"], r["txn_tm"])
        if tm < last_time.get(acc, tm):
            error("Out-of-order transaction time")
        last_time[acc] = tm
        if not START <= d <= ASOF or d < day(accounts[acc]["opened"]):
            error("Date outside account window")
        signed = v if r["dbtcrdrccd"] == "C" else -v
        if v < 0 or signed != dec(r["cr_hpnam"]) - dec(r["dhamt"]):
            error("Debit/credit amount mismatch")
        balances[acc] += signed
        if balances[acc] != dec(r["acct_bal"]) or balances[acc] < 0:
            error("Ledger balance mismatch")
        if dec(r["cny_amt"]) != v or dec(r["cntpr_amt"]) != v:
            error("Currency/counterparty amount mismatch")
        if r["cust_nm"] != tags[cid]["cust_nm"]:
            error("Customer name mismatch")
        if "MOCK" in json.dumps(r):
            error("Residual mock placeholder")
        if r["chnl_tpcd"] in {"WEB", "MOBILE"}:
            field = "corp_ebnkg_sign_dt" if r["chnl_tpcd"] == "WEB" else "mb_sign_dt"
            if not tags[cid].get(field) or day(tags[cid][field]) > d:
                error("Transaction predates signing")
        deltas[acc][d] += signed
        f = features[cid]
        kind = r["ev_ecd"]
        if kind == "PAYROLL":
            payroll[(cid, d.strftime("%Y%m"))][r["cntrprt_txn_accno"]] += v
            payroll_counts[cid] += 1
            employees[cid].add(r["cntrprt_txn_accno"])
        if kind in {"SETTLE_IN", "SETTLE_OUT"}:
            f["settlement_amount"] += v
            f["settlement_count"] += 1
            if date(2026, 3, 1) <= d < date(2026, 6, 1):
                f["prior_count"] += 1
            if date(2026, 6, 1) <= d <= date(2026, 8, 31):
                f["recent_count"] += 1
            if kind == "SETTLE_IN":
                if date(2026, 3, 1) <= d < date(2026, 6, 1):
                    f["prior_receipts"] += v
                if date(2026, 6, 1) <= d <= date(2026, 8, 31):
                    f["recent_receipts"] += v
                    incoming_cp[cid][r["cntrprt_txn_accno"]] += v
            elif date(2026, 6, 1) <= d <= date(2026, 8, 31):
                f["recent_business_out"] += v
        if r["be_rvrs_ind"] == "1":
            reverse_pending[key] = (signed, r["cntrprt_txn_accno"], r["txn_dt"])
        if r["rvrs_cd"] == "1":
            original = reverse_pending.pop((acc, int(r["rvrs_acc_dtl_sn"])), None)
            if not original or original[0] != -signed or original[1] != r["cntrprt_txn_accno"]:
                error("Broken reversal")
        dates[d.day] += 1
        times[r["txn_tm"][:2]] += 1
        kinds[kind] += 1
        if count % 100000 == 0:
            print(f"Validated ledger: {count:,}", flush=True)
    if reverse_pending:
        error("Unreversed originals")
    daily = defaultdict(lambda: defaultdict(Decimal))
    ddaily = defaultdict(lambda: defaultdict(Decimal))
    for acc, a in accounts.items():
        if balances[acc] != dec(a["closing"]):
            error("Account closing mismatch")
        running = dec(a["opening"])
        for i in range((ASOF - START).days + 1):
            d = START + timedelta(days=i)
            running += deltas[acc][d]
            daily[a["cust_ind"]][d] += running
            if a["product"] == "DEMAND":
                ddaily[a["cust_ind"]][d] += running
    for cid, t in tags.items():
        checks = {
            "exchg_c_bal": daily[cid][ASOF],
            "exchg_c_bal_yavg": money(
                sum((v for d, v in daily[cid].items() if d.year == 2026), Decimal(0))
                / (ASOF - date(2026, 1, 1) + timedelta(days=1)).days
            ),
            "c_exchg_cny_12m_yaug": money(sum(daily[cid].values()) / len(daily[cid])),
            "exchg_cny_dmd_12m_yaug": money(sum(ddaily[cid].values()) / len(ddaily[cid])),
            "agt_cnt_12m": payroll_counts[cid],
            "agt_num_12m": len(employees[cid]),
            "agt_amt_12m": sum(
                (sum(v.values()) for (c, m), v in payroll.items() if c == cid), Decimal(0)
            ),
            "manu_settle_amt": features[cid]["settlement_amount"],
        }
        for k, v in checks.items():
            if dec(t.get(k)) != dec(v):
                error(f"{cid} {k} differs from full ledger")
    journeys = list(rows(OUTPUT / "marketing_customer_journey.jsonl.gz"))
    event_pay = defaultdict(Decimal)
    event_keys = set()
    for e in journeys:
        if e["ROWKEY"] in event_keys:
            error("Duplicate journey key")
        event_keys.add(e["ROWKEY"])
        cid = e["CUST_ID"]
        if cid not in tags:
            error("Unknown journey customer")
        p = json.loads(e["PROPERTIES"])
        if e["EVT_TYPE"] == "YWJC0012":
            event_pay[(cid, e["OCCUR_DT"][:6])] += dec(p["代发金额"])
        if e["EVT_TYPE"] == "YWJC0009-2" and money(p["存款金额"]) != money(ddaily[cid][ASOF]):
            error("Deposit snapshot differs from ledger")
    for k, v in payroll.items():
        if sum(v.values()) != event_pay[k]:
            error("Payroll journey total mismatch")
    if set(event_pay) != set(payroll):
        error("Payroll month coverage mismatch")
    credit = list(rows(OUTPUT / "marketing_credit.jsonl.gz"))
    business = list(rows(OUTPUT / "marketing_business.jsonl.gz"))
    from source_mock.semantic_validation import row_errors

    schemas = {t["sheet"]: t for t in load(SOURCE / "schema.json")["tables"]}
    for label, records in (("征信", credit), ("工商", business)):
        headers = set(manifest["tables"][label]["columns"])
        for record in records:
            schema = schemas[record["source_table"]]
            decoded = {}
            for field in schema["columns"]:
                key = (
                    field["name"]
                    if field["name"] in headers
                    else field["name"] + "__" + re.match(r"[A-Za-z]+", field["type"])[0].lower()
                )
                decoded[field["name"]] = record.get(key, "")
            for violation in row_errors(schema, decoded):
                error(schema["sheet"] + ": " + violation)
    loan_balances = defaultdict(Decimal)
    overdues = defaultdict(Decimal)
    for r in credit:
        if r["source_table"] == "PBCEC_ED01B_REPAYMENT":
            cid = "CUST_" + r["EA01AI01"].zfill(4)
            loan_balances[cid] += dec(r.get("ED01BJ01"))
            overdues[cid] += dec(r.get("ED01BJ04"))
            if dec(r.get("ED01BJ04")) > dec(r.get("ED01BJ01")):
                error("Overdue greater than debt")
            if dec(r.get("ED01BJ04")) > 0 and day(r["ED01BR05"]) <= day(r["ED01BR04"]):
                error("Overdue due date precedes actual payment")
    for cid, t in tags.items():
        if loan_balances[cid] != dec(t["report_crdt_bal"]):
            error("Credit balance differs from tags")
    active_legal = set()
    for r in business:
        if (
            r["source_table"] == "T_SAIC_PUNISHBREAK"
            and dec(r.get("UNPERFORMPART")) > 0
            and not r.get("EXITDATE")
        ):
            name = r.get("cust_nm")
            active_legal.update(cid for cid, t in tags.items() if t["cust_nm"] == name)
    findings = defaultdict(list)
    evidence = []
    for cid, t in tags.items():
        f = features[cid]
        prior = f["prior_receipts"]
        recent = f["recent_receipts"]
        ratio = recent / prior if prior else None
        cp_share = (
            max(incoming_cp[cid].values(), default=Decimal(0)) / recent if recent else Decimal(0)
        )
        activity = f["recent_count"] / f["prior_count"] if f["prior_count"] else None
        if ratio is not None and Decimal(0) < ratio < Decimal(".55"):
            findings["cashflow_decline"].append(cid)
        if recent > 100000 and cp_share > Decimal(".8"):
            findings["counterparty_concentration"].append(cid)
        if overdues[cid] > 0:
            findings["credit_overdue"].append(cid)
        if cid in active_legal:
            findings["legal_execution"].append(cid)
        if activity is not None and activity < Decimal(".2") and f["prior_count"] >= 15:
            findings["activity_decline"].append(cid)
        evidence.append(
            {
                "客户号": cid,
                "客户名称": t["cust_nm"],
                "3至5月经营回款": amount(prior),
                "6至8月经营回款": amount(recent),
                "回款环比": round(float(ratio), 4) if ratio is not None else None,
                "最大回款客户占比": round(float(cp_share), 4),
                "活跃度比值": round(float(activity), 4) if activity is not None else None,
                "当前逾期金额": amount(overdues[cid]),
                "存在未结执行": cid in active_legal,
                "期末存款": t["exchg_c_bal"],
            }
        )
    # Expected labels enter only after independent evidence extraction.
    truth = load(OUTPUT / "risk_truth.json")
    evaluation = {}
    for kind, ids in truth["scenarios"].items():
        detected = set(findings[kind])
        expected = set(ids)
        evaluation[kind] = {
            "expected": len(expected),
            "detected_expected": len(expected & detected),
            "missed": sorted(expected - detected),
            "additional_flags": sorted(detected - expected),
        }
    report = {
        "ledger_rows": count,
        "customers": len(tags),
        "errors": errors,
        "error_count": error_count,
        "risk_evidence_check": evaluation,
        "findings": dict(findings),
        "transaction_kinds": dict(kinds),
        "transaction_day_of_month": dict(dates),
        "transaction_hour": dict(times),
        "external_program_tested": False,
    }
    dump(OUTPUT / "validation.json", report)
    dump(OUTPUT / "risk_evidence.json", evidence)
    with (OUTPUT / "风险证据汇总.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(evidence[0]))
        w.writeheader()
        w.writerows(evidence)
    print(
        json.dumps(
            {"errors": error_count, "examples": errors[:8], "evaluation": evaluation},
            ensure_ascii=False,
        ),
        flush=True,
    )
    if error_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
