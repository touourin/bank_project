"""Synthetic company profiles; weights are test assumptions, not bank statistics."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

SECTORS = (
    ("TECH", "信息科技", "软件开发、信息系统集成及技术咨询", 12500),
    ("MFG", "精密制造", "工业零部件、自动化设备的研发与制造", 8500),
    ("TRADE", "商贸", "日用百货、五金及电子产品批发", 7500),
    ("LOGISTICS", "供应链", "仓储服务、国内货物运输代理", 8000),
    ("FOOD", "食品", "食品加工、预包装食品销售", 6500),
    ("MEDICAL", "医疗科技", "医疗器械研发、技术咨询及销售", 11000),
)
REGIONS = (
    ("上海", "浦东", "SH", "PD"),
    ("上海", "闵行", "SH", "MH"),
    ("上海", "嘉定", "SH", "JD"),
    ("苏州", "工业园区", "JS", "SZ"),
    ("杭州", "滨江", "ZJ", "HZ"),
    ("宁波", "鄞州", "ZJ", "NB"),
)


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


@dataclass(frozen=True)
class Customer:
    index: int
    as_of: date
    profile: int
    sector: tuple
    region: tuple
    size: str
    employees: int
    registered_capital: Decimal
    deposit: Decimal
    credit_application: Decimal
    approval_ratio: Decimal
    utilization: Decimal
    repaid_ratio: Decimal
    onboard_days: int
    founded_days: int
    listed: bool
    group: int | None
    seed: int

    @property
    def customer_id(self) -> str:
        return f"MOCK_CUST_{self.index:04d}"

    @property
    def name(self) -> str:
        # A compositional name avoids copying a real company directory.
        first = "澄禾沐岚岳朔翊璟珩颂澜屿"
        second = "川原辰宁知禾润森远衡昕途"
        brand = first[(self.index - 1) // 12 % 12] + second[(self.index - 1) % 12]
        return f"{self.region[0]}{brand}{self.sector[1]}有限公司（模拟{self.index:04d}）"

    @property
    def account(self) -> str:
        return f"{self.index:020d}"

    @property
    def secondary_account(self) -> str:
        return f"{100000 + self.index:020d}"

    @property
    def branch(self) -> str:
        return f"{self.region[0]}{self.region[1]}模拟支行"

    @property
    def address(self) -> str:
        return f"{self.region[0]}市{self.region[1]}模拟产业园{self.index % 80 + 1}号"

    @property
    def credit_code(self) -> str:
        return f"MOCK{self.index:014d}"

    @property
    def limit(self) -> Decimal:
        return (
            money(self.credit_application * self.approval_ratio)
            if self.profile in {1, 2}
            else Decimal(0)
        )

    @property
    def disbursement(self) -> Decimal:
        return money(self.limit * self.utilization)

    @property
    def repayment(self) -> Decimal:
        return (
            self.disbursement if self.profile == 2 else money(self.disbursement * self.repaid_ratio)
        )

    @property
    def loan_balance(self) -> Decimal:
        return self.disbursement - self.repayment

    def ago(self, days: int) -> date:
        return self.as_of - timedelta(days=days)


def make_customer(index: int, as_of: date, rng: random.Random) -> Customer:
    # The first 20 ensure scenario coverage for small development fixtures.
    profile = (index - 1) % 5 if index <= 20 else rng.choices(range(5), [28, 35, 12, 7, 18])[0]
    size = rng.choices(["SMALL", "MEDIUM", "LARGE"], [72, 24, 4])[0]
    listed = index == 1 or (size == "LARGE" and rng.random() < 0.2)
    if listed:
        size = "LARGE"
    employees_low, employees_high, capital_low, capital_high = {
        "SMALL": (8, 90, 3, 50),
        "MEDIUM": (90, 500, 50, 500),
        "LARGE": (500, 2400, 500, 3000),
    }[size]
    capital = Decimal(rng.randint(capital_low, capital_high) * 100000)
    sector = rng.choice(SECTORS)
    deposit = money(capital * Decimal(rng.randint(2, 65)) / 100 + Decimal(rng.randint(0, 99)) / 100)
    if index > 20 and profile == 4 and rng.random() < 0.25:
        deposit = Decimal(0)
    onboard_days = rng.randint(400, 1600)
    if index > 20 and profile == 4 and rng.random() < 0.4:
        onboard_days = rng.randint(35, 120)
    return Customer(
        index=index,
        as_of=as_of,
        profile=profile,
        sector=sector,
        region=rng.choice(REGIONS),
        size=size,
        employees=rng.randint(employees_low, employees_high),
        registered_capital=capital,
        deposit=deposit,
        credit_application=Decimal(rng.randint(3, 20)) * capital / 10,
        approval_ratio=Decimal(rng.randint(60, 100)) / 100,
        utilization=Decimal(rng.randint(25, 90)) / 100,
        repaid_ratio=Decimal(rng.randint(10, 65)) / 100,
        onboard_days=onboard_days,
        founded_days=onboard_days + rng.randint(365, 6500),
        listed=listed,
        group=rng.randint(1, 60) if rng.random() < 0.22 else None,
        seed=rng.getrandbits(64),
    )
