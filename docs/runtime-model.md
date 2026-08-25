# ZenMoney Runtime Model

This document describes the token, cache, and sync behavior implemented in the current `scripts/zenmoney` code.

## Token resolution

Implemented in `scripts/zenmoney/config.py`.

Resolution order:

1. `ZENMONEY_TOKEN`
2. `config.json` -> `token`

Implications:

- Environment variables are the preferred secret source.
- `config.json` remains a local fallback for environments that cannot inject secrets.
- If both sources are present, the environment variable wins.
- Missing token handling still happens at request time in `scripts/zenmoney/transport.py`.

## Windows launcher behavior

In this repository checkout, `python` is available and `python3` is not. Use one of these command forms on Windows:

```powershell
python scripts/cli.py --list
py -3 scripts/cli.py --list
```

If you run the skill in an environment that only exposes `python3`, substitute that interpreter manually.

## Dependencies

Install runtime packages from `requirements.txt`:

```bash
python -m pip install -r requirements.txt
```

The current code imports:

- `httpx` in `scripts/zenmoney/transport.py`
- `dateutil.relativedelta` in `scripts/zenmoney/domain.py`

## Runtime files

### `config.json`

User-maintained configuration, including:

- `billing_period_start_day`
- `budget_mode`
- `plan_user_id`
- `plan_settings_override`
- `difference_calculation_mode`
- `round_balance_to_integer`
- `accounts_meta`
- optional fallback `token`

### `.cache.json`

Local file-backed ZenMoney entity cache. The current cache code persists:

- `serverTimestamp`
- entity arrays such as accounts, transactions, reminders, categories, tags, and instruments

The cache is used to avoid full reloads between CLI invocations and to build derived structures such as account maps and category indexes.

State persistence is guarded:

- `config.json` and `.cache.json` writes use a per-file lock.
- JSON is written to a temp file, flushed/fsynced, then committed with `os.replace`.
- Cache saves compare the loaded `serverTimestamp` with the on-disk value and raise `LOST_UPDATE` instead of overwriting newer state.
- Invalid JSON or a non-object top-level value raises `CORRUPT_STATE`; the runtime does not silently replace a corrupt state file.

## Diff sync model

ZenMoney sync is incremental through `POST /v8/diff/`.

Current flow:

1. Load `.cache.json` into memory.
2. Run local migrations such as legacy `references/account_meta.json` import when needed.
3. Choose tool sync behavior from `scripts/zenmoney/dispatch.py`.
4. If the tool requires live data, call the API diff endpoint and update `.cache.json`.
5. Execute the tool handler against the refreshed or cached state.

## Write confirmation

Write tools post changes through `/v8/diff/`, apply the response to the cache, then force-fetch changed entity types to confirm the server accepted the write. Force-fetch verification resets `serverTimestamp` to `0`: ZenMoney otherwise returns only changes after the supplied cursor, which is not a complete entity snapshot and must not replace a cache store. The runtime confirms significant submitted fields for accounts, budgets, reminders, reminder markers, and transactions, plus deletion absence. If the confirmation sync does not match the submitted state, the write fails with a runtime error instead of returning optimistic success.

## Cache entity shape

The cache persists top-level arrays keyed by ZenMoney entity type:

- `instrument`
- `account`
- `tag`
- `merchant`
- `transaction`
- `budget`
- `reminder`
- `reminderMarker`
- `user`
- `country`
- `company`

Practical invariants used by the current code:

- `account.id` is a UUID-like string; `account.instrument` points to `instrument.id`
- `transaction` stores both income and outcome sides, so transfers are represented as one entity with two accounts/instruments
- `tag.parent` is nullable; category full paths are reconstructed from the cache, not from a separate references file
- `budget` is keyed in-memory as `"{user}:{tag-or-null}:{date}"`; the user component is mandatory so family budgets cannot overwrite one another
- `reminderMarker.reminder` points back to `reminder.id`

Tool arguments expose aggregate budgets as `ALL`, `ALL (aggregate)`, or `00000000-0000-0000-0000-000000000000`. The runtime normalizes these to the zero UUID in validated arguments, uses that zero UUID in the current budget write payload, and identifies the cached row by the selected user plus zero-UUID tag and date. Raw server rows with `tag: null` load under a distinct `{user}:null:{date}` cache key.

## Diff application assumptions

Implemented in `scripts/zenmoney/cache.py`.

- Non-budget entities are upserted by `id`
- Budgets are upserted by `(user, tag, date)` rather than raw `id`
- Deletions remove entities by `deletion[].object` plus `deletion[].id`
- Tag updates invalidate derived category indexes
- Force-fetch responses replace requested entity stores only when fetched from `serverTimestamp: 0`
- The cache is authoritative for runtime-derived maps such as category trees and account maps

## Tool sync policy

Default policy is prefetch sync before the handler runs.

Explicit overrides in `scripts/zenmoney/dispatch.py`:

- `check_auth_status` -> forced live sync
- `suggest` -> forced live sync
- `setup_budget_mode` -> cache-only

Operational meaning:

- `prefetch_sync`: dispatch calls `/v8/diff/` before the handler
- `cache_only`: dispatch does not prefetch, and the handler is expected to work only with local config/cache state
- `forced_live`: dispatch does not prefetch, because the handler itself performs a direct live API call and should not rely on a stale prefetched snapshot

Examples:

- `setup_budget_mode` is `cache_only` because it edits local config only
- `check_auth_status` and `suggest` are `forced_live` because they make live API requests inside the handler

This means most read and write tools use cached state plus a best-effort refresh, while auth checks and suggestions intentionally skip prefetch because they own the live call themselves.

The advanced analytics handlers (`get_category_report`, `get_money_flow`, `get_income_outcome_comparison`, `get_balance_trend`) consume the refreshed cache after the normal prefetch. Category, comparison, and balance-series conversions fetch dated rates from `POST /instrument-rates/`; the synced current `Instrument.rate` is only the documented fallback. Balance history is reconstructed from current account balances by reversing synced transactions after each requested point, and the response exposes that provenance in metadata.

`scripts/cli.py` enforces the token gate only for non-cache-only tools. `setup_budget_mode` can run without `ZENMONEY_TOKEN` or `config.json -> token`; live reads and writes cannot.

## Reminder recurrence

Reminder marker generation is implemented in `scripts/zenmoney/domain.py`.

- `step` must be positive.
- `points` are offsets within the recurrence window: every point must satisfy `0 <= point < step`.
- Missing `points` behaves as `[0]`.
- `day` and `week` intervals add day/week offsets.
- `month` and `year` intervals add month/year offsets with `dateutil.relativedelta`.
- The day of month for monthly/yearly markers comes from `start_date`, not from `points`; if that day does not exist in a target month, it clamps to the real month end.

## Module layout

`scripts/zenmoney/` is split by responsibility:

- `dispatch.py` — single tool entry point. Validates args via `validation.py`, applies sync policy, invokes handler, maps exceptions to structured payloads.
- `validation.py` — argument validation pipeline (`validate_tool_args`) plus `map_exception` that converts raised exceptions into `ToolError` payloads.
- `errors.py` — typed `ToolError` subclasses with machine-readable codes (`INVALID_BOOL`, `INVALID_DATE_RANGE`, `AMBIGUOUS_CATEGORY`, etc.).
- `read_tools.py` / `write_tools.py` / `reminder_tools.py` / `budget_tools.py` — tool implementations grouped by domain. Handlers that take arguments re-call `validate_tool_args` to support direct invocation (idempotent via internal marker); a few argument-less tools (e.g. `tool_get_categories`) skip the call.
- `transport.py` — HTTP client and `/v8/diff/` sync.
- `cache.py` — in-memory + file-backed entity cache, including the cached `tags_by_id` index invalidated on tag-affecting `apply_diff` and `load`.
- `domain.py` — pure domain helpers: validators, formatters, transfer classification, marker date generation.
- `config.py` — token resolution (env-first, `config.json` fallback) and runtime paths.
- `tools.py` — thin shim re-exporting `TOOLS` dispatch table and `_run_tool` for `cli.py` backwards compatibility.

## Validation and errors

Two recent code paths already shape runtime behavior:

- `scripts/zenmoney/validation.py` validates tool arguments before handler dispatch
- typed errors are returned by the CLI/tool layer instead of ad hoc string-only failures

This document is intentionally limited to behavior visible in the current codebase and scoped docs.
