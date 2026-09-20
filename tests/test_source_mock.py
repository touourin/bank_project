"""Regression checks for source coverage, missingness and independent reconciliation."""

import csv
import hashlib
import json
import shutil
from decimal import Decimal

import pytest

from scripts.source_mock.common import ROOT, read_csv, scalar_error
from scripts.source_mock.generate import build
from scripts.source_mock.validation import validate

REVIEW = ROOT / "data/schema-review/normalized-source-schema.json"


@pytest.fixture(scope="module")
def source_package(tmp_path_factory):
    if not REVIEW.exists():
        pytest.skip("Requires the locally supplied source dictionaries and audit schema")
    path = tmp_path_factory.mktemp("source-mock")
    build(path, REVIEW, ROOT / "examples/mock", 20, 20, 20260917)
    return path


def test_schema_and_cross_source_totals(source_package):
    result = validate(source_package)
    assert result["errors"] == []
    assert result["checks"]["fields_checked"] == 2407
    assert result["checks"]["credit_null_cells"] > 0
    assert result["checks"]["credit_zero_cells"] > 0
    assert result["checks"]["transaction_balance_steps"] > 20000


def test_missing_values_are_distinct_from_zero_and_keys_stay_present(source_package):
    rows = read_csv(source_package / "credit/PBCEC_EB01A_CREDITCUE.csv")
    values = {r["EB01AJ03"] for r in rows}
    assert "" in values and "0.00" in values
    assert all(r["EA01AI01"] and r["cust_nm"] and r["dt"] for r in rows)
    policy = json.loads((source_package / "null-policy.json").read_text())
    assert policy["observations"]["classified_other_bank_amount_or_count"]["empty"] == 36
    assert policy["observations"]["report_date_sentinel"]["count"] == 33
    assert not any("19000102" in row.values() for row in rows)


def test_business_capital_and_shareholders_use_their_respective_units(source_package):
    basics = {
        r["unify_credit_code"]: r for r in read_csv(source_package / "business/T_SAIC_BASIC.csv")
    }
    shareholders = read_csv(source_package / "business/T_SAIC_SHAREHOLDER.csv")
    for row in shareholders:
        capital = Decimal(basics[row["unify_credit_code"]]["cert_capt_amt"])
        assert Decimal(row["SUBCONAM"]) * 10000 == capital
        assert Decimal(row["SUMCONAM"]) * 10000 == capital
        assert Decimal(row["FUNDEDRATIO"]) == 1
    nonprofit_codes = {
        r["unify_credit_code"]
        for r in read_csv(source_package / "reference/customer_identity.csv")
        if r["entity_type"] == "nonprofit"
    }
    assert nonprofit_codes
    assert not nonprofit_codes.intersection(basics)


def test_statement_totals_follow_source_meanings(source_package):
    rows = read_csv(source_package / "credit/PBCEC_EG01AB_DEBTINFO2002.csv")
    row = next(r for r in rows if r["EG01BJ19"])

    def value(n):
        return Decimal(row[f"EG01BJ{n:02d}"])

    # Current assets exclude inventory subcomponents 13 and 14, and must not
    # accidentally include line 19 (the total itself) or long-term investment.
    assert value(19) == sum(value(n) for n in [*range(1, 13), 15, 16, 17, 18])
    assert value(44) == value(19) + value(24) + value(34) + value(42) + value(43)
    assert value(44) == value(87)


def test_repayment_history_contains_observed_months_not_future_dates(source_package):
    for table, prefix in [("PD01D_LATEST24MONTH", "PD01D"), ("PD01E_LATEST5YEAR", "PD01E")]:
        rows = read_csv(source_package / f"credit/PBCPC_{table}.csv")
        row = rows[0]
        assert row[prefix + "R01"] == row[prefix + "R02"] == row[prefix + "R03"] == "2026-09"
        if prefix == "PD01E":
            assert row["PD01ES01"] == "1"
    tel = read_csv(source_package / "credit/PBCPC_PE01AZ_TELPAYMENT.csv")
    assert tel[0]["PE01AQ02"] == "N" * 24


def mutate_csv(package, relative, mutate):
    path = package / relative
    rows = read_csv(path)
    mutate(rows)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    # Refresh the checksum: business validation must detect the wrong value even
    # when a producer republishes a matching checksum.
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][relative]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False))


def test_validator_rejects_credit_total_drift(source_package, tmp_path):
    package = tmp_path / "changed"
    shutil.copytree(source_package, package)

    def change(rows):
        rows[0]["ED060J01"] = str(Decimal(rows[0]["ED060J01"]) + 1)

    mutate_csv(package, "credit/PBCEC_ED06_FACILITYAGREEMENT.csv", change)
    result = validate(package)
    assert any("Credit label differs" in message for message in result["errors"])


def test_validator_rejects_broken_account_balance(source_package, tmp_path):
    package = tmp_path / "changed"
    shutil.copytree(source_package, package)

    def change(rows):
        rows[0]["acct_bal"] = str(Decimal(rows[0]["acct_bal"]) + 1)

    mutate_csv(package, "transactions/交易流水表.csv", change)
    result = validate(package)
    assert "Broken rolling account balance" in result["errors"]


def test_same_seed_produces_identical_csv_and_missingness(source_package, tmp_path):
    build(tmp_path, REVIEW, ROOT / "examples/mock", 20, 20, 20260917)
    first = json.loads((source_package / "manifest.json").read_text())
    second = json.loads((tmp_path / "manifest.json").read_text())
    assert first == second


@pytest.mark.parametrize(
    ("value", "field", "valid"),
    [
        ("", {"type": "DECIMAL(26,8)", "mock_required": False}, True),
        ("0.00000000", {"type": "DECIMAL(26,8)", "mock_required": False}, True),
        ("", {"type": "VARCHAR(22)", "mock_required": True}, False),
        ("NaN", {"type": "DECIMAL(26,8)"}, False),
        ("185.333400", {"type": "INT"}, False),
        ("20260230", {"type": "VARCHAR(8)", "format": "YYYYMMDD"}, False),
    ],
)
def test_null_and_type_validation(value, field, valid):
    assert (scalar_error(value, field) is None) == valid
