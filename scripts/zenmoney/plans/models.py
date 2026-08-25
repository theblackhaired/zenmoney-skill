from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from numbers import Real
from typing import Any, Literal

from ..errors import InvalidArgumentError


EventKind = Literal["income", "outcome", "transfer"]
EventSource = Literal["transaction", "reminder_marker"]


def decimal_amount(
    value: int | float | Decimal,
    field: str,
    *,
    non_negative: bool = False,
) -> Decimal:
    requirement = "finite non-negative number" if non_negative else "finite number"
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        raise InvalidArgumentError(f"{field} must be a {requirement}")
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    if not amount.is_finite() or (non_negative and amount < 0):
        raise InvalidArgumentError(f"{field} must be a {requirement}")
    return amount


@dataclass(frozen=True, slots=True)
class AccountSide:
    account_id: str
    amount: Decimal
    currency: Any
    in_balance: bool
    archived: bool
    known_account: bool
    account_type: str | None = None
    account_subtype: str | None = None
    credit_limit: int | float = 0
    savings: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "amount",
            decimal_amount(self.amount, "amount", non_negative=True),
        )


@dataclass(frozen=True, slots=True)
class PlanEvent:
    source_id: str
    source_type: EventSource
    date: str
    kind: EventKind
    outcome_side: AccountSide | None
    income_side: AccountSide | None
    category_ids: tuple[str, ...]
    marker_state: str | None
    is_forecast: bool


@dataclass(frozen=True, slots=True)
class CategoryBucket:
    category_id: str
    name: str
    parent_id: str | None


@dataclass(frozen=True, slots=True)
class PlanRowSide:
    fact: Decimal = Decimal(0)
    fact_with_refund: Decimal | None = None
    planned: Decimal = Decimal(0)
    processed: Decimal = Decimal(0)
    explicit_budget: Decimal = Decimal(0)
    lock: bool = False
    effective_budget: Decimal = Decimal(0)
    residue: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fact", decimal_amount(self.fact, "fact"))
        object.__setattr__(
            self,
            "fact_with_refund",
            self.fact
            if self.fact_with_refund is None
            else decimal_amount(self.fact_with_refund, "fact_with_refund"),
        )
        for field in (
            "planned",
            "processed",
            "explicit_budget",
            "effective_budget",
            "residue",
        ):
            object.__setattr__(self, field, decimal_amount(getattr(self, field), field))


@dataclass(frozen=True, slots=True)
class PlanCategoryRow:
    category: CategoryBucket
    income: PlanRowSide
    outcome: PlanRowSide
    children: tuple[PlanCategoryRow, ...] = ()
