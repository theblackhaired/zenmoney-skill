from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from .models import PlanCategoryRow, PlanRowSide


ZERO = Decimal(0)
RESIDUE_EPSILON = Decimal("0.01")


def _own_residue(
    *, effective_budget: Decimal, fact_with_refund: Decimal, planned: Decimal
) -> Decimal:
    if abs(effective_budget) < RESIDUE_EPSILON:
        return ZERO
    return max(ZERO, planned, effective_budget - fact_with_refund)


def _calculate_side(
    side: PlanRowSide, children: tuple[PlanRowSide, ...]
) -> PlanRowSide:
    fact = side.fact + sum((child.fact for child in children), start=ZERO)
    fact_with_refund = side.fact_with_refund + sum(
        (child.fact_with_refund for child in children), start=ZERO
    )
    planned = side.planned + sum((child.planned for child in children), start=ZERO)
    processed = side.processed + sum(
        (child.processed for child in children), start=ZERO
    )

    effective_budget = side.explicit_budget
    if not side.lock:
        effective_budget += side.planned + side.processed
        effective_budget += sum(
            (child.effective_budget for child in children), start=ZERO
        )

    own_residue = _own_residue(
        effective_budget=effective_budget,
        fact_with_refund=fact_with_refund,
        planned=planned,
    )
    residue = max(
        own_residue,
        sum((child.residue for child in children), start=ZERO),
    )
    return replace(
        side,
        fact=fact,
        fact_with_refund=fact_with_refund,
        planned=planned,
        processed=processed,
        effective_budget=effective_budget,
        residue=residue,
    )


def calculate_row(row: PlanCategoryRow) -> PlanCategoryRow:
    children = tuple(calculate_row(child) for child in row.children)
    return replace(
        row,
        income=_calculate_side(row.income, tuple(child.income for child in children)),
        outcome=_calculate_side(
            row.outcome, tuple(child.outcome for child in children)
        ),
        children=children,
    )
