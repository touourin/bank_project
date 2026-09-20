"""Turn the audited dictionary into an explicit synthetic-data contract."""

from __future__ import annotations

import copy
from pathlib import Path

from .common import read_csv


def prepare_schema(review: dict, tag_schema: dict) -> dict:
    result = copy.deepcopy(review)
    result["kind"] = "synthetic_source_contract"
    result["version"] = "1.0"
    result["policy"] = (
        "共同字段以客户标签为准；全部源字段保留；条件映射仅在明确的合成主体范围内解析。"
        "这是已实现的mock数据契约，不是银行正式DDL或生产迁移。"
    )
    tag_fields = {c["name"]: c for c in tag_schema["tables"]["CCM_C_CUST_FLAG_INFO"]["columns"]}
    # The supplied customers are CNY limited companies. These conditional aliases
    # are now resolved for that explicit mock population, not for production.
    resolved = {
        ("T_SAIC_BASIC", "FRNAME"),
        ("T_SAIC_BASIC", "REGCAPCUR"),
        ("T_SAIC_ORGDETAIL", "FDDBR"),
        ("PBCEC_EC01_BASICIDENTITY", "EC010Q02"),
        ("PBCEC_EC05_ACTUALCONTROLLER", "EC050Q01"),
    }
    for t in result["tables"]:
        for f in t["columns"]:
            raw = f["source_name"].strip()
            if f["status"] == "invalid_source_name":
                if f.get("proposed_name") != "EG05BJ01":
                    raise ValueError(f"Unresolved invalid field: {raw}")
                f.update(name="EG05BJ01", status="mock_typo_correction")
            if (t["sheet"], raw) in resolved:
                f.update(
                    name=f["candidate_target"],
                    type=f["candidate_type"],
                    status="mock_resolved_alias",
                )
            f["source_key"] = "EG05BJ01" if raw.startswith("EG05BJ01 ") else raw
            # PK overrides contradictory nullable=Y only in this synthetic contract.
            f["mock_required"] = f["source_primary_key"] == "PK" or f["source_nullable"] == "N"
            if f["name"] in {"cust_ind", "cust_nm", "unify_credit_code", "dt"}:
                f["mock_required"] = True
            if f["name"] in tag_fields:
                if f["type"].lower() != tag_fields[f["name"]]["sql_type"].lower():
                    raise ValueError(f"Canonical type mismatch: {t['sheet']}.{f['name']}")
                if "format" in tag_fields[f["name"]]:
                    f["format"] = tag_fields[f["name"]]["format"]
            f["generation_rule_defined"] = True
            f["generation_rule"] = "shared_identity_or_domain_fact_else_semantic_fixture"
        names = [f["name"] for f in t["columns"]]
        if len(names) != len(set(names)):
            raise ValueError(f"Alias collision: {t['sheet']}")
        t["mock_primary_key"] = [f["name"] for f in t["columns"] if f["source_primary_key"] == "PK"]
        if t["domain"] == "transactions":
            t["mock_primary_key"] = ["src_sys", "accno", "txn_dt", "acc_dtl_sn"]
        t["filename"] = f"{t['domain']}/{t['sheet']}.csv"
    return result


def null_policy() -> dict:
    # Observations verified in the supplied 50-row customer feature sample.
    # These are not raw-record estimates for unrelated fields.
    return dict(
        empty_csv_cell="SQL NULL; never string 'NULL' or numeric zero",
        baseline_sample="数据表结构.xlsx / 小微潜客加工数据",
        observed_rows=50,
        observations={
            "classified_other_bank_amount_or_count": dict(
                empty=36, denominator=50, rate="0.72", range="BN2:BQ51"
            ),
            "other_bank_institution_count": dict(
                empty=47, denominator=50, rate="0.94", range="BR2:BR51"
            ),
            "report_date_sentinel": dict(
                value="19000102",
                count=33,
                range="BI2:BI51",
                treatment="uninterpreted; not generated as real date or converted to null",
            ),
        },
        strategy="classification components missing together at customer/report level; other optional fields use documented synthetic missingness",
        unmapped_optional_rate="0.20 (mock assumption, not estimated from source samples)",
        financial_statement_missing_rate="0.20 by whole monetary block, preserving arithmetic when reported",
        coverage_rows="first 3 eligible subjects per table keep optional values for nonempty field coverage",
        required="never null when source nullable=N, source PK, normalized identity, or required reconciliation evidence",
        preserved_baseline="existing 1,000 customers all have a report; report presence is retained, partial missing fields are simulated",
    )


def load_inputs(directory: Path, limit: int) -> tuple[list[dict], list[dict]]:
    tags = read_csv(directory / "CCM_C_CUST_FLAG_INFO.csv")[:limit]
    if not tags:
        raise ValueError("No baseline customers")
    ids = {r["cust_ind"] for r in tags}
    journeys = [
        r for r in read_csv(directory / "E_CRM_C_CUST_TOUR_EVT_SUM.csv") if r["CUST_ID"] in ids
    ]
    return tags, journeys
