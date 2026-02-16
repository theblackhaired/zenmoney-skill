---
name: zenmoney
description: "Token-efficient personal finance management through ZenMoney API via executor. Use when the user needs to: (1) Analyze spending and income (expenses by category, monthly budgets, financial analytics), (2) Create or modify transactions (add expenses, record income, transfers between accounts), (3) View account balances and financial data, (4) Plan budget or check financial capacity for purchases, (5) Work with reminders and scheduled payments, (6) Get ML-powered category suggestions for transactions. Triggers include questions about money, spending, budgets, accounts, or any financial management tasks in Russian or English. Context-efficient: ~100 tokens idle vs ~10k for direct MCP."
---

# ZenMoney Personal Finance Assistant (Executor Mode)

Управляй финансами через ZenMoney API в Claude Code. Анализируй расходы, создавай транзакции, планируй бюджет через естественный диалог.

**Architecture:** `Claude Code → Bash → executor.py → MCP Server → ZenMoney API`

**Benefits:** Context-efficient (~100 tokens idle vs ~10k for direct MCP tools preloading)

## Quick Start

### Initial Setup

```bash
# 1. Build MCP server
npm install && npm run build

# 2. Install Python MCP package
pip install mcp

# 3. Configure (one-time)
cd ~/.claude/skills/zenmoney/
cp mcp-config.example.json mcp-config.json

# Edit mcp-config.json:
# - Set absolute path to dist/index.js
# - Set ZENMONEY_TOKEN (get from https://budgera.com/settings/export)

# 4. Restart Claude Code
```

### Tool Invocation Pattern

All tools are called via executor.py:

```bash
# List available tools
python ~/.claude/skills/zenmoney/executor.py --list

# Get tool schema
python ~/.claude/skills/zenmoney/executor.py --describe get_accounts

# Call a tool
python ~/.claude/skills/zenmoney/executor.py --call '{
  "tool": "get_accounts",
  "arguments": {"include_archived": false}
}'
```

**IMPORTANT:** Always use absolute path to executor.py or cd into skill directory first.

## Core Tools (22 total)

### Read Tools (7)

**get_accounts** - View all accounts with balances
```bash
python executor.py --call '{"tool": "get_accounts", "arguments": {"include_archived": false}}'
```

**get_transactions** - Get transactions for period
```bash
python executor.py --call '{
  "tool": "get_transactions",
  "arguments": {
    "start_date": "2026-02-01",
    "end_date": "2026-02-28",
    "limit": 100
  }
}'
```

**get_categories** - Get all categories
```bash
python executor.py --call '{"tool": "get_categories", "arguments": {}}'
```

**get_instruments** - Get currencies and rates
```bash
python executor.py --call '{"tool": "get_instruments", "arguments": {}}'
```

**get_budgets** - View budgets for month
```bash
python executor.py --call '{"tool": "get_budgets", "arguments": {"month": "2026-03"}}'
```

**get_reminders** - View scheduled payments
```bash
python executor.py --call '{"tool": "get_reminders", "arguments": {"include_processed": false}}'
```

**get_analytics** - Spending/income analytics
```bash
python executor.py --call '{
  "tool": "get_analytics",
  "arguments": {
    "start_date": "2026-02-01",
    "end_date": "2026-02-28",
    "group_by": "category",
    "type": "expense"
  }
}'
```

### Analytics Tools (1)

**suggest** - ML-powered category suggestions
```bash
python executor.py --call '{"tool": "suggest", "arguments": {"payee": "Пятёрочка"}}'
```

### Write Tools (12)

**create_transaction** - Create new transaction
```bash
python executor.py --call '{
  "tool": "create_transaction",
  "arguments": {
    "type": "expense",
    "amount": 500,
    "account_id": "account-uuid",
    "category_ids": ["category-uuid"],
    "payee": "Кофе",
    "date": "2026-02-16"
  }
}'
```

**update_transaction** - Modify existing transaction
```bash
python executor.py --call '{
  "tool": "update_transaction",
  "arguments": {
    "id": "transaction-uuid",
    "amount": 600,
    "comment": "Updated"
  }
}'
```

**delete_transaction** - Delete transaction
```bash
python executor.py --call '{"tool": "delete_transaction", "arguments": {"id": "transaction-uuid"}}'
```

**create_account** - Create new account
```bash
python executor.py --call '{
  "tool": "create_account",
  "arguments": {
    "title": "Наличные",
    "type": "cash",
    "currency_id": 643,
    "balance": 5000
  }
}'
```

**create_reminder** - Create recurring reminder
```bash
python executor.py --call '{
  "tool": "create_reminder",
  "arguments": {
    "type": "expense",
    "amount": 2500,
    "account_id": "account-uuid",
    "interval": "month",
    "points": [5],
    "payee": "Аренда"
  }
}'
```

**update_reminder** - Modify reminder
```bash
python executor.py --call '{
  "tool": "update_reminder",
  "arguments": {
    "id": "reminder-uuid",
    "amount": 3000
  }
}'
```

**delete_reminder** - Delete recurring reminder
```bash
python executor.py --call '{"tool": "delete_reminder", "arguments": {"id": "reminder-uuid"}}'
```

**create_reminder_marker** - Create one-time reminder
```bash
python executor.py --call '{
  "tool": "create_reminder_marker",
  "arguments": {
    "type": "income",
    "amount": 150000,
    "account_id": "account-uuid",
    "date": "2026-03-05",
    "payee": "Зарплата"
  }
}'
```

**delete_reminder_marker** - Delete one-time reminder
```bash
python executor.py --call '{"tool": "delete_reminder_marker", "arguments": {"id": "marker-uuid"}}'
```

**create_budget** - Create/update budget limit
```bash
python executor.py --call '{
  "tool": "create_budget",
  "arguments": {
    "month": "2026-03",
    "category": "Продукты",
    "outcome": 10000
  }
}'
```

**update_budget** - Modify existing budget
```bash
python executor.py --call '{
  "tool": "update_budget",
  "arguments": {
    "month": "2026-03",
    "category": "Продукты",
    "outcome": 12000
  }
}'
```

**delete_budget** - Remove budget limit
```bash
python executor.py --call '{"tool": "delete_budget", "arguments": {"month": "2026-03", "category": "Продукты"}}'
```

### System Tools (1)

**check_auth_status** - Verify token validity
```bash
python executor.py --call '{"tool": "check_auth_status", "arguments": {}}'
```

For full parameter documentation, see [tools-full.md](references/tools-full.md).

## Usage Workflow

### Example 1: Monthly Expense Analysis

```
User: Сколько я потратил в феврале 2026?

Claude:
1. Calls get_analytics via executor:
   python executor.py --call '{
     "tool": "get_analytics",
     "arguments": {
       "start_date": "2026-02-01",
       "end_date": "2026-02-28",
       "type": "expense",
       "group_by": "category"
     }
   }'

2. Parses JSON response and presents:
   - Total expenses
   - Top-5 categories
   - Percentage distribution
```

### Example 2: Quick Expense Entry

```
User: Купил кофе за 250 рублей

Claude:
1. Get ML category suggestion:
   python executor.py --call '{"tool": "suggest", "arguments": {"payee": "кофе"}}'

2. Get primary account:
   python executor.py --call '{"tool": "get_accounts", "arguments": {}}'

3. Create transaction:
   python executor.py --call '{
     "tool": "create_transaction",
     "arguments": {
       "type": "expense",
       "amount": 250,
       "account_id": "primary-account-uuid",
       "category_ids": ["suggested-category-uuid"],
       "payee": "Кофе"
     }
   }'
```

## Smart Features

### Auto Context Detection

Claude understands queries without precise wording:
- "Сколько потратил?" → current month expenses via get_analytics
- "Добавь кофе 200р" → create expense with ML category via suggest
- "Баланс" → show all accounts via get_accounts

### Natural Date Parsing

Supports intuitive date expressions:
- "в этом месяце" → current calendar month
- "за последние 30 дней" → last 30 days from today
- "в январе" → 2026-01-01 to 2026-01-31

### Error Handling

If executor fails:
1. Check mcp-config.json is correctly configured
2. Verify MCP server is built: `npm run build`
3. Test executor directly: `python executor.py --list`
4. Check token validity: Use check_auth_status

## Additional Resources

- **Troubleshooting**: See [troubleshooting.md](references/troubleshooting.md) for auth issues, executor errors
- **Advanced Usage**: See [advanced.md](references/advanced.md) for bulk operations, integrations
- **Full Tool Reference**: See [tools-full.md](references/tools-full.md) for complete documentation of all 22 tools

## Architecture

```
[Claude Code] → [Skill] → [Bash] → [executor.py] → [MCP Server] → [ZenMoney API]
                                           ↓
                                    [DataCache]
```

**Benefits:**
- **Context efficiency:** ~100 tokens idle vs ~10k for preloaded MCP tools
- **Streamlined deployment:** Copy directory, configure token, restart
- **Full access:** All 22 tools available through unified interface
- **Python MCP handles:** Protocol complexity and validation

**Caching:**
- First request: full sync of all data
- Subsequent: incremental sync (only changes)
- Cache stored in MCP server memory (resets on restart)

**Security:**
- Token stored in mcp-config.json (git-ignored)
- All requests via HTTPS
- Auto token refresh on expiration (credential-based auth)
