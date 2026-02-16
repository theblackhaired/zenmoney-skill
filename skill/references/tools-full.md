# Complete Tool Reference

All 22 ZenMoney MCP tools with full parameter documentation and examples.

## Table of Contents

### Read Tools (7)
- [get_accounts](#get_accounts)
- [get_transactions](#get_transactions)
- [get_categories](#get_categories)
- [get_instruments](#get_instruments)
- [get_budgets](#get_budgets)
- [get_reminders](#get_reminders)
- [get_analytics](#get_analytics)

### Analytics Tools (2)
- [get_analytics](#get_analytics) (also listed above)
- [suggest](#suggest)

### Write Tools (12)
- [create_transaction](#create_transaction)
- [update_transaction](#update_transaction)
- [delete_transaction](#delete_transaction)
- [create_account](#create_account)
- [create_reminder](#create_reminder)
- [update_reminder](#update_reminder)
- [delete_reminder](#delete_reminder)
- [create_reminder_marker](#create_reminder_marker)
- [delete_reminder_marker](#delete_reminder_marker)
- [create_budget](#create_budget)
- [update_budget](#update_budget)
- [delete_budget](#delete_budget)

### System Tools (1)
- [check_auth_status](#check_auth_status)

---

## Read Tools

### get_accounts

Get all ZenMoney accounts with balances.

**Parameters:**
- `include_archived` (boolean, optional) - Include archived accounts (default: false)

**Returns:**
```json
[
  {
    "id": "uuid",
    "title": "Тинькофф",
    "type": "ccard",
    "balance": 15000.50,
    "currency": "RUB",
    "archived": false
  }
]
```

**Account types:**
- `cash` - Cash wallet
- `ccard` - Credit card
- `checking` - Checking account
- `deposit` - Deposit account
- `loan` - Loan account

**Examples:**
```
Покажи все мои счета
Список счетов включая архивные
Какой баланс на картах?
```

---

### get_transactions

Get transactions filtered by date range, account, category, or type.

**Parameters:**
- `start_date` (string, **required**) - Start date (yyyy-MM-dd)
- `end_date` (string, optional) - End date (yyyy-MM-dd, default: today)
- `account_id` (string, optional) - Filter by account UUID
- `category_id` (string, optional) - Filter by category UUID
- `type` (string, optional) - Filter by type: 'expense', 'income', 'transfer'
- `limit` (number, optional) - Max results (default: 100, max: 500)

**Returns:**
```json
[
  {
    "id": "uuid",
    "date": "2026-02-15",
    "type": "expense",
    "amount": 250.00,
    "account": "Тинькофф",
    "payee": "Кофе",
    "categories": ["Кафе и рестораны"],
    "comment": "утренний кофе"
  }
]
```

**Examples:**
```
Покажи расходы за февраль
Транзакции по карте Тинькофф за последний месяц
Все переводы между счетами за год
Последние 50 транзакций
```

---

### get_categories

Get all categories (tags) in hierarchical structure.

**Parameters:** None

**Returns:**
```json
[
  {
    "id": "uuid",
    "title": "Продукты",
    "parent": null,
    "children": ["Супермаркеты", "Рынки"]
  }
]
```

**Examples:**
```
Покажи все категории
Какие есть подкатегории в Транспорте?
Список категорий расходов
```

---

### get_instruments

Get list of currencies and exchange rates.

**Parameters:** None

**Returns:**
```json
[
  {
    "id": 643,
    "code": "RUB",
    "title": "Рубль",
    "rate": 1.0
  },
  {
    "id": 840,
    "code": "USD",
    "title": "Доллар США",
    "rate": 0.0112
  }
]
```

**Examples:**
```
Какие валюты поддерживаются?
Покажи курс доллара
Список всех валют
```

---

### get_budgets

Get budgets for a specific month. Shows planned income/outcome per category.

**Parameters:**
- `month` (string, **required**) - Month in yyyy-MM format (e.g., "2026-03")

**Returns:**
```json
[
  {
    "category": "Продукты",
    "month": "2026-03-01",
    "income": 0,
    "incomeLock": false,
    "outcome": 10000,
    "outcomeLock": false
  }
]
```

**Examples:**
```
Покажи бюджет на март 2026
Какой бюджет на текущий месяц?
Лимиты расходов по категориям
```

---

### get_reminders

Get scheduled payment reminders. Shows recurring transactions and their markers.

**Parameters:**
- `include_processed` (boolean, optional) - Include already processed reminders (default: false)

**Returns:**
```json
[
  {
    "id": "reminder-uuid",
    "type": "expense",
    "amount": 2500,
    "payee": "Интернет",
    "interval": "month",
    "points": [5],
    "markers": [
      {
        "id": "marker-uuid",
        "date": "2026-03-05",
        "state": "planned"
      }
    ]
  }
]
```

**Marker states:**
- `planned` - Scheduled, not yet executed
- `processed` - Already executed/completed
- `deleted` - Cancelled/removed

**Examples:**
```
Какие платежи запланированы?
Покажи все напоминания на март
Список регулярных платежей
```

---

### get_analytics

Spending and income analytics with grouping. Powerful tool for financial analysis.

**Parameters:**
- `start_date` (string, **required**) - Start date (yyyy-MM-dd)
- `end_date` (string, optional) - End date (yyyy-MM-dd, default: today)
- `group_by` (string, optional) - Group by: 'category', 'account', 'merchant' (default: 'category')
- `type` (string, optional) - Type: 'expense', 'income', 'all' (default: 'expense')

**Returns:**
```json
{
  "total": 45000,
  "count": 87,
  "groups": [
    {
      "name": "Продукты",
      "amount": 15000,
      "count": 24,
      "percentage": 33.3
    }
  ]
}
```

**Examples:**
```
Анализ расходов по категориям за февраль
Сколько потратил в каждом магазине за месяц?
Доходы по счетам за год
Все операции (доходы + расходы) за квартал
```

---

### suggest

ML-powered category suggestions for transaction payee names.

**Parameters:**
- `payee` (string, **required**) - Merchant/payee name

**Returns:**
```json
{
  "payee": "Пятёрочка",
  "suggestions": [
    {
      "category_id": "uuid",
      "category_name": "Продукты",
      "confidence": 0.95
    }
  ]
}
```

**Examples:**
```
Какую категорию предложить для "Пятёрочка"?
Подскажи категорию для Starbucks
Категория для транзакции в Яндекс.Такси
```

---

## Write Tools

### create_transaction

Create a new transaction in ZenMoney.

**Parameters:**
- `type` (string, **required**) - Transaction type: 'expense', 'income', 'transfer'
- `amount` (number, **required**) - Amount (positive number)
- `account_id` (string, **required**) - Account UUID
  - For expense: source account (money goes out)
  - For income: destination account (money comes in)
  - For transfer: source account
- `to_account_id` (string, **required for transfers**) - Destination account UUID
- `category_ids` (array of strings, optional) - Category UUIDs
- `date` (string, optional) - Date (yyyy-MM-dd, default: today)
- `payee` (string, optional) - Merchant/payee name
- `comment` (string, optional) - Comment/note
- `currency_id` (number, optional) - Currency ID if different from account currency

**Examples:**

**Expense:**
```
create_transaction(
  type="expense",
  amount=500,
  account_id="тинькофф-uuid",
  category_ids=["кафе-uuid"],
  payee="Кофе",
  comment="утренний кофе"
)
```

**Income:**
```
create_transaction(
  type="income",
  amount=150000,
  account_id="зарплатная-карта-uuid",
  category_ids=["зарплата-uuid"],
  payee="Зарплата",
  date="2026-03-05"
)
```

**Transfer:**
```
create_transaction(
  type="transfer",
  amount=10000,
  account_id="карта-uuid",
  to_account_id="наличные-uuid",
  comment="снятие наличных"
)
```

**Command examples:**
```
Добавь расход 1500 рублей на кофе
Создай доход 50000 как зарплату на карту
Перевод 10000 с карты на наличные
```

---

### update_transaction

Modify an existing transaction. Only provide fields you want to change.

**Parameters:**
- `id` (string, **required**) - Transaction UUID
- `amount` (number, optional) - New amount
- `category_ids` (array, optional) - New category UUIDs
- `payee` (string, optional) - New payee name
- `comment` (string, optional) - New comment

**Examples:**
```
update_transaction(
  id="transaction-uuid",
  amount=2000,
  comment="деловой обед"
)
```

**Command examples:**
```
Измени сумму транзакции на 2000
Добавь комментарий "деловой обед" к последней транзакции
Поменяй категорию у транзакции на Продукты
```

---

### delete_transaction

Delete a transaction (soft-delete by setting `deleted: true`).

**Parameters:**
- `id` (string, **required**) - Transaction UUID

**Examples:**
```
delete_transaction(id="transaction-uuid")
```

**Command examples:**
```
Удали последнюю транзакцию
Удали ошибочный платёж
```

**Warning:** Deletion is soft (sets deleted flag) but should still be done carefully.

---

### create_account

Create a new account in ZenMoney.

**Parameters:**
- `title` (string, **required**) - Account name
- `type` (string, **required**) - Account type: 'cash', 'ccard', 'checking'
- `currency_id` (number, **required**) - Currency instrument ID (get from get_instruments)
- `balance` (number, optional) - Initial balance (default: 0)
- `credit_limit` (number, optional) - Credit limit for ccard type (default: 0)

**Supported types:**
- `cash` - Cash wallet
- `ccard` - Credit card
- `checking` - Checking account

**Note:** Loan and deposit account types not supported via API.

**Examples:**
```
create_account(
  title="Наличные доллары",
  type="cash",
  currency_id=840,  # USD
  balance=500
)

create_account(
  title="Альфа-Банк",
  type="checking",
  currency_id=643,  # RUB
  balance=10000
)

create_account(
  title="Кредитка Тинькофф",
  type="ccard",
  currency_id=643,
  credit_limit=100000
)
```

**Command examples:**
```
Создай кошелёк "Наличные доллары" в долларах
Добавь счёт "Альфа-банк" типа checking
Создай кредитку с лимитом 100000
```

---

### create_reminder

Create a recurring reminder (planned transaction) in ZenMoney.

**Parameters:**
- `type` (string, **required**) - Reminder type: 'expense', 'income', 'transfer'
- `amount` (number, **required**) - Amount (positive number)
- `account_id` (string, **required**) - Account UUID
- `interval` (string, **required**) - Recurrence: 'day', 'week', 'month', 'year'
- `to_account_id` (string, required for transfers) - Destination account
- `category_ids` (array, optional) - Category UUIDs
- `start_date` (string, optional) - Start date (yyyy-MM-dd, default: today)
- `end_date` (string, optional) - End date (yyyy-MM-dd, null for indefinite)
- `step` (number, optional) - Step multiplier (e.g., 2 for every 2 months, default: 1)
- `points` (array of numbers, optional) - Specific points in interval
  - For month: [1, 15] = 1st and 15th day
  - For week: [1, 5] = Monday and Friday
- `payee` (string, optional) - Payee name
- `comment` (string, optional) - Comment
- `notify` (boolean, optional) - Enable notifications (default: true)

**Examples:**

**Monthly rent:**
```
create_reminder(
  type="expense",
  amount=25000,
  account_id="карта-uuid",
  interval="month",
  points=[1],  # 1st of each month
  payee="Аренда",
  comment="Квартира"
)
```

**Bi-weekly salary:**
```
create_reminder(
  type="income",
  amount=75000,
  account_id="зарплатная-uuid",
  interval="week",
  step=2,  # every 2 weeks
  payee="Зарплата"
)
```

**Quarterly insurance:**
```
create_reminder(
  type="expense",
  amount=15000,
  account_id="карта-uuid",
  interval="month",
  step=3,  # every 3 months
  payee="Страховка"
)
```

**Command examples:**
```
Создай ежемесячное напоминание на аренду 25000р
Напомни про зарплату каждые 2 недели
Квартальный платёж за страховку 15000р
```

---

### update_reminder

Update an existing reminder. Only provide fields you want to change.

**Parameters:**
- `id` (string, **required**) - Reminder UUID
- `amount` (number, optional) - New amount
- `category_ids` (array, optional) - New category UUIDs
- `payee` (string, optional) - New payee
- `comment` (string, optional) - New comment
- `interval` (string, optional) - New interval
- `step` (number, optional) - New step
- `points` (array, optional) - New points
- `end_date` (string, optional) - New end date
- `notify` (boolean, optional) - New notify setting

**Examples:**
```
update_reminder(
  id="reminder-uuid",
  amount=30000,  # rent increased
  comment="Новая квартира"
)
```

**Command examples:**
```
Увеличь сумму напоминания об аренде до 30000
Измени дату платежа на 5 число
```

---

### delete_reminder

Delete a recurring reminder (soft-delete).

**Parameters:**
- `id` (string, **required**) - Reminder UUID

**Examples:**
```
delete_reminder(id="reminder-uuid")
```

**Command examples:**
```
Удали напоминание об аренде
Убери регулярный платёж за интернет
```

**Note:** This deletes the recurring template. Associated markers may remain.

---

### create_reminder_marker

Create a one-time reminder marker (разовое напоминание) for a specific date.

Perfect for salary/payments that vary each month.

**Parameters:**
- `type` (string, **required**) - Type: 'expense', 'income', 'transfer'
- `amount` (number, **required**) - Amount (positive number)
- `account_id` (string, **required**) - Account UUID
- `date` (string, **required**) - Date when transaction should occur (yyyy-MM-dd)
- `to_account_id` (string, required for transfers) - Destination account
- `category_ids` (array, optional) - Category UUIDs
- `payee` (string, optional) - Payee name
- `comment` (string, optional) - Comment
- `notify` (boolean, optional) - Enable notifications (default: true)
- `reminder_id` (string, optional) - Link to existing Reminder. If not provided, creates one-time Reminder automatically.

**Examples:**

**Variable salary:**
```
create_reminder_marker(
  type="income",
  amount=150000,  # this month's amount
  account_id="зарплатная-uuid",
  date="2026-03-05",
  payee="Зарплата",
  comment="Основная + премия"
)
```

**One-time bill:**
```
create_reminder_marker(
  type="expense",
  amount=5000,
  account_id="карта-uuid",
  date="2026-03-20",
  payee="Налог на имущество",
  comment="Годовой платёж"
)
```

**Command examples:**
```
Напомни про зарплату 150000р пятого марта
Создай разовое напоминание на оплату налога 5000р
```

---

### delete_reminder_marker

Delete a reminder marker (разовое напоминание). Soft-delete by setting state to 'deleted'.

**Parameters:**
- `id` (string, **required**) - ReminderMarker UUID

**Examples:**
```
delete_reminder_marker(id="marker-uuid")
```

**Command examples:**
```
Удали напоминание о зарплате на 5 марта
Убери разовое напоминание
```

---

### create_budget

Create or update budget limit for a category in a specific month.

**Parameters:**
- `month` (string, **required**) - Month in yyyy-MM format (e.g., "2026-03")
- `category` (string, **required**) - Category name or UUID. Use "ALL" for aggregate budget across all categories.
- `income` (number, optional) - Income budget limit (default: 0)
- `outcome` (number, optional) - Outcome budget limit (default: 0)
- `income_lock` (boolean, optional) - Lock income budget to prevent auto-changes (default: false)
- `outcome_lock` (boolean, optional) - Lock outcome budget to prevent auto-changes (default: false)

**Special category "ALL":**
Use `category="ALL"` to create aggregate budget across all categories.
Internally uses category ID: `00000000-0000-0000-0000-000000000000`

**Examples:**

**Spending limit:**
```
create_budget(
  month="2026-03",
  category="Продукты",
  outcome=10000
)
```

**Income expectation:**
```
create_budget(
  month="2026-03",
  category="Зарплата",
  income=240000
)
```

**Aggregate budget:**
```
create_budget(
  month="2026-03",
  category="ALL",
  outcome=150000,  # total monthly spending limit
  outcome_lock=true  # prevent auto-adjustment
)
```

**Command examples:**
```
Установи бюджет 10000р на Продукты для марта
Запланируй доход 240000 по категории Зарплата
Общий лимит расходов 150000 на март
```

---

### update_budget

Update existing budget for a category. Only provide fields you want to change.

**Parameters:**
- `month` (string, **required**) - Month in yyyy-MM format
- `category` (string, **required**) - Category name or UUID. Use "ALL" for aggregate.
- `income` (number, optional) - New income budget limit
- `outcome` (number, optional) - New outcome budget limit
- `income_lock` (boolean, optional) - New income lock state
- `outcome_lock` (boolean, optional) - New outcome lock state

**Examples:**
```
update_budget(
  month="2026-03",
  category="Продукты",
  outcome=12000,  # increased from 10000
  outcome_lock=true  # now locked
)
```

**Command examples:**
```
Увеличь бюджет на Продукты до 12000
Заблокируй бюджет на Развлечения
```

---

### delete_budget

Delete budget for a category by setting both income and outcome to 0.

**Parameters:**
- `month` (string, **required**) - Month in yyyy-MM format
- `category` (string, **required**) - Category name or UUID. Use "ALL" for aggregate.

**Examples:**
```
delete_budget(
  month="2026-03",
  category="Продукты"
)
```

**Command examples:**
```
Удали бюджет на Продукты для марта
Убери лимит расходов на Развлечения
```

---

## System Tools

### check_auth_status

Verify auth token validity and connection status.

**Parameters:** None

**Returns:**
```json
{
  "authenticated": true,
  "user_id": "user-uuid",
  "token_valid": true
}
```

**Examples:**
```
Проверь подключение к ZenMoney
Рабочий ли токен?
Статус авторизации
```

**Use when:**
- Seeing unexpected 401 errors
- After token renewal
- Regular weekly auth checks
- Troubleshooting connection issues
