# Advanced Usage

## Custom Calculation Periods

If your billing period is not a calendar month:

```
Анализ с 20 января по 19 февраля  # explicit dates
Расходы за мой период (20-19)     # Claude remembers pattern
```

**Example: Salary-based period (20th to 19th)**
```
get_analytics(
  start_date="2026-01-20",
  end_date="2026-02-19",
  type="all"
)
```

## Grouped Analysis

### Top Merchants by Spending

```
get_analytics(
  start_date="2026-01-01",
  end_date="2026-12-31",
  group_by="merchant",
  type="expense"
)
```

Shows which stores/merchants you spent most at.

### Monthly Comparison

```
# Get data for each month
jan_expenses = get_analytics(start_date="2026-01-01", end_date="2026-01-31")
feb_expenses = get_analytics(start_date="2026-02-01", end_date="2026-02-28")

# Compare totals and categories
```

### Category Trends Over Time

```
Динамика расходов на продукты за последние 6 месяцев
```

Claude will:
1. Get analytics for each month
2. Calculate category-specific spending
3. Show trend analysis

## Bulk Operations

### Multiple Transaction Creation

```
Создай 5 транзакций:
- Кофе 250р
- Обед 500р
- Такси 300р
- Продукты 1500р
- Кино 800р
```

Claude will:
1. Use `suggest` for each payee to get categories
2. Get primary account
3. Create all transactions with `create_transaction`
4. Report success for each

### Batch Category Updates

```
Обнови категории у всех транзакций от "Перекрёсток" на "Продукты"
```

Claude will:
1. Get transactions filtered by payee="Перекрёсток"
2. Get "Продукты" category UUID
3. Update each with `update_transaction`

### Cleanup Operations

```
Удали все тестовые транзакции за сегодня
```

Claude will:
1. Get today's transactions
2. Filter by comment/payee containing "тест"
3. Delete each with `delete_transaction`

**Warning:** Deletions are soft-deletes (set `deleted: true`) but should still be done carefully.

## Integration with Other Tools

### Google Sheets Export

Requires `google-sheets` MCP server.

```
Экспортируй февральскую аналитику в Google Sheets
```

Claude will:
1. Get analytics for February
2. Format data as table
3. Create/update Google Sheets document

### Browser Automation

Requires `claude-in-chrome` MCP server.

```
Открой ZenMoney в браузере и покажи текущий месяц
```

Useful for:
- Verifying data in UI
- Accessing features not available via API
- Screenshots for reports

### Filesystem Reports

```
Сохрани годовой отчёт в PDF
```

Claude will:
1. Generate analytics for the year
2. Format as Markdown report
3. Save to file (or convert to PDF if tools available)

## Custom Workflows

### Monthly Financial Review

Create a repeatable command:

```markdown
# Monthly Review Workflow

1. Get analytics for current month (all types)
2. Compare with budget
3. Identify top 5 expense categories
4. Check account balances
5. List upcoming reminders
6. Generate summary report
```

Invoke with: "Запусти месячный обзор"

### Debt Repayment Tracker

```markdown
# Credit Card Repayment Plan

1. Get current credit card balances
2. Get budgets for current month
3. Calculate available funds for repayment
4. Create reminders for payment dates
5. Track progress monthly
```

### Savings Goal Monitor

```markdown
# Savings Progress

1. Get income for period
2. Get expenses for period
3. Calculate surplus
4. Compare to savings goal
5. Estimate months to goal
```

## Advanced Budget Management

### Aggregate Budgets

Set overall monthly spending limit across all categories:

```
create_budget(
  month="2026-03",
  category="ALL",
  outcome=150000
)
```

Uses special category ID `00000000-0000-0000-0000-000000000000`.

### Budget Locks

Prevent ZenMoney from auto-adjusting budgets:

```
create_budget(
  month="2026-03",
  category="Продукты",
  outcome=10000,
  outcome_lock=true
)
```

### Income Budgets

Track expected income by category:

```
create_budget(
  month="2026-03",
  category="Зарплата",
  income=240000
)
```

## Reminder Strategies

### Recurring Bills

Use `create_reminder` for regular payments:

```
create_reminder(
  type="expense",
  amount=2500,
  account_id="...",
  interval="month",
  points=[5],  # 5th of each month
  payee="Интернет",
  comment="Билайн"
)
```

### Variable Recurring Payments

When amount varies each month (e.g., utilities, salary bonuses):

```
create_reminder_marker(
  type="income",
  amount=150000,  # this month's amount
  account_id="...",
  date="2026-03-05",
  payee="Зарплата"
)
```

Creates one-time reminder without recurring template.

### Payment Series

For loan repayment schedule:

```
# Create markers for each payment
create_reminder_marker(date="2026-03-05", amount=47159, payee="Райффайзен")
create_reminder_marker(date="2026-03-05", amount=52841, payee="Сбербанк")
create_reminder_marker(date="2026-04-05", amount=66647, payee="Сбербанк")
```

## Multi-Currency Workflows

### Currency Conversion

When creating transactions in foreign currency:

```
create_transaction(
  type="expense",
  amount=100,  # amount in foreign currency
  currency_id=840,  # USD (get from get_instruments)
  account_id="..."  # RUB account
)
```

ZenMoney will automatically convert using current rate.

### Exchange Rate Tracking

```
get_instruments()
```

Shows all supported currencies and exchange rates. Useful for:
- Planning foreign purchases
- Tracking currency positions
- Calculating multi-currency totals

## Performance Optimization

### Minimize API Calls

**Instead of:**
```
get_accounts()  # 3 separate calls
get_categories()
get_instruments()
```

**Do:**
```
# Call once, cache results in memory for session
accounts = get_accounts()
categories = get_categories()
instruments = get_instruments()
```

### Use Filters

**Instead of:**
```
get_transactions(start_date="2026-01-01", limit=500)
# then filter in code
```

**Do:**
```
get_transactions(
  start_date="2026-01-01",
  category_id="specific_category_uuid",
  type="expense"
)
```

### Incremental Analysis

For large date ranges, split into smaller periods:

```
# Instead of entire year at once
for month in months:
  analytics = get_analytics(start_date=month_start, end_date=month_end)
  # process monthly data
```

## Best Practices

1. **Always verify before deletion** - Use `get_transactions` to preview what will be deleted
2. **Use ML suggestions** - Call `suggest(payee="...")` before creating transactions
3. **Check auth regularly** - Run `check_auth_status` if seeing unexpected errors
4. **Descriptive payees** - Use specific merchant names for better ML categorization
5. **Restart for fresh data** - If data seems stale, restart Claude Code to re-sync
6. **Lock important budgets** - Use `outcome_lock=true` for strict budget limits
7. **Use reminder markers for variable payments** - Better than recurring reminders with fixed amounts
8. **Test bulk operations** - Try on one item first before batch processing
