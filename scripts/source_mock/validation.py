"""Independent read-back validation of published CSV files and source-to-tag totals."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .common import ROOT, amount, day, dec, read_csv, scalar_error, write_json
from .semantic_validation import ledger_errors, row_errors


def validate(directory: Path) -> dict:
    schema = json.loads((directory / "schema.json").read_text())
    manifest = json.loads((directory / "manifest.json").read_text())
    errors = []
    counts = Counter()

    def error(message):
        if len(errors) < 200:
            errors.append(message)
        counts["errors"] += 1

    people = read_csv(directory / "reference/customer_identity.csv")
    ids = {p["cust_ind"] for p in people}
    enterprise_reports = {p["enterprise_report_id"]: p["cust_ind"] for p in people}
    personal_reports = {p["personal_report_id"]: p["cust_ind"] for p in people}
    tags = {r["cust_ind"]: r for r in read_csv(directory / "expected/customer_tags.csv")}
    table_rows = {}
    for table in schema["tables"]:
        path = directory / table["filename"]
        if not path.exists():
            error(f"Missing file {table['filename']}")
            continue
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != manifest["files"][table["filename"]]["sha256"]:
            error(f"Checksum mismatch: {table['filename']}")
        observed = Counter()
        zeros = Counter()
        keys = set()
        row_count = 0
        retained = []
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != [c["name"] for c in table["columns"]]:
                error(f"Column mismatch: {table['sheet']}")
                continue
            for row_count, row in enumerate(reader, 1):
                if None in row or any(v is None for v in row.values()):
                    error(f"Ragged row: {table['sheet']}:{row_count + 1}")
                    continue
                for f in table["columns"]:
                    value = row[f["name"]]
                    if e := scalar_error(value, f):
                        error(f"{table['sheet']}:{row_count + 1}.{f['name']}: {e}")
                    if value == "":
                        observed[f["name"]] += 1
                    elif value.strip("0.") == "":
                        zeros[f["name"]] += 1
                key = tuple(row[c] for c in table["mock_primary_key"])
                if key in keys:
                    error(f"Duplicate key: {table['sheet']}:{key}")
                keys.add(key)
                if row.get("EA01AI01") and row["EA01AI01"] not in enterprise_reports:
                    error(f"Orphan enterprise report: {table['sheet']}")
                if row.get("PA01AI01") and row["PA01AI01"] not in personal_reports:
                    error(f"Orphan personal report: {table['sheet']}")
                for eq in table.get("financial_equations", []):
                    values = [row[term["field"]] for term in eq["terms"]] + [row[eq["target"]]]
                    if all(v != "" for v in values):
                        expected = sum(
                            (dec(row[t["field"]]) * t["sign"] for t in eq["terms"]), Decimal(0)
                        )
                        if dec(row[eq["target"]]) != expected:
                            error(
                                f"Financial equation: {table['sheet']}:{row_count + 1}.{eq['target']}"
                            )
                        counts["financial_equations_checked"] += 1
                if manifest.get("semantic_revision") and table["domain"] != "transactions":
                    for message in row_errors(table, row):
                        error(f"{table['sheet']}:{row_count + 1}: {message}")
                    counts["semantic_source_rows"] += 1
                if table["domain"] != "transactions":
                    retained.append(row)
        table_rows[table["sheet"]] = retained
        stats = manifest["files"][table["filename"]]
        if row_count != stats["rows"]:
            error(f"Row count mismatch: {table['sheet']}")
        if not row_count:
            error(f"Empty table: {table['sheet']}")
        for f in table["columns"]:
            name = f["name"]
            if observed[name] == row_count:
                error(f"Field has no applicable nonempty example: {table['sheet']}.{name}")
            expected = dict(
                nonempty=row_count - observed[name], null=observed[name], zero=zeros[name]
            )
            if expected != stats["field_coverage"][name]:
                error(f"Coverage counters differ: {table['sheet']}.{name}")
            counts["fields_checked"] += 1
            if table["domain"] == "credit":
                counts["credit_null_cells"] += observed[name]
                counts["credit_zero_cells"] += zeros[name]
        counts[table["domain"] + "_rows"] += row_count

    links = read_csv(directory / "reference/source_row_links.csv")
    linked = set()
    for link in links:
        if link["cust_ind"] not in ids:
            error("Lineage references unknown customer")
        key = (link["table"], int(link["csv_row"]) - 2)
        if key in linked:
            error("Duplicate row lineage")
        linked.add(key)
        rows = table_rows.get(link["table"], [])
        if not 0 <= key[1] < len(rows):
            error("Lineage points outside source table")
            continue
        row = rows[key[1]]
        p = next(p for p in people if p["cust_ind"] == link["cust_ind"])
        for field, expected in (
            ("cust_nm", p["cust_nm"]),
            ("unify_credit_code", p["unify_credit_code"]),
        ):
            if field in row and row[field] != expected:
                error(f"Identity mismatch: {link['table']}.{field}")
    if len(linked) != sum(len(rows) for rows in table_rows.values()):
        error("Incomplete row lineage")

    # Aggregate loan and agreement records independently, using lender identity.
    agreements = {}
    credit_totals = defaultdict(lambda: defaultdict(Decimal))
    for row in table_rows.get("PBCEC_ED06_FACILITYAGREEMENT", []):
        cid = enterprise_reports[row["EA01AI01"]]
        key = (row["EA01AI01"], row["ED060I01"])
        agreements[key] = row
        credit_totals[cid]["limit"] += dec(row["ED060J01"])
        if row["ED060I02"] == ("0001" if manifest.get("semantic_revision") else "M001"):
            credit_totals[cid]["our_limit"] += dec(row["ED060J01"])
        if dec(row["ED060J04"]) > dec(row["ED060J01"]):
            error("Used credit exceeds limit")
    loans = {}
    for row in table_rows.get("PBCEC_ED01A_BASICLOANACCT", []):
        key = (row["EA01AI01"], row["ED01AI01"])
        loans[key] = row
        if (row["EA01AI01"], row["ED01AI03"]) not in agreements:
            error("Loan without agreement")
    for row in table_rows.get("PBCEC_ED01B_REPAYMENT", []):
        key = (row["EA01AI01"], row["ED01AI01"])
        if key not in loans:
            error("Repayment without loan")
            continue
        loan = loans[key]
        cid = enterprise_reports[row["EA01AI01"]]
        credit_totals[cid]["balance"] += dec(row["ED01BJ01"])
        if loan["ED01AI02"] == ("0001" if manifest.get("semantic_revision") else "M001"):
            credit_totals[cid]["our_balance"] += dec(row["ED01BJ01"])
        if dec(row["ED01BJ01"]) > dec(loan["ED01AJ01"]):
            error("Loan balance exceeds principal")
    for cid, t in tags.items():
        for target, source in (
            ("report_crdt_amt", "limit"),
            ("report_crdt_our_bank_amt", "our_limit"),
            ("report_crdt_bal", "balance"),
            ("report_crdt_our_bank_bal", "our_balance"),
        ):
            if dec(t[target]) != credit_totals[cid][source]:
                error(f"Credit label differs: {cid}.{target}")
            counts["tag_comparisons"] += 1
    credit_summary = {r["EA01AI01"]: r for r in table_rows.get("PBCEC_EB04_FACILITYAGTSUM", [])}
    for rid, cid in enterprise_reports.items():
        summary = credit_summary.get(rid)
        if summary:
            for fields, metric in (
                (("EB040J01", "EB040J04"), "limit"),
                (("EB040J02", "EB040J05"), "balance"),
            ):
                if sum((dec(summary[f]) for f in fields), Decimal(0)) != credit_totals[cid][metric]:
                    error("Credit summary differs from agreement/loan detail")
    basic_by_code = {r["unify_credit_code"]: r for r in table_rows.get("T_SAIC_BASIC", [])}
    if manifest.get("semantic_revision"):
        shareholders = defaultdict(list)
        for r in table_rows.get("T_SAIC_SHAREHOLDER", []):
            shareholders[r["unify_credit_code"]].append(r)
        for code, rs in shareholders.items():
            if sum((dec(r["FUNDEDRATIO"]) for r in rs), Decimal(0)) != 1:
                error("Shareholder ratios do not sum to one")
            if sum((dec(r["SUBCONAM"]) * 10000 for r in rs), Decimal(0)) != dec(
                basic_by_code[code]["cert_capt_amt"]
            ):
                error("Shareholder contributions differ from registered capital")
            if any(int(r["INVAMOUNT"]) != len(rs) for r in rs):
                error("Shareholder count differs from detail")
            counts["shareholder_groups_checked"] += 1
        for r in table_rows.get("T_SAIC_LIQUIDATION", []):
            if basic_by_code[r["unify_credit_code"]]["survival_status"] not in {"注销", "吊销"}:
                error("Completed liquidation belongs to active company")
    for t in tags.values():
        basic = basic_by_code.get(t["unify_credit_code"])
        if basic is None:
            error("Customer missing business basic row")
            continue
        for f in (
            "cust_nm",
            "found_dt",
            "survival_status",
            "opscope",
            "cert_capt_amt",
            "org_capt_amt",
            "mec_num",
            "legal_rep_nm",
        ):
            if basic[f] != t[f]:
                error(f"Business label differs: {t['cust_ind']}.{f}")
            counts["tag_comparisons"] += 1
    validate_transactions(directory, tags, people, error, counts)
    if counts["credit_null_cells"] == 0 or counts["credit_zero_cells"] == 0:
        error("Credit fixtures must include both null and genuine zero values")
    return dict(
        status="passed" if not errors else "failed",
        errors=errors,
        checks=dict(counts),
        scope="Schema, field coverage, PK/nulls, entity/report links, explicit financial equations, core credit/company/payroll/account/day-average reconciliation",
    )


def validate_transactions(directory, tags, people, error, counts):
    config = json.loads((directory / "manifest.json").read_text())
    semantic = bool(config.get("semantic_revision"))
    accounts = {a["accno"]: a for a in read_csv(directory / "reference/accounts.csv")}
    identities = {p["cust_ind"]: p for p in people}
    balances = {n: dec(a["opening_balance"]) for n, a in accounts.items()}
    changes = defaultdict(lambda: defaultdict(Decimal))
    metrics = defaultdict(lambda: defaultdict(Decimal))
    employees = defaultdict(set)
    months = defaultdict(set)
    settle_months = defaultdict(set)
    latest = defaultdict(str)
    originals = {}
    reversed_keys = set()
    last_order = {}
    with (directory / "transactions/交易流水表.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        for row in csv.DictReader(stream):
            acc = row["accno"]
            if acc not in accounts:
                error("Unknown transaction account")
                continue
            a = accounts[acc]
            cid = row["cust_ind"]
            if cid != a["cust_ind"] or row["cust_nm"] != identities[cid]["cust_nm"]:
                error("Account/customer mismatch")
            occurred = date.fromisoformat(row["txn_dt"])
            if not day(a["open_acct_dt"]) <= occurred <= day(a["dt"]):
                error("Transaction outside account lifetime/snapshot")
            order = (row["txn_dt"], row["txn_tm"], int(row["acc_dtl_sn"]))
            if acc in last_order and order < last_order[acc]:
                error("Transaction order not monotonic")
            last_order[acc] = order
            value = dec(row["txnamt"])
            if value <= 0:
                error("Nonpositive transaction amount")
            debit, credit = dec(row["dhamt"]), dec(row["cr_hpnam"])
            if row["dbtcrdrccd"] not in {"C", "D"} or (debit, credit) != (
                (Decimal(0), value) if row["dbtcrdrccd"] == "C" else (value, Decimal(0))
            ):
                error("Debit/credit amount or direction mismatch")
            signed = credit - debit
            balances[acc] += signed
            changes[acc][occurred] += signed
            if balances[acc] != dec(row["acct_bal"]) or balances[acc] < 0:
                error("Broken rolling account balance")
            if dec(row["cny_amt"]) != dec(amount(value * dec(row["rate"]))):
                error("Currency conversion mismatch")
            token = (acc, row["acc_dtl_sn"])
            if row["be_rvrs_ind"] == "1":
                originals[token] = signed
            if row["rvrs_cd"] == "1":
                original = (acc, row["rvrs_acc_dtl_sn"])
                if (
                    original not in originals
                    or originals[original] != -signed
                    or original in reversed_keys
                ):
                    error("Invalid reversal linkage")
                reversed_keys.add(original)
            if cid not in tags:
                continue
            if semantic:
                for message in ledger_errors(row, tags[cid]):
                    error(message)
                if row["agnc_psn_nm"] and (
                    row["agnc_psn_nm"] != identities[cid]["person_name"]
                    or row["agnc_psn_crdt_no"] != identities[cid]["person_certificate"]
                ):
                    error("Agent identity differs from customer master")
                counts["semantic_transaction_rows"] += 1
            if row["ev_ecd"] == "MOCK_PAYROLL":
                metrics[cid]["agt_amt_12m"] += value
                metrics[cid]["agt_cnt_12m"] += 1
                employees[cid].add(row["cntrprt_txn_accno"])
                months[cid].add(row["txn_dt"][:7])
                latest[cid] = max(latest[cid], row["txn_dt"].replace("-", ""))
                if row["txn_dt"][:7].replace("-", "") == tags[cid]["dt"][:6]:
                    metrics[cid]["PYRL_AMT_M"] += value
            if (
                row["ev_ecd"] in {"MOCK_SETTLE_IN", "MOCK_SETTLE_OUT"}
                and row["be_rvrs_ind"] == "0"
                and row["rvrs_cd"] == "0"
            ):
                metrics[cid]["manu_settle_amt"] += value
                metrics[cid]["settle_count"] += 1
                settle_months[cid].add(row["txn_dt"][:7])
            counts["transaction_balance_steps"] += 1
    if set(originals) != reversed_keys:
        error("Reversed originals missing reversing entries")
    for acc, a in accounts.items():
        cid = a["cust_ind"]
        if balances[acc] != dec(a["closing_balance"]):
            error("Account closing balance differs")
        rate = Decimal("7.1") if a["ccycd"] == "USD" else Decimal(1)
        metrics[cid]["exchg_c_bal"] += balances[acc] * rate
        start, end = day(a["period_start"]), day(a["dt"])
        balance = dec(a["opening_balance"])
        for offset in range((end - start).days + 1):
            d = start + timedelta(days=offset)
            balance += changes[acc][d]
            if d < day(a["open_acct_dt"]) and balance:
                error("Balance before account opening")
            metrics[cid]["sum_12m"] += balance * rate
            if d.year == end.year:
                metrics[cid]["sum_ytd"] += balance * rate
            if a["prod_cd"] == "DEMAND":
                metrics[cid]["sum_demand"] += balance * rate
    for cid, t in tags.items():
        m = metrics[cid]
        as_of = day(t["dt"])
        a = next(a for a in accounts.values() if a["cust_ind"] == cid)
        days = (as_of - day(a["period_start"])).days + 1
        m.update(
            agt_num_12m=Decimal(len(employees[cid])),
            PYRL_MON_NUM_12M=Decimal(len(months[cid])),
            NUM_PYRL_AMT_12M_AVG=m["agt_amt_12m"] / m["agt_cnt_12m"]
            if m["agt_cnt_12m"]
            else Decimal(0),
            MANU_SETTLE_AMT_MON_NUM=Decimal(len(settle_months[cid])),
            manu_settle_amt_avg_cnt=(m["settle_count"] / 12).quantize(
                Decimal(1), rounding=ROUND_HALF_UP
            ),
            exchg_c_bal_yavg=m["sum_ytd"] / ((as_of - date(as_of.year, 1, 1)).days + 1),
            c_exchg_cny_12m_yaug=m["sum_12m"] / days,
            exchg_cny_dmd_12m_yaug=m["sum_demand"] / days,
        )
        for name in (
            "agt_amt_12m",
            "agt_cnt_12m",
            "agt_num_12m",
            "PYRL_MON_NUM_12M",
            "PYRL_AMT_M",
            "NUM_PYRL_AMT_12M_AVG",
            "MANU_SETTLE_AMT_MON_NUM",
            "manu_settle_amt_avg_cnt",
            "manu_settle_amt",
            "exchg_c_bal",
            "exchg_c_bal_yavg",
            "c_exchg_cny_12m_yaug",
            "exchg_cny_dmd_12m_yaug",
        ):
            scale = (
                2
                if config.get("currency_average_scale") == 2
                and name in {"exchg_c_bal_yavg", "c_exchg_cny_12m_yaug", "exchg_cny_dmd_12m_yaug"}
                else 8
            )
            if dec(t[name]) != dec(amount(m[name], scale)):
                error(f"Transaction-derived label differs: {cid}.{name}")
            counts["tag_comparisons"] += 1
        if latest[cid] != t["LATEST_PYRL_DT"]:
            error("Latest payroll date differs")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, default=ROOT / "data/mock-sources")
    args = p.parse_args()
    result = validate(args.input)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    write_json(args.input / "validation.json", result)
    raise SystemExit(bool(result["errors"]))
