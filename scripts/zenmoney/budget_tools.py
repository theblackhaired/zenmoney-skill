from __future__ import annotations

import datetime
import json
from typing import Any

from . import cache as _cache
from . import config
from .config import _cfg_path
from .domain import (
    ALL_CATEGORIES_ID,
    DEFAULT_INCOME_VS_EXPENSE,
    _BUDGET_MODE_DEFAULTS,
    _fmt_budget,
    _now_ts,
    _today,
    _tx_type,
    classify_transfer,
)
from .errors import EntityNotFoundError, InvalidArgumentError
from .transport import _write_diff
from .validation import require_user_or_account_owner, validate_tool_args


def _is_deleted_marker(marker: dict) -> bool:
    return marker.get("deleted") or marker.get("state") == "deleted"


def _is_planned_marker(marker: dict) -> bool:
    return not _is_deleted_marker(marker) and marker.get("state", "planned") == "planned"


def _is_processed_marker(marker: dict) -> bool:
    return not _is_deleted_marker(marker) and marker.get("state") == "processed"


def _budget_cache_key(category_id: str, month_date: str) -> str:
    return f"{category_id}:{month_date}"


def _category_display_name(category_id: str | None, fallback: str | None = None) -> str:
    if category_id == ALL_CATEGORIES_ID:
        return "ALL (aggregate)"
    if category_id is None:
        return "Uncategorized"
    return (_cache.CACHE.get_tag(category_id) or {}).get("title", fallback or category_id)


def _ensure_write_confirmed(diff: dict[str, Any], entity: str, ids: list[str | None]) -> None:
    if not isinstance(diff, dict):
        raise RuntimeError("_write_diff returned invalid response")
    if not diff:
        return
    expected_ids = {str(item_id) for item_id in ids if item_id is not None}
    if not expected_ids:
        return
    returned_ids = {
        str(item.get("id"))
        for item in diff.get(entity, [])
        if isinstance(item, dict) and item.get("id") is not None
    }
    cached_ids = set(_cache.CACHE.data.get(entity, {}))
    if expected_ids.isdisjoint(returned_ids) and expected_ids.isdisjoint(cached_ids):
        raise RuntimeError(f"_write_diff did not confirm {entity} write")


async def tool_get_budgets(args: dict) -> str:
    args = validate_tool_args("get_budgets", args)
    month = args["month"]
    month_date = f"{month}-01"
    budgets = [b for b in _cache.CACHE.budgets() if b.get("date") == month_date]
    return json.dumps([_fmt_budget(b) for b in budgets], ensure_ascii=False)

async def tool_analyze_budget_detailed(args: dict) -> str:
    """Detailed budget analysis with income vs expenses by category."""
    args = validate_tool_args("analyze_budget_detailed", args)

    # Build category index and accounts map from cache
    cat_index = _cache.CACHE.build_category_index()

    # Load accounts metadata from config
    with config.state_file_lock(_cfg_path):
        cfg = config.read_json_state(_cfg_path)

    accounts_meta = cfg.get("accounts_meta", {})
    accounts_map = _cache.CACHE.build_accounts_map(accounts_meta)

    currency_sections: dict[str, set[str]] = {
        "income": set(),
        "expense": set(),
        "transfers": set(),
        "forecast": set(),
    }

    def account_currency(account_id: str | None) -> str | None:
        if not account_id:
            return None
        return accounts_map.get(account_id, {}).get("currency")

    def remember_currency(section: str, currency: str | None) -> None:
        if currency and currency != "?":
            currency_sections[section].add(currency)

    def transfer_balance_amount(
        *,
        outcome_amount: float,
        income_amount: float,
        outcome_currency: str | None,
        income_currency: str | None,
        from_in_balance: bool,
        to_in_balance: bool,
    ) -> tuple[float, str | None]:
        if not from_in_balance and to_in_balance:
            return income_amount, income_currency or outcome_currency
        if from_in_balance and not to_in_balance:
            return outcome_amount, outcome_currency or income_currency
        if to_in_balance:
            return income_amount, income_currency or outcome_currency
        return outcome_amount, outcome_currency or income_currency

    def ensure_single_currency() -> None:
        visible_sections = {
            section: sorted(values)
            for section, values in currency_sections.items()
            if values
        }
        distinct_currencies = sorted(
            {
                currency
                for values in visible_sections.values()
                for currency in values
            }
        )
        if len(distinct_currencies) > 1:
            raise InvalidArgumentError(
                "analyze_budget_detailed does not support mixed currencies in scalar aggregates. "
                f"Detected currencies: {', '.join(distinct_currencies)}",
                {"currencies": visible_sections},
            )

    # Check if budget mode is configured
    if not cfg.get("budget_mode_configured", False):
        budget_modes_config = cfg.get("budget_modes", {})
        modes = []
        for mode_id, mode_data in budget_modes_config.items():
            modes.append({
                "id": mode_id,
                "label": mode_data.get("label", mode_id),
                "description": mode_data.get("description", "")
            })
        return json.dumps({
            "setup_required": True,
            "message": "Необходимо выбрать режим работы с бюджетом",
            "modes": modes
        }, ensure_ascii=False, indent=2)

    # Resolve budget mode
    mode_name = args.get("budget_mode") or cfg.get("budget_mode") or "income_vs_expense"
    mode_config = cfg.get("budget_modes", {}).get(mode_name)
    if not mode_config:
        mode_config = _BUDGET_MODE_DEFAULTS.get(mode_name, DEFAULT_INCOME_VS_EXPENSE)

    # Determine period from billing_period_start_day or use provided dates
    show_forecast = args["show_forecast"]
    show_calendar = args["show_calendar"]

    # Calculate period dates
    if args.get("start_date"):
        start_date = args["start_date"]
        end_date = args.get("end_date") or _today()
    else:
        # Auto-calculate from billing_period_start_day
        billing_start_day = cfg.get("billing_period_start_day", 1)

        today = datetime.date.today()
        if today.day >= billing_start_day:
            start_date = datetime.date(today.year, today.month, billing_start_day).isoformat()
            next_month = today.replace(day=28) + datetime.timedelta(days=4)
            next_month = next_month.replace(day=1)
            end_date = (next_month.replace(day=billing_start_day) - datetime.timedelta(days=1)).isoformat()
        else:
            prev_month = (today.replace(day=1) - datetime.timedelta(days=1))
            start_date = datetime.date(prev_month.year, prev_month.month, billing_start_day).isoformat()
            end_date = datetime.date(today.year, today.month, billing_start_day - 1).isoformat()

    # Helper to enrich category with metadata
    def enrich_category(cat_id: str) -> dict:
        if cat_id == ALL_CATEGORIES_ID:
            return {
                "category_id": ALL_CATEGORIES_ID,
                "category_name": "ALL (aggregate)",
                "category_full_name": "ALL (aggregate)",
                "parent_id": None,
                "parent_name": None,
                "is_parent": False,
            }
        if not cat_id or cat_id not in cat_index:
            return {
                "category_id": cat_id or "uncategorized",
                "category_name": "Без категории",
                "category_full_name": "Без категории",
                "parent_id": None,
                "parent_name": None,
                "is_parent": False,
            }
        meta = cat_index[cat_id]
        cat_name = meta.get("title", "Unknown")
        parent_name = meta.get("parent_title")
        category_full_name = f"{parent_name} / {cat_name}" if parent_name else cat_name
        return {
            "category_id": cat_id,
            "category_name": cat_name,
            "category_full_name": category_full_name,
            "parent_id": meta.get("parent_id"),
            "parent_name": parent_name,
            "is_parent": meta.get("is_parent", False),
        }

    # Get actual transactions
    txs = [t for t in _cache.CACHE.transactions() if not t.get("deleted")]
    txs = [t for t in txs if t.get("date", "") >= start_date and t.get("date", "") <= end_date]

    # Get reminders with markers
    reminders_income = []
    reminders_expense = []
    reminders_transfer = []

    for r in (_cache.CACHE.reminders() or []):
        if r.get("deleted"):
            continue
        markers = [m for m in (_cache.CACHE.reminder_markers() or [])
                  if m.get("reminder") == r["id"]
                  and not _is_deleted_marker(m)
                  and start_date <= m.get("date", "") <= end_date]
        if not markers:
            continue

        # Determine type
        if r.get("income", 0) > 0 and r.get("outcome", 0) == 0:
            rtype = "income"
        elif r.get("outcome", 0) > 0 and r.get("income", 0) == 0:
            rtype = "expense"
        else:
            rtype = "transfer"

        reminder_data = {
            "id": r["id"],
            "payee": r.get("payee"),
            "comment": r.get("comment"),
            "categories": [_cache.CACHE.get_tag(tid)["title"] if _cache.CACHE.get_tag(tid) else None for tid in (r.get("tag") or [])],
            "category_ids": r.get("tag") or [],
            "account_id": r.get("outcomeAccount") if rtype == "expense" else r.get("incomeAccount"),
            "from_account_id": r.get("outcomeAccount") if rtype == "transfer" else None,
            "to_account_id": r.get("incomeAccount") if rtype == "transfer" else None,
            "type": rtype,
            "markers": [
                {
                    "date": m.get("date"),
                    "income": m.get("income", 0),
                    "outcome": m.get("outcome", 0),
                    "state": m.get("state", "planned"),
                }
                for m in markers
            ],
            "total_income": sum(m.get("income", 0) for m in markers),
            "total_outcome": sum(m.get("outcome", 0) for m in markers),
        }

        if rtype == "income":
            reminders_income.append(reminder_data)
        elif rtype == "expense":
            reminders_expense.append(reminder_data)
        else:
            reminders_transfer.append(reminder_data)

    month = start_date[:7]
    # Get fresh budgets from API. ZenMoney month key = billing period start month,
    # so get_budgets("2026-02") returns budgets for the Feb 20 – Mar 19 billing period.
    budgets_raw = json.loads(await tool_get_budgets({"month": month}))
    budgets_map = {}
    for b in budgets_raw:
        cat_id = b.get("category_id")
        if cat_id is not None:
            budgets_map[cat_id] = {
                "income": b.get("income", 0),
                "outcome": b.get("outcome", 0),
                "outcome_lock": b.get("outcomeLock", False),
                "category_name": b.get("category"),  # Save name for debugging
            }

    # Process income
    income_by_category: dict[str, dict] = {}
    for tx in txs:
        tt = _tx_type(tx)
        if tt != "income":
            continue

        # Check if account should be counted based on mode
        acct_id = tx.get("incomeAccount")
        if not mode_config.get("count_all_movements", False):
            acct = accounts_map.get(acct_id, {})
            if not acct.get("inBalance", False):
                continue

        cat_ids = tx.get("tag", [])
        cat_id = cat_ids[0] if cat_ids else None
        cat_meta = enrich_category(cat_id)
        cat_key = cat_meta["category_id"]

        if cat_key not in income_by_category:
            income_by_category[cat_key] = {
                **cat_meta,
                "actual": 0,
                "planned": 0,
                "items": [],
            }

        remember_currency("income", account_currency(acct_id))
        income_by_category[cat_key]["actual"] += tx.get("income", 0)
        income_by_category[cat_key]["items"].append({
            "date": tx.get("date"),
            "payee": tx.get("payee"),
            "amount": tx.get("income", 0),
            "comment": tx.get("comment"),
            "status": "completed",
        })

    # Add planned income from reminders
    for rem in reminders_income:
        cat_ids = rem.get("category_ids", [])
        cat_id = cat_ids[0] if cat_ids else None
        cat_meta = enrich_category(cat_id)
        cat_key = cat_meta["category_id"]

        # Check account based on mode
        if not mode_config.get("count_all_movements", False):
            acct = accounts_map.get(rem.get("account_id"), {})
            if not acct.get("inBalance", False):
                continue

        if cat_key not in income_by_category:
            income_by_category[cat_key] = {
                **cat_meta,
                "actual": 0,
                "planned": 0,
                "items": [],
            }

        remember_currency("income", account_currency(rem.get("account_id")))
        income_by_category[cat_key]["planned"] += sum(m["income"] for m in rem["markers"] if _is_planned_marker(m))
        for marker in rem["markers"]:
            if not _is_planned_marker(marker):
                continue
            income_by_category[cat_key]["items"].append({
                "date": marker["date"],
                "payee": rem.get("payee"),
                "amount": marker["income"],
                "comment": rem.get("comment"),
                "status": marker.get("state", "planned"),
            })

    # Process expenses
    expense_by_category: dict[str, dict] = {}
    for tx in txs:
        tt = _tx_type(tx)
        if tt != "expense":
            continue

        # Check if account should be counted based on mode
        acct_id = tx.get("outcomeAccount")
        _exp_cfg = mode_config.get("expense", {})
        if not mode_config.get("count_all_movements", False) and not _exp_cfg.get("include_off_balance_expenses", False):
            acct = accounts_map.get(acct_id, {})
            if not acct.get("inBalance", False):
                continue

        cat_ids = tx.get("tag", [])
        cat_id = cat_ids[0] if cat_ids else None
        cat_meta = enrich_category(cat_id)
        cat_key = cat_meta["category_id"]

        if cat_key not in expense_by_category:
            expense_by_category[cat_key] = {
                **cat_meta,
                "actual": 0,
                "planned_from_reminders": 0,
                "processed_from_reminders": 0,
                "budget": 0,
                "outcome_lock": False,
                "items": [],
            }

        remember_currency("expense", account_currency(acct_id))
        expense_by_category[cat_key]["actual"] += tx.get("outcome", 0)
        expense_by_category[cat_key]["items"].append({
            "date": tx.get("date"),
            "payee": tx.get("payee"),
            "amount": tx.get("outcome", 0),
            "comment": tx.get("comment"),
            "status": "completed",
        })

    # Add planned expenses from reminders
    for rem in reminders_expense:
        cat_ids = rem.get("category_ids", [])
        cat_id = cat_ids[0] if cat_ids else None
        cat_meta = enrich_category(cat_id)
        cat_key = cat_meta["category_id"]

        # Check account based on mode
        _exp_cfg = mode_config.get("expense", {})
        if not mode_config.get("count_all_movements", False) and not _exp_cfg.get("include_off_balance_expenses", False):
            acct = accounts_map.get(rem.get("account_id"), {})
            if not acct.get("inBalance", False):
                continue

        if cat_key not in expense_by_category:
            expense_by_category[cat_key] = {
                **cat_meta,
                "actual": 0,
                "planned_from_reminders": 0,
                "processed_from_reminders": 0,
                "budget": 0,
                "outcome_lock": False,
                "items": [],
            }

        remember_currency("expense", account_currency(rem.get("account_id")))
        expense_by_category[cat_key]["planned_from_reminders"] += sum(m["outcome"] for m in rem["markers"] if _is_planned_marker(m))
        expense_by_category[cat_key]["processed_from_reminders"] += sum(
            m["outcome"] for m in rem["markers"] if _is_processed_marker(m)
        )
        for marker in rem["markers"]:
            if not _is_planned_marker(marker):
                continue
            expense_by_category[cat_key]["items"].append({
                "date": marker["date"],
                "payee": rem.get("payee"),
                "amount": marker["outcome"],
                "comment": rem.get("comment"),
                "status": marker.get("state", "planned"),
            })

    # Add budget data
    for cat_key, cat_data in expense_by_category.items():
        if cat_key in budgets_map:
            cat_data["budget"] = budgets_map[cat_key]["outcome"]
            cat_data["outcome_lock"] = budgets_map[cat_key]["outcome_lock"]

    # Add budget-only categories (categories with budget but no reminders/transactions)
    for cat_id, budget_data in budgets_map.items():
        if budget_data["outcome"] == 0:
            continue

        # Check if this category already exists in expense_by_category
        if cat_id in expense_by_category:
            found = True
        else:
            found = False

        # If not found, create new expense category
        if not found:
            # Find category by UUID in cache
            cat_obj = None
            for c in (_cache.CACHE.tags() or []):
                if c.get("id") == cat_id:
                    cat_obj = c
                    break

            if cat_obj:
                cat_meta = enrich_category(cat_obj["id"])
                cat_key = cat_meta["category_id"]

                expense_by_category[cat_key] = {
                    **cat_meta,
                    "actual": 0,
                    "planned_from_reminders": 0,
                    "processed_from_reminders": 0,
                    "budget": budget_data["outcome"],
                    "outcome_lock": budget_data["outcome_lock"],
                    "items": [],
                }

    # Process transfers
    transfer_items = []

    # Add actual transfers from transactions
    for tx in txs:
        tt = _tx_type(tx)
        if tt != "transfer":
            continue

        from_acct_id = tx.get("outcomeAccount")
        to_acct_id = tx.get("incomeAccount")

        # Check if this affects inBalance accounts
        from_acct = accounts_map.get(from_acct_id, {})
        to_acct = accounts_map.get(to_acct_id, {})

        from_in_balance = from_acct.get("inBalance", False)
        to_in_balance = to_acct.get("inBalance", False)
        outcome_amount = tx.get("outcome", 0)
        income_amount = tx.get("income", 0)
        outcome_currency = account_currency(from_acct_id)
        income_currency = account_currency(to_acct_id)
        balance_amount, balance_currency = transfer_balance_amount(
            outcome_amount=outcome_amount,
            income_amount=income_amount,
            outcome_currency=outcome_currency,
            income_currency=income_currency,
            from_in_balance=from_in_balance,
            to_in_balance=to_in_balance,
        )

        # Skip if both are off-balance and we're not in count_all_movements mode
        if not mode_config.get("count_all_movements", False) and not from_in_balance and not to_in_balance:
            continue

        # Transfer affects balance if:
        # - From inBalance to off-balance (outflow)
        # - From off-balance to inBalance (inflow) - when count_all_movements=true
        # - Between inBalance accounts (no net effect on total balance, but show in calendar)

        transfer_items.append({
            "date": tx.get("date"),
            "from_account": from_acct.get("title", "Unknown"),
            "to_account": to_acct.get("title", "Unknown"),
            "amount": outcome_amount,
            "income_amount": income_amount,
            "outcome_amount": outcome_amount,
            "income_currency": income_currency,
            "outcome_currency": outcome_currency,
            "balance_amount": balance_amount,
            "balance_currency": balance_currency,
            "comment": tx.get("comment"),
            "status": "completed",
            "from_in_balance": from_in_balance,
            "to_in_balance": to_in_balance,
            "from_account_type": from_acct.get("type"),
            "from_account_subtype": from_acct.get("subtype"),
            "from_account_savings": from_acct.get("savings", False),
            "to_account_type": to_acct.get("type"),
            "to_account_subtype": to_acct.get("subtype"),
            "to_account_savings": to_acct.get("savings", False),
        })

    # Add planned transfers from reminders
    for rem in reminders_transfer:
        from_acct_id = rem.get("from_account_id")
        to_acct_id = rem.get("to_account_id")

        from_acct = accounts_map.get(from_acct_id, {})
        to_acct = accounts_map.get(to_acct_id, {})

        from_in_balance = from_acct.get("inBalance", False)
        to_in_balance = to_acct.get("inBalance", False)
        outcome_currency = account_currency(from_acct_id)
        income_currency = account_currency(to_acct_id)

        if not mode_config.get("count_all_movements", False) and not from_in_balance and not to_in_balance:
            continue

        for marker in rem["markers"]:
            if not _is_planned_marker(marker):
                continue

            outcome_amount = marker.get("outcome", 0)
            income_amount = marker.get("income", 0)
            balance_amount, balance_currency = transfer_balance_amount(
                outcome_amount=outcome_amount,
                income_amount=income_amount,
                outcome_currency=outcome_currency,
                income_currency=income_currency,
                from_in_balance=from_in_balance,
                to_in_balance=to_in_balance,
            )

            transfer_items.append({
                "date": marker["date"],
                "from_account": from_acct.get("title", "Unknown"),
                "to_account": to_acct.get("title", "Unknown"),
                "amount": outcome_amount,
                "income_amount": income_amount,
                "outcome_amount": outcome_amount,
                "income_currency": income_currency,
                "outcome_currency": outcome_currency,
                "balance_amount": balance_amount,
                "balance_currency": balance_currency,
                "comment": rem.get("comment"),
                "status": marker.get("state", "planned"),
                "from_in_balance": from_in_balance,
                "to_in_balance": to_in_balance,
                "from_account_type": from_acct.get("type"),
                "from_account_subtype": from_acct.get("subtype"),
                "from_account_savings": from_acct.get("savings", False),
                "to_account_type": to_acct.get("type"),
                "to_account_subtype": to_acct.get("subtype"),
                "to_account_savings": to_acct.get("savings", False),
            })

    # Helper to build hierarchical tree from flat categories
    def build_category_tree(flat_categories: dict[str, dict], is_expense: bool = False) -> list[dict]:
        """Convert flat category dict to hierarchical tree structure."""
        # Group by parent_id
        by_parent: dict[str | None, list[dict]] = {}
        for cat_data in flat_categories.values():
            parent_id = cat_data.get("parent_id")
            if parent_id not in by_parent:
                by_parent[parent_id] = []
            by_parent[parent_id].append(cat_data)

        def aggregate_sums(node: dict, children: list[dict]) -> None:
            """Recursively aggregate sums from children to parent, preserving parent's own values."""
            if is_expense:
                # Facts and marker states always roll up. Child budgets roll up
                # only until an explicitly locked parent, matching ZM Plans.
                node["actual"] += sum(child["actual"] for child in children)
                node["planned_from_reminders"] += sum(child["planned_from_reminders"] for child in children)
                node["processed_from_reminders"] += sum(child["processed_from_reminders"] for child in children)
                if not node.get("outcome_lock", False):
                    node["budget"] += sum(child["budget"] for child in children)
            else:
                # For income: actual, planned
                node["actual"] += sum(child["actual"] for child in children)
                node["planned"] += sum(child["planned"] for child in children)

        def build_node(cat_data: dict) -> dict:
            """Recursively build tree node."""
            cat_id = cat_data["category_id"]

            # Create clean node without parent_id, parent_name, is_parent
            node = {
                "category_id": cat_data["category_id"],
                "category_name": cat_data["category_name"],
                "category_full_name": cat_data["category_full_name"],
            }

            # Copy numeric fields
            if is_expense:
                node["actual"] = cat_data["actual"]
                node["planned_from_reminders"] = cat_data["planned_from_reminders"]
                node["processed_from_reminders"] = cat_data["processed_from_reminders"]
                node["budget"] = cat_data["budget"]
                node["outcome_lock"] = cat_data.get("outcome_lock", False)
            else:
                node["actual"] = cat_data["actual"]
                node["planned"] = cat_data["planned"]

            # Check if this category has children
            children_data = by_parent.get(cat_id, [])
            if children_data:
                # Has children - recursively build them
                children = [build_node(child_data) for child_data in children_data]
                node["children"] = children

                # Aggregate sums from children
                aggregate_sums(node, children)
            else:
                # Leaf node - add items
                node["items"] = cat_data["items"]

            return node

        # Build tree from root categories (parent_id = None)
        root_categories = by_parent.get(None, [])
        tree = [build_node(cat_data) for cat_data in root_categories]

        return tree

    # Add parent categories with zero values if not present
    all_categories = _cache.CACHE.tags() or []
    category_parents = set()
    for cat in all_categories:
        if cat.get("parent"):
            category_parents.add(cat["parent"])

    for parent_id in category_parents:
        # Find parent category object
        parent_cat = next((c for c in all_categories if c.get("id") == parent_id), None)
        if not parent_cat:
            continue

        parent_meta = enrich_category(parent_id)

        # Add to expense_by_category if not present
        if parent_id not in expense_by_category:
            expense_by_category[parent_id] = {
                **parent_meta,
                "actual": 0,
                "planned_from_reminders": 0,
                "processed_from_reminders": 0,
                "budget": 0,
                "outcome_lock": False,
                "items": [],
            }

        # Add to income_by_category if not present
        if parent_id not in income_by_category:
            income_by_category[parent_id] = {
                **parent_meta,
                "actual": 0,
                "planned": 0,
                "items": [],
            }

    # Build hierarchical trees
    income_tree = build_category_tree(income_by_category, is_expense=False)
    expense_tree = build_category_tree(expense_by_category, is_expense=True)

    # Calculate totals from tree roots (already aggregated)
    total_income_actual = sum(c["actual"] for c in income_tree)
    total_income_planned = sum(c["planned"] for c in income_tree)

    # For expenses, calculate: budget, actual, planned, remaining (ZenMoney logic)
    def sum_leaf_budgets(nodes):
        """Recursively sum budgets, using max(parent_budget, children_sum) for parent nodes."""
        total = 0
        for node in nodes:
            if node.get("children"):
                children_sum = sum_leaf_budgets(node["children"])
                parent_budget = node.get("budget", 0)
                total += max(parent_budget, children_sum)
            else:
                total += node.get("budget", 0)
        return total

    def sum_leaf_planned(nodes):
        """Recursively sum planned_from_reminders, using max(parent_planned, children_sum) for parent nodes."""
        total = 0
        for node in nodes:
            if node.get("children"):
                children_sum = sum_leaf_planned(node["children"])
                parent_planned = node.get("planned_from_reminders", 0)
                total += max(parent_planned, children_sum)
            else:
                total += node.get("planned_from_reminders", 0)
        return total

    def remaining_reserve(node: dict) -> float:
        """Return the ZM Plans reserve for one category subtree."""
        actual = node.get("actual", 0)
        planned = node.get("planned_from_reminders", 0)
        processed = node.get("processed_from_reminders", 0)
        budget = node.get("budget", 0)
        effective_budget = budget if node.get("outcome_lock", False) else budget + planned + processed

        own_reserve = 0 if abs(effective_budget) < 0.01 else max(0, planned, effective_budget - actual)
        children_reserve = sum(remaining_reserve(child) for child in node.get("children", []))
        return max(own_reserve, children_reserve)

    aggregate_budget = budgets_map.get(ALL_CATEGORIES_ID, {}).get("outcome", 0)
    category_budget = sum_leaf_budgets(expense_tree)
    total_expense_budget = max(aggregate_budget, category_budget)
    total_expense_planned = sum_leaf_planned(expense_tree)
    total_expense_processed = sum(c.get("processed_from_reminders", 0) for c in expense_tree)
    total_expense_for_balance = sum(
        root.get("actual", 0) + remaining_reserve(root)
        for root in expense_tree
    )

    # Root nodes already contain their own facts plus all child facts.
    total_expense_actual = sum(root.get("actual", 0) for root in expense_tree)
    total_expense_remaining = total_expense_for_balance - total_expense_actual
    total_expense_expected = total_expense_actual + total_expense_planned

    # Calculate transfer totals using mode-aware classify_transfer
    total_transfers_out = 0
    total_transfers_in = 0
    for item in transfer_items:
        result = classify_transfer(item, mode_config)
        if result:
            remember_currency("transfers", item.get("balance_currency"))
            transfer_type, amount = result
            if transfer_type == "expense":
                total_transfers_out += amount
            elif transfer_type == "income":
                total_transfers_in += amount

    total_transfers_net = total_transfers_out - total_transfers_in

    if show_forecast:
        for account in accounts_map.values():
            if account.get("archived"):
                continue
            if mode_config.get("count_all_movements", False) or account.get("inBalance", False):
                remember_currency("forecast", account.get("currency"))

    ensure_single_currency()

    # Build output
    result = {
        "summary": {
            "budget_mode": mode_name,
            "budget_mode_label": mode_config.get("label", mode_name),
            "period": {"start": start_date, "end": end_date},
            "income": {
                "actual": total_income_actual,
                "planned": total_income_planned,
                "total": total_income_actual + total_income_planned,
            },
            "expense": {
                "budget": total_expense_budget,
                "budget_scope": "configured_max_including_all",
                "category_budget": category_budget,
                "aggregate_budget": aggregate_budget,
                "actual": total_expense_actual,
                "planned": total_expense_planned,
                "processed_planned": total_expense_processed,
                "category_difference_policy": "none",
                "remaining": total_expense_remaining,
                "expected_total": total_expense_expected,
                "for_balance": total_expense_for_balance,
                "description": "for_balance = fact + category-tree reserve under category_difference_policy=none. Unlocked budgets include planned and processed markers; locked budgets keep the explicit budget. ALL is reported separately and excluded from Plans category totals."
            },
            "transfers": {
                "out": total_transfers_out,
                "in": total_transfers_in,
                "net": total_transfers_net,
                "description": "Net transfers based on account types (credit, savings, debt) and inBalance flags",
            },
        }
    }

    # ZenMoney formula: Свободно = Все доходы за период - Все расходы по плану - Переводы (нетто)
    # This matches ZenMoney Plans tab: total income - total expense plan - total transfer plan
    total_income = total_income_actual + total_income_planned
    total_plan = total_expense_for_balance + total_transfers_net
    remaining_plan = total_plan - total_expense_actual

    balance_raw = total_income - total_plan

    result["summary"]["balance"] = (
        round(balance_raw)
        if cfg.get("round_balance_to_integer", True)
        else balance_raw
    )

    # Add detailed breakdown for debugging
    result["summary"]["balance_breakdown"] = {
        "total_income": total_income,
        "total_expense_plan": total_expense_for_balance,
        "total_transfers_net": total_transfers_net,
        "total_plan": total_plan,
        "remaining_plan": remaining_plan,
        "formula": f"{total_income} - {total_expense_for_balance} - {total_transfers_net} = {balance_raw}",
        "formula_readable": f"Все доходы ({total_income:,.0f}) - Расходы по плану ({total_expense_for_balance:,.0f}) - Переводы ({total_transfers_net:,.0f}) = {balance_raw:,.2f}"
    }

    # Add income, expenses, transfers to result
    result["income"] = sorted(income_tree, key=lambda x: x["actual"] + x["planned"], reverse=True)
    result["expenses"] = sorted(expense_tree, key=lambda x: max(x["actual"] + x["planned_from_reminders"], x["budget"]), reverse=True)
    result["transfers"] = sorted(transfer_items, key=lambda x: x["date"])

    # Helper to recursively collect items from tree
    def collect_items_from_tree(nodes: list[dict], item_type: str) -> list[dict]:
        """Recursively collect all items from tree nodes."""
        items = []
        for node in nodes:
            # If node has children, recurse
            if "children" in node:
                items.extend(collect_items_from_tree(node["children"], item_type))
            # If node has items (leaf node), collect them
            elif "items" in node:
                for item in node["items"]:
                    items.append({
                        "date": item["date"],
                        "type": item_type,
                        "category": node["category_name"],
                        "payee": item["payee"],
                        "amount": item["amount"],
                        "status": item["status"],
                    })
        return items

    calendar_ops = []
    calendar_ops.extend(collect_items_from_tree(income_tree, "income"))
    calendar_ops.extend(collect_items_from_tree(expense_tree, "expense"))
    for item in transfer_items:
        calendar_ops.append({
            "date": item["date"],
            "type": "transfer",
            "from_account": item["from_account"],
            "to_account": item["to_account"],
            "amount": item["amount"],
            "balance_amount": item["balance_amount"],
            "balance_currency": item.get("balance_currency"),
            "comment": item["comment"],
            "status": item["status"],
            "from_in_balance": item["from_in_balance"],
            "to_in_balance": item["to_in_balance"],
        })
    calendar_ops.sort(key=lambda x: x["date"])

    if show_calendar:
        result["calendar"] = calendar_ops

    # Add forecast if requested
    if show_forecast:
        # Get current balance
        current_balance = sum(
            a.get("balance", 0)
            for a in accounts_map.values()
            if not a.get("archived") and (
                mode_config.get("count_all_movements", False) or a.get("inBalance", False)
            )
        )

        # Build daily forecast
        forecast = []
        balance = current_balance

        # Group calendar by date
        from collections import defaultdict
        daily_ops: dict[str, list] = defaultdict(list)
        today_str = _today()
        forecast_touches_current_balance = start_date <= today_str <= end_date
        for op in calendar_ops:
            if forecast_touches_current_balance and op.get("status") == "completed":
                continue
            if forecast_touches_current_balance and op.get("date", "") < today_str:
                continue
            daily_ops[op["date"]].append(op)

        current_date = datetime.date.fromisoformat(start_date)
        end_date_obj = datetime.date.fromisoformat(end_date)

        while current_date <= end_date_obj:
            date_str = current_date.isoformat()
            ops = daily_ops.get(date_str, [])

            for op in ops:
                if op["type"] == "income":
                    balance += op["amount"]
                elif op["type"] == "expense":
                    balance -= op["amount"]
                elif op["type"] == "transfer":
                    # Transfer impact on balance depends on account types:
                    # - inBalance → off-balance: decreases balance
                    # - off-balance → inBalance: increases balance
                    # - inBalance → inBalance: no net effect (both sides counted)
                    # - off-balance → off-balance: no effect
                    from_in = op.get("from_in_balance", False)
                    to_in = op.get("to_in_balance", False)

                    if from_in and not to_in:
                        # Outflow from tracked balance
                        balance -= op.get("balance_amount", op["amount"])
                    elif not from_in and to_in:
                        # Inflow to tracked balance (when count_all_movements=true)
                        balance += op.get("balance_amount", op["amount"])
                    # else: both in or both out = no net change to tracked balance

            if ops:  # Only add to forecast if there were operations
                forecast.append({
                    "date": date_str,
                    "balance": round(balance) if cfg.get("round_balance_to_integer", True) else round(balance, 2),
                    "operations_count": len(ops),
                })

            current_date += datetime.timedelta(days=1)

        result["forecast"] = forecast

    return json.dumps(result, ensure_ascii=False, indent=2)


async def tool_setup_budget_mode(args: dict) -> str:
    """Setup budget mode configuration (balance_vs_expense or income_vs_expense)."""
    args = validate_tool_args("setup_budget_mode", args)
    mode = args["mode"]

    cfg = config.setup_budget_mode_config(mode)

    # Get mode details
    mode_config = cfg.get("budget_modes", {}).get(mode, {})

    return json.dumps({
        "success": True,
        "mode": mode,
        "label": mode_config.get("label", mode),
        "description": mode_config.get("description", ""),
        "message": f"Режим '{mode_config.get('label', mode)}' успешно установлен"
    }, ensure_ascii=False, indent=2)

async def tool_create_budget(args: dict) -> str:
    args = validate_tool_args("create_budget", args)
    month = args["month"]
    category = args["category"]
    income = args["income"]
    outcome = args["outcome"]
    income_lock = args["income_lock"]
    outcome_lock = args["outcome_lock"]

    category_id = args["category_id"]
    month_date = f"{month}-01"

    user = require_user_or_account_owner()

    budget: dict[str, Any] = {
        "user": user["id"],
        "changed": _now_ts(),
        "tag": category_id,
        "date": month_date,
        "income": income,
        "incomeLock": income_lock,
        "outcome": outcome,
        "outcomeLock": outcome_lock,
    }

    diff = await _write_diff({"budget": [budget]})
    _ensure_write_confirmed(diff, "budget", [_budget_cache_key(category_id, month_date)])
    cat_name = _category_display_name(category_id, category)
    return json.dumps({
        "success": True,
        "budget": {
            "month": month, "category": cat_name, "category_id": category_id,
            "income": income, "outcome": outcome,
            "income_lock": income_lock, "outcome_lock": outcome_lock,
        },
    }, ensure_ascii=False)


async def tool_update_budget(args: dict) -> str:
    args = validate_tool_args("update_budget", args)
    month = args["month"]
    category = args["category"]

    category_id = args["category_id"]
    month_date = f"{month}-01"
    budget_key = _budget_cache_key(category_id, month_date)

    existing = _cache.CACHE.data["budget"].get(budget_key)
    if not existing:
        raise EntityNotFoundError(f'Budget not found for category "{category}" in {month}. Use create_budget to create.')

    updated = {**existing, "changed": _now_ts()}
    if "income" in args:
        updated["income"] = args["income"]
    if "outcome" in args:
        updated["outcome"] = args["outcome"]
    if "income_lock" in args:
        updated["incomeLock"] = args["income_lock"]
    if "outcome_lock" in args:
        updated["outcomeLock"] = args["outcome_lock"]

    diff = await _write_diff({"budget": [updated]})
    _ensure_write_confirmed(diff, "budget", [budget_key])
    cat_name = _category_display_name(category_id, category)
    return json.dumps({
        "success": True, "message": "Budget updated",
        "budget": {
            "month": month, "category": cat_name,
            "income": updated["income"], "outcome": updated["outcome"],
            "income_lock": updated.get("incomeLock", False),
            "outcome_lock": updated.get("outcomeLock", False),
        },
    }, ensure_ascii=False)


async def tool_delete_budget(args: dict) -> str:
    args = validate_tool_args("delete_budget", args)
    month = args["month"]
    category = args["category"]

    category_id = args["category_id"]
    month_date = f"{month}-01"
    budget_key = _budget_cache_key(category_id, month_date)

    existing = _cache.CACHE.data["budget"].get(budget_key)
    if not existing:
        raise EntityNotFoundError(f'Budget not found for category "{category}" in {month}.')

    deleted = {
        **existing,
        "changed": _now_ts(),
        "income": 0,
        "outcome": 0,
        "incomeLock": False,
        "outcomeLock": False,
    }
    diff = await _write_diff({"budget": [deleted]})
    _ensure_write_confirmed(diff, "budget", [budget_key])
    cat_name = _category_display_name(category_id, category)
    return json.dumps({"success": True, "message": "Budget deleted", "category": cat_name, "month": month}, ensure_ascii=False)
