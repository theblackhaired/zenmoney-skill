#!/usr/bin/env python3
"""ZenMoney CLI — standalone Python executor for OpenClaw AgentSkill.

Usage:
  python cli.py --list
  python cli.py --describe get_accounts
  python cli.py --call '{"tool":"get_accounts","arguments":{}}'
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Config: load token from config.json or env
# ---------------------------------------------------------------------------
_cfg_path = ROOT / "config.json"
if _cfg_path.exists():
    try:
        _cfg = json.loads(_cfg_path.read_text(encoding="utf-8"))
        if _cfg.get("token") and not os.environ.get("ZENMONEY_TOKEN"):
            os.environ["ZENMONEY_TOKEN"] = _cfg["token"]
    except Exception:
        pass

TOKEN = os.environ.get("ZENMONEY_TOKEN", "")
BASE_URL = "https://api.zenmoney.ru"
CACHE_PATH = ROOT / ".cache.json"
REFS_DIR = ROOT / "references"

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _validate_uuid(val: str, field: str) -> None:
    if not _UUID_RE.match(val):
        raise ValueError(f"Invalid UUID for {field}: {val}")


def _validate_date(val: str, field: str) -> None:
    if not _DATE_RE.match(val):
        raise ValueError(f"Invalid date for {field}: {val}. Expected yyyy-MM-dd")


def _validate_month(val: str, field: str) -> None:
    if not _MONTH_RE.match(val):
        raise ValueError(f"Invalid month for {field}: {val}. Expected yyyy-MM")


def _validate_positive(val: float, field: str) -> None:
    if val < 0:
        raise ValueError(f"{field} must be non-negative, got {val}")


def _today() -> str:
    return datetime.date.today().isoformat()


def _now_ts() -> int:
    return int(time.time())


def _new_uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Cache (file-backed)
# ---------------------------------------------------------------------------
_ENTITY_KEYS = [
    "instrument", "account", "tag", "merchant",
    "transaction", "budget", "reminder", "reminderMarker",
    "user", "country", "company",
]
# Keys whose entities have numeric ids
_NUMERIC_ID_KEYS = {"instrument", "user", "country", "company"}


class Cache:
    """File-backed ZenMoney entity cache with incremental sync."""

    def __init__(self) -> None:
        self.server_timestamp: int = 0
        # entity_name -> {id_str: entity_dict}
        self.data: dict[str, dict[str, Any]] = {k: {} for k in _ENTITY_KEYS}
        self.data["deletion"] = {}

    # -- persistence --------------------------------------------------------

    def load(self) -> None:
        if not CACHE_PATH.exists():
            return
        try:
            raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        self.server_timestamp = raw.get("serverTimestamp", 0)
        for key in _ENTITY_KEYS:
            arr = raw.get(key, [])
            store: dict[str, Any] = {}
            for item in arr:
                if key == "budget":
                    bk = self._budget_key(item)
                    store[bk] = item
                else:
                    store[str(item.get("id", ""))] = item
            self.data[key] = store

    def save(self) -> None:
        out: dict[str, Any] = {"serverTimestamp": self.server_timestamp}
        for key in _ENTITY_KEYS:
            out[key] = list(self.data[key].values())
        CACHE_PATH.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

    # -- apply diff ---------------------------------------------------------

    def apply_diff(self, diff: dict[str, Any]) -> None:
        if "serverTimestamp" in diff:
            self.server_timestamp = diff["serverTimestamp"]
        for key in _ENTITY_KEYS:
            items = diff.get(key)
            if not items:
                continue
            for item in items:
                if key == "budget":
                    bk = self._budget_key(item)
                    self.data[key][bk] = item
                else:
                    self.data[key][str(item.get("id", ""))] = item
        # deletions
        for d in diff.get("deletion", []):
            obj_type = d.get("object", "")
            did = str(d.get("id", ""))
            if obj_type in self.data and did in self.data[obj_type]:
                del self.data[obj_type][did]

    @staticmethod
    def _budget_key(b: dict) -> str:
        tag = b.get("tag")
        return f"{'null' if tag is None else tag}:{b.get('date', '')}"

    # -- helpers ------------------------------------------------------------

    def accounts(self) -> list[dict]:
        return list(self.data["account"].values())

    def transactions(self) -> list[dict]:
        return list(self.data["transaction"].values())

    def tags(self) -> list[dict]:
        return list(self.data["tag"].values())

    def instruments(self) -> list[dict]:
        return list(self.data["instrument"].values())

    def budgets(self) -> list[dict]:
        return list(self.data["budget"].values())

    def reminders(self) -> list[dict]:
        return list(self.data["reminder"].values())

    def reminder_markers(self) -> list[dict]:
        return list(self.data["reminderMarker"].values())

    def merchants(self) -> list[dict]:
        return list(self.data["merchant"].values())

    def users(self) -> list[dict]:
        return list(self.data["user"].values())

    def get(self, entity: str, eid: str) -> dict | None:
        return self.data.get(entity, {}).get(str(eid))

    def get_instrument(self, iid: int | str) -> dict | None:
        return self.data["instrument"].get(str(iid))

    def get_account(self, aid: str) -> dict | None:
        return self.data["account"].get(aid)

    def get_tag(self, tid: str) -> dict | None:
        return self.data["tag"].get(tid)

    def get_merchant(self, mid: str) -> dict | None:
        return self.data["merchant"].get(mid)

    def first_user(self) -> dict | None:
        users = self.users()
        return users[0] if users else None


CACHE = Cache()


# ---------------------------------------------------------------------------
# HTTP client (httpx, async)
# ---------------------------------------------------------------------------
import httpx  # noqa: E402

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=60.0)
    return _client


async def _close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


async def _api_post(endpoint: str, body: dict) -> dict:
    """POST to ZenMoney API, returns parsed JSON."""
    if not TOKEN:
        raise RuntimeError("ZENMONEY_TOKEN is not set. Set env var or add to config.json")
    client = _get_client()
    resp = await client.post(
        f"{BASE_URL}{endpoint}",
        json=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TOKEN}",
        },
    )
    if resp.status_code == 401:
        raise RuntimeError("Token expired (401). Get a new token from https://budgera.com/settings/export")
    resp.raise_for_status()
    return resp.json()


async def _sync(extra: dict | None = None) -> dict:
    """Incremental or full sync via /v8/diff/."""
    body: dict[str, Any] = {
        "currentClientTimestamp": _now_ts(),
        "serverTimestamp": CACHE.server_timestamp,
    }
    if extra:
        body.update(extra)
    diff = await _api_post("/v8/diff/", body)
    CACHE.apply_diff(diff)
    CACHE.save()
    return diff


async def _write_diff(changes: dict) -> dict:
    """Write entities through diff and update cache."""
    body: dict[str, Any] = {
        "currentClientTimestamp": _now_ts(),
        "serverTimestamp": CACHE.server_timestamp,
    }
    body.update(changes)
    diff = await _api_post("/v8/diff/", body)
    CACHE.apply_diff(diff)
    CACHE.save()
    return diff


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------

def _fmt_account(a: dict) -> dict:
    instr = CACHE.get_instrument(a.get("instrument", 0))
    result: dict[str, Any] = {
        "id": a["id"],
        "title": a.get("title", ""),
        "type": a.get("type", ""),
        "balance": a.get("balance", 0),
        "currency": instr["shortTitle"] if instr else "Unknown",
        "inBalance": a.get("inBalance", True),
    }
    if a.get("creditLimit"):
        result["creditLimit"] = a["creditLimit"]
    if a.get("archive"):
        result["archived"] = True
    return result


def _tx_type(t: dict) -> str:
    is_transfer = (
        t.get("outcomeAccount") != t.get("incomeAccount")
        and t.get("outcome", 0) > 0
        and t.get("income", 0) > 0
    )
    if is_transfer:
        return "transfer"
    if t.get("outcome", 0) > 0 and t.get("income", 0) == 0:
        return "expense"
    if t.get("income", 0) > 0 and t.get("outcome", 0) == 0:
        return "income"
    return "unknown"


def _reminder_type(r: dict) -> str:
    """Determine reminder type: expense, income, transfer, or unknown."""
    is_transfer = (
        r.get("outcomeAccount") != r.get("incomeAccount")
        and r.get("outcome", 0) > 0
        and r.get("income", 0) > 0
    )
    if is_transfer:
        return "transfer"
    if r.get("outcome", 0) > 0 and r.get("income", 0) == 0:
        return "expense"
    if r.get("income", 0) > 0 and r.get("outcome", 0) == 0:
        return "income"
    return "unknown"


# ---------------------------------------------------------------------------
# Budget mode defaults
# ---------------------------------------------------------------------------
DEFAULT_BALANCE_VS_EXPENSE: dict[str, Any] = {
    "label": "Баланс vs Расходы",
    "description": "Учитываются все движения денег по счетам, включая счета вне баланса",
    "count_all_movements": True,
    "income": {
        "from_savings": True,
        "from_credit": True,
        "from_debt": True,
        "from_other_off_balance": True,
    },
    "expense": {
        "to_savings": True,
        "to_credit": True,
        "to_debt": True,
        "to_other_off_balance": True,
    },
}

DEFAULT_INCOME_VS_EXPENSE: dict[str, Any] = {
    "label": "Доходы vs Расходы",
    "description": "Исключает лишние переводы из расчётов",
    "count_all_movements": False,
    "income": {
        "from_savings": True,
        "from_credit": False,
        "from_debt": False,
        "from_other_off_balance": False,
    },
    "expense": {
        "to_savings": False,
        "to_credit": True,
        "to_debt": False,
        "to_other_off_balance": False,
    },
}

_BUDGET_MODE_DEFAULTS: dict[str, dict[str, Any]] = {
    "balance_vs_expense": DEFAULT_BALANCE_VS_EXPENSE,
    "income_vs_expense": DEFAULT_INCOME_VS_EXPENSE,
}


def classify_transfer(item: dict, mode_config: dict) -> tuple[str, float] | None:
    """Classify a transfer as ('expense', amount), ('income', amount), or None.

    Uses *mode_config* flags to decide which transfers count.
    """
    to_type = item.get("to_account_type")
    to_subtype = item.get("to_account_subtype")
    to_savings = item.get("to_account_savings", False)

    from_type = item.get("from_account_type")
    from_subtype = item.get("from_account_subtype")
    from_savings = item.get("from_account_savings", False)

    from_in_balance = item.get("from_in_balance", False)
    to_in_balance = item.get("to_in_balance", False)

    count_all = mode_config.get("count_all_movements", False)
    expense_cfg = mode_config.get("expense", {})
    income_cfg = mode_config.get("income", {})

    amount = item.get("amount", 0)

    # --- Expense checks (outflows) ---

    # 1. Transfer TO credit account (debt repayment)
    if to_subtype == "credit" and expense_cfg.get("to_credit", False):
        if count_all or from_in_balance:
            return ("expense", amount)

    # 2. Transfer TO loan/debt account (debt repayment)
    if to_type in ("loan", "debt") and expense_cfg.get("to_debt", False):
        if count_all or from_in_balance:
            return ("expense", amount)

    # 3. Transfer TO savings account (withdrawal from circulation)
    if (to_subtype == "savings" or to_savings) and expense_cfg.get("to_savings", False):
        if count_all or from_in_balance:
            return ("expense", amount)

    # --- Income checks (inflows) ---

    # 4. Transfer FROM savings account (return to circulation)
    if (from_subtype == "savings" or from_savings) and income_cfg.get("from_savings", False):
        if count_all or to_in_balance:
            return ("income", amount)

    # 5. Transfer FROM credit account
    if from_subtype == "credit" and income_cfg.get("from_credit", False):
        if count_all or to_in_balance:
            return ("income", amount)

    # 6. Transfer FROM loan/debt account
    if from_type in ("loan", "debt") and income_cfg.get("from_debt", False):
        if count_all or to_in_balance:
            return ("income", amount)

    # --- Generic off-balance checks ---

    # 7. Generic off-balance outflow (from inBalance to off-balance)
    if expense_cfg.get("to_other_off_balance", False):
        if count_all or (from_in_balance and not to_in_balance):
            return ("expense", amount)

    # 8. Generic off-balance inflow (from off-balance to inBalance)
    if income_cfg.get("from_other_off_balance", False):
        if count_all or (not from_in_balance and to_in_balance):
            return ("income", amount)

    # 9. No balance impact
    return None


def _fmt_transaction(t: dict) -> dict:
    tt = _tx_type(t)
    out_acct = CACHE.get_account(t.get("outcomeAccount", ""))
    in_acct = CACHE.get_account(t.get("incomeAccount", ""))
    out_instr = CACHE.get_instrument(t.get("outcomeInstrument", 0))
    in_instr = CACHE.get_instrument(t.get("incomeInstrument", 0))
    categories = []
    for tid in (t.get("tag") or []):
        tag = CACHE.get_tag(tid)
        if tag:
            categories.append(tag["title"])
    merchant_name = None
    if t.get("merchant"):
        m = CACHE.get_merchant(t["merchant"])
        if m:
            merchant_name = m["title"]

    result: dict[str, Any] = {"id": t["id"], "date": t.get("date", ""), "type": tt}

    if tt == "expense":
        result["amount"] = t.get("outcome", 0)
        result["currency"] = out_instr["shortTitle"] if out_instr else "RUB"
        result["account"] = out_acct["title"] if out_acct else None
    elif tt == "income":
        result["amount"] = t.get("income", 0)
        result["currency"] = in_instr["shortTitle"] if in_instr else "RUB"
        result["account"] = in_acct["title"] if in_acct else None
    else:  # transfer
        result["outcomeAmount"] = t.get("outcome", 0)
        result["outcomeCurrency"] = out_instr["shortTitle"] if out_instr else "RUB"
        result["fromAccount"] = out_acct["title"] if out_acct else None
        result["incomeAmount"] = t.get("income", 0)
        result["incomeCurrency"] = in_instr["shortTitle"] if in_instr else "RUB"
        result["toAccount"] = in_acct["title"] if in_acct else None

    if categories:
        result["categories"] = categories
    if t.get("payee"):
        result["payee"] = t["payee"]
    if t.get("comment"):
        result["comment"] = t["comment"]
    if t.get("hold"):
        result["hold"] = True
    if merchant_name:
        result["merchant"] = merchant_name
    return result


def _fmt_budget(b: dict) -> dict:
    tag = CACHE.get_tag(b["tag"]) if b.get("tag") else None
    return {
        "category": tag["title"] if tag else ("Total" if b.get("tag") is None else b.get("tag")),
        "month": b.get("date", ""),
        "income": b.get("income", 0),
        "incomeLock": b.get("incomeLock", False),
        "outcome": b.get("outcome", 0),
        "outcomeLock": b.get("outcomeLock", False),
    }


def _fmt_reminder(r: dict) -> dict:
    in_acct = CACHE.get_account(r.get("incomeAccount", ""))
    out_acct = CACHE.get_account(r.get("outcomeAccount", ""))
    categories = []
    for tid in (r.get("tag") or []):
        tag = CACHE.get_tag(tid)
        if tag:
            categories.append(tag["title"])
    result: dict[str, Any] = {
        "id": r["id"],
        "payee": r.get("payee"),
        "comment": r.get("comment"),
    }
    if r.get("income", 0) != 0:
        result["income"] = r["income"]
    if r.get("outcome", 0) != 0:
        result["outcome"] = r["outcome"]
    result["fromAccount"] = out_acct["title"] if out_acct else None
    result["toAccount"] = in_acct["title"] if in_acct else None
    if categories:
        result["categories"] = categories
    result["interval"] = r.get("interval")
    result["step"] = r.get("step")
    result["startDate"] = r.get("startDate")
    result["endDate"] = r.get("endDate")
    result["notify"] = r.get("notify", True)
    return result


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
            "start_date": "str yyyy-MM-dd (required)",
            "end_date": "str yyyy-MM-dd (default today)",
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
        "desc": "Get scheduled payment reminders with their markers. When marker_from/marker_to are specified, filters reminders by marker dates in that period and sorts by first marker date. Without these params, uses legacy sort by startDate.",
        "params": {
            "marker_from": "str yyyy-MM-dd (optional) — start of marker date range (inclusive)",
            "marker_to": "str yyyy-MM-dd (optional) — end of marker date range (inclusive)",
            "category": "str (optional) — filter by category name",
            "type": "str expense|income|transfer|all (optional, default all) — filter by operation type",
            "include_processed": "bool (default false)",
            "active_only": "bool (default true)",
            "limit": "int (default 50)",
            "markers_limit": "int (default 5) — max markers per reminder (only used in legacy mode without marker_from/marker_to)",
            "offset": "int (default 0)",
        },
    },
    "rebuild_references": {
        "desc": "Rebuild reference cache files (accounts.json, categories.json) from ZenMoney data. Run after account/category changes.",
        "params": {},
    },
    "analyze_budget_detailed": {
        "desc": "Detailed budget analysis with income vs expenses breakdown by category, plan vs fact comparison, payment calendar, and balance forecast",
        "params": {
            "start_date": "str yyyy-MM-dd (optional, auto-calculated from billing_period_start_day if not provided)",
            "end_date": "str yyyy-MM-dd (optional, auto-calculated from billing_period_start_day if not provided)",
            "include_off_balance": "bool (default false) — include accounts with inBalance=false",
            "budget_mode": "str balance_vs_expense|income_vs_expense (default from config or income_vs_expense) — controls which transfers count as income/expense",
            "group_by": "str category|date (default category)",
            "show_forecast": "bool (default true) — show daily balance forecast",
            "show_calendar": "bool (default true) — show payment calendar",
        },
    },
    "get_analytics": {
        "desc": "Spending/income analytics grouped by category, account, or merchant",
        "params": {
            "start_date": "str yyyy-MM-dd (required)",
            "end_date": "str yyyy-MM-dd (default today)",
            "group_by": "str category|account|merchant (default category)",
            "type": "str expense|income|all (default expense)",
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
            "category": "str name or UUID, 'ALL' for aggregate (required)",
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
            "category": "str name or UUID (required)",
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
            "category": "str name or UUID (required)",
        },
    },
    "create_reminder": {
        "desc": "Create a recurring reminder (planned transaction)",
        "params": {
            "type": "str expense|income|transfer (required)",
            "amount": "float (required, positive)",
            "account_id": "str UUID (required)",
            "to_account_id": "str UUID (for transfers)",
            "category_ids": "list[str] UUIDs (optional)",
            "payee": "str (optional)",
            "comment": "str (optional)",
            "interval": "str day|week|month|year (required)",
            "step": "int (default 1)",
            "points": "list[int] (optional)",
            "start_date": "str yyyy-MM-dd (default today)",
            "end_date": "str yyyy-MM-dd (optional)",
            "notify": "bool (default true)",
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

def _g(key: str, args: dict, default: Any = None) -> Any:
    return args.get(key, default)


def _find_category_id(name: str) -> str:
    """Resolve category name or UUID to a category id."""
    if name.upper() == "ALL":
        return "00000000-0000-0000-0000-000000000000"
    if CACHE.get_tag(name):
        return name
    for tag in CACHE.tags():
        if tag["title"].lower() == name.lower():
            return tag["id"]
    raise ValueError(f"Category not found: {name}")


def _build_tx_spec(
    tx_type: str,
    amount: float,
    account_id: str,
    to_account_id: str | None,
    currency_id: int | None,
    income_amount: float | None,
) -> dict:
    """Build incomeAccount/outcomeAccount/income/outcome fields from type."""
    account = CACHE.get_account(account_id)
    if not account:
        raise ValueError(f"Account not found: {account_id}")
    instrument_id = currency_id if currency_id is not None else account.get("instrument", 0)

    spec: dict[str, Any] = {
        "incomeInstrument": instrument_id,
        "incomeAccount": account_id,
        "income": 0,
        "outcomeInstrument": instrument_id,
        "outcomeAccount": account_id,
        "outcome": 0,
    }

    if tx_type == "expense":
        spec["outcome"] = amount
        spec["outcomeAccount"] = account_id
        spec["outcomeInstrument"] = instrument_id
        spec["incomeAccount"] = account_id
        spec["incomeInstrument"] = instrument_id
        spec["income"] = 0
    elif tx_type == "income":
        spec["income"] = amount
        spec["incomeAccount"] = account_id
        spec["incomeInstrument"] = instrument_id
        spec["outcomeAccount"] = account_id
        spec["outcomeInstrument"] = instrument_id
        spec["outcome"] = 0
    elif tx_type == "transfer":
        if not to_account_id:
            raise ValueError("to_account_id is required for transfer type")
        to_acct = CACHE.get_account(to_account_id)
        if not to_acct:
            raise ValueError(f"Destination account not found: {to_account_id}")
        spec["outcome"] = amount
        spec["outcomeAccount"] = account_id
        spec["outcomeInstrument"] = account.get("instrument", 0)
        spec["incomeAccount"] = to_account_id
        spec["incomeInstrument"] = to_acct.get("instrument", 0)
        if account.get("instrument") != to_acct.get("instrument"):
            if not income_amount:
                raise ValueError("income_amount is required for cross-currency transfers")
            spec["income"] = income_amount
        else:
            spec["income"] = amount
    return spec


# -- Read tools --

async def tool_get_accounts(args: dict) -> str:
    include_archived = bool(_g("include_archived", args, False))
    accounts = CACHE.accounts()
    if not include_archived:
        accounts = [a for a in accounts if not a.get("archive")]
    return json.dumps([_fmt_account(a) for a in accounts], ensure_ascii=False)


async def tool_get_transactions(args: dict) -> str:
    start_date = args["start_date"]
    _validate_date(start_date, "start_date")
    end_date = _g("end_date", args) or _today()
    if _g("end_date", args):
        _validate_date(end_date, "end_date")
    account_id = _g("account_id", args)
    category_id = _g("category_id", args)
    tx_type = _g("type", args)
    limit = min(int(_g("limit", args, 100)), 500)
    offset = int(_g("offset", args, 0))

    if account_id:
        _validate_uuid(account_id, "account_id")
    if category_id:
        _validate_uuid(category_id, "category_id")

    txs = [t for t in CACHE.transactions() if not t.get("deleted")]
    txs = [t for t in txs if t.get("date", "") >= start_date and t.get("date", "") <= end_date]

    if account_id:
        txs = [t for t in txs if t.get("incomeAccount") == account_id or t.get("outcomeAccount") == account_id]
    if category_id:
        txs = [t for t in txs if category_id in (t.get("tag") or [])]
    if tx_type:
        txs = [t for t in txs if _tx_type(t) == tx_type]

    txs.sort(key=lambda t: (t.get("date", ""), t.get("created", 0)), reverse=True)
    total = len(txs)
    limited = txs[offset:offset + limit]
    result: dict[str, Any] = {"transactions": [_fmt_transaction(t) for t in limited]}
    if total > offset + len(limited):
        result["truncated"] = True
        result["total"] = total
        result["showing"] = len(limited)
        result["offset"] = offset
    return json.dumps(result, ensure_ascii=False)


async def tool_get_categories(args: dict) -> str:
    tags = CACHE.tags()
    roots = [t for t in tags if not t.get("parent")]
    children = [t for t in tags if t.get("parent")]
    tree = []
    for root in roots:
        child_list = [{"id": c["id"], "title": c["title"]} for c in children if c.get("parent") == root["id"]]
        node: dict[str, Any] = {"id": root["id"], "title": root["title"]}
        if child_list:
            node["children"] = child_list
        tree.append(node)
    return json.dumps(tree, ensure_ascii=False)


async def tool_get_instruments(args: dict) -> str:
    include_all = bool(_g("include_all", args, False))
    instruments = CACHE.instruments()
    if not include_all:
        used_ids = {a.get("instrument") for a in CACHE.accounts()}
        instruments = [i for i in instruments if i.get("id") in used_ids]
    formatted = [
        {"id": i["id"], "code": i.get("shortTitle", ""), "title": i.get("title", ""),
         "symbol": i.get("symbol", ""), "rate": i.get("rate", 1)}
        for i in instruments
    ]
    return json.dumps(formatted, ensure_ascii=False)


async def tool_get_budgets(args: dict) -> str:
    month = args["month"]
    _validate_month(month, "month")
    month_date = f"{month}-01"
    budgets = [b for b in CACHE.budgets() if b.get("date") == month_date]
    return json.dumps([_fmt_budget(b) for b in budgets], ensure_ascii=False)


async def tool_get_reminders(args: dict) -> str:
    include_processed = bool(_g("include_processed", args, False))
    active_only = bool(_g("active_only", args, True))
    limit = int(_g("limit", args, 50))
    markers_limit = int(_g("markers_limit", args, 5))
    offset = int(_g("offset", args, 0))
    marker_from = _g("marker_from", args)
    marker_to = _g("marker_to", args)
    category = _g("category", args)
    r_type = _g("type", args, "all")
    today_str = _today()

    if marker_from:
        _validate_date(marker_from, "marker_from")
    if marker_to:
        _validate_date(marker_to, "marker_to")

    reminders = CACHE.reminders()
    if active_only:
        reminders = [r for r in reminders if not r.get("endDate") or r["endDate"] >= today_str]

    # Filter by category
    if category:
        filtered = []
        for r in reminders:
            tags = r.get("tag") or []
            cat_names = []
            for tid in tags:
                tag = CACHE.get_tag(tid)
                if tag:
                    cat_names.append(tag["title"])
            if category in cat_names:
                filtered.append(r)
        reminders = filtered

    # Filter by type
    if r_type and r_type != "all":
        reminders = [r for r in reminders if _reminder_type(r) == r_type]

    # Marker-based filtering mode
    if marker_from and marker_to:
        all_markers = CACHE.reminder_markers()
        result_list = []
        for r in reminders:
            markers = [m for m in all_markers if m.get("reminder") == r["id"]]
            if not include_processed:
                markers = [m for m in markers if m.get("state") == "planned"]
            # Filter markers to the requested date range
            markers = [m for m in markers if marker_from <= m.get("date", "") <= marker_to]
            if not markers:
                continue
            markers.sort(key=lambda m: m.get("date", ""))
            fmt = _fmt_reminder(r)
            fmt["type"] = _reminder_type(r)
            fmt["markers"] = [
                {"id": m["id"], "date": m.get("date"), "state": m.get("state"),
                 "income": m.get("income", 0), "outcome": m.get("outcome", 0)}
                for m in markers
            ]
            fmt["markers_total_outcome"] = sum(m.get("outcome", 0) for m in markers)
            fmt["markers_total_income"] = sum(m.get("income", 0) for m in markers)
            fmt["_sort_key"] = markers[0].get("date", "")
            result_list.append(fmt)

        result_list.sort(key=lambda x: x.pop("_sort_key"))
        total = len(result_list)
        eff_limit = min(limit, 200)
        result_list = result_list[offset:offset + eff_limit]

        output: dict[str, Any] = {"reminders": result_list, "mode": "marker_range", "marker_from": marker_from, "marker_to": marker_to}
        if total > offset + len(result_list):
            output["truncated"] = True
        output["total"] = total
        output["showing"] = len(result_list)
        output["offset"] = offset
        return json.dumps(output, ensure_ascii=False)

    # Legacy mode — sort by startDate
    reminders.sort(key=lambda r: r.get("startDate", ""), reverse=True)
    total = len(reminders)
    eff_limit = min(limit, 200)
    reminders = reminders[offset:offset + eff_limit]

    result_list = []
    for r in reminders:
        fmt = _fmt_reminder(r)
        fmt["type"] = _reminder_type(r)
        markers = [m for m in CACHE.reminder_markers() if m.get("reminder") == r["id"]]
        if not include_processed:
            markers = [m for m in markers if m.get("state") == "planned"]
        markers.sort(key=lambda m: m.get("date", ""))
        markers = markers[:markers_limit]
        if markers:
            fmt["markers"] = [
                {"id": m["id"], "date": m.get("date"), "state": m.get("state"),
                 "income": m.get("income", 0), "outcome": m.get("outcome", 0)}
                for m in markers
            ]
        result_list.append(fmt)

    output: dict[str, Any] = {"reminders": result_list}
    if total > offset + len(result_list):
        output["truncated"] = True
        output["total"] = total
        output["showing"] = len(result_list)
        output["offset"] = offset
    return json.dumps(output, ensure_ascii=False)


async def tool_rebuild_references(args: dict) -> str:
    REFS_DIR.mkdir(exist_ok=True)

    # --- Load manual account metadata (descriptions, roles) ---
    meta_path = REFS_DIR / "account_meta.json"
    account_meta: dict[str, dict] = {}
    if meta_path.exists():
        try:
            account_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # --- accounts.json ---
    accounts_out = []
    for a in CACHE.data.get("account", {}).values():
        company = CACHE.data.get("company", {}).get(str(a.get("company", "")), {})
        instr = CACHE.data.get("instrument", {}).get(str(a.get("instrument", "")), {})

        # Determine subtype
        atype = a.get("type", "")
        credit_limit = a.get("creditLimit", 0)
        savings = a.get("savings", False)
        if atype == "ccard" and credit_limit > 0:
            subtype = "credit"
        elif atype == "ccard":
            subtype = "debit"
        elif atype == "checking" and savings:
            subtype = "savings"
        elif atype == "checking":
            subtype = "checking"
        elif atype == "cash":
            subtype = "cash"
        elif atype == "debt":
            subtype = "debt"
        else:
            subtype = atype

        accounts_out.append({
            "id": a["id"],
            "title": a.get("title", ""),
            "bank": company.get("title"),
            "type": atype,
            "subtype": subtype,
            "inBalance": a.get("inBalance", False),
            "balance": a.get("balance", 0),
            "creditLimit": credit_limit,
            "currency": instr.get("shortTitle", "?"),
            "savings": savings,
            "archived": a.get("archive", False),
            "description": account_meta.get(a["id"], {}).get("description"),
        })

    accounts_out.sort(key=lambda x: (x["archived"], not x["inBalance"], x["title"]))
    accounts_data = {
        "generated": _today(),
        "total": len(accounts_out),
        "active": len([a for a in accounts_out if not a["archived"]]),
        "in_balance": len([a for a in accounts_out if a["inBalance"] and not a["archived"]]),
        "accounts": accounts_out,
    }
    (REFS_DIR / "accounts.json").write_text(
        json.dumps(accounts_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # --- categories.json ---
    tags = list(CACHE.data.get("tag", {}).values())
    parents = [t for t in tags if not t.get("parent")]
    children_map: dict[str, list] = {}
    for t in tags:
        pid = t.get("parent")
        if pid:
            children_map.setdefault(pid, []).append(t)

    # Build hierarchical structure with parent_id in children
    categories_out = []
    for p in sorted(parents, key=lambda t: t.get("title", "")):
        kids = children_map.get(p["id"], [])
        cat = {
            "id": p["id"],
            "title": p.get("title", ""),
            "children": [
                {"id": c["id"], "title": c.get("title", ""), "parent_id": p["id"]}
                for c in sorted(kids, key=lambda c: c.get("title", ""))
            ],
        }
        categories_out.append(cat)

    # Build flat index for fast lookup
    index = {}
    for p in parents:
        index[p["id"]] = {
            "title": p.get("title", ""),
            "parent_id": None,
            "parent_title": None,
            "is_parent": True,
            "children_count": len(children_map.get(p["id"], [])),
        }

    for t in tags:
        if t.get("parent"):
            parent = next((p for p in parents if p["id"] == t["parent"]), None)
            index[t["id"]] = {
                "title": t.get("title", ""),
                "parent_id": t["parent"],
                "parent_title": parent.get("title", "") if parent else None,
                "is_parent": False,
                "children_count": 0,
            }

    categories_data = {
        "generated": _today(),
        "total": len(tags),
        "parents": len(parents),
        "categories": categories_out,
        "index": index,
    }
    (REFS_DIR / "categories.json").write_text(
        json.dumps(categories_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return json.dumps({
        "status": "ok",
        "generated": _today(),
        "accounts": f"{len(accounts_out)} accounts ({accounts_data['active']} active, {accounts_data['in_balance']} in balance)",
        "categories": f"{len(tags)} categories ({len(parents)} parent, {len(tags) - len(parents)} child, {len(index)} indexed)",
        "files": ["references/accounts.json", "references/categories.json"],
        "features": ["parent_id in children", "flat index for fast lookup"],
    }, ensure_ascii=False)


async def tool_analyze_budget_detailed(args: dict) -> str:
    """Detailed budget analysis with income vs expenses by category."""

    # Load category index from references
    cat_index_path = REFS_DIR / "categories.json"
    cat_index = {}
    if cat_index_path.exists():
        try:
            cat_data = json.loads(cat_index_path.read_text(encoding="utf-8"))
            cat_index = cat_data.get("index", {})
        except Exception:
            pass

    # Load accounts reference
    accounts_ref_path = REFS_DIR / "accounts.json"
    accounts_map = {}
    if accounts_ref_path.exists():
        try:
            acc_data = json.loads(accounts_ref_path.read_text(encoding="utf-8"))
            accounts_map = {a["id"]: a for a in acc_data.get("accounts", [])}
        except Exception:
            pass

    # Load config for billing period and budget mode
    cfg_path = ROOT / "config.json"
    config: dict[str, Any] = {}
    if cfg_path.exists():
        try:
            config = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Resolve budget mode
    mode_name = _g("budget_mode", args) or config.get("budget_mode") or "income_vs_expense"
    mode_config = config.get("budget_modes", {}).get(mode_name)
    if not mode_config:
        mode_config = _BUDGET_MODE_DEFAULTS.get(mode_name, DEFAULT_INCOME_VS_EXPENSE)

    # Determine period from billing_period_start_day or use provided dates
    include_off_balance = _g("include_off_balance", args, False)
    show_forecast = _g("show_forecast", args, True)
    show_calendar = _g("show_calendar", args, True)

    # Calculate period dates
    if _g("start_date", args):
        start_date = args["start_date"]
        _validate_date(start_date, "start_date")
        end_date = _g("end_date", args) or _today()
        if _g("end_date", args):
            _validate_date(end_date, "end_date")
    else:
        # Auto-calculate from billing_period_start_day
        billing_start_day = config.get("billing_period_start_day", 1)

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
        if not cat_id or cat_id not in cat_index:
            return {
                "category_id": cat_id or "uncategorized",
                "category_name": "Без категории",
                "parent_id": None,
                "parent_name": None,
                "is_parent": False,
            }
        meta = cat_index[cat_id]
        return {
            "category_id": cat_id,
            "category_name": meta.get("title", "Unknown"),
            "parent_id": meta.get("parent_id"),
            "parent_name": meta.get("parent_title"),
            "is_parent": meta.get("is_parent", False),
        }

    # Get actual transactions
    txs = [t for t in CACHE.transactions() if not t.get("deleted")]
    txs = [t for t in txs if t.get("date", "") >= start_date and t.get("date", "") <= end_date]

    # Get reminders with markers
    reminders_income = []
    reminders_expense = []
    reminders_transfer = []

    for r in (CACHE.reminders() or []):
        if r.get("deleted"):
            continue
        markers = [m for m in (CACHE.reminder_markers() or [])
                  if m.get("reminder") == r["id"]
                  and not m.get("deleted")
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
            "categories": [CACHE.get_tag(tid)["title"] if CACHE.get_tag(tid) else None for tid in (r.get("tag") or [])],
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

    # Get budgets
    month = start_date[:7]  # yyyy-MM
    # Get fresh budgets from API instead of cache
    budgets_raw = json.loads(await tool_get_budgets({"month": month}))
    budgets_map = {}
    for b in budgets_raw:
        cat_name = b.get("category")
        if cat_name:
            budgets_map[cat_name] = {
                "income": b.get("income", 0),
                "outcome": b.get("outcome", 0),
            }

    # Process income
    income_by_category: dict[str, dict] = {}
    for tx in txs:
        tt = _tx_type(tx)
        if tt != "income":
            continue

        # Check if account is in balance
        acct_id = tx.get("incomeAccount")
        if not include_off_balance:
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

        # Check account
        if not include_off_balance:
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

        income_by_category[cat_key]["planned"] += sum(m["income"] for m in rem["markers"] if m.get("state") != "processed")
        for marker in rem["markers"]:
            if marker.get("state") == "processed":
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

        # Check if account is in balance
        acct_id = tx.get("outcomeAccount")
        if not include_off_balance:
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
                "budget": 0,
                "items": [],
            }

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

        # Check account
        if not include_off_balance:
            acct = accounts_map.get(rem.get("account_id"), {})
            if not acct.get("inBalance", False):
                continue

        if cat_key not in expense_by_category:
            expense_by_category[cat_key] = {
                **cat_meta,
                "actual": 0,
                "planned_from_reminders": 0,
                "budget": 0,
                "items": [],
            }

        expense_by_category[cat_key]["planned_from_reminders"] += sum(m["outcome"] for m in rem["markers"] if m.get("state") != "processed")
        for marker in rem["markers"]:
            if marker.get("state") == "processed":
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
        cat_name = cat_data["category_name"]
        if cat_name in budgets_map:
            cat_data["budget"] = budgets_map[cat_name]["outcome"]

    # Add budget-only categories (categories with budget but no reminders/transactions)
    for cat_name, budget_data in budgets_map.items():
        if budget_data["outcome"] == 0:
            continue

        # Find category in expense_by_category
        found = False
        for cat_key, cat_data in expense_by_category.items():
            if cat_data["category_name"] == cat_name:
                found = True
                break

        # If not found, create new expense category
        if not found:
            # Find category UUID from cache
            cat_obj = None
            for c in (CACHE.tags() or []):
                if c.get("title") == cat_name:
                    cat_obj = c
                    break

            if cat_obj:
                cat_meta = enrich_category(cat_obj["id"])
                cat_key = cat_meta["category_id"]

                expense_by_category[cat_key] = {
                    **cat_meta,
                    "actual": 0,
                    "planned_from_reminders": 0,
                    "budget": budget_data["outcome"],
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

        # Skip if both are off-balance and we're not including off-balance
        if not include_off_balance and not from_in_balance and not to_in_balance:
            continue

        # Transfer affects balance if:
        # - From inBalance to off-balance (outflow)
        # - From off-balance to inBalance (inflow) - only if include_off_balance
        # - Between inBalance accounts (no net effect on total balance, but show in calendar)

        transfer_items.append({
            "date": tx.get("date"),
            "from_account": from_acct.get("title", "Unknown"),
            "to_account": to_acct.get("title", "Unknown"),
            "amount": tx.get("outcome", 0),
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

        if not include_off_balance and not from_in_balance and not to_in_balance:
            continue

        for marker in rem["markers"]:
            if marker.get("state") == "processed":
                continue

            transfer_items.append({
                "date": marker["date"],
                "from_account": from_acct.get("title", "Unknown"),
                "to_account": to_acct.get("title", "Unknown"),
                "amount": marker["outcome"],
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

    # Calculate totals
    total_income_actual = sum(c["actual"] for c in income_by_category.values())
    total_income_planned = sum(c["planned"] for c in income_by_category.values())

    # For expenses, use max(actual + planned_from_reminders, budget) for each category
    # This prevents double-counting when actual spending is within budget
    total_expense_expected = sum(
        max(c["actual"] + c["planned_from_reminders"], c["budget"])
        for c in expense_by_category.values()
    )

    # Calculate transfer totals using mode-aware classify_transfer
    total_transfers_out = 0
    total_transfers_in = 0
    for item in transfer_items:
        result = classify_transfer(item, mode_config)
        if result:
            transfer_type, amount = result
            if transfer_type == "expense":
                total_transfers_out += amount
            elif transfer_type == "income":
                total_transfers_in += amount

    total_transfers_net = total_transfers_out - total_transfers_in

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
                "expected": total_expense_expected,
                "description": "max(actual + planned_from_reminders, budget) per category",
            },
            "transfers": {
                "out": total_transfers_out,
                "in": total_transfers_in,
                "net": total_transfers_net,
                "description": "Net transfers based on account types (credit, savings, debt) and inBalance flags",
            },
            "balance": (total_income_actual + total_income_planned) - total_expense_expected - total_transfers_net,
        },
        "income": sorted(income_by_category.values(), key=lambda x: x["actual"] + x["planned"], reverse=True),
        "expenses": sorted(expense_by_category.values(), key=lambda x: max(x["actual"] + x["planned_from_reminders"], x["budget"]), reverse=True),
        "transfers": sorted(transfer_items, key=lambda x: x["date"]),
    }

    # Add calendar if requested
    if show_calendar:
        calendar = []
        # Add all items from income and expenses
        for cat in income_by_category.values():
            for item in cat["items"]:
                calendar.append({
                    "date": item["date"],
                    "type": "income",
                    "category": cat["category_name"],
                    "payee": item["payee"],
                    "amount": item["amount"],
                    "status": item["status"],
                })
        for cat in expense_by_category.values():
            for item in cat["items"]:
                calendar.append({
                    "date": item["date"],
                    "type": "expense",
                    "category": cat["category_name"],
                    "payee": item["payee"],
                    "amount": item["amount"],
                    "status": item["status"],
                })
        # Add transfers
        for item in transfer_items:
            calendar.append({
                "date": item["date"],
                "type": "transfer",
                "from_account": item["from_account"],
                "to_account": item["to_account"],
                "amount": item["amount"],
                "comment": item["comment"],
                "status": item["status"],
                "from_in_balance": item["from_in_balance"],
                "to_in_balance": item["to_in_balance"],
            })
        calendar.sort(key=lambda x: x["date"])
        result["calendar"] = calendar

    # Add forecast if requested
    if show_forecast:
        # Get current balance
        current_balance = sum(
            a.get("balance", 0)
            for a in accounts_map.values()
            if include_off_balance or a.get("inBalance", False)
        )

        # Build daily forecast
        forecast = []
        balance = current_balance

        # Group calendar by date
        from collections import defaultdict
        daily_ops: dict[str, list] = defaultdict(list)
        if show_calendar and "calendar" in result:
            for op in result["calendar"]:
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
                        balance -= op["amount"]
                    elif not from_in and to_in:
                        # Inflow to tracked balance (only if include_off_balance)
                        balance += op["amount"]
                    # else: both in or both out = no net change to tracked balance

            if ops:  # Only add to forecast if there were operations
                forecast.append({
                    "date": date_str,
                    "balance": round(balance, 2),
                    "operations_count": len(ops),
                })

            current_date += datetime.timedelta(days=1)

        result["forecast"] = forecast

    return json.dumps(result, ensure_ascii=False, indent=2)


async def tool_get_analytics(args: dict) -> str:
    start_date = args["start_date"]
    _validate_date(start_date, "start_date")
    end_date = _g("end_date", args) or _today()
    if _g("end_date", args):
        _validate_date(end_date, "end_date")
    group_by = _g("group_by", args, "category")
    an_type = _g("type", args, "expense")

    txs = [t for t in CACHE.transactions() if not t.get("deleted")]
    txs = [t for t in txs if t.get("date", "") >= start_date and t.get("date", "") <= end_date]

    # Filter by type
    filtered = []
    for t in txs:
        tt = _tx_type(t)
        if an_type == "expense" and tt == "expense":
            filtered.append(t)
        elif an_type == "income" and tt == "income":
            filtered.append(t)
        elif an_type == "all" and tt in ("expense", "income"):
            filtered.append(t)

    # Group
    groups: dict[str, dict[str, Any]] = {}
    for tx in filtered:
        key = "Uncategorized"
        currency = "RUB"

        if group_by == "category":
            tag_ids = tx.get("tag") or []
            if tag_ids:
                tag = CACHE.get_tag(tag_ids[0])
                key = tag["title"] if tag else "Uncategorized"
            acct_id = tx.get("outcomeAccount") if tx.get("outcome", 0) > 0 else tx.get("incomeAccount")
            acct = CACHE.get_account(acct_id) if acct_id else None
            instr = CACHE.get_instrument(acct["instrument"]) if acct else None
            currency = instr["shortTitle"] if instr else "RUB"
        elif group_by == "account":
            acct_id = tx.get("incomeAccount") if an_type == "income" else tx.get("outcomeAccount")
            acct = CACHE.get_account(acct_id) if acct_id else None
            key = acct["title"] if acct else "Unknown Account"
            instr = CACHE.get_instrument(acct["instrument"]) if acct else None
            currency = instr["shortTitle"] if instr else "RUB"
        elif group_by == "merchant":
            if tx.get("merchant"):
                m = CACHE.get_merchant(tx["merchant"])
                key = m["title"] if m else (tx.get("payee") or "Unknown Merchant")
            elif tx.get("payee"):
                key = tx["payee"]
            acct_id = tx.get("outcomeAccount") if tx.get("outcome", 0) > 0 else tx.get("incomeAccount")
            acct = CACHE.get_account(acct_id) if acct_id else None
            instr = CACHE.get_instrument(acct["instrument"]) if acct else None
            currency = instr["shortTitle"] if instr else "RUB"

        if key not in groups:
            groups[key] = {"income": 0, "outcome": 0, "count": 0, "currency": currency}
        g = groups[key]
        g["income"] += tx.get("income", 0)
        g["outcome"] += tx.get("outcome", 0)
        g["count"] += 1

    grand_total = 0.0
    for g in groups.values():
        if an_type == "expense":
            grand_total += g["outcome"]
        elif an_type == "income":
            grand_total += g["income"]
        else:
            grand_total += g["income"] + g["outcome"]

    groups_list = []
    for name, data in groups.items():
        total_val = data["outcome"] if an_type == "expense" else data["income"] if an_type == "income" else data["income"] + data["outcome"]
        entry: dict[str, Any] = {"name": name, "total": total_val, "count": data["count"], "currency": data["currency"]}
        if an_type == "all":
            entry["income"] = data["income"]
            entry["outcome"] = data["outcome"]
        groups_list.append(entry)
    groups_list.sort(key=lambda x: x["total"], reverse=True)

    return json.dumps({
        "period": {"from": start_date, "to": end_date},
        "type": an_type,
        "groupBy": group_by,
        "grandTotal": grand_total,
        "transactionCount": len(filtered),
        "groups": groups_list,
    }, ensure_ascii=False)


async def tool_suggest(args: dict) -> str:
    payee = args["payee"]
    result = await _api_post("/v8/suggest/", {"payee": payee})
    return json.dumps(result, ensure_ascii=False)


async def tool_get_merchants(args: dict) -> str:
    search = _g("search", args)
    limit = int(_g("limit", args, 50))
    offset = int(_g("offset", args, 0))
    merchants = CACHE.merchants()
    if search:
        q = search.lower()
        merchants = [m for m in merchants if q in m.get("title", "").lower()]
    total = len(merchants)
    eff_limit = min(limit, 200)
    limited = merchants[offset:offset + eff_limit]
    formatted = [{"id": m["id"], "title": m["title"]} for m in limited]
    result: dict[str, Any] = {"merchants": formatted}
    if total > offset + len(limited):
        result["truncated"] = True
        result["total"] = total
        result["showing"] = len(limited)
        result["offset"] = offset
    return json.dumps(result, ensure_ascii=False)


async def tool_check_auth_status(args: dict) -> str:
    try:
        await _sync()
        return json.dumps({"status": "authenticated", "message": "Token is valid and working"}, ensure_ascii=False)
    except Exception as e:
        msg = str(e)
        return json.dumps({
            "status": "error",
            "error": msg,
            "solution": (
                "Token expired. Get a new token from https://budgera.com/settings/export"
                if "401" in msg or "expired" in msg.lower()
                else "Check your credentials or network connection"
            ),
        }, ensure_ascii=False)


# -- Write tools --

async def tool_create_transaction(args: dict) -> str:
    tx_type = args["type"]
    amount = float(args["amount"])
    account_id = args["account_id"]
    to_account_id = _g("to_account_id", args)
    category_ids = _g("category_ids", args)
    date = _g("date", args) or _today()
    payee = _g("payee", args)
    comment = _g("comment", args)
    currency_id = _g("currency_id", args)
    income_amount = _g("income_amount", args)

    _validate_uuid(account_id, "account_id")
    if to_account_id:
        _validate_uuid(to_account_id, "to_account_id")
    if category_ids:
        for i, cid in enumerate(category_ids):
            _validate_uuid(cid, f"category_ids[{i}]")
    if _g("date", args):
        _validate_date(date, "date")
    if currency_id is not None:
        currency_id = int(currency_id)
    if income_amount is not None:
        income_amount = float(income_amount)

    spec = _build_tx_spec(tx_type, amount, account_id, to_account_id, currency_id, income_amount)

    user = CACHE.first_user()
    if not user:
        raise ValueError("No user found in cache")
    now = _now_ts()

    tx: dict[str, Any] = {
        "id": _new_uuid(),
        "user": user["id"],
        "changed": now,
        "created": now,
        "deleted": False,
        "hold": None,
        **spec,
        "tag": category_ids if category_ids else None,
        "merchant": None,
        "payee": payee,
        "originalPayee": None,
        "comment": comment,
        "date": date,
        "mcc": None,
        "reminderMarker": None,
        "opIncome": None,
        "opIncomeInstrument": None,
        "opOutcome": None,
        "opOutcomeInstrument": None,
        "latitude": None,
        "longitude": None,
        "qrCode": None,
        "incomeBankID": None,
        "outcomeBankID": None,
    }

    await _write_diff({"transaction": [tx]})
    created = CACHE.get("transaction", tx["id"]) or tx
    return json.dumps({"created": _fmt_transaction(created)}, ensure_ascii=False)


async def tool_update_transaction(args: dict) -> str:
    tid = args["id"]
    _validate_uuid(tid, "id")
    if _g("date", args):
        _validate_date(args["date"], "date")
    if _g("category_ids", args):
        for i, cid in enumerate(args["category_ids"]):
            _validate_uuid(cid, f"category_ids[{i}]")

    existing = CACHE.get("transaction", tid)
    if not existing:
        raise ValueError(f"Transaction not found: {tid}")

    updated = {**existing, "changed": _now_ts()}
    amount = _g("amount", args)
    if amount is not None:
        amount = float(amount)
        tt = _tx_type(existing)
        if tt == "transfer":
            if existing.get("outcomeInstrument") != existing.get("incomeInstrument"):
                raise ValueError("Cannot update amount on cross-currency transfers. Delete and recreate.")
            updated["outcome"] = amount
            updated["income"] = amount
        elif existing.get("outcome", 0) > 0:
            updated["outcome"] = amount
        else:
            updated["income"] = amount

    if "category_ids" in args:
        updated["tag"] = args["category_ids"]
    if "date" in args:
        updated["date"] = args["date"]
    if "payee" in args:
        updated["payee"] = args["payee"]
    if "comment" in args:
        updated["comment"] = args["comment"]

    await _write_diff({"transaction": [updated]})
    result = CACHE.get("transaction", tid) or updated
    return json.dumps({"updated": _fmt_transaction(result)}, ensure_ascii=False)


async def tool_delete_transaction(args: dict) -> str:
    tid = args["id"]
    _validate_uuid(tid, "id")

    existing = CACHE.get("transaction", tid)
    if not existing:
        raise ValueError(f"Transaction not found: {tid}")

    deleted = {**existing, "deleted": True, "changed": _now_ts()}
    await _write_diff({"transaction": [deleted]})
    return json.dumps({
        "deleted": True, "id": tid,
        "date": existing.get("date"),
        "amount": existing.get("outcome") or existing.get("income"),
    }, ensure_ascii=False)


async def tool_create_account(args: dict) -> str:
    title = args["title"]
    acct_type = args["type"]
    currency_id = int(args["currency_id"])
    balance = float(_g("balance", args, 0))
    credit_limit = float(_g("credit_limit", args, 0))

    if not CACHE.get_instrument(currency_id):
        raise ValueError(f"Unknown currency_id: {currency_id}. Use get_instruments to see available currencies.")

    user = CACHE.first_user()
    if not user:
        raise ValueError("No user found in cache")
    now = _now_ts()

    new_account: dict[str, Any] = {
        "id": _new_uuid(),
        "user": user["id"],
        "instrument": currency_id,
        "type": acct_type,
        "role": None,
        "company": None,
        "title": title,
        "syncID": None,
        "balance": balance,
        "startBalance": balance,
        "creditLimit": credit_limit,
        "inBalance": True,
        "savings": False,
        "enableCorrection": False,
        "enableSMS": False,
        "archive": False,
        "private": False,
        "capitalization": None,
        "percent": None,
        "startDate": None,
        "endDateOffset": None,
        "endDateOffsetInterval": None,
        "payoffStep": None,
        "payoffInterval": None,
        "changed": now,
    }

    await _write_diff({"account": [new_account]})
    created = CACHE.get_account(new_account["id"]) or new_account
    return json.dumps({"created": _fmt_account(created)}, ensure_ascii=False)


async def tool_create_budget(args: dict) -> str:
    month = args["month"]
    _validate_month(month, "month")
    category = args["category"]
    income = float(_g("income", args, 0))
    outcome = float(_g("outcome", args, 0))
    income_lock = bool(_g("income_lock", args, False))
    outcome_lock = bool(_g("outcome_lock", args, False))

    _validate_positive(income, "income")
    _validate_positive(outcome, "outcome")

    category_id = _find_category_id(category)
    month_date = f"{month}-01"

    user = CACHE.first_user()
    if not user:
        # fallback: get user from any account
        accts = CACHE.accounts()
        if not accts:
            raise ValueError("No accounts found. Cannot determine user ID.")
        user = {"id": accts[0].get("user")}

    budget: dict[str, Any] = {
        "user": user["id"],
        "changed": _now_ts(),
        "tag": None if category_id == "00000000-0000-0000-0000-000000000000" else category_id,
        "date": month_date,
        "income": income,
        "incomeLock": income_lock,
        "outcome": outcome,
        "outcomeLock": outcome_lock,
    }

    await _write_diff({"budget": [budget]})
    cat_name = "ALL (aggregate)" if category_id == "00000000-0000-0000-0000-000000000000" else (
        (CACHE.get_tag(category_id) or {}).get("title", category)
    )
    return json.dumps({
        "success": True,
        "budget": {
            "month": month, "category": cat_name, "category_id": category_id,
            "income": income, "outcome": outcome,
            "income_lock": income_lock, "outcome_lock": outcome_lock,
        },
    }, ensure_ascii=False)


async def tool_update_budget(args: dict) -> str:
    month = args["month"]
    _validate_month(month, "month")
    category = args["category"]

    category_id = _find_category_id(category)
    month_date = f"{month}-01"
    budget_key = f"{'null' if category_id == '00000000-0000-0000-0000-000000000000' else category_id}:{month_date}"

    existing = CACHE.data["budget"].get(budget_key)
    if not existing:
        raise ValueError(f'Budget not found for category "{category}" in {month}. Use create_budget to create.')

    updated = {**existing, "changed": _now_ts()}
    if "income" in args:
        _validate_positive(float(args["income"]), "income")
        updated["income"] = float(args["income"])
    if "outcome" in args:
        _validate_positive(float(args["outcome"]), "outcome")
        updated["outcome"] = float(args["outcome"])
    if "income_lock" in args:
        updated["incomeLock"] = bool(args["income_lock"])
    if "outcome_lock" in args:
        updated["outcomeLock"] = bool(args["outcome_lock"])

    await _write_diff({"budget": [updated]})
    cat_name = "ALL (aggregate)" if category_id == "00000000-0000-0000-0000-000000000000" else (
        (CACHE.get_tag(category_id) or {}).get("title", category)
    )
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
    month = args["month"]
    _validate_month(month, "month")
    category = args["category"]

    category_id = _find_category_id(category)
    month_date = f"{month}-01"
    budget_key = f"{'null' if category_id == '00000000-0000-0000-0000-000000000000' else category_id}:{month_date}"

    existing = CACHE.data["budget"].get(budget_key)
    if not existing:
        raise ValueError(f'Budget not found for category "{category}" in {month}.')

    deleted = {**existing, "changed": _now_ts(), "income": 0, "outcome": 0}
    await _write_diff({"budget": [deleted]})
    cat_name = "ALL (aggregate)" if category_id == "00000000-0000-0000-0000-000000000000" else (
        (CACHE.get_tag(category_id) or {}).get("title", category)
    )
    return json.dumps({"success": True, "message": "Budget deleted", "category": cat_name, "month": month}, ensure_ascii=False)


async def tool_create_reminder(args: dict) -> str:
    tx_type = args["type"]
    amount = float(args["amount"])
    account_id = args["account_id"]
    to_account_id = _g("to_account_id", args)
    category_ids = _g("category_ids", args)
    payee = _g("payee", args)
    comment = _g("comment", args)
    interval = args["interval"]
    step = int(_g("step", args, 1))
    points = _g("points", args)
    start_date = _g("start_date", args) or _today()
    end_date = _g("end_date", args)
    notify = bool(_g("notify", args, True))

    _validate_uuid(account_id, "account_id")
    if to_account_id:
        _validate_uuid(to_account_id, "to_account_id")
    if category_ids:
        for cid in category_ids:
            _validate_uuid(cid, "category_id")
    _validate_positive(amount, "amount")
    if _g("start_date", args):
        _validate_date(start_date, "start_date")
    if end_date:
        _validate_date(end_date, "end_date")
    if tx_type == "transfer" and not to_account_id:
        raise ValueError("to_account_id is required for transfer type")

    account = CACHE.get_account(account_id)
    if not account:
        raise ValueError(f"Account not found: {account_id}")
    to_acct = CACHE.get_account(to_account_id) if to_account_id else None
    if to_account_id and not to_acct:
        raise ValueError(f"Destination account not found: {to_account_id}")

    # Validate categories
    if category_ids:
        for cid in category_ids:
            if not CACHE.get_tag(cid):
                raise ValueError(f"Category not found: {cid}")

    user_id = account.get("user")
    now = _now_ts()

    reminder: dict[str, Any] = {
        "id": _new_uuid(),
        "user": user_id,
        "changed": now,
        "incomeInstrument": account["instrument"] if tx_type == "income" else (to_acct["instrument"] if to_acct else account["instrument"]),
        "incomeAccount": account_id if tx_type == "income" else (to_account_id or account_id),
        "income": 0 if tx_type == "expense" else amount,
        "outcomeInstrument": account["instrument"] if tx_type != "income" else account["instrument"],
        "outcomeAccount": account_id if tx_type != "income" else account_id,
        "outcome": 0 if tx_type == "income" else amount,
        "tag": category_ids if category_ids else None,
        "merchant": None,
        "payee": payee,
        "comment": comment,
        "interval": interval,
        "step": step,
        "points": points if points else [],
        "startDate": start_date,
        "endDate": end_date,
        "notify": notify,
    }

    await _write_diff({"reminder": [reminder]})
    return json.dumps({
        "success": True,
        "reminder": {
            "id": reminder["id"], "type": tx_type, "amount": amount,
            "account": account.get("title"),
            "to_account": to_acct.get("title") if to_acct else None,
            "recurrence": f"Every {str(step) + ' ' if step > 1 else ''}{interval}{'s' if step > 1 else ''}",
            "start_date": start_date,
            "end_date": end_date or "indefinite",
            "points": points or "all",
        },
    }, ensure_ascii=False)


async def tool_update_reminder(args: dict) -> str:
    rid = args["id"]
    _validate_uuid(rid, "id")
    if _g("amount", args):
        _validate_positive(float(args["amount"]), "amount")
    if _g("category_ids", args):
        for cid in args["category_ids"]:
            _validate_uuid(cid, "category_id")
    if _g("end_date", args):
        _validate_date(args["end_date"], "end_date")

    existing = CACHE.get("reminder", rid)
    if not existing:
        raise ValueError(f"Reminder not found: {rid}")

    if _g("category_ids", args):
        for cid in args["category_ids"]:
            if not CACHE.get_tag(cid):
                raise ValueError(f"Category not found: {cid}")

    updated = {**existing, "changed": _now_ts()}

    if "amount" in args:
        amount = float(args["amount"])
        is_income = existing.get("income", 0) > 0 and existing.get("outcome", 0) == 0
        is_expense = existing.get("outcome", 0) > 0 and existing.get("income", 0) == 0
        if is_income:
            updated["income"] = amount
        elif is_expense:
            updated["outcome"] = amount
        else:
            updated["income"] = amount
            updated["outcome"] = amount

    if "category_ids" in args:
        updated["tag"] = args["category_ids"]
    if "payee" in args:
        updated["payee"] = args["payee"]
    if "comment" in args:
        updated["comment"] = args["comment"]
    if "interval" in args:
        updated["interval"] = args["interval"]
    if "step" in args:
        updated["step"] = int(args["step"])
    if "points" in args:
        updated["points"] = args["points"]
    if "end_date" in args:
        updated["endDate"] = args["end_date"]
    if "notify" in args:
        updated["notify"] = bool(args["notify"])

    await _write_diff({"reminder": [updated]})
    return json.dumps({"success": True, "message": "Reminder updated", "id": rid}, ensure_ascii=False)


async def tool_delete_reminder(args: dict) -> str:
    rid = args["id"]
    _validate_uuid(rid, "id")

    existing = CACHE.get("reminder", rid)
    if not existing:
        raise ValueError(f"Reminder not found: {rid}")

    now = _now_ts()
    deletions: list[dict] = [{"id": rid, "object": "reminder", "stamp": now, "user": existing["user"]}]

    for m in CACHE.reminder_markers():
        if m.get("reminder") == rid:
            deletions.append({"id": m["id"], "object": "reminderMarker", "stamp": now, "user": m["user"]})

    await _write_diff({"deletion": deletions})
    return json.dumps({
        "success": True,
        "message": f"Reminder deleted with {len(deletions) - 1} associated markers",
        "id": rid,
    }, ensure_ascii=False)


async def tool_create_reminder_marker(args: dict) -> str:
    tx_type = args["type"]
    amount = float(args["amount"])
    account_id = args["account_id"]
    to_account_id = _g("to_account_id", args)
    category_ids = _g("category_ids", args)
    payee = _g("payee", args)
    comment = _g("comment", args)
    date = args["date"]
    reminder_id = _g("reminder_id", args)
    notify = bool(_g("notify", args, True))

    _validate_uuid(account_id, "account_id")
    if to_account_id:
        _validate_uuid(to_account_id, "to_account_id")
    if category_ids:
        for cid in category_ids:
            _validate_uuid(cid, "category_id")
    if reminder_id:
        _validate_uuid(reminder_id, "reminder_id")
    _validate_date(date, "date")
    _validate_positive(amount, "amount")
    if tx_type == "transfer" and not to_account_id:
        raise ValueError("to_account_id is required for transfer type")

    account = CACHE.get_account(account_id)
    if not account:
        raise ValueError(f"Account not found: {account_id}")
    to_acct = CACHE.get_account(to_account_id) if to_account_id else None
    if to_account_id and not to_acct:
        raise ValueError(f"Destination account not found: {to_account_id}")
    if category_ids:
        for cid in category_ids:
            if not CACHE.get_tag(cid):
                raise ValueError(f"Category not found: {cid}")

    user_id = account.get("user")
    now = _now_ts()

    # If no reminder_id, create a one-time Reminder
    effective_reminder_id = reminder_id
    auto_created = False
    if not effective_reminder_id:
        one_time: dict[str, Any] = {
            "id": _new_uuid(),
            "user": user_id,
            "changed": now,
            "incomeInstrument": account["instrument"] if tx_type == "income" else (to_acct["instrument"] if to_acct else account["instrument"]),
            "incomeAccount": account_id if tx_type == "income" else (to_account_id or account_id),
            "income": 0 if tx_type == "expense" else amount,
            "outcomeInstrument": account["instrument"],
            "outcomeAccount": account_id if tx_type != "income" else account_id,
            "outcome": 0 if tx_type == "income" else amount,
            "tag": category_ids if category_ids else None,
            "merchant": None,
            "payee": payee,
            "comment": comment,
            "interval": None,
            "step": None,
            "points": None,
            "startDate": date,
            "endDate": date,
            "notify": notify,
        }
        await _write_diff({"reminder": [one_time]})
        effective_reminder_id = one_time["id"]
        auto_created = True
    else:
        if not CACHE.get("reminder", effective_reminder_id):
            raise ValueError(f"Reminder not found: {effective_reminder_id}")

    marker: dict[str, Any] = {
        "id": _new_uuid(),
        "user": user_id,
        "changed": now,
        "incomeInstrument": account["instrument"] if tx_type == "income" else (to_acct["instrument"] if to_acct else account["instrument"]),
        "incomeAccount": account_id if tx_type == "income" else (to_account_id or account_id),
        "income": 0 if tx_type == "expense" else amount,
        "outcomeInstrument": account["instrument"],
        "outcomeAccount": account_id if tx_type != "income" else account_id,
        "outcome": 0 if tx_type == "income" else amount,
        "tag": category_ids if category_ids else None,
        "merchant": None,
        "payee": payee,
        "comment": comment,
        "date": date,
        "reminder": effective_reminder_id,
        "state": "planned",
        "notify": notify,
    }

    await _write_diff({"reminderMarker": [marker]})
    return json.dumps({
        "success": True,
        "reminder_marker": {
            "id": marker["id"], "type": tx_type, "amount": amount,
            "account": account.get("title"),
            "to_account": to_acct.get("title") if to_acct else None,
            "date": date, "state": "planned",
            "reminder_id": effective_reminder_id,
            "auto_created_reminder": auto_created,
        },
    }, ensure_ascii=False)


async def tool_delete_reminder_marker(args: dict) -> str:
    mid = args["id"]
    _validate_uuid(mid, "id")

    marker = CACHE.get("reminderMarker", mid)
    if not marker:
        raise ValueError(f"ReminderMarker not found: {mid}")

    await _write_diff({
        "deletion": [{"id": mid, "object": "reminderMarker", "stamp": _now_ts(), "user": marker["user"]}],
    })
    return json.dumps({"success": True, "message": "ReminderMarker deleted", "id": mid}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

HANDLERS: dict[str, Any] = {
    "get_accounts": tool_get_accounts,
    "get_transactions": tool_get_transactions,
    "get_categories": tool_get_categories,
    "get_instruments": tool_get_instruments,
    "get_budgets": tool_get_budgets,
    "get_reminders": tool_get_reminders,
    "rebuild_references": tool_rebuild_references,
    "analyze_budget_detailed": tool_analyze_budget_detailed,
    "get_analytics": tool_get_analytics,
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
# Main
# ---------------------------------------------------------------------------

async def _run_tool(name: str, args: dict) -> str:
    CACHE.load()
    await _sync()
    try:
        handler = HANDLERS.get(name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {name}. Use --list to see available tools."})
        return await handler(args)
    finally:
        await _close_client()


def main() -> None:
    parser = argparse.ArgumentParser(description="ZenMoney CLI executor")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List all tools")
    group.add_argument("--describe", type=str, metavar="TOOL", help="Describe a tool")
    group.add_argument("--call", type=str, metavar="JSON", help='Call: {"tool":"name","arguments":{...}}')
    parsed = parser.parse_args()

    if parsed.list:
        tools = [{"name": n, "description": d["desc"]} for n, d in TOOL_DOCS.items()]
        print(json.dumps(tools, ensure_ascii=False, indent=2))
        return

    if parsed.describe:
        doc = TOOL_DOCS.get(parsed.describe)
        if not doc:
            print(json.dumps({"error": f"Unknown tool: {parsed.describe}"}), file=sys.stderr)
            sys.exit(1)
        print(json.dumps(
            {"name": parsed.describe, "description": doc["desc"], "parameters": doc["params"]},
            ensure_ascii=False, indent=2,
        ))
        return

    if parsed.call:
        if not TOKEN:
            print(json.dumps({"error": "ZENMONEY_TOKEN not set. Set env var or add to config.json"}), file=sys.stderr)
            sys.exit(1)

        try:
            payload = json.loads(parsed.call)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"Invalid JSON: {e}"}), file=sys.stderr)
            sys.exit(1)

        tool_name = payload.get("tool", "")
        arguments = payload.get("arguments", {})

        if tool_name not in TOOL_DOCS:
            print(json.dumps({"error": f"Unknown tool: {tool_name}. Use --list to see available tools."}), file=sys.stderr)
            sys.exit(1)

        try:
            result = asyncio.run(_run_tool(tool_name, arguments))
            print(result)
        except Exception as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
