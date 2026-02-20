# Changelog

## [2026-02-20] — Reference cache + Marker-based filtering

### Added — Reference cache system
- New tool `rebuild_references` — generates JSON reference files from ZenMoney data
- `references/accounts.json` — all accounts with bank, subtype (credit/debit/savings/checking/cash/debt), balance, currency, inBalance, archived
- `references/categories.json` — category tree with parent-child relationships and UUIDs
- Account subtype detection: ccard+creditLimit>0=credit, ccard+0=debit, checking+savings=savings, etc.
- `references/account_meta.json` — manual account descriptions (role, purpose), merged into `accounts.json` during rebuild
- `accounts.json` now includes `description` field from `account_meta.json` (null if not defined)

### Added — Billing period config
- `config.json`: new param `billing_period_start_day` — configurable billing period start day (default: 20)
- `SKILL.md`: added billing period formula, reference cache docs, get_reminders modes, account_meta rules

### Added — Startup readiness check & initialization workflow
- `SKILL.md`: readiness check — verifies 5 required files on every skill invocation, auto-initializes missing data
- `SKILL.md`: initialization workflow — 4-step setup (billing period, rebuild refs, generate descriptions, create PROFILE.md)

### Added — Marker-based filtering for reminders

### Added
- `get_reminders`: new params `marker_from`, `marker_to` — filter reminders by marker dates in a given period
- `get_reminders`: new param `category` — filter by category name (e.g. "Иностранные сервисы")
- `get_reminders`: new param `type` — filter by operation type (`expense` / `income` / `transfer` / `all`)
- `get_reminders`: response now includes `type` field for each reminder
- `get_reminders`: marker mode response includes `markers_total_outcome` and `markers_total_income` per reminder
- Helper function `_reminder_type()` — determines reminder type using same logic as `_tx_type()`

### Fixed
- Old behavior sorted by `startDate` desc with limit, causing old recurring reminders (Spotify, ChatGPT Plus, Google One, etc.) to fall outside the first page — effectively invisible via API
- GrowFood with 5 markers/period was shown as 10k instead of 50k due to counting reminder outcome instead of sum of marker outcomes

### Unchanged
- Legacy mode (without `marker_from`/`marker_to`) preserved for backward compatibility
