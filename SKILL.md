---
name: zenmoney
description: "Personal finance management through ZenMoney API — 23 tools for accounts, transactions, budgets, reminders, analytics, references, and ML suggestions. Triggers: money, spending, budgets, accounts, financial management."
metadata:
  openclaw:
    requires:
      bins: [python3]
---

# ZenMoney Personal Finance Assistant

23 tools для ZenMoney API. Все возвращают JSON.

## Проверка готовности (при каждом вызове)

Перед выполнением любого запроса проверь наличие данных:

1. **`references/accounts.json`** — если нет → запусти `rebuild_references()`
2. **`references/categories.json`** — если нет → запусти `rebuild_references()`
3. **`references/account_meta.json`** — если нет или пустой → запусти инициализацию описаний (см. "Первичная инициализация", шаг 3)
4. **`config.json` → `billing_period_start_day`** — если нет → спроси пользователя и запиши
5. **`skill/PROFILE.md`** — если нет → предложи создать по шаблону

Если все файлы на месте — работай как обычно. Если чего-то не хватает — сначала заполни недостающее, потом выполняй запрос.

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

## Tool Reference (23 tools)

**Read:**
- `get_accounts` — `include_archived`
- `get_transactions` — `start_date`(req), `end_date`, `account_id`, `category_id`, `type`(expense/income/transfer), `limit`(max 500), `offset`
- `get_categories` — no args
- `get_instruments` — `include_all`
- `get_budgets` — `month`(req, yyyy-MM)
- `get_reminders` — `include_processed`, `active_only`, `limit`, `markers_limit`, `offset`, `marker_from`(yyyy-MM-dd), `marker_to`(yyyy-MM-dd), `category`(name), `type`(expense/income/transfer/all)
- `get_analytics` — `start_date`(req), `end_date`, `group_by`(category/account/merchant), `type`(expense/income/all)
- `suggest` — `payee`(req)
- `get_merchants` — `search`, `limit`, `offset`
- `check_auth_status` — no args
- `rebuild_references` — regenerates `references/accounts.json` and `references/categories.json` from cache

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
| Плановые платежи за период | `get_reminders(marker_from, marker_to, type="expense")` |
| Обновить справочники | `rebuild_references()` |
| UUID счёта по имени | Read `references/accounts.json` |
| UUID категории по имени | Read `references/categories.json` |

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

## Платёжный период (config.json)

Параметр `billing_period_start_day` в `config.json` задаёт день начала платёжного периода.

- **Текущее значение:** 20 (период с 20-го по 19-е следующего месяца)
- Используй для вычисления дат `marker_from` / `marker_to` в `get_reminders`
- Используй для определения `month` в `get_budgets` / `create_budget`

**Формула текущего периода:**
```
today = текущая дата
if today.day >= start_day:
    marker_from = today.year-today.month-start_day
    marker_to = next_month.year-next_month.month-(start_day - 1)
else:
    marker_from = prev_month.year-prev_month.month-start_day
    marker_to = today.year-today.month-(start_day - 1)
```

## User Profile

Before performing budgets, reminders, or financial planning — read `PROFILE.md` in skill directory.
It contains: billing period rule (20th–19th), account UUIDs, category UUIDs, financial plan 2026, birthday budgets, transfer rules.

## Первичная инициализация

При первом запуске скилла или после значительных изменений в ZenMoney выполни:

### 1. Настрой платёжный период
- Спроси пользователя: "С какого числа у вас начинается платёжный период?"
- Запиши значение в `config.json` → `billing_period_start_day`

### 2. Сгенерируй справочники
```bash
python3 scripts/cli.py --call '{"tool":"rebuild_references","arguments":{}}'
```

### 3. Заполни описания счетов (account_meta.json)
- Прочитай `references/accounts.json` — список всех счетов
- Для каждого активного счёта (`archived: false`) определи назначение:
  - По названию и банку (если очевидно)
  - По последним транзакциям: `get_transactions(start_date="-30d", account_id=UUID, limit=20)`
  - По напоминаниям: какие регулярные платежи привязаны к счёту
- Сгенерируй описание по правилам (см. "account_meta.json — правила описаний")
- Запиши в `references/account_meta.json`
- Перезапусти `rebuild_references()` для мержа описаний в `accounts.json`

### 4. Проверь PROFILE.md
- Если `skill/PROFILE.md` не существует — создай по шаблону `skill/PROFILE.example.md`
- Заполни на основе данных из справочников и вопросов пользователю

**Когда повторять:** при добавлении/удалении счетов, смене банка, изменении структуры категорий.

## Справочники (references/)

Предгенерированные JSON-файлы для быстрого поиска UUID без API-вызовов.

| Файл | Содержимое | Обновление |
|------|------------|------------|
| `references/accounts.json` | Все счета: bank, type, subtype, balance, currency, inBalance, archived, description | `rebuild_references()` |
| `references/categories.json` | Дерево категорий с parent-child и UUID | `rebuild_references()` |
| `references/account_meta.json` | Ручные описания счетов (роль, назначение) | Редактировать вручную |

### Как использовать

1. **Вместо `get_accounts()`** — читай `references/accounts.json` для получения UUID, типа и описания счёта
2. **Вместо `get_categories()`** — читай `references/categories.json` для получения UUID категории
3. **После изменения счетов/категорий** — вызови `rebuild_references()` для обновления кэша

### account_meta.json — правила описаний

Файл `references/account_meta.json` содержит ручные аннотации счетов. Ключ — UUID счёта, значение — объект с полем `description`.

**Формат:**
```json
{
  "UUID-счёта": {
    "description": "Краткое описание роли и назначения"
  }
}
```

**Правила генерации description:**
- Описание должно объяснять **для чего** используется счёт, а не дублировать его название
- Указывать основные операции: "приход ЗП", "иностранные подписки", "рассрочки"
- Указывать привязанные сервисы/платежи через запятую, если есть
- Для транзитных счетов — указывать цель: "Транзитный для погашения кредитки X"
- Для неактивных — указать "Неактивна" или "Не используется"
- Максимум 1 строка, ~5-15 слов
- Писать на русском

**Примеры:**
- `"Основной расчётный счёт, приход ЗП и аванса"`
- `"Иностранные подписки (Claude, Duolingo, ChatGPT, VDS)"`
- `"Транзитный для погашения кредитки Сбербанк"`
- `"Накопления, подушка безопасности, фонд Япония"`
- `"Неактивна"`

При `rebuild_references()` описания из `account_meta.json` автоматически мержатся в `accounts.json` (поле `description`, null если не задано).

## Режимы get_reminders

### Legacy-режим (без marker_from/marker_to)
Возвращает напоминания отсортированные по startDate desc. Подходит для просмотра недавних.

### Marker-режим (с marker_from + marker_to)
Фильтрует напоминания по **маркерам в заданном периоде**. Возвращает только те напоминания, у которых есть маркеры в указанном диапазоне дат. Каждое напоминание включает:
- `markers_total_outcome` / `markers_total_income` — суммы по маркерам за период
- `type` — тип операции (expense/income/transfer)
- `markers_count` — количество маркеров в периоде

**Рекомендуется для:**
- Подсчёта плановых расходов на платёжный период
- Анализа подписок по категориям
- Сравнения плана с фактом

**Пример — подписки на иностранные сервисы за период:**
```bash
python3 scripts/cli.py --call '{"tool":"get_reminders","arguments":{"marker_from":"2026-02-20","marker_to":"2026-03-19","category":"Иностранные сервисы","type":"expense"}}'
```

## Форматы данных

- Даты: yyyy-MM-dd
- Месяцы: yyyy-MM
- UUID: стандартный формат (get from get_accounts, get_categories)
- Валюта: instrument id (get from get_instruments)
- Типы транзакций: expense, income, transfer
- Типы счетов: cash, ccard, checking
- Интервалы напоминаний: day, week, month, year
