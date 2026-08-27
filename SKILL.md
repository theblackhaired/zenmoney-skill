---
name: zenmoney
description: "Personal finance management through ZenMoney API — 28 tools for accounts, transactions, Plans, budgets, reminders, analytics, and ML suggestions. Triggers: money, spending, budgets, accounts, financial management."
metadata:
  openclaw:
    requires:
      bins: [python]
---

# ZenMoney Personal Finance Assistant

28 tools для ZenMoney API. Все возвращают JSON.

## Проверка готовности (при каждом вызове)

Перед выполнением любого запроса проверь наличие данных:

1. **Токен доступа** — сначала проверь `ZENMONEY_TOKEN`, если env var пустой — используй `config.json` → `token`, если нет обоих источников — сначала настрой авторизацию
2. **`config.json` → `billing_period_start_day`** — если нет → спроси пользователя и запиши
3. **`config.json` → `accounts_meta`** — если нет или пустой → запусти инициализацию описаний (см. "Первичная инициализация", шаг 3)

Локальный `skill/PROFILE.md` необязателен. Если пользователь уже создал его, файл можно читать как приватный контекст; автоматически создавать или коммитить его нельзя.

Если обязательные runtime-данные на месте — работай как обычно. Если чего-то не хватает — сначала заполни недостающее, потом выполняй запрос.

## Auth and Runtime Files

- Предпочтительный источник токена: `ZENMONEY_TOKEN`
- Фолбэк только при пустом env var: `config.json` → `token`
- `setup_budget_mode` is cache-only and may run without a token; live reads and writes still require `ZENMONEY_TOKEN` or `config.json` -> `token`
- Локальный кэш хранится в `.cache.json` и использует `serverTimestamp` для diff-sync через ZenMoney API
- Writes are server-confirmed after `/v8/diff/`; if force-fetch verification does not confirm the submitted entity fields or deletion state, treat the write as failed
- `config.json` and `.cache.json` writes use file locks, atomic replace, and `LOST_UPDATE` protection for concurrent cache changes
- Почти все tools работают по модели `load cache -> prefetch sync -> handler`; исключения описаны в `scripts/zenmoney/tools.py`
- Подробности по токену, кэшу и sync policy: `docs/runtime-model.md`

## Как вызывать

```bash
python scripts/cli.py --call '{"tool":"TOOL_NAME","arguments":{...}}'
```

Windows: используй `python` или `py -3`. Если в окружении есть только `python3`, подставь его вручную.

PowerShell надёжно передаёт JSON через stdin:

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
'{"tool":"get_accounts","arguments":{}}' | python scripts/cli.py --call -
```

Для автоматизированных PowerShell-вызовов с кириллицей сериализуй JSON с `ensure_ascii=True`, чтобы не-ASCII символы передавались как `\uXXXX`. Не вставляй кириллицу напрямую в текст shell-команды.

Примеры:
```bash
python scripts/cli.py --call '{"tool":"get_accounts","arguments":{}}'
python scripts/cli.py --call '{"tool":"get_analytics","arguments":{"period":"month","report":"outcome","group_by":"category","currency_mode":"split"}}'
python scripts/cli.py --call '{"tool":"suggest","arguments":{"payee":"Тестовый магазин"}}'
python scripts/cli.py --list
python scripts/cli.py --describe get_transactions
```

## Period Contract

For `get_transactions` and `get_analytics`, select exactly one form:

- `period=billing_period|week|month|year` plus optional integer `period_offset`
- both inclusive `start_date` and `end_date` for a custom range

`period=week` requires `first_weekday=0..6` (`0` Monday). `analyze_budget_detailed` accepts only `period=billing_period`. Old magic values inside `start_date` and incomplete custom ranges are rejected.

## Tool Reference (28 tools)

**Read:**
- `get_accounts` — `include_archived`
- `get_transactions` — named `period` + optional `period_offset`, or exact `start_date` + `end_date`; `first_weekday` for week; `account_id`, `category_id`, `type`, `limit`, `offset`
- `get_categories` — no args
- `get_instruments` — `include_all`
- `get_budgets` — `month`(req, yyyy-MM)
- `get_reminders` — `include_processed`, `active_only`, `limit`, `markers_limit`, `offset`, `marker_from`(yyyy-MM-dd), `marker_to`(yyyy-MM-dd), `category`(name), `type`(expense/income/transfer/all)
- `analyze_budget_detailed` — `period`(req: billing_period), `period_offset`, `budget_mode`, `difference_calculation_mode`, `show_forecast`, `show_calendar`
- `get_analytics` — named `period` + optional `period_offset`, or exact `start_date` + `end_date`; `first_weekday` for week; `report`(req: income/outcome/net), `group_by`(category/account/merchant; default category), `currency_mode`(split/scalar; default split), strict account/category/merchant scopes
- `get_category_report` — strict period; `direction`(INCOME/OUTCOME), `group_by`(TAG/PAYEE), `budget_method`(BUDGET/MEAN), `comparison_periods`, `difference_calculation_mode`, account scope
- `get_money_flow` — strict period and account scope; returns native-currency flow buckets, residue/overspending, and weights
- `get_income_outcome_comparison` — strict period; `mode`(WHOLE_PERIOD/AVERAGE_VALUES), `comparison_periods`, account scope
- `get_balance_trend` — strict period and account scope; optional `currency_filter` and `currency`
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
- `create_reminder` — `type`, `amount`, `account_id`, `interval`(req), `step`(positive), `points`(offsets where `0 <= point < step`), `start_date`, `end_date`, `payee`, `comment`, `notify`, `generate_markers`(default 12, 0 to skip)
- `update_reminder` — `id`(req), partial fields
- `delete_reminder` — `id`(req)
- `create_reminder_marker` — `type`, `amount`, `account_id`, `date`(req), `reminder_id`, `payee`, `comment`, `notify`
- `delete_reminder_marker` — `id`(req)

## Быстрый маршрутизатор

| Задача | Tool(s) |
|---|---|
| Баланс, счета | `get_accounts()` |
| Расходы за период | `get_transactions(period="month", type="expense")` |
| Аналитика расходов | `get_analytics(period="month", report="outcome", group_by="category", currency_mode="split")` |
| Планы по категориям: план, свободно, перерасход | `analyze_budget_detailed(period="billing_period")` → `expenses[].plan`, `remaining`, `overspend` |
| План/факт по категориям | `get_category_report(period="billing_period", direction="OUTCOME")` |
| Денежный поток | `get_money_flow(period="month")` |
| Доходы против расходов | `get_income_outcome_comparison(period="month")` |
| Динамика баланса | `get_balance_trend(period="month")` |
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
| UUID счёта по имени | `get_accounts()` |
| UUID категории по имени | `get_categories()` |

## Budget and Reminder Semantics

- Budget `category` accepts `ALL`, `ALL (aggregate)`, or `00000000-0000-0000-0000-000000000000` for the aggregate budget. The runtime validates that as the zero UUID and writes the current budget payload with that zero UUID.
- Reminder `points` are recurrence offsets, not month days. For monthly/yearly reminders, the day of month comes from `start_date`; missing month days clamp to the real month end.
- Omit `points` to use `[0]`, meaning one marker on each base recurrence date.

## Workflows

**Анализ расходов:**
```bash
python scripts/cli.py --call '{"tool":"get_analytics","arguments":{"start_date":"2026-02-01","end_date":"2026-02-28","report":"outcome","group_by":"category","currency_mode":"split"}}'
```

**Добавить транзакцию:**
1. `suggest` с payee → UUID категории
2. `get_accounts` → UUID счёта
3. `create_transaction` с type/amount/account_id/category_ids

**Проверка строки экрана «Планы»:** `analyze_budget_detailed(period="billing_period")` → найди категорию в дереве `expenses`; используй `plan`, `remaining`, `overspend`. Поле `reserve_remaining` — внутренний резерв итоговой формулы, а не экранное «свободно».

Для данных экрана «Планы» используй только `analyze_budget_detailed`. Если вызов завершился ошибкой, остановись и сообщи её; не аппроксимируй поля Plans через `get_analytics`, `get_reminders`, `get_budgets` или их комбинацию. Если исторические `/instrument-rates/` недоступны, Plans использует синхронизированные текущие `Instrument.rate` и явно сообщает это в `metadata.instrument_rates`; невалидный текущий курс остаётся ошибкой.

## Analytics Semantics

- `report` обязателен: `income`, `outcome` или `net`.
- `group_by` опционален: по умолчанию `category`; также принимает `account` или `merchant`.
- `currency_mode` опционален: по умолчанию `split`; также принимает `scalar`.
- `account_scope` опционален: по умолчанию `in_balance`; принимает `all`, `in_balance`, `selected`. Для `selected` нужен непустой `account_ids`.
- `category_scope` опционален: по умолчанию `all`; для `selected` нужен непустой `category_ids`. `category_role=primary|additional|any` разрешён только при `category_scope="selected"`.
- `merchant_scope` опционален: по умолчанию `all`; для `selected` нужен непустой фильтр `merchant_ids` и/или `payees`.
- Политики фиксированы: `tag_policy=primary_tag`, `currency_conversion=none`, `transfers=excluded`, `unknown_currency=separate_bucket`.
- Поля ответа используют `snake_case`.
- Стабильные ключи групп имеют префиксы `category:`, `account:`, `merchant:`; при `group_by="merchant"` операция без merchant ID использует запасной ключ `payee:`.
- Фильтры разных измерений объединяются через AND; значения внутри одного selected-измерения объединяются через OR.
- Пустые selected-списки невалидны; неизвестные ID счетов, категорий и merchants возвращают `ENTITY_NOT_FOUND`.
- `account_scope="in_balance"` применяет фильтр к стороне операции, соответствующей `report`; аккаунты с отсутствующим `inBalance` исключаются, архивные аккаунты разрешены.
- Группировка по категориям всегда использует первый тег; неизвестный tag ID и отсутствие тегов — разные группы.
- Merchant ID имеет приоритет над `payee`; `payees` сравниваются после NFC-нормализации точным способом с учётом регистра.
- Ответ возвращает нормализованные `filters` и `policies`. Неизвестные аргументы и единственные формы `account_id`, `category_id`, `merchant_id`, `payee` отклоняются.
- Движение денег проектируется как отдельный отчёт и не входит в `get_analytics`.
- `get_category_report` применяет `REFUNDS`, `INCOME_OUTCOME_AND_REFUNDS` или `NONE`; возврат — противоположная операция на том же счёте с основной категорией, видимой только на целевой стороне.
- Четыре advanced-отчёта используют общий строгий period/account scope; конвертация берёт исторический курс на дату и только затем текущий fallback.
- `AVERAGE_VALUES` для диапазона длиннее 31 дня возвращает явную unsupported-ошибку, пока точная APK-формула не подтверждена.

## Платёжный период (config.json)

Параметр `billing_period_start_day` в `config.json` задаёт день начала платёжного периода (целое 1..31).

- Допустимый день начала задаётся локально в `config.json`; tracked-файлы не содержат пользовательское значение.
- Plans и Analytics вычисляют границы одним resolver.
- `Budget.date` всегда остаётся `YYYY-MM-01` логического месяца периода.

**Формула границы логического месяца:**
```
if start_day существует в месяце:
    boundary = дата с start_day
else:
    boundary = первое число следующего месяца
internal_range = [boundary(logical_month), boundary(next_logical_month))
public_end_date = next_boundary - 1 день
```

**Округление баланса:**
- `round_balance_to_integer` (boolean, по умолчанию true) — округлять итоговый баланс и прогноз до целых рублей
- Используется для совпадения с отображением в ZenMoney приложении

## Local private profile

If a private profile is needed, keep it outside the tracked repository or in an ignored local file. Never commit account identifiers, category identifiers, balances, plans, or user-specific period settings.

## Первичная инициализация

При первом запуске скилла или после значительных изменений в ZenMoney выполни:

### 1. Настрой платёжный период
- Спроси пользователя: "С какого числа у вас начинается платёжный период?"
- Запиши значение в `config.json` → `billing_period_start_day`

### 2. Выбери режим работы с планами (бюджетом)
- По умолчанию `analyze_budget_detailed` читает режим и переключатели из синхронизированного объекта пользователя ZenMoney
- Доступно 2 режима (аналогично настройкам ZenMoney → Планы → Настройки → Режим работы):
  - **"Баланс vs Расходы"** (`balance_vs_expense`) — включает входящий баланс и все переводы, пересекающие границу балансовых счетов
  - **"Доходы vs Расходы"** (`income_vs_expense`) — исключает лишние переводы, фокус на реальных доходах/расходах
- Локально переопределить режим и политику разницы можно через `setup_budget_mode(mode="...", difference_calculation_mode="...")`; `balance_vs_expense` всегда применяет `NONE`
- При нескольких пользователях в семейном профиле укажи авторитетный `plan_user_id`, иначе расчёт остановится с явной ошибкой неоднозначности

### 3. Заполни описания счетов (accounts_meta в config.json)
- Вызови `get_accounts()` — список всех счетов
- Для каждого активного счёта (`archived: false`) определи назначение:
  - По названию и банку (если очевидно)
  - По последним транзакциям: `get_transactions(start_date="2026-04-01", end_date="2026-04-30", account_id=UUID, limit=20)`
  - По напоминаниям: какие регулярные платежи привязаны к счёту
- Сгенерируй описание по правилам (см. "accounts_meta — правила описаний")
- Запиши в `config.json` → `accounts_meta`

**Когда повторять:** при добавлении/удалении счетов, смене банка, изменении структуры категорий.

## accounts_meta — правила описаний

Описания счетов хранятся в `config.json` → `accounts_meta`. Ключ — UUID счёта, значение — объект с полем `description`.

Данные категорий и счетов генерируются на лету из кэша API (не из файлов). Описания из `accounts_meta` автоматически мержатся в результаты `analyze_budget_detailed`.

**Формат в config.json:**
```json
{
  "accounts_meta": {
    "UUID-счёта": {
      "description": "Краткое описание роли и назначения"
    }
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
- `"Основной расчётный счёт"`
- `"Регулярные подписки"`
- `"Транзитный счёт для обязательных платежей"`
- `"Накопления на цель"`
- `"Не используется"`

## Режимы get_reminders

### recent-summary mode (без marker_from/marker_to)
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

**Пример — регулярные подписки за период:**
```bash
python scripts/cli.py --call '{"tool":"get_reminders","arguments":{"marker_from":"2026-04-01","marker_to":"2026-04-30","category":"Подписки","type":"expense"}}'
```

## Форматы данных

- Даты: yyyy-MM-dd
- Месяцы: yyyy-MM
- UUID: стандартный формат (get from get_accounts, get_categories)
- Валюта: instrument id (get from get_instruments)
- Типы транзакций: expense, income, transfer
- Типы счетов: cash, ccard, checking
- Интервалы напоминаний: day, week, month, year
