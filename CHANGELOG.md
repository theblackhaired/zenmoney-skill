# Changelog

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

## [2026-02-21] — Budget balance calculation fix

### Fixed — Critical balance calculation accuracy

**Income vs Expense mode (FULLY WORKING, exact match):**
- **analyze_budget_detailed**: Fixed expense calculation formula to apply `max(actual+planned, budget)` at leaf category level instead of root level
  - Impact: Category "Продукты и питание" with budget=10k on "Продукты" and planned=50k on "Доставка еды" now correctly counts as 60k expenses (10k + 50k) instead of 51k (max(51k, 10k))
  - This fix resolves 8,984 ₽ undercount in expense_expected, bringing balance from +7,244 ₽ to -1,739 ₽
- **config.json**: Corrected income_vs_expense mode config — set `to_other_off_balance: false` to exclude transfers to non-structural off-balance accounts (e.g., HML.APP)
  - ZenMoney "Доходы vs Расходы" mode only counts structured off-balance transfers: credit repayments, savings deposits, debt payments
  - Generic off-balance accounts are excluded to prevent overcounting internal project funds transfers
  - This fix resolves remaining 1,600 ₽ discrepancy, bringing balance from -1,739 ₽ to **exact ZenMoney match of -139 ₽** ✅
- **classify_transfer**: Fixed count_all_movements bypass in lines 437/442 — removed `count_all or` condition from generic off-balance checks
  - Prevents inBalance-to-inBalance transfers from being misclassified as expenses in balance_vs_expense mode
  - Lines 437, 442: Changed `if count_all or (from_in_balance and not to_in_balance)` to `if from_in_balance and not to_in_balance`

**Balance vs Expense mode (PARTIALLY IMPLEMENTED, ⚠️ NOT PRODUCTION-READY):**
- **classify_transfer**: Added early-return guard for inBalance-to-inBalance transfers at line 398
  - `if from_in_balance and to_in_balance: return None` — transfers within balance perimeter are balance-neutral
  - Prevents double-classification of internal transfers (checking → credit card) when both accounts are inBalance
- **calculate_initial_balance**: Implemented automatic calculation of "Входящий баланс" (lines 1163-1235)
  - Formula: `balance_at_start = current_balance - sum(transactions_after_start)`
  - Handles multi-currency conversion using instrument rates
  - Uses `time.mktime()` for Windows-compatible timestamp conversion
- **Conditional balance formula** (lines 1847-1865):
  - balance_vs_expense: `balance = initial_balance + income_actual + transfers_in - expense_actual - transfers_out`
  - income_vs_expense: `balance = (income_actual + income_planned) - expense_expected - transfers_net` (unchanged)
- **⚠️ Known Issues**:
  - Shows balance **+85,611 ₽** instead of ZenMoney's **+76 ₽** (~85k discrepancy)
  - Root cause: `expense_actual` = 1,020 ₽ (only completed transactions), but ZenMoney likely includes planned expenses
  - Data structure analysis shows minimal actual expenses vs 144k expected
  - **Status**: Formula implemented correctly, but data interpretation differs from ZenMoney
  - **Recommendation**: Use income_vs_expense mode for production (exact match with ZenMoney)

### Changed
- Added `sum_leaf_expected()` helper function in `scripts/cli.py:1693-1703` for recursive leaf-level max() calculation
- Added `calculate_initial_balance()` function in `scripts/cli.py:1163-1235` for automatic initial balance calculation
- Added `sum_actual_recursive()` helper in `scripts/cli.py:1805-1815` for recursive actual expense summation
- Added conditional balance calculation logic based on `count_all_movements` flag (lines 1847-1865)
- Added debug output for balance_vs_expense mode (lines 1867-1877) with formula breakdown
- **Production-ready**: Income vs Expense mode budget analysis now matches ZenMoney exactly ✅
- **Experimental**: Balance vs Expense mode implemented but has data interpretation issues ⚠️

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
