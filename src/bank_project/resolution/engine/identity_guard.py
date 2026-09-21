"""Conservative name-level identity checks for periods, meetings and model numbers.

These checks deliberately ignore numbers in general descriptions: an organization
can appear in reports from different years without becoming a different entity.
Missing specificity is unknown, never filled from a model-generated description.
It blocks merging until source identity information is aligned, without claiming
that the records have been proven to describe different entities.
"""

import re
import unicodedata
from calendar import monthrange

VERSION = "identity-dimensions-v2"
NUMBER = r"[0-9零〇一二三四五六七八九十百两]+"


def integer(value):
    value = unicodedata.normalize("NFKC", value)
    if value.isdigit():
        return int(value)
    digits = dict(
        zip("零〇一二三四五六七八九两", (0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2), strict=True)
    )
    digits.update({str(i): i for i in range(10)})
    if "十" not in value and "百" not in value:
        return int("".join(str(digits[c]) for c in value))
    total, current = 0, 0
    for char in value:
        if char in digits:
            current = digits[char]
        else:
            total += (current or 1) * {"十": 10, "百": 100}[char]
            current = 0
    return total + current


def dimensions(mention):
    name = unicodedata.normalize("NFKC", mention.name).strip()
    compact = re.sub(r"\s+", "", name)
    kind = mention.type.casefold().replace(" ", "")
    if kind in {
        "organization",
        "organisation",
        "company",
        "组织机构",
        "公司",
        "企业",
        "person",
        "人物",
        "人员",
        "geo",
        "location",
        "地点",
    }:
        return {}
    if any(word in compact for word in ("报告", "年报", "季报", "财报")):
        category = "report"
    elif "会议" in compact or re.search(r"第" + NUMBER + r"[届次]", compact):
        category = "meeting"
    elif kind in {"财务指标", "financialmetric", "metric"}:
        category = "metric"
    elif kind in {"产品或技术", "product", "technology", "产品"}:
        category = "product"
    elif kind in {"事件", "event", "项目或合同", "contract"}:
        category = "event"
    else:
        return {}
    result = {"category": category}
    if category == "product":
        # The prefix matters: A100 != H100. Keep suffix/version digits as well.
        models = re.findall(r"(?<![A-Za-z0-9])[A-Za-z]+[- ]?\d+(?:[.\-]\d+)*[A-Za-z]*", name)
        versions = re.findall(r"(?:版本?|[vV])\s*(\d+(?:\.\d+)+)", name)
        values = sorted(
            {re.sub(r"[ -]", "", v).upper() for v in models} | {"V" + v for v in versions}
        )
        if values:
            result["产品型号或版本"] = values
        return result
    years = sorted({int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", compact)})
    years = sorted(
        set(years)
        | {integer(y) for y in re.findall(r"([一二][零〇一二三四五六七八九]{3})年", compact)}
    )
    if years:
        result["年份"] = years
    if category == "meeting":
        for suffix, label in (("届", "届次"), ("次", "会议次数")):
            values = sorted(
                {integer(v) for v in re.findall(r"第(" + NUMBER + ")" + suffix, compact)}
            )
            if values:
                result[label] = values
    bodies = sorted(
        {word for word in ("董事会", "监事会", "股东大会", "股东会") if word in compact}
    )
    if bodies:
        result["机构职责"] = bodies
    # Distinguish explicit periods only. A bare year does not mean year-end.
    periods, dates, months = set(), set(), set()
    for month, day in re.findall(r"(" + NUMBER + r")月(?:(" + NUMBER + r")日|(?:末|底))", compact):
        m = integer(month)
        if not 1 <= m <= 12:
            continue
        if day:
            d = integer(day)
            # February without a known year cannot safely be normalized to month-end.
            ends = (
                {monthrange(y, m)[1] for y in years}
                if years
                else {monthrange(2000, m)[1], monthrange(2001, m)[1]}
            )
            if ends == {d}:
                dates.add(f"{m}月末")
            else:
                dates.add(f"{m}月{d}日")
        else:
            dates.add(f"{m}月末")
    if "年末" in compact or "年底" in compact:
        dates.add("12月末")
        months.add(12)
    # Bare month precision is useful for events; retain it as a separate dimension.
    months.update(
        integer(m) for m in re.findall(r"(" + NUMBER + r")月", compact) if 1 <= integer(m) <= 12
    )
    if months:
        result["月份"] = sorted(months)
    if "半年报" in compact or "半年度" in compact or "上半年" in compact:
        periods.add("上半年")
    elif "下半年" in compact:
        periods.add("下半年")
    elif "年报" in compact or "年度" in compact or "全年" in compact:
        periods.add("全年")
    for quarter in re.findall(r"第?([一二三四1234])季度", compact):
        periods.add(f"第{integer(quarter)}季度")
    if periods:
        result["报告期间"] = sorted(periods)
    if dates:
        result["观测时点"] = sorted(dates)
    return result


def assess_identity(left, right):
    """Return a no-merge boundary; absence never proves same identity."""
    a, b = dimensions(left), dimensions(right)
    if not a or not b:
        return None
    conflicts, missing = [], []
    for key in (
        "年份",
        "届次",
        "会议次数",
        "机构职责",
        "产品型号或版本",
        "月份",
        "报告期间",
        "观测时点",
    ):
        x, y = a.get(key), b.get(key)
        if x and y and not set(x) & set(y):
            conflicts.append({"field": key, "left": x, "right": y})
        elif set(x or []) != set(y or []):
            missing.append({"field": key, "left": x or [], "right": y or []})
    if conflicts:
        return {
            "version": VERSION,
            "verdict": "different",
            "block_merge": True,
            "reason": "IDENTITY_DIMENSION_CONFLICT",
            "differences": conflicts,
            "message": "身份字段冲突（"
            + "、".join(c["field"] for c in conflicts)
            + "），应保留为不同实体",
        }
    if missing:
        return {
            "version": VERSION,
            "verdict": "uncertain",
            "block_merge": True,
            "reason": "IDENTITY_DIMENSION_UNSPECIFIED",
            "differences": missing,
            "message": "身份限定不完整或粒度不一致（"
            + "、".join(c["field"] for c in missing)
            + "），缺少证明为同一实体的依据，暂不合并；保留独立节点",
        }
    return None
