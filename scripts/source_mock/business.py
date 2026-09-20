"""Company dictionary rows use the same identities and capital as customer tags."""

from .common import dec
from .facts import make_row

CORE = {
    "T_SAIC_BASIC",
    "T_SAIC_ORGBASIC",
    "T_SAIC_ORGDETAIL",
    "T_SAIC_PERSON",
    "T_SAIC_SHAREHOLDER",
}


def rows_for(table: dict, subjects: list, coverage_customers: int = 100):
    name = table["sheet"]
    companies = [s for s in subjects if s.entity_type == "company"]
    population = companies if name in CORE else companies[:coverage_customers]
    for s in population:
        overrides = {}
        if name == "T_SAIC_BASIC":
            overrides = dict(
                REGCAP=dec(s.tag["cert_capt_amt"]),
                RECCAP=dec(s.tag["org_capt_amt"]),
                FRNAME=s.tag["legal_rep_nm"],
                REGCAPCUR="CNY",
                ENTSTATUS=s.tag["survival_status"],
                EMPNUM=s.tag["mec_num"],
                ESDATE=s.tag["found_dt"],
                OPSCOPE=s.tag["opscope"],
                ENTTYPE="有限责任公司",
                ENTTYPECODE="M01",
                DOM=s.tag["busin_addr"],
                INDUSTRYCOCODE=s.tag["industry_sml_cd"],
                INDUSTRYCONAME="模拟行业",
                REGORGPROVINCE=s.tag["cert_province"],
                REGORGCITY=s.tag["cert_city"],
                REGORGDISTRICT=s.tag["cert_district"],
                CANDATE=s.as_of.isoformat() if s.tag["survival_status"] == "注销" else None,
                REVDATE=s.as_of.isoformat() if s.tag["survival_status"] == "吊销" else None,
            )
            # Explicit lifecycle coverage without falsifying the active company:
            # historic revoked/cancelled affiliated entities are represented in
            # dedicated relationship/scenario tables, not the base company row.
        if name == "T_SAIC_ORGDETAIL":
            overrides["FDDBR"] = s.tag["legal_rep_nm"]
        if name == "T_SAIC_SHAREHOLDER":
            overrides.update(
                FUNDEDRATIO="1.00",
                INVSUMFUNDEDRATIO="1.00",
                INVAMOUNT="1",
                SHANAME=s.person,
                SUBCONAM=dec(s.tag["cert_capt_amt"]) / 10000,
                SUMCONAM=dec(s.tag["cert_capt_amt"]) / 10000,
                REGCAPCUR="CNY",
            )
        if name.startswith("T_SAIC_MORTGAGE"):
            overrides.update(
                MAB_GUAR_AMT=dec(s.tag["cert_capt_amt"]) / 20,
                MAB_DEBT_AMT=dec(s.tag["cert_capt_amt"]) / 20,
            )
        if name == "ENT_RISK_WARNING_SIGNAL":
            overrides.update(
                CUST_CODE=s.tag["cust_ind"],
                CUST_NAME=s.tag["cust_nm"],
                CERTIFICATE_CODE=s.tag["unify_credit_code"],
                CERTIFICATE_TYPE="01",
                SIGNAL_NOTES="历史模拟预警，已处理",
            )
        row = make_row(table, s, overrides)
        yield row, s
