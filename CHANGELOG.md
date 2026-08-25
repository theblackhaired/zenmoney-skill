# Changelog

## Unreleased

- Breaking: replaced all read/report date shorthands with one strict period resolver; Plans now requires `period=billing_period`, Analytics/transactions require a named period or complete custom range, and weeks require `first_weekday`.
- The resolver matches Android 26.6 rollover semantics for billing days 29-31 and calendar `Budget.date` anchors.
- Removed user-specific financial examples and duplicated stale history from tracked skill documentation; local private profiles remain optional and ignored by Git.
- Replaced `get_analytics` with an explicit breaking contract:
  - `type=expense` -> `report=outcome`
  - `type=income` -> `report=income`
  - `type=net` -> `report=net`
  - `type=all` and `turnover` are removed until a separate money-movement contract exists
- Added required Analytics argument `report` and optional defaults `group_by=category`, `currency_mode=split`.
- Documented strict Analytics filter slice for issue #20: account, category, and merchant scopes; selected-list validation; `ENTITY_NOT_FOUND`; filter echo; and rejection of unknown arguments and singular aliases.
- Added fixed Analytics policies: `tag_policy=primary_tag`, `currency_conversion=none`, `transfers=excluded`, `unknown_currency=separate_bucket`.
- Standardized Analytics output on `snake_case` and stable group key prefixes; `payee:` is only the merchant-grouping fallback key.

## 2026-07-24

- Fixed false write failures when ZenMoney normalizes server-derived transaction fields such as `originalPayee` and operation amounts after accepting a write.
- Added reliable PowerShell CLI input through stdin with `--call -`.
- Fixed force-fetch cache replacement by resetting `serverTimestamp` to `0` before requesting an authoritative entity snapshot.
- Documented explicit UTF-8 PowerShell pipeline encoding for Cyrillic JSON input.

## [2026-07-24] — Runtime contract documentation refresh

### Documentation
- Added `requirements.txt` with the runtime dependencies used by current code: `httpx` and `python-dateutil`.
- Documented server-confirmed writes, cache/config file locking, atomic state writes, and `LOST_UPDATE` cache protection.
- Clarified reminder recurrence: `points` are offsets where `0 <= point < step`; monthly/yearly marker days come from `start_date`.
- Clarified aggregate budget category semantics for `ALL` / `ALL (aggregate)` / zero UUID.
- Clarified that `setup_budget_mode` is cache-only and can run without a ZenMoney token.

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
- `get_reminders`: new param `category` — filter by category name
- `get_reminders`: new param `type` — filter by operation type (`expense` / `income` / `transfer` / `all`)
- `get_reminders`: response now includes `type` field for each reminder
- `get_reminders`: marker mode response includes `markers_total_outcome` and `markers_total_income` per reminder
- Helper function `_reminder_type()` — determines reminder type using same logic as `_tx_type()`

### Fixed
- Old behavior sorted by `startDate` desc with limit, causing older recurring reminders to fall outside the first page.
- Marker totals now sum marker amounts instead of reusing the reminder template amount.

### Unchanged
- recent-summary mode (without `marker_from`/`marker_to`) remains available for recent reminders
