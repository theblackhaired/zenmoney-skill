# ZenMoney Skill

Script-based CLI skill for personal finance management through the ZenMoney API.

The skill currently exposes 24 tools for accounts, transactions, budgets, reminders, analytics, and ML suggestions.

## How it works

The agent runner reads the repository-root `SKILL.md` and invokes the CLI:

```bash
python scripts/cli.py --list
python scripts/cli.py --describe get_transactions
python scripts/cli.py --call '{"tool":"get_accounts","arguments":{}}'
python scripts/cli.py --call '{"tool":"get_analytics","arguments":{"start_date":"2026-02-01","report":"outcome","group_by":"category","currency_mode":"split"}}'
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

## Tools (24)

**Read:**
- `get_accounts` - list accounts with balances
- `get_transactions` - query by date, account, category, type, limit, and offset
- `get_categories` - category tree
- `get_instruments` - currencies and rates
- `get_budgets` - monthly budget limits
- `analyze_budget_detailed` - detailed budget analysis with `balance_vs_expense` and `income_vs_expense`
- `get_reminders` - scheduled payments and markers
- `get_analytics` - income, outcome, and net aggregations
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

## Date shortcuts

Read/report tools that accept `start_date` and `end_date` now support a small shorthand set in addition to ISO dates:

- `-30d` - relative day offset from today
- `today` - current date
- `this_month` - expands to the current calendar month
- `billing_period` - expands using `config.json -> billing_period_start_day`

Currently this applies to `get_transactions`, `get_analytics`, and `analyze_budget_detailed`.

## Analytics contract

`get_analytics` uses a breaking explicit report contract:

- `report` is required: `income`, `outcome`, or `net`.
- `group_by` is optional: `category` by default; also accepts `account` or `merchant`.
- `currency_mode` is optional: `split` by default; also accepts `scalar`.
- `tag_policy` is `primary_tag`.
- `currency_conversion` is `none`.
- `transfers` are `excluded`.
- `unknown_currency` is `separate_bucket`.
- Output field names use `snake_case`.
- Stable group keys are prefixed: `category:`, `account:`, `merchant:`; merchant grouping may use `payee:` as the fallback key when no merchant entity is available.
- Money-movement totals are not part of `get_analytics` until a separate money-movement contract exists.
- Full Analytics output contract: [docs/plans-analytics-parity.md](docs/plans-analytics-parity.md).

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
- `billing_period_start_day` - day of month when the billing period starts
- `budget_mode` - `balance_vs_expense` or `income_vs_expense`
- `budget_mode_configured` - remembers whether budget mode setup has already been completed
- `budget_modes` - per-mode transfer classification flags
- `round_balance_to_integer` - rounds forecast and balance output to integer rubles
- `accounts_meta` - user-maintained account descriptions merged into budget analysis output

### Getting a token

- [zerro.app](https://zerro.app) - authorize with ZenMoney and extract the token from browser storage
- [budgera.com/settings/export](https://budgera.com/settings/export) - copy the API token

## Budget analysis modes

`analyze_budget_detailed` supports two modes:

- `income_vs_expense` - recommended mode focused on actual income versus spending
- `balance_vs_expense` - broader balance model that includes transfers and off-balance movements

You can switch modes with `setup_budget_mode` or by updating `config.json`.

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
