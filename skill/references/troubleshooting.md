# Troubleshooting Guide

## Authentication Issues

### Error: "No authentication configured"

**Solution (choose one):**

**Option 1: BUDGERA (fastest)**
1. Open https://budgera.com/settings/export
2. Copy token from "ZenMoney API Token" section
3. Paste into `.mcp.json`:
   ```json
   {
     "mcpServers": {
       "zenmoney": {
         "env": {
           "ZENMONEY_TOKEN": "your_copied_token"
         }
       }
     }
   }
   ```
4. Restart Claude Code

**Option 2: Browser OAuth**
```bash
npm run auth
# After successful authorization, restart Claude Code
```

### Error: 401 / Token expired

Token expires after ~24 hours.

**Solution (choose one):**

**Option 1: BUDGERA**
1. Open https://budgera.com/settings/export
2. Copy new token
3. Update in `.mcp.json`
4. Restart Claude Code

**Option 2: Re-authenticate**
```bash
npm run auth
```

**Check status:**
```
Проверь подключение к ZenMoney
```

### Error: "ReminderMarker not found"

ReminderMarker may have been already deleted or processed.

**Solution:**
1. Use `get_reminders(include_processed=true)` to see all reminders
2. Check if the marker exists in the list
3. If not found, it was already deleted

## Data Issues

### Empty Results

**Possible causes:**
1. No data for specified period
2. Cache not synchronized (restart Claude Code)
3. Invalid account/category ID

**Solution:**
```
Покажи все счета  # verify accounts exist
Покажи категории  # verify category structure
```

### Budget Not Found

Budgets are identified by `(category, month)` pair, not UUID.

**Solution:**
1. Use `get_budgets(month="2026-03")` to check existing budgets
2. Use `create_budget` to create if missing
3. Verify category name/UUID is correct

### Stale Data After External Changes

Cache doesn't auto-refresh after changes in ZenMoney app.

**Solution:**
- Restart Claude Code to force full re-sync
- First request after restart will sync all data

## Build Issues

### MCP Server Not Loading

**Solution:**
1. Ensure build completed successfully:
   ```bash
   npm run build
   ```
2. Check `.mcp.json` configuration is correct
3. Restart Claude Code
4. Check Claude Code logs for errors

### TypeScript Errors

**Solution:**
```bash
npm install  # reinstall dependencies
npm run build  # rebuild
```

## Performance Issues

### Slow First Request

**Normal behavior:** First request performs full sync of all data (accounts, transactions, categories, etc.). This can take 5-10 seconds.

Subsequent requests are fast (incremental sync).

### Large Transaction Lists

Use `limit` parameter to reduce result size:
```
get_transactions(start_date="2026-01-01", limit=50)
```

Maximum limit: 500 transactions per request.

## Common Mistakes

### Wrong Date Format

**Incorrect:**
- "2026-1-5" (missing leading zeros)
- "05-01-2026" (wrong order)
- "2026/01/05" (wrong separator)

**Correct:**
- "2026-01-05" (yyyy-MM-dd)
- "2026-01" (yyyy-MM for months)

### Negative Amounts

All amounts must be positive. Transaction type determines direction:
- `expense` - money out
- `income` - money in
- `transfer` - money between accounts

**Incorrect:**
```
create_transaction(type="expense", amount=-500)
```

**Correct:**
```
create_transaction(type="expense", amount=500)
```

### Missing Required Parameters

Each tool has required parameters. Check tool documentation before use.

**Common missing parameters:**
- `get_transactions` - requires `start_date`
- `create_transaction` - requires `type`, `amount`, `account_id`
- `get_budgets` - requires `month`

## Getting Help

If issue persists:
1. Use `check_auth_status` to verify connection
2. Check README.md for setup instructions
3. Restart Claude Code to clear cache
4. Review tool documentation in tools-full.md
