"""Synthetic statement templates with explicit arithmetic, not independent random totals."""

from decimal import Decimal

from .common import dec

# Each formula is a sum of signed source line numbers. Nested "of which" lines
# are excluded from totals. These templates are mock accounting assumptions.
FORMULAS = {
    1: {
        19: list(range(1, 13)) + [15, 16, 17, 18],
        20: [21, 22],
        24: [20, 23],
        27: [25, -26],
        29: [27, -28],
        34: [29, 30, 31, 32, 33],
        42: [35, 37, 40],
        44: [19, 24, 34, 42, 43],
        59: list(range(45, 59)),
        66: [60, 61, 62, 63, 64],
        68: [59, 66, 67],
        70: [71, 72, 73, 76, 77],
        84: [44, -68, -69, -70, -78, -79, 83, -85],
        86: [70, 78, 79, -83, 84, 85],
        87: [68, 69, 86],
    },
    2: {
        12: list(range(1, 12)),
        30: list(range(13, 30)),
        31: [12, 30],
        44: list(range(32, 44)),
        52: list(range(45, 52)),
        53: [44, 52],
        58: [31, -53, -54, -55, 56, -57],
        59: [54, 55, -56, 57, 58],
        60: [53, 59],
    },
    3: {
        5: [1, -4],
        14: [5, -6, -8, -9, -10, 11, 12, 13],
        20: [14, 15, -16, -17, -18, -19],
        39: [20, 21, 22, 23, 25, 30, -32, -37],
        43: [39, -40, -41, 42],
        47: [43, 44, 45, 46],
        57: [47, -48, -49, -50, -51, -52, -53, -54, -55, -56],
        63: [57, -58, -59, -60, -61, -62],
    },
    4: {11: [1, -2, -3, -4, -5, -6, -7, 8, 9], 15: [11, 12, -13], 17: [15, -16]},
    5: {
        4: [1, 2, 3],
        9: [5, 6, 7, 8],
        10: [4, -9],
        15: [11, 12, 13, 14],
        19: [16, 17, 18],
        20: [15, -19],
        24: [21, 22, 23],
        28: [25, 26, 27],
        29: [24, -28],
        31: [10, 20, 29, 30],
        47: [10] + [-x for x in range(32, 47)],
        48: list(range(32, 48)),
        53: [31, 54, -55, 56],
        57: [53, -54, 55, -56],
    },
    6: {
        4: [1, 2, 3],
        9: [5, 6, 7, 8],
        10: [4, -9],
        16: [11, 12, 13, 14, 15],
        21: [17, 18, 19, 20],
        22: [16, -21],
        26: [23, 24, 25],
        30: [27, 28, 29],
        31: [26, -30],
        33: [10, 22, 31, 32],
        35: [34, 33],
        53: [10] + [-x for x in range(36, 53)],
        54: list(range(36, 54)),
        58: [35, -60],
        59: [34, -61],
        62: [58, -59, 60, -61],
    },
    7: {
        12: list(range(1, 12)),
        23: list(range(13, 23)),
        24: [12, 23],
        33: list(range(25, 33)),
        34: [35, 36],
        41: [34, 37, 38, 39, 40],
        48: [24, -33, -41] + [-x for x in range(42, 48)],
        49: list(range(42, 49)),
        50: [33, 41, 49],
    },
    8: {
        10: list(range(1, 10)),
        12: [13, -14],
        16: [17, -18],
        20: [11, 12, 15, 16, 19],
        21: [10, 20],
        32: list(range(22, 32)),
        35: [33, 34],
        36: [32, 35],
        45: [21, -36] + [-x for x in range(37, 45)],
        46: list(range(37, 46)),
        47: [36, 46],
    },
    9: {
        7: [1, 2, 3, 4, 6],
        9: [8],
        11: [10],
        12: [7, 9, 11],
        21: [13, 14, 15, 16, 19, 20],
        24: [22, 23],
        27: [25, 26],
        28: [21, 24, 27],
        29: [7, -21],
        30: [29, -31],
        32: [9, -24],
        34: [35, 36, 37, 38],
    },
    10: {
        1: [2, -3],
        5: [6, 7, 8, 9],
        11: [12, 13, 14, 15],
        4: [5, -11],
        16: [17, -18],
        19: [16],
        20: [4, 19],
        22: [20, -21],
        25: [22, -23, -24],
    },
}


def statement_values(table: dict, subject) -> tuple[dict, list[dict]]:
    number = int(table["sheet"][8:10])
    prefix = f"EG{number:02d}BJ"
    fields = [f["source_key"] for f in table["columns"] if f["source_key"].startswith(prefix)]
    base = (dec(subject.tag["cert_capt_amt"]) / 100).quantize(Decimal("0.01"))
    values = {int(name[-2:]): base * (1 + int(name[-2:]) % 3) for name in fields}
    # Reserve enough operating income / cash to avoid contrived negative cash.
    if number in {3, 4}:
        values[1] = base * 100
        values[6 if number == 3 else 2] = base * 50
    if number in {5, 6}:
        values[1] = base * 100
        values[54 if number == 5 else 34] = base * 100
    if number == 1:
        for child in (2, 5, 13, 26, 28, 36, 38, 39, 41, 65, 74, 75, 80, 81, 82):
            values[child] = Decimal(0)
    if number == 8:
        values[13] = base * 10
        values[17] = base * 5
    if number == 7:
        values[1] = base * 100
    if number == 9:
        values[4] = base * 50
    if number == 10:
        values[2] = base * 20
        values[6] = base * 50
    equations = []
    for target, terms in FORMULAS[number].items():
        values[target] = sum((values[abs(t)] * (1 if t > 0 else -1) for t in terms), Decimal(0))
        equations.append(
            dict(
                target=f"{prefix}{target:02d}",
                terms=[dict(field=f"{prefix}{abs(t):02d}", sign=1 if t > 0 else -1) for t in terms],
            )
        )
    return {f"{prefix}{i:02d}": v.quantize(Decimal("0.01")) for i, v in values.items()}, equations
