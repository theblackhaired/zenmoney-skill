---
name: zenmoney
description: "Personal finance management through ZenMoney API — 22 tools for accounts, transactions, budgets, reminders, analytics, and ML suggestions. Triggers: money, spending, budgets, accounts, financial management."
metadata:
  openclaw:
    requires:
      bins: [python3]
---

# ZenMoney Personal Finance Assistant

22 tools для ZenMoney API. Все возвращают JSON.

## Как вызывать

```bash
python3 scripts/cli.py --call '{"tool":"TOOL_NAME","arguments":{...}}'
```

Примеры:
```bash
python3 scripts/cli.py --call '{"tool":"get_accounts","arguments":{}}'
python3 scripts/cli.py --call '{"tool":"get_analytics","arguments":{"start_date":"2026-02-01","type":"expense","group_by":"category"}}'
python3 scripts/cli.py --call '{"tool":"suggest","arguments":{"payee":"Яндекс Еда"}}'
python3 scripts/cli.py --list
python3 scripts/cli.py --describe get_transactions
```

## Tool Reference (22 tools)

**Read:**
- `get_accounts` — `include_archived`
- `get_transactions` — `start_date`(req), `end_date`, `account_id`, `category_id`, `type`(expense/income/transfer), `limit`(max 500), `offset`
- `get_categories` — no args
- `get_instruments` — `include_all`
- `get_budgets` — `month`(req, yyyy-MM)
- `get_reminders` — `include_processed`, `active_only`, `limit`, `markers_limit`, `offset`
- `get_analytics` — `start_date`(req), `end_date`, `group_by`(category/account/merchant), `type`(expense/income/all)
- `suggest` — `payee`(req)
- `get_merchants` — `search`, `limit`, `offset`
- `check_auth_status` — no args

**Write:**
- `create_transaction` — `type`(req), `amount`(req), `account_id`(req), `to_account_id`, `category_ids`, `date`, `payee`, `comment`, `currency_id`, `income_amount`
- `update_transaction` — `id`(req), `amount`, `category_ids`, `date`, `payee`, `comment`
- `delete_transaction` — `id`(req)
- `create_account` — `title`, `type`(cash/ccard/checking), `currency_id`(req), `balance`, `credit_limit`
- `create_budget` — `month`(req, yyyy-MM), `category`(req, name/UUID/"ALL"), `income`, `outcome`, `income_lock`, `outcome_lock`
- `update_budget` — `month`(req), `category`(req), partial fields
- `delete_budget` — `month`(req), `category`(req)
- `create_reminder` — `type`, `amount`, `account_id`, `interval`(req), `step`, `points`, `start_date`, `end_date`, `payee`, `comment`, `notify`
- `update_reminder` — `id`(req), partial fields
- `delete_reminder` — `id`(req)
- `create_reminder_marker` — `type`, `amount`, `account_id`, `date`(req), `reminder_id`, `payee`, `comment`, `notify`
- `delete_reminder_marker` — `id`(req)

## Быстрый маршрутизатор

| Задача | Tool(s) |
|---|---|
| Баланс, счета | `get_accounts()` |
| Расходы за период | `get_transactions(start_date, type="expense")` |
| Аналитика расходов | `get_analytics(start_date, group_by="category")` |
| Добавить расход/доход | `suggest(payee)` → `create_transaction(...)` |
| Перевод между счетами | `create_transaction(type="transfer", account_id, to_account_id)` |
| Бюджет на месяц | `get_budgets(month)` |
| Установить бюджет | `create_budget(month, category, outcome)` |
| Напоминания/подписки | `get_reminders()` |
| Создать напоминание | `create_reminder(type, amount, account_id, interval)` |
| Категории | `get_categories()` |
| Валюты | `get_instruments()` |
| ML подсказка категории | `suggest(payee)` |

## Workflows

**Анализ расходов:**
```bash
python3 scripts/cli.py --call '{"tool":"get_analytics","arguments":{"start_date":"2026-02-01","end_date":"2026-02-28","type":"expense","group_by":"category"}}'
```

**Добавить транзакцию:**
1. `suggest` с payee → UUID категории
2. `get_accounts` → UUID счёта
3. `create_transaction` с type/amount/account_id/category_ids

**Проверка бюджета:** `get_budgets` + `get_analytics` + `get_accounts` → остаток

## User Profile

Before performing budgets, reminders, or financial planning — read `PROFILE.md` in skill directory.
It contains: billing period rule (20th–19th), account UUIDs, category UUIDs, financial plan 2026, birthday budgets, transfer rules.

## Форматы данных

- Даты: yyyy-MM-dd
- Месяцы: yyyy-MM
- UUID: стандартный формат (get from get_accounts, get_categories)
- Валюта: instrument id (get from get_instruments)
- Типы транзакций: expense, income, transfer
- Типы счетов: cash, ccard, checking
- Интервалы напоминаний: day, week, month, year
