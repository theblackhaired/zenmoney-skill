"""The public MCP catalog, derived from the original skill contract."""

from __future__ import annotations

import os
import re
import sys
from importlib import import_module
from pathlib import Path

from mcp.types import Tool, ToolAnnotations

from .auth import READ_SCOPE, WRITE_SCOPE

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def default_state_dir(*, system: str, environ: dict[str, str], home: Path) -> Path:
    """Choose a private per-user location before loading the legacy core."""
    if system == "nt":
        return Path(environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "zenmoney-mcp"
    return Path(environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "zenmoney-mcp"


if not os.environ.get("ZENMONEY_STATE_DIR"):
    os.environ["ZENMONEY_STATE_DIR"] = str(default_state_dir(
        system=os.name, environ=os.environ, home=Path.home(),
    ))

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

TOOL_DOCS = import_module("zenmoney.tools").TOOL_DOCS

WRITE_TOOLS = frozenset({
    "setup_budget_mode",
    "create_transaction", "update_transaction", "delete_transaction",
    "create_account", "create_budget", "update_budget", "delete_budget",
    "create_reminder", "update_reminder", "delete_reminder",
    "create_reminder_marker", "delete_reminder_marker",
})
READ_TOOLS = frozenset(TOOL_DOCS) - WRITE_TOOLS

VOLUMINOUS_READ_TOOLS = frozenset({
    "get_accounts", "get_transactions", "get_categories", "get_instruments",
    "get_budgets", "get_reminders", "analyze_budget_detailed", "get_analytics",
    "get_category_report", "get_money_flow", "get_income_outcome_comparison",
    "get_balance_trend", "get_merchants",
})

_ENUMS = {
    "period": ["billing_period", "week", "month", "year"],
    "account_scope": ["all", "in_balance", "selected"],
    "category_scope": ["all", "selected"],
    "merchant_scope": ["all", "selected"],
    "category_role": ["primary", "additional", "any"],
    "currency_mode": ["split", "scalar"],
    "difference_calculation_mode": ["REFUNDS", "INCOME_OUTCOME_AND_REFUNDS", "NONE"],
    "budget_mode": ["balance_vs_expense", "income_vs_expense"],
    "direction": ["INCOME", "OUTCOME"],
    "budget_method": ["BUDGET", "MEAN"],
    "currency_filter": ["USER", "POPULAR"],
    "interval": ["day", "week", "month", "year"],
}

# These are the unconditional requirements in validation.py. Conditional
# requirements (transfer destination, selected IDs, period/date pairing) are
# represented separately below; a description parser cannot distinguish them.
_REQUIRED = {
    "get_budgets": {"month"},
    "analyze_budget_detailed": {"period"},
    "setup_budget_mode": {"mode"},
    "get_analytics": {"report"},
    "suggest": {"payee"},
    "create_transaction": {"type", "amount", "account_id"},
    "update_transaction": {"id"},
    "delete_transaction": {"id"},
    "create_account": {"title", "type", "currency_id"},
    "create_budget": {"month", "category"},
    "update_budget": {"month", "category"},
    "delete_budget": {"month", "category"},
    "create_reminder": {"type", "amount", "account_id", "interval"},
    "update_reminder": {"id"},
    "delete_reminder": {"id"},
    "create_reminder_marker": {"type", "amount", "account_id", "date"},
    "delete_reminder_marker": {"id"},
}

_PERIOD_TOOLS = frozenset({
    "get_transactions", "get_analytics", "get_category_report",
    "get_money_flow", "get_income_outcome_comparison", "get_balance_trend",
})
_PERIOD_DOCS = {
    "period": "str named period, mutually exclusive with start_date/end_date",
    "period_offset": "int offset for named periods only",
    "first_weekday": "int 0 Monday through 6 Sunday; required for period=week",
    "start_date": "str custom range start, inclusive; requires end_date",
    "end_date": "str custom range end, inclusive; requires start_date",
}


def _enum_for(tool_name: str, field: str) -> list[str] | None:
    if field == "type":
        if tool_name == "get_reminders":
            return ["expense", "income", "transfer", "all"]
        return ["expense", "income", "transfer"]
    if field == "group_by":
        return ["TAG", "PAYEE"] if tool_name == "get_category_report" else ["category", "account", "merchant"]
    if field == "report":
        # The core returns UNSUPPORTED_CALCULATION for turnover; let it do so.
        return ["income", "outcome", "net", "turnover"]
    if field == "mode":
        return (["balance_vs_expense", "income_vs_expense"] if tool_name == "setup_budget_mode"
                else ["WHOLE_PERIOD", "AVERAGE_VALUES"])
    if field == "period" and tool_name == "analyze_budget_detailed":
        return ["billing_period"]
    return _ENUMS.get(field)


def _property_schema(tool_name: str, name: str, documentation: str) -> dict:
    first = documentation.split(" ", 1)[0]
    if first.startswith("list[int]"):
        schema = {"type": "array", "items": {"type": "integer"}}
    elif first.startswith("list[str"):
        schema = {"type": "array", "items": {"type": "string"}}
    elif first == "bool":
        schema = {"type": "boolean"}
    elif first == "int":
        # The legacy validator also accepts numeric strings and integral floats.
        schema = {"type": ["number", "string"]}
    elif first == "float":
        schema = {"type": ["number", "string"]}
    else:
        schema = {"type": "string"}
    schema["description"] = documentation
    if name == "currency" and tool_name == "get_balance_trend":
        schema = {"anyOf": [{"type": "string"}, {"type": "integer"}], "description": documentation}
    enum = _enum_for(tool_name, name)
    if enum is not None:
        schema["enum"] = enum
    elif schema.get("type") == "string" and "|" in first:
        options = first.removeprefix("str").split("|")
        if len(options) > 1 and all(re.fullmatch(r"[A-Za-z_]+", x) for x in options):
            schema["enum"] = options
    if name in {"month"}:
        schema["pattern"] = r"^\d{4}-(0[1-9]|1[0-2])$"
    elif name in {"date", "start_date", "end_date", "marker_from", "marker_to"}:
        schema["format"] = "date"
    if name == "first_weekday":
        schema.update(minimum=0, maximum=6)
    if name == "comparison_periods":
        schema.update(minimum=0, maximum=12)
    if name == "limit" and "max 500" in documentation:
        schema["maximum"] = 500
    return schema


def build_tools() -> list[Tool]:
    result = []
    for name, doc in TOOL_DOCS.items():
        is_write = name in WRITE_TOOLS
        documented = dict(doc["params"])
        if name in _PERIOD_TOOLS:
            # Advanced reports document the selector in prose, while the
            # validator accepts all five fields for every report.
            for field, explanation in _PERIOD_DOCS.items():
                documented.setdefault(field, explanation)
        properties = {
            key: _property_schema(name, key, explanation)
            for key, explanation in documented.items()
        }
        required = sorted(_REQUIRED.get(name, set()))
        if is_write:
            properties["confirm_write"] = {
                "type": "boolean", "const": True,
                "description": "Explicitly confirm this write. Must be true.",
            }
            required.append("confirm_write")
        else:
            properties["response_mode"] = {
                "type": "string", "enum": ["compact", "full"],
                "description": "Use full for the complete unsummarized financial JSON result.",
                "default": "compact" if name in VOLUMINOUS_READ_TOOLS else "full",
            }
        schema = {"type": "object", "properties": properties, "additionalProperties": False}
        if required:
            schema["required"] = required
        if name in _PERIOD_TOOLS:
            schema["oneOf"] = [
                {"required": ["period"], "not": {"anyOf": [{"required": ["start_date"]}, {"required": ["end_date"]}]}},
                {"required": ["start_date", "end_date"], "not": {"required": ["period"]}},
            ]
            schema["if"] = {"properties": {"period": {"const": "week"}}, "required": ["period"]}
            schema["then"] = {"required": ["first_weekday"]}
        if name == "get_reminders":
            schema["dependentRequired"] = {
                "marker_from": ["marker_to"], "marker_to": ["marker_from"],
            }
        if name in {"create_transaction", "create_reminder", "create_reminder_marker"}:
            schema.setdefault("allOf", []).append({
                "if": {"properties": {"type": {"const": "transfer"}}, "required": ["type"]},
                "then": {"required": ["to_account_id"]},
            })
        result.append(Tool(
            name=name,
            description=doc["desc"],
            inputSchema=schema,
            _meta={"securitySchemes": [{
                "type": "oauth2",
                # Ask for both grants during initial connection; per-call
                # authorization still follows each tool's read/write role.
                "scopes": [READ_SCOPE, WRITE_SCOPE],
            }]},
            annotations=ToolAnnotations(
                readOnlyHint=not is_write,
                destructiveHint=is_write and name.startswith("delete_"),
                idempotentHint=not is_write,
                openWorldHint=name != "setup_budget_mode",
            ),
        ))
    return result


TOOLS = build_tools()
TOOL_BY_NAME = {tool.name: tool for tool in TOOLS}
