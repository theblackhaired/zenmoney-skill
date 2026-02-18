# ZenMoney Skill

Script-based CLI skill for [OpenClaw](https://openclaw.com) — personal finance management through ZenMoney API.

22 tools for accounts, transactions, budgets, reminders, analytics, and ML suggestions.

## How it works

OpenClaw agent reads `skill/SKILL.md` and calls the CLI via `exec`:

```bash
python3 scripts/cli.py --list
python3 scripts/cli.py --describe get_transactions
python3 scripts/cli.py --call '{"tool":"get_accounts","arguments":{}}'
python3 scripts/cli.py --call '{"tool":"get_analytics","arguments":{"start_date":"2026-02-01","type":"expense","group_by":"category"}}'
```

## Tools (22)

**Read:**
- `get_accounts` — list accounts with balances
- `get_transactions` — query by date, account, category, type (with pagination)
- `get_categories` — category tree
- `get_instruments` — currencies and rates
- `get_budgets` — monthly budget limits
- `get_reminders` — scheduled payments and markers (with pagination)
- `get_analytics` — spending/income aggregations
- `suggest` — ML category/merchant suggestions
- `get_merchants` — merchant search (with pagination)
- `check_auth_status` — verify token validity

**Write:**
- `create_transaction`, `update_transaction`, `delete_transaction`
- `create_account`
- `create_budget`, `update_budget`, `delete_budget`
- `create_reminder`, `update_reminder`, `delete_reminder`
- `create_reminder_marker`, `delete_reminder_marker`

## Setup

### Requirements

- Python 3.8+
- No pip dependencies (uses only stdlib `urllib`)

### Configuration

Create `config.json` in project root:

```json
{
  "token": "your-zenmoney-access-token"
}
```

Or set environment variable `ZENMONEY_TOKEN`.

### Getting a token

- From [zerro.app](https://zerro.app) — authorize with ZenMoney, extract token from browser storage
- From [budgera.com/settings/export](https://budgera.com/settings/export) — copy API token

## Architecture

- `scripts/cli.py` — standalone CLI, 22 tools, file-based cache (`.cache.json`)
- `skill/SKILL.md` — skill definition with tool reference and routing table
- `skill/PROFILE.example.md` — user profile template for financial planning context

### Sync protocol

ZenMoney uses diff-based sync via `POST /v8/diff/`. The CLI maintains `.cache.json` with `serverTimestamp` for incremental sync between invocations.

## License

MIT
