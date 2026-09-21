"""Explicit financial distinctions used before candidate ranking, never to merge.

Only primary names of known financial records supply exclusions. Unknown metric
names, mixed measures and missing periods remain eligible for later evidence
checks; descriptions and aliases cannot supply missing identity dimensions.
"""

import re
import unicodedata

from .identity_guard import NUMBER, dimensions

METRIC_NAMES = {
    "收入": ("营业收入", "营收", "收入"),
    "净利润": ("净利润", "净利"),
    "毛利": ("毛利润", "毛利"),
    "股本": ("总股本", "股本总额", "股份总数"),
    "应收账款": ("应收账款", "应收帐款"),
    "开发支出": ("开发支出",),
    "审计费用": ("审计费用", "审计费"),
    "担保额度": ("担保额度总金额", "担保总额度", "担保额度"),
    "担保余额": ("担保总余额", "担保余额"),
}
REPORT_ENDINGS = ("年报", "半年报", "季报", "年度报告", "半年度报告", "财务报告", "财报")
PERIOD_FIELDS = ("年份", "月份", "报告期间", "观测时点")


def financial_periods(mention, name):
    """Use the existing normalizer only when its single values are unambiguous."""
    # Bare four-digit amounts and shortened ranges (2024/25年) are not years.
    year_pattern = r"(?<!\d)(?:19|20)\d{2}(?!\d)|[一二][零〇一二三四五六七八九]{3}"
    if any(not name[m.end() :].startswith("年") for m in re.finditer(year_pattern, name)):
        return {}
    if re.search(r"(?:19|20)\d{2}年(?:[、,，/—–~～\-－]|至|到|及|与|和)\d{2}年", name):
        return {}
    identity = dimensions(mention)
    # Do not infer a range's meaning from its endpoints (2020-2022 includes 2021).
    if len(identity.get("年份", [])) > 1:
        return {}
    result = {"年份": identity["年份"][0]} if identity.get("年份") else {}
    # Omitted units and coordinated periods can fool the older normalizer into
    # returning a singleton: 第一、第二季度, 1-9月, 上半年及下半年.
    ambiguous = (
        re.search(
            NUMBER
            + r"(?:年|月[末底]?|日|季度)?(?:[、,，/—–~～\-－]|至|到|以及|及|与|和)第?"
            + NUMBER,
            name,
        )
        or re.search(r"[一二三四1234]{2,}季度", name)
        or ("上半年" in name and "下半年" in name)
        or re.search(r"上[、及与和/]?下半年", name)
        # 半年末 contains 年末 but is not necessarily December 31.
        or re.search(r"半年[末底]", name)
    )
    if not ambiguous:
        for field in PERIOD_FIELDS[1:]:
            values = identity.get(field, [])
            if len(values) == 1:
                result[field] = values[0]
    return result


def financial_dimensions(mention):
    """Return only unambiguous dimensions that can exclude a financial pair."""
    kind = re.sub(r"\s+", "", mention.type).casefold()
    if kind not in {"财务指标", "financialmetric", "metric"}:
        return {}
    name = re.sub(r"\s+", "", unicodedata.normalize("NFKC", mention.name))
    result = financial_periods(mention, name)

    measures = {
        metric for metric, aliases in METRIC_NAMES.items() if any(a in name for a in aliases)
    }
    # Multiple measures, ratios and changes are not a single base measure.
    # An unknown suffix must not be stripped: 营收增长率 is not 营收.
    # 净收入 may mean net profit; the generic 收入 suffix cannot settle that.
    if name.endswith("净收入"):
        return result
    if len(measures) == 1:
        metric = next(iter(measures))
        if name.endswith(METRIC_NAMES[metric]):
            result["指标种类"] = metric
    elif not measures and name.endswith(REPORT_ENDINGS):
        result["指标种类"] = "报告"
    return result


def financial_conflicts(left, right):
    """Compare precomputed dimensions; absence or equal values never prove same."""
    return tuple(key for key in left.keys() & right.keys() if left[key] != right[key])
