"""Check the portable fixtures and failures that would break handoff between modules."""

import copy
import json
from datetime import date
from decimal import Decimal

import pytest

from scripts.mock_data.common import JOURNEY_TABLE, TAG_TABLE, json_exact, load_schema, read_csv
from scripts.mock_data.generation import build_dataset, generate
from scripts.mock_data.validation import parse_properties, validate_directory, validate_rows


@pytest.fixture
def schema():
    return load_schema()


@pytest.fixture
def dataset(schema):
    return build_dataset(schema, customers=20, as_of=date(2026, 9, 17), seed=20260917)


def event(dataset, code):
    return next(row for row in dataset[JOURNEY_TABLE] if row["EVT_TYPE"] == code)


def test_dataset_scope_and_user_decisions(dataset, schema):
    assert validate_rows(dataset, schema) == []
    assert len(dataset[TAG_TABLE]) == 20
    assert len(dataset[TAG_TABLE][0]) == 155
    codes = {row["EVT_TYPE"] for row in dataset[JOURNEY_TABLE]}
    assert codes == {code for code, spec in schema["events"].items() if spec["generate"]}
    assert "YWJC0007" not in codes
    assert {"DSJ0001", "DSJ0004", "MOCK_FX_SIGN", "MOCK_LC_SIGN", "MOCK_CREDIT_SIGN"} <= codes
    assert schema["events"]["YWJC0017"]["name"] == "未定义1"
    assert schema["events"]["YWJC0018"]["name"] == "未定义2"
    assert parse_properties(event(dataset, "YWJC0002")["PROPERTIES"])["账户类型"] == ""
    assert "授信品种" not in parse_properties(event(dataset, "YWJC0003-2")["PROPERTIES"])
    assert "授信品种" in parse_properties(event(dataset, "YWJC0006-1")["PROPERTIES"])
    assert "贷款品种" in parse_properties(event(dataset, "YWJC0006-2")["PROPERTIES"])
    assert "舆情属性" in parse_properties(event(dataset, "DSJ0006")["PROPERTIES"])
    assert "账号" in parse_properties(event(dataset, "YWJC0011")["PROPERTIES"])


def test_csv_round_trip_is_reproducible_and_preserves_identifiers(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    generate(first)
    generate(second)
    for path in first.iterdir():
        assert path.read_bytes() == (second / path.name).read_bytes()
    assert validate_directory(first) == []
    _, rows = read_csv(first / f"{JOURNEY_TABLE}.csv")
    account_event = next(row for row in rows if row["EVT_TYPE"] == "YWJC0002")
    account = parse_properties(account_event["PROPERTIES"])["账户号"]
    assert isinstance(account, str) and len(account) == 20 and account.startswith("0")
    assert {r["KEY_FLAG"] for r in rows} == {"0", "1"}


def test_json_money_never_round_trips_through_float():
    amount = Decimal("123456789012345678.12345678")
    encoded = json_exact({"金额": amount})
    assert '"金额":123456789012345678.12345678' in encoded
    assert parse_properties(encoded)["金额"] == amount


@pytest.mark.parametrize("as_of", [date(2024, 2, 29), date(2026, 1, 1), date(2026, 3, 1)])
def test_generation_at_month_and_year_boundaries(schema, as_of):
    assert validate_rows(build_dataset(schema, customers=5, as_of=as_of, seed=1), schema) == []


@pytest.mark.parametrize("value", [0, -1, 10001])
def test_invalid_customer_counts_are_rejected(schema, value):
    with pytest.raises(ValueError, match="customers"):
        build_dataset(schema, customers=value, as_of=date(2026, 9, 17), seed=1)


def test_optional_and_distinct_properties_are_not_forced_into_aliases(dataset, schema):
    row = event(dataset, "YWJC0006-1")
    properties = parse_properties(row["PROPERTIES"])
    properties["授信品种"] = "与已结清样例不同的授信产品"
    row["PROPERTIES"] = json_exact(properties)
    assert validate_rows(dataset, schema) == []


def test_validator_rejects_orphan_and_inconsistent_customer_ids(dataset, schema):
    row = event(dataset, "YWJC0001")
    row["CUST_ID"] = "MISSING_CUSTOMER"
    assert any("找不到对应" in error for error in validate_rows(dataset, schema))
    row["CUST_ID"] = dataset[TAG_TABLE][0]["cust_ind"]
    properties = parse_properties(row["PROPERTIES"])
    properties["客户号"] = "DIFFERENT_CUSTOMER"
    row["PROPERTIES"] = json_exact(properties)
    assert any("建档客户号" in error for error in validate_rows(dataset, schema))


@pytest.mark.parametrize("properties", ['{"a":1,"a":2}', '{"金额":NaN}', "[]", "{broken"])
def test_invalid_or_ambiguous_json_is_rejected(dataset, schema, properties):
    event(dataset, "YWJC0002")["PROPERTIES"] = properties
    assert validate_rows(dataset, schema)


def test_validator_rejects_duplicate_events(dataset, schema):
    dataset[JOURNEY_TABLE].append(copy.deepcopy(dataset[JOURNEY_TABLE][0]))
    assert any("主键重复" in error for error in validate_rows(dataset, schema))


@pytest.mark.parametrize(
    "field,value",
    [
        ("cert_capt_amt", "1234567890123456789.00000000"),
        ("cert_capt_amt", "1.123456789"),
        ("mec_num", "2.5"),
        ("found_dt", "20260230"),
        ("pyrl_sign_ind", "Y"),
    ],
)
def test_validator_rejects_type_precision_and_format_drift(dataset, schema, field, value):
    dataset[TAG_TABLE][0][field] = value
    assert any(field in error for error in validate_rows(dataset, schema))


def test_validator_rejects_floating_loan_balance_and_payroll_totals(dataset, schema):
    tag = dataset[TAG_TABLE][1]
    tag["exchg_c_loan_bal"] = "1.00000000"
    tag["agt_amt_12m"] = "1.00000000"
    errors = validate_rows(dataset, schema)
    assert any("exchg_c_loan_bal" in e for e in errors)
    assert any("agt_amt_12m" in e for e in errors)


def test_validator_rejects_future_event_and_excluded_code(dataset, schema):
    row = dataset[JOURNEY_TABLE][0]
    row["OCCUR_DT"] = "20270917"
    row["EVT_TYPE"] = "YWJC0007"
    errors = validate_rows(dataset, schema)
    assert any("发生日期" in e for e in errors)
    assert any("生成范围" in e for e in errors)


def test_manifest_detects_modified_csv(tmp_path):
    generate(tmp_path)
    path = tmp_path / f"{TAG_TABLE}.csv"
    path.write_bytes(path.read_bytes().replace("模拟客户0001".encode(), "另一个模拟名".encode()))
    assert any("校验和" in e for e in validate_directory(tmp_path))
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["synthetic"] is True


def test_supplements_follow_requirements_and_keep_provenance(dataset, schema):
    expected = {
        "MOCK_FX_SIGN": {"业务首次开展日期", "经办机构"},
        "MOCK_LC_SIGN": {"签约日期", "经办机构"},
        "MOCK_CREDIT_SIGN": {
            "合同签约日期",
            "授信品种",
            "合同金额",
            "币种",
            "合同到期日",
            "合同状态",
        },
        "DSJ0001": {"注册日期", "工商注册机关", "统一社会信用代码", "注册资本"},
        "DSJ0004": {"上市日期", "股票板块", "股票代码"},
    }
    for code, fields in expected.items():
        properties = parse_properties(event(dataset, code)["PROPERTIES"])
        assert set(properties) == fields
        spec = schema["events"][code]
        assert spec["confirmation_status"] == "pending_bank"
        assert (spec["definition"] == "temporary_mock_code") == code.startswith("MOCK_")
        assert spec["requirement_source"]["sheet"] == "客户旅程"
    tags = {t["cust_ind"]: t for t in dataset[TAG_TABLE]}
    for row in dataset[JOURNEY_TABLE]:
        properties = parse_properties(row["PROPERTIES"])
        if row["EVT_TYPE"] == "DSJ0001":
            assert properties["注册日期"] == row["OCCUR_DT"] == tags[row["CUST_ID"]]["found_dt"]
        elif row["EVT_TYPE"] == "MOCK_CREDIT_SIGN":
            assert properties["合同签约日期"] == row["OCCUR_DT"]
        elif row["EVT_TYPE"] in {"YWJC0017", "YWJC0018"}:
            assert set(properties) == {"签约日期", "经办机构"}


@pytest.mark.parametrize(
    "code,field,value,error",
    [
        ("DSJ0001", "统一社会信用代码", "WRONG_ID", "统一社会信用代码"),
        ("DSJ0001", "注册资本", 1, "注册资本"),
        ("DSJ0001", "注册日期", "20000101", "注册日期"),
        ("MOCK_CREDIT_SIGN", "合同金额", 1, "合同金额"),
        ("MOCK_CREDIT_SIGN", "合同到期日", "20000101", "到期日早于"),
    ],
)
def test_validator_rejects_inconsistent_supplement_properties(
    dataset, schema, code, field, value, error
):
    row = event(dataset, code)
    properties = parse_properties(row["PROPERTIES"])
    properties[field] = value
    row["PROPERTIES"] = json_exact(properties)
    assert any(error in e for e in validate_rows(dataset, schema))


def test_validator_rejects_inconsistent_listing_and_contract_order(dataset, schema):
    listing = event(dataset, "DSJ0004")
    tag = next(t for t in dataset[TAG_TABLE] if t["cust_ind"] == listing["CUST_ID"])
    tag["ipo_ind"] = "0"
    event(dataset, "MOCK_CREDIT_SIGN")["OCCUR_DT"] = "20000101"
    errors = validate_rows(dataset, schema)
    assert any("ipo_ind" in e for e in errors)
    assert any("批复通过与放款之间" in e for e in errors)


def test_confirmation_manifest_tracks_actual_coverage_and_rejects_tampering(tmp_path):
    manifest = generate(tmp_path, customers=1)
    pending = manifest["pending_bank_confirmation"]
    assert len(pending) == 5
    assert {p["official_code"] for p in pending if p["official_code"]} == {"DSJ0001", "DSJ0004"}
    assert all(p["mock_rows"] == manifest["event_counts"].get(p["mock_code"], 0) for p in pending)
    assert next(p for p in pending if p["mock_code"] == "MOCK_CREDIT_SIGN")["mock_rows"] == 0
    assert validate_directory(tmp_path) == []
    pending[0]["official_code"] = pending[0]["mock_code"]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert any("待银行确认" in e for e in validate_directory(tmp_path))
