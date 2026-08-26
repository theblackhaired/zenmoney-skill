# ZenMoney Skill

Script-based CLI skill for personal finance management through the ZenMoney API.

The skill currently exposes 28 tools for accounts, transactions, Plans, budgets, reminders, analytics, and ML suggestions.

## How it works

The agent runner reads the repository-root `SKILL.md` and invokes the CLI:

```bash
python scripts/cli.py --list
python scripts/cli.py --describe get_transactions
python scripts/cli.py --call '{"tool":"get_accounts","arguments":{}}'
python scripts/cli.py --call '{"tool":"get_analytics","arguments":{"period":"month","report":"outcome","group_by":"category","currency_mode":"split"}}'
```

PowerShell:

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
'{"tool":"get_accounts","arguments":{}}' | python scripts/cli.py --call -
```

For automated PowerShell calls containing non-ASCII values, serialize JSON with ASCII Unicode escapes (`ensure_ascii=True`, producing `\uXXXX`). This protects the payload even when the automation host mangles the command text before PowerShell starts.

Windows notes:

- `python` is the supported launcher in this checkout.
- If you prefer the Windows launcher, `py -3 scripts/cli.py ...` is equivalent.
- If your environment only exposes `python3`, substitute it manually.

## Tools (28)

**Read:**
- `get_accounts` - list accounts with balances
- `get_transactions` - query by date, account, category, type, limit, and offset
- `get_categories` - category tree
- `get_instruments` - currencies and rates
- `get_budgets` - monthly budget limits
- `analyze_budget_detailed` - detailed budget analysis with `balance_vs_expense` and `income_vs_expense`
- `get_reminders` - scheduled payments and markers
- `get_analytics` - income, outcome, and net aggregations
- `get_category_report` - category/payee report with budget or historical-mean comparison and ZenMoney difference modes
- `get_money_flow` - income/outcome flow, residue, overspending, and weights by native currency
- `get_income_outcome_comparison` - selected and preceding period comparison
- `get_balance_trend` - reconstructed historical balance trend for the selected account perimeter
- `suggest` - ML category and merchant suggestions
- `get_merchants` - merchant search
- `check_auth_status` - verify token validity

**Write:**
- `create_transaction`, `update_transaction`, `delete_transaction`
- `create_account`
- `setup_budget_mode`
- `create_budget`, `update_budget`, `delete_budget`
- `create_reminder`, `update_reminder`, `delete_reminder`
- `create_reminder_marker`, `delete_reminder_marker`

## Period contract

Read/report tools (`get_transactions`, `get_analytics`, and the four advanced analytics reports) accept exactly one period selector:

- named `period=billing_period|week|month|year` with integer `period_offset` (`0` current, `-1` previous);
- or an exact custom range with both inclusive `start_date` and `end_date`.

`period=week` requires `first_weekday=0..6` (`0` Monday). Plans accepts only `period=billing_period`, matching the mobile Plans surface. Old magic values and incomplete date ranges are rejected; there is no second shorthand resolver.

Billing days 29-31 follow Android 26.6: when the requested day does not exist, the boundary is day 1 of the next month. Internal ranges are half-open; public `end_date` is inclusive. `Budget.date` remains the first day of the logical calendar month even when its billing boundary rolls into the next month.

## Analytics contract

`get_analytics` uses a breaking explicit report contract:

- `report` is required: `income`, `outcome`, or `net`.
- A named period or complete custom range is required; the response echoes resolved boundaries.
- `group_by` is optional: `category` by default; also accepts `account` or `merchant`.
- `currency_mode` is optional: `split` by default; also accepts `scalar`.
- `account_scope` is optional: `in_balance` by default; also accepts `all` or `selected` with `account_ids`.
- `category_scope` is optional: `all` by default; `selected` requires `category_ids` and may use `category_role=primary|additional|any`.
- `merchant_scope` is optional: `all` by default; `selected` requires at least one of `merchant_ids` or `payees`.
- `tag_policy` is `primary_tag`.
- `currency_conversion` is `none`.
- `transfers` are `excluded`.
- `unknown_currency` is `separate_bucket`.
- Output field names use `snake_case`.
- Stable group keys are prefixed: `category:`, `account:`, `merchant:`; merchant grouping uses `payee:` only when a transaction has no merchant ID.
- Filter dimensions combine with AND; values inside one selected dimension combine with OR. Empty selected lists are invalid, unknown entity IDs return `ENTITY_NOT_FOUND`, and unknown arguments or singular aliases are rejected.
- Money-movement totals are not part of `get_analytics` until a separate money-movement contract exists.
- Full Analytics output contract: [docs/plans-analytics-parity.md](docs/plans-analytics-parity.md).

The four advanced reports reuse the same strict period and account-perimeter contracts. They convert monetary values with historical `/instrument-rates/` data and use current instrument rates only as an explicit fallback. `get_category_report` supports `REFUNDS`, `INCOME_OUTCOME_AND_REFUNDS`, and `NONE`; the saved mode defaults to `REFUNDS`. `AVERAGE_VALUES` in the income/outcome comparison fails explicitly for ranges longer than 31 days while the APK formula remains unconfirmed.

## Setup

### Requirements

- Python 3.10+ because the current code uses PEP 604 `|` type unions
- Python packages from `requirements.txt`:
  - `httpx` for ZenMoney HTTP calls
  - `python-dateutil` for month/year reminder recurrence

Install:

```bash
python -m pip install -r requirements.txt
```

### Token Source

Prefer the `ZENMONEY_TOKEN` environment variable. `config.json` is only a fallback when the env var is unset.

PowerShell:

```powershell
$env:ZENMONEY_TOKEN = "your-zenmoney-access-token"
```

Bash:

```bash
export ZENMONEY_TOKEN="your-zenmoney-access-token"
```

Resolution order in `scripts/zenmoney/config.py`:

1. `ZENMONEY_TOKEN`
2. `config.json` -> `token`

If both are present, the environment variable wins.

`setup_budget_mode` is cache-only and may run without a token because it only updates local config. Live reads and writes still require `ZENMONEY_TOKEN` or `config.json -> token`.

### Configuration

Create `config.json` in the project root for non-secret settings:

```json
{
  "billing_period_start_day": 20,
  "budget_mode": "income_vs_expense",
  "round_balance_to_integer": true
}
```

Optional fallback if you cannot inject environment variables:

```json
{
  "token": "your-zenmoney-access-token"
}
```

Configuration options:

- `token` - optional fallback token source; prefer `ZENMONEY_TOKEN`
- `billing_period_start_day` - required for `period=billing_period`; integer 1..31; missing days roll to day 1 of the next month
- `budget_mode` - optional local override: `balance_vs_expense` or `income_vs_expense`; otherwise the synced ZenMoney user mode is used
- `plan_user_id` - required only when a family sync contains multiple users and the Plans preference owner cannot be selected unambiguously
- `plan_settings_override` - optional explicit list of ZenMoney `PlanSetting` values; otherwise synced `user.planSettings` is used
- `difference_calculation_mode` - optional `REFUNDS`, `INCOME_OUTCOME_AND_REFUNDS`, or `NONE`; defaults to `REFUNDS`, while balance mode forces `NONE`
- `round_balance_to_integer` - rounds forecast and balance output to integer rubles
- `accounts_meta` - user-maintained account descriptions merged into budget analysis output

### Getting a token

- [zerro.app](https://zerro.app) - authorize with ZenMoney and extract the token from browser storage
- [budgera.com/settings/export](https://budgera.com/settings/export) - copy the API token

## Budget analysis modes

`analyze_budget_detailed` supports the two current Plans UI modes:

- `income_vs_expense` - `EXCLUDE_OPENING_BALANCE` plus the eight synced directed transfer exclusions
- `balance_vs_expense` - `BALANCE`; includes opening balance and every transfer that crosses the balance perimeter

By default the skill reads `user.planBalanceMode` and JSON-encoded `user.planSettings` from the normal `/v8/diff/` cache. You can switch modes locally with `setup_budget_mode` or `config.json`; no guessed per-mode dictionaries are used. The old `BUDGET_LIMIT` SmartBudget formula fails explicitly until its conflicting APK branches have a dedicated contract.

If synced user preferences are unavailable, the report fails explicitly. A complete offline override for `income_vs_expense` therefore needs both `budget_mode` and `plan_settings_override`; `balance_vs_expense` has no active transfer exclusions.

Plans calls require `period=billing_period`; use `period_offset=-1` for the previous plan period.

Each expense-category row exposes the Plans display contract directly: `plan` is the denominator shown after “из”, `remaining` is the non-negative free amount, and `overspend` is the non-negative amount over plan. `reserve_remaining` is the separate internal tree reserve used by the overall balance formula; it can differ from a parent row's displayed free amount.

Aggregate budgets use the sentinel category `ALL` / `ALL (aggregate)`, normalized to `00000000-0000-0000-0000-000000000000` in tool arguments and written with that zero UUID in the current budget write path.

## Reminder recurrence

`create_reminder` and `update_reminder` use `interval`, `step`, and optional `points` to generate markers:

- `step` must be positive.
- `points` are recurrence offsets and each value must satisfy `0 <= point < step`.
- For monthly and yearly reminders, the day of month comes from `start_date`; month ends are clamped to the real last day.
- Omit `points` to use `[0]`, meaning one marker at each base occurrence.

## Runtime model

- The CLI persists a file-backed entity cache in `.cache.json`.
- ZenMoney sync is diff-based via `POST /v8/diff/`, tracked with `serverTimestamp`.
- Most tools load the cache and prefetch a fresh sync before execution.
- Writes are server-confirmed: after posting changes, the runtime force-fetches changed entity types from `serverTimestamp: 0` and fails if the expected entity fields or deletion state are not present on the server.
- Cache/config writes use file locks and atomic replace; stale cache saves raise `LOST_UPDATE` instead of overwriting newer state.
- `check_auth_status` and `suggest` force live sync.
- `setup_budget_mode` is cache-only because it only updates local config.

More detail: [docs/runtime-model.md](docs/runtime-model.md)

## Architecture

- `SKILL.md` - agent-facing usage and routing guide
- `scripts/cli.py` - CLI entrypoint for `--list`, `--describe`, and `--call`
- `scripts/zenmoney/config.py` - config loading and token resolution
- `scripts/zenmoney/validation.py` - argument validation
- `scripts/zenmoney/tools.py` - tool registry, handlers, and sync policy
- `scripts/zenmoney/cache.py` - local diff cache persistence and derived indexes

## License

MIT
