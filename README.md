# ZenMoney MCP Server

An MCP (Model Context Protocol) server that provides Claude Code with full access to ZenMoney personal finance data and operations. Manage accounts, transactions, budgets, and get spending analytics through natural conversation.

## Features

**17 Tools** for complete personal finance management:

**Read Tools:**
- `get_accounts` - View all accounts with balances and currency information
- `get_transactions` - Query transactions by date range, account, or category
- `get_categories` - Browse hierarchical category structure (tags)
- `get_merchants` - Search merchant database
- `get_budgets` - View monthly budget limits
- `get_reminders` - Check scheduled payment reminders
- `get_instruments` - Get available currencies and exchange rates

**Write Tools:**
- `create_transaction` - Create expenses, income entries, or transfers
- `update_transaction` - Modify existing transactions
- `delete_transaction` - Remove transactions
- `create_account` - Add new accounts (cash, cards, checking)
- `create_reminder` - Create recurring scheduled transactions (expenses, income, transfers)
- `update_reminder` - Modify existing reminders
- `delete_reminder` - Remove planned transactions

**Analytics:**
- `get_analytics` - Analyze spending by category, account, or merchant for any date range
- `suggest` - Get ML-powered category and merchant suggestions for transaction names

**System:**
- `check_auth_status` - Verify current authentication status and token validity

## Key Capabilities

- **Automatic Token Refresh** - Handles OAuth2 token expiration transparently with re-authentication
- **Diff-Based Sync** - Uses ZenMoney's efficient incremental sync protocol (full sync on first call, then only deltas)
- **In-Memory Caching** - Fast data access after initial sync without repeated API calls
- **Zero External HTTP Dependencies** - Uses Node.js native `fetch` API only
- **TypeScript First** - Fully typed with native MCP SDK support

## Installation

### Prerequisites

- Node.js >= 18.0.0
- npm or yarn

### Setup

```bash
cd zenmoney-skill
npm install
npm run build
```

The compiled JavaScript will be in `dist/index.js`.

## Quick Start with Skill

Для удобной работы с ZenMoney в Claude Code рекомендуется установить skill:

### Автоматическая установка

**Windows:**
```bash
npm run install-skill
```

**macOS/Linux:**
```bash
npm run install-skill:unix
```

### Ручная установка

**Windows:**
```powershell
# Создать директорию для skills
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.claude\skills"

# Создать символическую ссылку (требует права администратора)
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.claude\skills\zenmoney.skill.md" -Target "$PWD\zenmoney.skill.md"
```

**macOS/Linux:**
```bash
# Создать директорию для skills
mkdir -p ~/.claude/skills

# Создать символическую ссылку
ln -s "$(pwd)/zenmoney.skill.md" ~/.claude/skills/zenmoney.skill.md
```

### Использование skill

После установки и перезапуска Claude Code:

```
Сколько я потратил в этом месяце?
Добавь расход 500 рублей на кофе
Покажи все мои счета
Смогу ли я отвести девушку на свидание?
```

Claude автоматически распознает финансовые запросы и использует ZenMoney MCP сервер.

Подробная документация: [zenmoney.skill.md](./zenmoney.skill.md)

## Configuration

Configure the MCP server in Claude Desktop or Claude Code settings by adding the server to your MCP config file.

### Option 1: Using Access Token (Recommended)

If you already have a ZenMoney access token (from previous OAuth flow or zerro.app):

```json
{
  "mcpServers": {
    "zenmoney": {
      "command": "node",
      "args": ["/absolute/path/to/zenmoney-skill/dist/index.js"],
      "env": {
        "ZENMONEY_TOKEN": "your-access-token-here"
      }
    }
  }
}
```

### Option 2: Using Credentials (Full OAuth Flow)

For password-based authentication, provide all four credentials:

```json
{
  "mcpServers": {
    "zenmoney": {
      "command": "node",
      "args": ["/absolute/path/to/zenmoney-skill/dist/index.js"],
      "env": {
        "ZENMONEY_USERNAME": "your-email@example.com",
        "ZENMONEY_PASSWORD": "your-password",
        "ZENMONEY_API_KEY": "your-consumer-key",
        "ZENMONEY_API_SECRET": "your-consumer-secret"
      }
    }
  }
}
```

### Configuration Locations

**Claude Desktop:**
- macOS: `~/.claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

**Claude Code:**
- Check your IDE settings for MCP server configuration

## Getting API Credentials

### Get Consumer Key & Secret

1. Register as a developer at http://developers.zenmoney.ru/index.html
2. Create an OAuth application
3. Save your `consumer_key` (API_KEY) and `consumer_secret` (API_SECRET)

### Get Access Token

**Option A: Browser-Based OAuth (Recommended, Easiest)**

The simplest way to authorize with ZenMoney:

```bash
# 1. Set up OAuth app credentials (required once)
export ZENMONEY_CLIENT_ID=your_client_id
export ZENMONEY_CLIENT_SECRET=your_client_secret

# 2. Run browser authorization
npm run auth
```

This will:
- Open browser for ZenMoney authorization
- Handle OAuth2 flow automatically
- Save token to `.mcp.json` in project root
- Display success message when complete

After authorization, restart Claude Code to use the new token.

**Option B: From BUDGERA (Easiest for existing users)**
- Visit https://budgera.com/settings/export
- Copy your ZenMoney API token from the settings
- Use it directly with `ZENMONEY_TOKEN` in `.mcp.json`
- No OAuth setup required!

**Option C: From zerro.app**
- Visit https://zerro.app and authorize with ZenMoney
- Extract the token from browser storage or network requests
- Use it directly with `ZENMONEY_TOKEN`

**Option D: First-time OAuth (automatic)**
- Provide all four credentials (username, password, API_KEY, API_SECRET)
- Server will automatically perform OAuth2 flow on first connection
- Token is generated and reused for subsequent requests

## Tools Reference

### get_accounts

Retrieve all accounts with current balances.

**Parameters:**
- `include_archived` (boolean, optional) - Include archived accounts. Default: false

**Returns:**
- List of accounts with id, title, type, balance, and currency information

### get_transactions

Query transactions by date range, account, or category.

**Parameters:**
- `start_date` (string, required) - Start date in yyyy-MM-dd format
- `end_date` (string, optional) - End date in yyyy-MM-dd format. Default: today
- `account_id` (string, optional) - Filter by account UUID
- `category_id` (string, optional) - Filter by category UUID
- `limit` (number, optional) - Max results. Default: 100, max: 500

**Returns:**
- Array of transactions with details (amount, payee, category, dates)
- Truncation info if results exceed limit

### create_transaction

Create a new transaction.

**Parameters:**
- `type` (string, required) - 'expense', 'income', or 'transfer'
- `amount` (number, required) - Positive number amount
- `account_id` (string, required) - Source/destination account UUID
- `to_account_id` (string, optional) - Required for transfers
- `category_ids` (array, optional) - Category UUIDs to apply
- `date` (string, optional) - Date in yyyy-MM-dd format. Default: today
- `payee` (string, optional) - Merchant/payee name
- `comment` (string, optional) - Additional notes

**Returns:**
- Created transaction object with assigned ID and timestamp

### update_transaction

Modify an existing transaction.

**Parameters:**
- `id` (string, required) - Transaction UUID
- `amount` (number, optional) - New amount
- `category_ids` (array, optional) - Replace categories
- `payee` (string, optional) - New payee name
- `comment` (string, optional) - Updated comment

**Returns:**
- Updated transaction object

### delete_transaction

Remove a transaction.

**Parameters:**
- `id` (string, required) - Transaction UUID

**Returns:**
- Confirmation of deletion

### create_reminder

Create a new recurring reminder (планируемая транзакция) for scheduled payments.

**Parameters:**
- `type` (string, required) - 'expense', 'income', or 'transfer'
- `amount` (number, required) - Transaction amount (positive number)
- `account_id` (string, required) - Source/destination account UUID
- `to_account_id` (string, optional) - Required for transfers
- `category_ids` (array, optional) - Category UUIDs to apply
- `payee` (string, optional) - Merchant/payee name
- `comment` (string, optional) - Additional notes
- `interval` (string, required) - Recurrence interval: 'day', 'week', 'month', or 'year'
- `step` (number, optional) - Step multiplier (e.g., 2 for every 2 months). Default: 1
- `points` (array, optional) - Specific points in interval (e.g., [1,15] for 1st and 15th day of month)
- `start_date` (string, optional) - Start date in yyyy-MM-dd format. Default: today
- `end_date` (string, optional) - End date in yyyy-MM-dd format. Optional, null for indefinite
- `notify` (boolean, optional) - Enable notifications. Default: true

**Returns:**
- Created reminder object with recurrence details

**Examples:**
- Monthly rent: `interval='month', step=1, points=[1]` (every 1st day)
- Bi-weekly salary: `interval='week', step=2` (every 2 weeks)
- Quarterly payment: `interval='month', step=3` (every 3 months)

### update_reminder

Update an existing reminder. Only provide fields you want to change.

**Parameters:**
- `id` (string, required) - Reminder UUID
- `amount` (number, optional) - New amount
- `category_ids` (array, optional) - Replace categories
- `payee` (string, optional) - New payee name
- `comment` (string, optional) - Updated comment
- `interval` (string, optional) - New interval: 'day', 'week', 'month', or 'year'
- `step` (number, optional) - New step multiplier
- `points` (array, optional) - New points array
- `end_date` (string, optional) - New end date (yyyy-MM-dd)
- `notify` (boolean, optional) - New notify setting

**Returns:**
- Updated reminder confirmation

### delete_reminder

Delete a reminder (soft-delete by setting past end date).

**Parameters:**
- `id` (string, required) - Reminder UUID

**Returns:**
- Confirmation of deletion

### get_categories

Browse all categories (tags) in hierarchical structure.

**Parameters:** None

**Returns:**
- Hierarchical list with parent-child relationships
- Each category includes id, title, icon, and income/outcome visibility flags

### get_instruments

Get available currencies and exchange rates.

**Parameters:** None

**Returns:**
- List of currencies with id, code (e.g., USD), title, symbol, and exchange rate to base currency

### get_merchants

Search the merchant database.

**Parameters:**
- `search` (string, optional) - Search query (case-insensitive)

**Returns:**
- List of merchants with id and title

### create_account

Create a new account.

**Parameters:**
- `title` (string, required) - Account name
- `type` (string, required) - 'cash', 'ccard', or 'checking'
- `currency_id` (number, required) - Currency instrument ID from get_instruments
- `balance` (number, optional) - Initial balance. Default: 0
- `credit_limit` (number, optional) - Credit limit for ccard type. Default: 0

**Returns:**
- Created account object with UUID

### get_budgets

View budgets for a specific month.

**Parameters:**
- `month` (string, required) - Month in yyyy-MM format (e.g., 2025-01)

**Returns:**
- List of budgets with planned income/outcome per category

### get_reminders

Check scheduled payment reminders.

**Parameters:**
- `include_processed` (boolean, optional) - Include completed reminders. Default: false

**Returns:**
- List of reminders with recurrence intervals and marker states

### get_analytics

Analyze spending and income with aggregations.

**Parameters:**
- `start_date` (string, required) - Start date in yyyy-MM-dd format
- `end_date` (string, optional) - End date. Default: today
- `group_by` (string, optional) - 'category', 'account', or 'merchant'. Default: 'category'
- `type` (string, optional) - 'expense', 'income', or 'all'. Default: 'expense'

**Returns:**
- Aggregated spending with counts and totals per group
- Transfers are automatically excluded from analytics

### suggest

Get ML-powered suggestions for categories and merchants.

**Parameters:**
- `payee` (string, required) - Merchant or payee name to get suggestions for

**Returns:**
- Suggested category IDs and merchant matches based on ZenMoney ML model

### check_auth_status

Verify current authentication and token validity.

**Parameters:** None

**Returns:**
- Status: 'authenticated' if token is valid
- Error details with solution steps if authentication fails
- Note: Automatically tries a minimal API call to verify connectivity

## Architecture

### Project Structure

```
src/
├── index.ts              # Entry point, CLI argument parsing
├── server.ts             # MCP server setup and tool registration
├── api/
│   ├── client.ts         # HTTP client with OAuth2 and auto-refresh
│   ├── auth.ts           # OAuth2 authentication flow
│   └── types.ts          # TypeScript types for all ZenMoney entities
├── tools/
│   ├── accounts.ts       # Account read/write tools
│   ├── transactions.ts   # Transaction CRUD tools
│   ├── categories.ts     # Category listing tool
│   ├── merchants.ts      # Merchant search tool
│   ├── instruments.ts    # Currency tool
│   ├── budgets.ts        # Budget tool
│   ├── reminders.ts      # Reminder read tool
│   ├── reminder.ts       # Reminder write tools (create/update/delete)
│   ├── analytics.ts      # Analytics and aggregation
│   ├── suggest.ts        # ML suggestion tool
│   └── auth.ts           # Authentication status check
└── utils/
    ├── cache.ts          # DataCache with diff-based sync
    ├── format.ts         # Output formatting utilities
    └── validation.ts     # Input validation helpers
scripts/
└── auth-browser.js       # Browser-based OAuth2 authorization flow
```

### Data Flow

1. **Initialization**: On first tool call, `DataCache.ensureInitialized()` is called
2. **Full Sync**: Calls `/v8/diff/` with `serverTimestamp=0` to download all data
3. **Incremental Sync**: Subsequent calls use last `serverTimestamp` to fetch only changes
4. **Caching**: All data stored in memory as Maps (accounts, transactions, tags, etc.)
5. **Fast Lookups**: Tools query cached data with O(1) lookups by ID
6. **Write Operations**: Tools call `cache.writeDiff()` which syncs changes back to ZenMoney
7. **Token Refresh**: On HTTP 401, automatically re-authenticates and retries

### ZenMoney Sync Protocol

ZenMoney uses a diff-based sync protocol optimized for mobile apps:

- **First call**: `serverTimestamp=0` returns all data
- **Subsequent calls**: Use the last returned `serverTimestamp` to fetch only new/modified entities
- **Deletions**: Returned in separate `deletion` array
- **Merged responses**: Can read and write in same request by including entity arrays

## Development

### Build

```bash
npm run build
```

Compiles TypeScript to JavaScript in `dist/`.

### Watch Mode

```bash
npm run dev
```

Automatically recompile on file changes.

### Running Locally

```bash
npm run build
ZENMONEY_TOKEN=your-token node dist/index.js
```

The server will connect to stdio and await MCP protocol messages.

## Error Handling

The server includes comprehensive error handling:

- **Invalid input**: Validates dates, UUIDs, amounts before API calls
- **API errors**: Returns detailed error messages from ZenMoney
- **Token expiration**: Automatically re-authenticates on 401 responses
- **Missing config**: Clear error messages if required env variables are missing
- **Network errors**: Propagates with context for debugging

## Security Considerations

- **Credentials in env variables**: Never commit credentials; use environment variables or secure stores
- **Token storage**: Tokens are only held in memory during server lifetime
- **HTTPS only**: All communication with ZenMoney API is encrypted
- **No logging of credentials**: Sensitive data is never logged

## Limitations

- **In-memory cache**: Data is lost when server restarts. No persistent storage.
- **Single user**: Each server instance serves one ZenMoney account
- **Sync timing**: Changes made outside MCP may take time to appear (cache depends on diffs)
- **Account types**: create_account supports only cash, ccard, and checking (no loan/deposit)

## Troubleshooting

### "Missing configuration" error

If you see this error, you need to authorize with ZenMoney first.

**Quick Fix (Recommended):**
```bash
# Set OAuth credentials
export ZENMONEY_CLIENT_ID=your_client_id
export ZENMONEY_CLIENT_SECRET=your_client_secret

# Run browser authorization
npm run auth
```

After `npm run auth` completes, restart Claude Code.

**Alternative:** Manually set `ZENMONEY_TOKEN` in `.mcp.json` or provide all four credential variables:
- `ZENMONEY_USERNAME`
- `ZENMONEY_PASSWORD`
- `ZENMONEY_API_KEY`
- `ZENMONEY_API_SECRET`

### "Token exchange failed" error
Your API key or secret is incorrect. Verify at http://developers.zenmoney.ru

### Tools return empty results
Cache may not be initialized. The server will automatically sync on first tool call. If still empty, your ZenMoney account may not have data for the requested filters.

### Transactions show incorrect amounts
Some transactions may be multi-currency transfers. Check the original operation currency fields (`opIncome`, `opOutcome`, etc.).

### Token expired
If you get authentication errors, the token may have expired. Check status:
```
Use check_auth_status tool
```

If expired, re-authorize:
```bash
npm run auth
```

## License

MIT

## Resources

- ZenMoney Developer Documentation: http://developers.zenmoney.ru
- zerro.app: https://zerro.app (alternative ZenMoney interface)
- MCP Specification: https://modelcontextprotocol.io

