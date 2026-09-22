"""Deterministic fixture scenarios. Names never determine a person's identity."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

REVISION = "person-relations-20260922-v1"


def person_name(index: int) -> str:
    # Twenty explicit homonym pairs retain separate IDs and certificates.
    if 901 <= index <= 920:
        index -= 100
    index -= 1
    return (
        "赵钱孙李周吴郑王陈杨黄徐朱胡林何"[index % 16]
        + "明远思宁文昕瑞安嘉涵宇泽景承启博舒清正和"[index // 16 % 20]
        + "博颖航悦晨欣哲轩衡岚溪澜川铭宁言书禾庭然"[index // 320 % 20]
    )


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    certificate: str
    previous_name: str


class Policy:
    def __init__(self, tags, identities):
        tags = sorted(tags, key=lambda row: row["cust_ind"])
        if len(tags) != 1000 or len({r["cust_ind"] for r in tags}) != 1000:
            raise ValueError("Expected the reviewed 1,000-customer fixture")
        identities = {r["unify_credit_code"]: r for r in identities}
        self.people, self.companies = {}, {}
        self.by_code, self.by_name, self.by_report = {}, {}, {}
        groups = sorted({(r["CST_MGRP_ID"], r["CST_MGRP_NM"]) for r in tags if r["CST_MGRP_ID"]})
        if len(groups) != 59 or len({key for key, _ in groups}) != 59:
            raise ValueError("Expected 59 unambiguous group identifiers")
        for i, tag in enumerate(tags, 1):
            original = identities[tag["unify_credit_code"]]
            pid = tag["legal_rep_cust_id"]
            if not pid or pid in self.people or not original["person_certificate"]:
                raise ValueError("Missing or duplicate source person identity")
            self.people[pid] = Person(
                pid, person_name(i), original["person_certificate"], tag["legal_rep_nm"]
            )
        person_ids = [r["legal_rep_cust_id"] for r in tags]
        grouped = defaultdict(list)
        independent = []
        for i, tag in enumerate(tags):
            if tag["CST_MGRP_ID"]:
                grouped[tag["CST_MGRP_ID"]].append(i)
            else:
                independent.append(i)
        delegates = {i: independent[n // 2 * 2] for n, i in enumerate(independent[:48])}
        managers = {i: independent[48 + (n ^ 1)] for n, i in enumerate(independent[48:248])}
        for i, tag in enumerate(tags):
            base = person_ids[i]
            legal, controller = base, base
            group_id, group_name = tag["CST_MGRP_ID"], tag["CST_MGRP_NM"]
            scenario = "independent_company"
            if group_id:
                members = grouped[group_id]
                controller = person_ids[members[0]]
                legal = person_ids[members[1]] if len(members) > 2 and i == members[-1] else base
                scenario = "group_common_controller"
            elif i in delegates:
                # One delegate represents two companies with different owners.
                legal = person_ids[delegates[i]]
                scenario = "shared_representative_separate_controllers"
            elif i in managers:
                # Paired professional managers / owners without inventing IDs.
                controller = person_ids[managers[i]]
                scenario = "representative_differs_from_controller"
            cid = tag["cust_ind"]
            self.companies[cid] = dict(
                customer_id=cid,
                name=tag["cust_nm"],
                credit_code=tag["unify_credit_code"],
                base=base,
                legal=legal,
                controller=controller,
                group_id=group_id,
                group_name=group_name,
                scenario=scenario,
            )
            self.by_code[tag["unify_credit_code"]] = cid
            self.by_name[tag["cust_nm"]] = cid
            self.by_report[identities[tag["unify_credit_code"]]["enterprise_report_id"]] = cid
        self.by_certificate = {p.certificate: p for p in self.people.values()}
        if any(len(index) != len(tags) for index in (self.by_code, self.by_name, self.by_report)):
            raise ValueError("Ambiguous company credit code, name, or report identifier")
        if len(self.by_certificate) != len(self.people):
            raise ValueError("Different source people share a certificate")

    def company(self, row):
        for key in ("cust_ind", "CUST_ID", "CUST_CODE"):
            if row.get(key) in self.companies:
                return self.companies[row[key]]
        for key in ("unify_credit_code", "EA01CI01"):
            if row.get(key) in self.by_code:
                return self.companies[self.by_code[row[key]]]
        if row.get("EA01AI01") in self.by_report:
            return self.companies[self.by_report[row["EA01AI01"]]]
        if row.get("cust_nm") in self.by_name:
            return self.companies[self.by_name[row["cust_nm"]]]
        return None

    def export(self):
        legal = Counter(c["legal"] for c in self.companies.values())
        controllers = Counter(c["controller"] for c in self.companies.values())
        names = defaultdict(set)
        for p in self.people.values():
            names[p.name].add(p.id)
        return {
            "revision": REVISION,
            "people": [asdict(p) for p in self.people.values()],
            "companies": list(self.companies.values()),
            "coverage": {
                "companies": len(self.companies),
                "people": len(self.people),
                "distinct_person_names": len(names),
                "homonym_pairs": sum(len(ids) * (len(ids) - 1) // 2 for ids in names.values()),
                "shared_legal_representatives": sum(n > 1 for n in legal.values()),
                "shared_controllers": sum(n > 1 for n in controllers.values()),
                "different_legal_and_controller": sum(
                    c["legal"] != c["controller"] for c in self.companies.values()
                ),
                "group_members": sum(c["group_id"] is not None for c in self.companies.values()),
                "groups": len({c["group_id"] for c in self.companies.values()} - {None}),
                "scenarios": dict(Counter(c["scenario"] for c in self.companies.values())),
            },
        }

    def transform(self, table, row):
        """Change declared person/role cells only; keep financial facts intact."""
        result = row.copy()
        company = self.company(row)

        def put(field, value, *, fill=False):
            if field in row and (fill or row[field] not in (None, "")):
                result[field] = value

        def role(person, name_fields=(), certificate_fields=()):
            for field in name_fields:
                put(field, person.name)
            for field in certificate_fields:
                put(field, person.certificate)

        # Personal reports are about their own subject, not the company's new representative.
        for cert, name in (("PA01BI01", "PA01BQ01"), ("id_number", "subject_name")):
            if row.get(cert) in self.by_certificate:
                role(self.by_certificate[row[cert]], (name,))
        if company is None:
            return result
        base, legal, controller = (self.people[company[k]] for k in ("base", "legal", "controller"))
        if table == "客户标签":
            for field, value in {
                "legal_rep_nm": legal.name,
                "legal_rep_cust_id": legal.id,
                "act_ctrl_psn_nm": controller.name,
                "act_ctrl_psn_cust_id": controller.id,
                "CST_MGRP_ID": company["group_id"],
                "CST_MGRP_NM": company["group_name"],
            }.items():
                put(field, value, fill=True)
            return result
        if table == "交易流水":
            # Historical agents remain the same people, even when roles differ.
            if row.get("agnc_psn_crdt_no") in self.by_certificate:
                role(self.by_certificate[row["agnc_psn_crdt_no"]], ("agnc_psn_nm",))
            # Funding accounts belong to the controlling natural-person shareholder.
            owner = controller if row.get("ev_ecd") == "FUNDING" else base
            for field in ("cntrprt_txn_accno_nm", "cntrprtbookentracnonm"):
                if row.get(field) == base.previous_name:
                    put(field, owner.name)
            return result
        role(legal, ("legal_rep_nm",))
        role(controller, ("act_ctrl_psn_nm",))
        if table == "PBCEC_EC02_CONTRIBUTIVE":
            role(controller, ("EC020Q01",), ("EC020I01",))
        elif table == "PBCEC_EC03_SENIOREXECUTIVE":
            role(legal, ("EC030Q01",), ("EC030I01",))
        elif table == "PBCEC_EC05_ACTUALCONTROLLER":
            role(controller, (), ("EC050I01",))
        elif table in {"VW_GSGR_RYPOSFR", "VW_GSGR_RYPOSPER"}:
            role(legal, ("RYNAME",), ("IDNT_CERT_ID",))
        elif table == "VW_GSGR_RYPOSSHA":
            role(controller, ("RYNAME",), ("IDNT_CERT_ID",))
        elif table == "T_SAIC_SHAREHOLDER" and row.get("INVTYPE") == "自然人股东":
            role(controller, ("SHANAME",))
        elif table == "T_SAIC_PERSON":
            role(legal, ("PERNAME",))
            put("SEX", "男" if int(legal.certificate[-2]) % 2 else "女")
        elif table in {"T_SAIC_FRPOSITION", "T_SAIC_FRINV"}:
            role(legal, ("NAME",))
        elif table == "T_SAIC_STOCKPAWN":
            role(controller, ("STK_PAWN_CZPER",), ("STK_PAWN_CZCERNO",))
        elif table in {"T_SAIC_PUNISHBREAK", "T_SAIC_PUNISHED"}:
            person = self.by_certificate.get(row.get("CARDNUM") or row.get("CARDNUMCLEAN"))
            if person:
                role(person, ("INAMECLEAN",))
                role(person if row.get("TYPE") == "自然人" else legal, ("BUSINESSENTITY",))
        elif table in {"T_SAIC_JUDICIALAID", "T_SAIC_JUDICIALAIDALTER", "T_SAIC_JUDICIALAIDDETAIL"}:
            # The frozen shareholding is in the named external investee.
            person = self.by_certificate.get(row.get("INAME_LICENCE") or row.get("LICENCE_NO"))
            if person:
                role(person, ("INAME",))
            elif row.get("INAME") == base.previous_name:
                put("INAME", base.name)
        # Personal risk observations keep the original subject/certificate.
        elif table.startswith("VW_GSGR_"):
            person = self.by_certificate.get(row.get("IDNT_CERT_ID"))
            if person:
                for key in ("BUSINESSENTITY", "NAME", "INAMECLEAN", "RYNAME"):
                    if row.get(key) == person.previous_name:
                        put(key, person.name)
        elif table == "T_SAIC_ENTINV" and row.get("NAME") == base.previous_name:
            # Legal representative of the investee, not of the investing company.
            put("NAME", base.name)
        return result
