from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from ..errors import InvalidArgumentError, UnsupportedCalculationError


DifferenceMode = Literal["REFUNDS", "INCOME_OUTCOME_AND_REFUNDS", "NONE"]

MODES: tuple[DifferenceMode, ...] = (
    "REFUNDS",
    "INCOME_OUTCOME_AND_REFUNDS",
    "NONE",
)
ZERO = Decimal(0)
_UNCATEGORIZED = {"uncategorized", "__uncategorized__"}


def apply_category_difference(
    *,
    income: Mapping[str, Any],
    outcome: Mapping[str, Any],
    categories: Mapping[str, Mapping[str, Any]],
    mode: DifferenceMode,
    refund_income: Mapping[str, Any] | None = None,
    refund_outcome: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Decimal] | dict[str, dict[str, Decimal]]]:
    """Return self values whose tree sums match the ZenMoney difference policy.

    The APK applies INCOME_OUTCOME_AND_REFUNDS after category-tree aggregation.
    Consequently a parent's adjusted self value may be negative: adding it to
    the already-adjusted children yields the exact adjusted parent total.
    """
    selected_mode = _mode(mode)
    raw_income = _amounts(income, "income")
    raw_outcome = _amounts(outcome, "outcome")
    eligible_income = _amounts(
        raw_income if refund_income is None else refund_income,
        "refund_income",
    )
    eligible_outcome = _amounts(
        raw_outcome if refund_outcome is None else refund_outcome,
        "refund_outcome",
    )
    category_rows = {str(key): dict(value) for key, value in categories.items()}
    keys = set(category_rows) | set(raw_income) | set(raw_outcome)
    children: dict[str | None, list[str]] = defaultdict(list)
    for key in keys:
        parent = category_rows.get(key, {}).get("parent")
        children[str(parent) if parent is not None and str(parent) in keys else None].append(key)

    adjusted_income: dict[str, Decimal] = {}
    adjusted_outcome: dict[str, Decimal] = {}
    total_income: dict[str, Decimal] = {}
    total_outcome: dict[str, Decimal] = {}
    visited: set[str] = set()

    def visit(
        key: str,
        visiting: frozenset[str],
    ) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]:
        if key in visiting:
            raise InvalidArgumentError("Category difference tree contains a cycle")
        child_values = [visit(child, visiting | {key}) for child in children.get(key, [])]
        raw_income_total = raw_income.get(key, ZERO) + sum((row[0] for row in child_values), ZERO)
        raw_outcome_total = raw_outcome.get(key, ZERO) + sum((row[1] for row in child_values), ZERO)
        refund_income_total = eligible_income.get(key, ZERO) + sum(
            (row[2] for row in child_values), ZERO
        )
        refund_outcome_total = eligible_outcome.get(key, ZERO) + sum(
            (row[3] for row in child_values), ZERO
        )
        desired_income, desired_outcome = _adjust(
            key,
            raw_income_total,
            raw_outcome_total,
            refund_income_total,
            refund_outcome_total,
            category_rows.get(key),
            selected_mode,
        )
        child_income = sum((row[4] for row in child_values), ZERO)
        child_outcome = sum((row[5] for row in child_values), ZERO)
        adjusted_income[key] = desired_income - child_income
        adjusted_outcome[key] = desired_outcome - child_outcome
        total_income[key] = desired_income
        total_outcome[key] = desired_outcome
        visited.add(key)
        return (
            raw_income_total,
            raw_outcome_total,
            refund_income_total,
            refund_outcome_total,
            desired_income,
            desired_outcome,
        )

    for root in sorted(children.get(None, [])):
        visit(root, frozenset())
    for key in sorted(keys - visited):
        visit(key, frozenset())

    return {
        "income": adjusted_income,
        "outcome": adjusted_outcome,
        "totals": {"income": total_income, "outcome": total_outcome},
    }


def _adjust(
    key: str,
    income: Decimal,
    outcome: Decimal,
    refund_income: Decimal,
    refund_outcome: Decimal,
    category: Mapping[str, Any] | None,
    mode: DifferenceMode,
) -> tuple[Decimal, Decimal]:
    if mode == "NONE":
        return income, outcome
    if mode == "INCOME_OUTCOME_AND_REFUNDS":
        difference = income - outcome
        return max(difference, ZERO), max(-difference, ZERO)
    if income == ZERO and outcome == ZERO:
        return ZERO, ZERO
    if key in _UNCATEGORIZED:
        return income, outcome
    if category is None or type(category.get("showIncome")) is not bool or type(category.get("showOutcome")) is not bool:
        raise UnsupportedCalculationError(
            "REFUNDS requires synced Tag.showIncome and Tag.showOutcome",
            {"category_id": key},
        )
    show_income = category["showIncome"]
    show_outcome = category["showOutcome"]
    if show_outcome and not show_income:
        applied = min(income, refund_income)
        return income - applied, outcome - applied
    if show_income and not show_outcome:
        applied = min(outcome, refund_outcome)
        return income - applied, outcome - applied
    return income, outcome


def _amounts(values: Mapping[str, Any], field: str) -> dict[str, Decimal]:
    if not isinstance(values, Mapping):
        raise InvalidArgumentError(f"{field} must be an object")
    return {str(key): _money(value, f"{field}[{key}]") for key, value in values.items()}


def _mode(value: Any) -> DifferenceMode:
    if value not in MODES:
        raise InvalidArgumentError(f"difference mode must be one of: {', '.join(MODES)}")
    return value


def _money(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise InvalidArgumentError(f"{field} must be a finite Decimal-compatible number")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidArgumentError(f"{field} must be a finite Decimal-compatible number") from exc
    if not result.is_finite() or result < ZERO:
        raise InvalidArgumentError(f"{field} must be a finite non-negative number")
    return result
