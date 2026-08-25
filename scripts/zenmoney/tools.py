from __future__ import annotations

import sys
from typing import Any

from . import config
from . import dispatch as _dispatch
from .budget_tools import (
    tool_analyze_budget_detailed,
    tool_create_budget,
    tool_delete_budget,
    tool_get_budgets,
    tool_setup_budget_mode,
    tool_update_budget,
)
from .advanced_analytics_tools import (
    tool_get_balance_trend,
    tool_get_category_report,
    tool_get_income_outcome_comparison,
    tool_get_money_flow,
)
from .read_tools import (
    tool_check_auth_status,
    tool_get_accounts,
    tool_get_analytics,
    tool_get_categories,
    tool_get_instruments,
    tool_get_merchants,
    tool_get_transactions,
    tool_suggest,
)
from .reminder_tools import (
    tool_create_reminder,
    tool_create_reminder_marker,
    tool_delete_reminder,
    tool_delete_reminder_marker,
    tool_get_reminders,
    tool_update_reminder,
)
from .write_tools import (
    tool_create_account,
    tool_create_transaction,
    tool_delete_transaction,
    tool_update_transaction,
)


# ---------------------------------------------------------------------------
# Tool metadata (for --list / --describe)
# ---------------------------------------------------------------------------

TOOL_DOCS: dict[str, dict] = {
    # -- Read tools --
    "get_accounts": {
        "desc": "Get all ZenMoney accounts with balances",
        "params": {"include_archived": "bool (default false) — include archived accounts"},
    },
    "get_transactions": {
        "desc": "Get transactions filtered by date, account, category, type",
        "params": {
            "period": "str billing_period|week|month|year with optional period_offset; mutually exclusive with custom dates",
            "period_offset": "int (default 0; named periods only)",
            "first_weekday": "int 0..6 (required for period=week; 0 Monday)",
            "start_date": "str yyyy-MM-dd (custom range only; end_date required)",
            "end_date": "str yyyy-MM-dd inclusive (custom range only; start_date required)",
            "account_id": "str UUID (optional)",
            "category_id": "str UUID (optional)",
            "type": "str expense|income|transfer (optional)",
            "limit": "int (default 100, max 500)",
            "offset": "int (default 0)",
        },
    },
    "get_categories": {
        "desc": "Get all categories (tags) as a tree with parent-child relationships",
        "params": {},
    },
    "get_instruments": {
        "desc": "Get currencies with IDs, codes, symbols and rates",
        "params": {"include_all": "bool (default false) — include all, not just used in accounts"},
    },
    "get_budgets": {
        "desc": "Get budgets for a specific month",
        "params": {"month": "str yyyy-MM (required)"},
    },
    "get_reminders": {
        "desc": "Get scheduled payment reminders with their markers. When marker_from/marker_to are specified, filters reminders by marker dates in that period and sorts by first marker date. Without these params, returns a recent summary sorted by startDate.",
        "params": {
            "marker_from": "str yyyy-MM-dd (optional) — start of marker date range (inclusive); if set then marker_to is required, and empty string is rejected",
            "marker_to": "str yyyy-MM-dd (optional) — end of marker date range (inclusive); if set then marker_from is required, and empty string is rejected",
            "category": "str (optional) — filter by category name, full path 'Parent / Child', or UUID; ambiguous short names raise",
            "type": "str expense|income|transfer|all (optional, default all) — filter by operation type",
            "include_processed": "bool (default false)",
            "active_only": "bool (default true)",
            "limit": "int (default 50)",
            "markers_limit": "int (default 5) — max markers per reminder (only used in recent-summary mode without marker_from/marker_to)",
            "offset": "int (default 0)",
        },
    },
    "analyze_budget_detailed": {
        "desc": "Detailed budget analysis with income vs expenses breakdown by category, plan vs fact comparison, payment calendar, and balance forecast",
        "params": {
            "period": "str billing_period (required); use period_offset for current/previous Plans periods",
            "period_offset": "int (default 0; -1 previous, 1 next)",
            "budget_mode": "str balance_vs_expense|income_vs_expense (optional local override; default from synced user.planBalanceMode) — applies the matching ZenMoney Plans policy",
            "show_forecast": "bool (default true) — show daily balance forecast",
            "show_calendar": "bool (default true) — show payment calendar",
            "difference_calculation_mode": "REFUNDS|INCOME_OUTCOME_AND_REFUNDS|NONE (optional; BALANCE mode always uses NONE)",
        },
    },
    "setup_budget_mode": {
        "desc": "Set a local Plans UI-mode override; transfer switches continue to come from synced user.planSettings unless explicitly overridden in config",
        "params": {
            "mode": "str balance_vs_expense|income_vs_expense (required) — budget mode to set",
            "difference_calculation_mode": "REFUNDS|INCOME_OUTCOME_AND_REFUNDS|NONE (optional)",
        },
    },
    "get_analytics": {
        "desc": "Income, outcome, or net analytics with strict account, category, and merchant scopes; filter dimensions combine with AND",
        "params": {
            "period": "str billing_period|week|month|year with optional period_offset; mutually exclusive with custom dates",
            "period_offset": "int (default 0; -1 previous, 1 next; named periods only)",
            "first_weekday": "int 0..6 (required for period=week; 0 Monday)",
            "start_date": "str yyyy-MM-dd (custom range only; end_date required)",
            "end_date": "str yyyy-MM-dd inclusive (custom range only; start_date required)",
            "group_by": "str category|account|merchant (default category)",
            "report": "str income|outcome|net (required); turnover is reserved and returns UNSUPPORTED_CALCULATION",
            "currency_mode": "str split|scalar (default split); scalar requires at most one currency",
            "account_scope": "str all|in_balance|selected (default in_balance); checks the report-side account, and in_balance includes archived accounts whose inBalance is true",
            "account_ids": "list[str UUID] (selected account_scope only, non-empty); archived accounts are allowed",
            "category_scope": "str all|selected (default all); selected requires category_ids",
            "category_ids": "list[str UUID] (selected category_scope only, non-empty)",
            "category_role": "str primary|additional|any (default any; selected category_scope only) — which transaction tag positions selected categories match",
            "merchant_scope": "str all|selected (default all); selected requires merchant_ids or payees",
            "merchant_ids": "list[str UUID] (selected merchant_scope only); merchant identity takes precedence over payee",
            "payees": "list[str] exact case-sensitive NFC-normalized payee fallbacks used only when a transaction has no merchant ID",
        },
    },
    "get_category_report": {
        "desc": "ZenMoney category or payee report with BUDGET/MEAN plan comparison",
        "params": {
            "period": "billing_period|week|month|year with optional period_offset, or custom start_date/end_date",
            "direction": "INCOME|OUTCOME (default OUTCOME)",
            "group_by": "TAG|PAYEE (default TAG; PAYEE always uses MEAN)",
            "budget_method": "BUDGET|MEAN (default BUDGET)",
            "comparison_periods": "int 0..12 (default 3)",
            "difference_calculation_mode": "REFUNDS|INCOME_OUTCOME_AND_REFUNDS|NONE (default saved mode or REFUNDS)",
            "account_scope": "all|in_balance|selected (default in_balance)",
            "account_ids": "list[str UUID] (selected scope only)",
        },
    },
    "get_money_flow": {
        "desc": "ZenMoney Money Flow buckets, weights, residue, and overspending by native currency",
        "params": {
            "period": "billing_period|week|month|year with optional period_offset, or custom start_date/end_date",
            "account_scope": "all|in_balance|selected (default in_balance)",
            "account_ids": "list[str UUID] (selected scope only)",
        },
    },
    "get_income_outcome_comparison": {
        "desc": "ZenMoney income/outcome comparison for the selected and preceding periods",
        "params": {
            "period": "billing_period|week|month|year with optional period_offset, or custom start_date/end_date",
            "mode": "WHOLE_PERIOD|AVERAGE_VALUES (AVERAGE_VALUES fails explicitly while its APK formula is unconfirmed)",
            "comparison_periods": "int 0..12 (default 3)",
            "account_scope": "all|in_balance|selected (default in_balance)",
            "account_ids": "list[str UUID] (selected scope only)",
        },
    },
    "get_balance_trend": {
        "desc": "ZenMoney balance trend reconstructed from synced current balances and transaction history",
        "params": {
            "period": "billing_period|week|month|year with optional period_offset, or custom start_date/end_date",
            "currency_filter": "USER|POPULAR (default USER)",
            "currency": "instrument id or code (optional; default user/main currency)",
            "account_scope": "all|in_balance|selected (default in_balance)",
            "account_ids": "list[str UUID] (selected scope only)",
        },
    },
    "suggest": {
        "desc": "ML suggestions for category/merchant by payee name",
        "params": {"payee": "str (required)"},
    },
    "get_merchants": {
        "desc": "Get merchants, optionally filtered by search query",
        "params": {"search": "str (optional)", "limit": "int (default 50)", "offset": "int (default 0)"},
    },
    "check_auth_status": {
        "desc": "Check authentication status and token validity",
        "params": {},
    },
    # -- Write tools --
    "create_transaction": {
        "desc": "Create a new transaction (expense, income, or transfer)",
        "params": {
            "type": "str expense|income|transfer (required)",
            "amount": "float (required, positive)",
            "account_id": "str UUID (required)",
            "to_account_id": "str UUID (required for transfer)",
            "category_ids": "list[str] UUIDs (optional)",
            "date": "str yyyy-MM-dd (default today)",
            "payee": "str (optional)",
            "comment": "str (optional)",
            "currency_id": "int (optional, override account currency)",
            "income_amount": "float (for cross-currency transfers)",
        },
    },
    "update_transaction": {
        "desc": "Update an existing transaction. Only pass fields to change.",
        "params": {
            "id": "str UUID (required)",
            "amount": "float (optional)",
            "category_ids": "list[str] UUIDs (optional)",
            "date": "str yyyy-MM-dd (optional)",
            "payee": "str (optional)",
            "comment": "str (optional)",
        },
    },
    "delete_transaction": {
        "desc": "Soft-delete a transaction",
        "params": {"id": "str UUID (required)"},
    },
    "create_account": {
        "desc": "Create a new account",
        "params": {
            "title": "str (required)",
            "type": "str cash|ccard|checking (required)",
            "currency_id": "int (required, instrument ID)",
            "balance": "float (default 0)",
            "credit_limit": "float (default 0)",
        },
    },
    "create_budget": {
        "desc": "Create or update budget for a category in a month",
        "params": {
            "month": "str yyyy-MM (required)",
            "category": "str name, full path Parent / Child, UUID, 'ALL', or 'ALL (aggregate)' for aggregate (required)",
            "income": "float (default 0)",
            "outcome": "float (default 0)",
            "income_lock": "bool (default false)",
            "outcome_lock": "bool (default false)",
        },
    },
    "update_budget": {
        "desc": "Update existing budget. Only pass fields to change.",
        "params": {
            "month": "str yyyy-MM (required)",
            "category": "str name, full path Parent / Child, UUID, 'ALL', or 'ALL (aggregate)' (required)",
            "income": "float (optional)",
            "outcome": "float (optional)",
            "income_lock": "bool (optional)",
            "outcome_lock": "bool (optional)",
        },
    },
    "delete_budget": {
        "desc": "Delete budget by zeroing income and outcome",
        "params": {
            "month": "str yyyy-MM (required)",
            "category": "str name, full path Parent / Child, UUID, 'ALL', or 'ALL (aggregate)' (required)",
        },
    },
    "create_reminder": {
        "desc": "Create a recurring reminder (planned transaction) with auto-generated markers",
        "params": {
            "type": "str expense|income|transfer (required)",
            "amount": "float (required, positive)",
            "account_id": "str UUID (required)",
            "to_account_id": "str UUID (for transfers)",
            "category_ids": "list[str] UUIDs (optional)",
            "payee": "str (optional)",
            "comment": "str (optional)",
            "interval": "str day|week|month|year (required)",
            "step": "int (default 1, positive)",
            "points": "list[int] recurrence offsets, each 0 <= point < step (default [0])",
            "start_date": "str yyyy-MM-dd (default today)",
            "end_date": "str yyyy-MM-dd (optional)",
            "notify": "bool (default true)",
            "generate_markers": "int (default 12) - number of markers to auto-generate, 0 to skip",
        },
    },
    "update_reminder": {
        "desc": "Update an existing reminder. Only pass fields to change.",
        "params": {
            "id": "str UUID (required)",
            "amount": "float (optional)",
            "category_ids": "list[str] UUIDs (optional)",
            "payee": "str (optional)",
            "comment": "str (optional)",
            "interval": "str day|week|month|year (optional)",
            "step": "int (optional)",
            "points": "list[int] (optional)",
            "end_date": "str yyyy-MM-dd (optional)",
            "notify": "bool (optional)",
            "regenerate_markers": "int (default 12) — count of new markers to generate when recurrence changes",
        },
    },
    "delete_reminder": {
        "desc": "Delete a reminder and all its associated markers",
        "params": {"id": "str UUID (required)"},
    },
    "create_reminder_marker": {
        "desc": "Create a one-time reminder marker for a specific date",
        "params": {
            "type": "str expense|income|transfer (required)",
            "amount": "float (required, positive)",
            "account_id": "str UUID (required)",
            "to_account_id": "str UUID (for transfers)",
            "category_ids": "list[str] UUIDs (optional)",
            "payee": "str (optional)",
            "comment": "str (optional)",
            "date": "str yyyy-MM-dd (required)",
            "reminder_id": "str UUID (optional, auto-creates one-time reminder if absent)",
            "notify": "bool (default true)",
        },
    },
    "delete_reminder_marker": {
        "desc": "Delete a reminder marker",
        "params": {"id": "str UUID (required)"},
    },
}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

TOOLS: dict[str, Any] = {
    "get_accounts": tool_get_accounts,
    "get_transactions": tool_get_transactions,
    "get_categories": tool_get_categories,
    "get_instruments": tool_get_instruments,
    "get_budgets": tool_get_budgets,
    "get_reminders": tool_get_reminders,
    "analyze_budget_detailed": tool_analyze_budget_detailed,
    "setup_budget_mode": tool_setup_budget_mode,
    "get_analytics": tool_get_analytics,
    "get_category_report": tool_get_category_report,
    "get_money_flow": tool_get_money_flow,
    "get_income_outcome_comparison": tool_get_income_outcome_comparison,
    "get_balance_trend": tool_get_balance_trend,
    "suggest": tool_suggest,
    "get_merchants": tool_get_merchants,
    "check_auth_status": tool_check_auth_status,
    "create_transaction": tool_create_transaction,
    "update_transaction": tool_update_transaction,
    "delete_transaction": tool_delete_transaction,
    "create_account": tool_create_account,
    "create_budget": tool_create_budget,
    "update_budget": tool_update_budget,
    "delete_budget": tool_delete_budget,
    "create_reminder": tool_create_reminder,
    "update_reminder": tool_update_reminder,
    "delete_reminder": tool_delete_reminder,
    "create_reminder_marker": tool_create_reminder_marker,
    "delete_reminder_marker": tool_delete_reminder_marker,
}


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------

def _migrate_account_meta() -> None:
    """Migrate account_meta.json from references/ to config.json if needed."""
    old_path = config.ROOT / "references" / "account_meta.json"

    if not old_path.exists():
        return

    with config.state_file_lock(config._cfg_path):
        cfg = config.read_json_state(config._cfg_path)
        if "accounts_meta" in cfg:
            return
        account_meta = config.read_json_state(old_path)
        cfg["accounts_meta"] = account_meta
        config.write_json_state_atomic(config._cfg_path, cfg, indent=2)
    print("Migrated account_meta.json to config.json", file=sys.stderr)



SyncPolicy = _dispatch.SyncPolicy
SYNC_POLICY_CACHE_ONLY = _dispatch.SYNC_POLICY_CACHE_ONLY
SYNC_POLICY_PREFETCH_SYNC = _dispatch.SYNC_POLICY_PREFETCH_SYNC
SYNC_POLICY_FORCED_LIVE = _dispatch.SYNC_POLICY_FORCED_LIVE
TOOL_SYNC_POLICY = _dispatch.TOOL_SYNC_POLICY


def _get_sync_policy(name: str) -> SyncPolicy:
    return _dispatch.get_sync_policy(name)


async def _run_tool(name: str, args: dict) -> str:
    return await _dispatch.run_tool(name, args, TOOLS, _migrate_account_meta)
