from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from ..errors import InvalidArgumentError


BudgetMethod = Literal["BUDGET", "MEAN"]
GroupBy = Literal["TAG", "PAYEE"]

ZERO = Decimal(0)
HUNDRED = Decimal(100)
METHODS: tuple[BudgetMethod, ...] = ("BUDGET", "MEAN")
GROUPS: tuple[GroupBy, ...] = ("TAG", "PAYEE")


def render_category_report(
    rows: list[dict[str, Any]],
    *,
    categories: list[dict[str, Any]] | None = None,
    budgets: dict[str, Any] | None = None,
    comparison_periods: list[dict[str, Any]] | None = None,
    group_by: GroupBy = "TAG",
    budget_method: BudgetMethod = "BUDGET",
) -> dict[str, Any]:
    group = _group_by(group_by)
    requested_method = _budget_method(budget_method)
    effective_method = "MEAN" if group == "PAYEE" else requested_method
    category_tree = _category_tree(categories or [])
    budget_values = {} if group == "PAYEE" else _budget_values(budgets or {})
    comparisons = _comparison_values(comparison_periods or [])
    amounts = _amounts(rows, group)

    if group == "PAYEE":
        roots = [
            _leaf(key, key, key, None, None, amounts[key], budget_values, comparisons, effective_method)
            for key in sorted(set(amounts) | set(budget_values))
        ]
    else:
        roots = _category_roots(category_tree, amounts, budget_values, comparisons, effective_method)

    return {
        "group_by": group,
        "budget_method": effective_method,
        "items": roots,
    }


def _amounts(rows: list[dict[str, Any]], group_by: GroupBy) -> dict[str, Decimal]:
    result: dict[str, Decimal] = defaultdict(lambda: ZERO)
    if not isinstance(rows, list):
        raise InvalidArgumentError("rows must be a list")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise InvalidArgumentError(f"rows[{index}] must be an object")
        key_field = "payee" if group_by == "PAYEE" else "category_id"
        key = row.get(key_field)
        if not isinstance(key, str) or not key:
            raise InvalidArgumentError(f"rows[{index}].{key_field} must be a non-empty string")
        result[key] += _money(row.get("amount", 0), f"rows[{index}].amount")
    return dict(result)


def _category_tree(categories: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    children: dict[str | None, list[str]] = defaultdict(list)
    if not isinstance(categories, list):
        raise InvalidArgumentError("categories must be a list")
    for index, raw in enumerate(categories):
        if not isinstance(raw, dict):
            raise InvalidArgumentError(f"categories[{index}] must be an object")
        category_id = raw.get("id")
        if not isinstance(category_id, str) or not category_id:
            raise InvalidArgumentError(f"categories[{index}].id must be a non-empty string")
        if category_id in result:
            raise InvalidArgumentError(f"duplicate category key: {category_id}")
        parent = raw.get("parent")
        if parent is not None and (not isinstance(parent, str) or not parent):
            raise InvalidArgumentError(f"categories[{index}].parent must be a non-empty string")
        result[category_id] = {
            "id": category_id,
            "parent": parent,
            "title": str(raw.get("title") or category_id),
            "icon": raw.get("icon"),
            "color": raw.get("color"),
        }
        children[parent].append(category_id)
    for sibling_ids in children.values():
        if len(sibling_ids) != len(set(sibling_ids)):
            raise InvalidArgumentError("duplicate child category key")
    for category_id, row in result.items():
        row["children"] = tuple(children.get(category_id, ()))
    return result


def _category_roots(
    categories: dict[str, dict[str, Any]],
    amounts: dict[str, Decimal],
    budgets: dict[str, Decimal],
    comparisons: list[dict[str, Decimal]],
    method: BudgetMethod,
) -> list[dict[str, Any]]:
    known = set(categories)
    keys = set(amounts) | set(budgets)
    missing = sorted(keys - known)
    for category_id in missing:
        categories[category_id] = {
            "id": category_id,
            "parent": None,
            "title": category_id,
            "icon": None,
            "color": None,
            "children": (),
        }
    roots = [
        category_id
        for category_id, row in categories.items()
        if row["parent"] is None or row["parent"] not in categories
    ]
    return [
        _category_node(category_id, categories, amounts, budgets, comparisons, method)
        for category_id in sorted(roots, key=lambda item: categories[item]["title"])
    ]


def _category_node(
    category_id: str,
    categories: dict[str, dict[str, Any]],
    amounts: dict[str, Decimal],
    budgets: dict[str, Decimal],
    comparisons: list[dict[str, Decimal]],
    method: BudgetMethod,
) -> dict[str, Any]:
    row = categories[category_id]
    children = [
        _category_node(child_id, categories, amounts, budgets, comparisons, method)
        for child_id in sorted(row["children"], key=lambda item: categories[item]["title"])
    ]
    if children:
        amount = sum((child["amount"] for child in children), ZERO) + amounts.get(category_id, ZERO)
        budget = sum((child["budget"] for child in children), ZERO)
        budget_source = method
    else:
        amount = amounts.get(category_id, ZERO)
        budget, budget_source = _budget(category_id, budgets, comparisons, method)
    return _item(
        key=category_id,
        title=row["title"],
        full_path=_full_path(category_id, categories),
        icon=row["icon"],
        color=row["color"],
        amount=amount,
        budget=budget,
        budget_method=budget_source,
        children=children,
    )


def _leaf(
    key: str,
    title: str,
    full_path: str,
    icon: Any,
    color: Any,
    amount: Decimal,
    budgets: dict[str, Decimal],
    comparisons: list[dict[str, Decimal]],
    method: BudgetMethod,
) -> dict[str, Any]:
    budget, source = _budget(key, budgets, comparisons, method)
    return _item(
        key=key,
        title=title,
        full_path=full_path,
        icon=icon,
        color=color,
        amount=amount,
        budget=budget,
        budget_method=source,
        children=[],
    )


def _item(
    *,
    key: str,
    title: str,
    full_path: str,
    icon: Any,
    color: Any,
    amount: Decimal,
    budget: Decimal,
    budget_method: str,
    children: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "full_path": full_path,
        "icon": icon,
        "color": color,
        "amount": amount,
        "budget": budget,
        "budget_method": budget_method,
        "budget_diff": budget - amount,
        "budget_percent": ZERO if budget == ZERO else (budget - amount) / budget * HUNDRED,
        "children": children,
    }


def _budget(
    key: str,
    budgets: dict[str, Decimal],
    comparisons: list[dict[str, Decimal]],
    method: BudgetMethod,
) -> tuple[Decimal, str]:
    explicit = budgets.get(key, ZERO)
    if method == "BUDGET" or explicit != ZERO:
        return explicit, "BUDGET"
    values = [period.get(key, ZERO) for period in comparisons]
    if not values or any(value == ZERO for value in values):
        return ZERO, "MEAN"
    return sum(values, ZERO) / len(values), "MEAN"


def _budget_values(raw: dict[str, Any]) -> dict[str, Decimal]:
    if not isinstance(raw, dict):
        raise InvalidArgumentError("budgets must be an object")
    return {str(key): _money(value, f"budgets[{key}]") for key, value in raw.items()}


def _comparison_values(raw: list[dict[str, Any]]) -> list[dict[str, Decimal]]:
    if not isinstance(raw, list):
        raise InvalidArgumentError("comparison_periods must be a list")
    result = []
    for index, period in enumerate(raw):
        if not isinstance(period, dict):
            raise InvalidArgumentError(f"comparison_periods[{index}] must be an object")
        result.append({
            str(key): _money(value, f"comparison_periods[{index}][{key}]")
            for key, value in period.items()
        })
    return result


def _full_path(category_id: str, categories: dict[str, dict[str, Any]]) -> str:
    parts = []
    seen = set()
    current = category_id
    while current in categories and current not in seen:
        seen.add(current)
        row = categories[current]
        parts.append(row["title"])
        parent = row["parent"]
        if parent is None:
            break
        current = parent
    return " / ".join(reversed(parts))


def _group_by(value: Any) -> GroupBy:
    if value not in GROUPS:
        raise InvalidArgumentError("group_by must be TAG or PAYEE")
    return value


def _budget_method(value: Any) -> BudgetMethod:
    if value not in METHODS:
        raise InvalidArgumentError("budget_method must be BUDGET or MEAN")
    return value


def _money(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise InvalidArgumentError(f"{field} must be a finite Decimal-compatible number")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidArgumentError(f"{field} must be a finite Decimal-compatible number") from exc
    if not amount.is_finite():
        raise InvalidArgumentError(f"{field} must be a finite Decimal-compatible number")
    return amount
