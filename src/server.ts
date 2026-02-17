import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { ZenMoneyClient, ClientConfig } from './api/client.js';
import { DataCache } from './utils/cache.js';
import { registerAccountTools } from './tools/accounts.js';
import { registerTransactionTools } from './tools/transactions.js';
import { registerCategoryTools } from './tools/categories.js';
import { registerMerchantTools } from './tools/merchants.js';
import { registerBudgetTools } from './tools/budgets.js';
import { registerReminderTools } from './tools/reminders.js';
import { registerInstrumentTools } from './tools/instruments.js';
import { registerAnalyticsTools } from './tools/analytics.js';
import { registerSuggestTools } from './tools/suggest.js';
import { registerAuthTools } from './tools/auth.js';
import { registerReminderWriteTools } from './tools/reminder.js';
import { registerBudgetWriteTools } from './tools/budget.js';

export function createServer(config: ClientConfig) {
  const client = new ZenMoneyClient(config);
  const cache = new DataCache(client);

  const server = new McpServer({
    name: 'zenmoney',
    version: '1.0.0',
  });

  // Register all tool groups
  registerAccountTools(server, cache);
  registerTransactionTools(server, cache);
  registerCategoryTools(server, cache);
  registerMerchantTools(server, cache);
  registerBudgetTools(server, cache);
  registerReminderTools(server, cache);
  registerInstrumentTools(server, cache);
  registerAnalyticsTools(server, cache);
  registerSuggestTools(server, client);
  registerAuthTools(server, client, cache);
  registerReminderWriteTools(server, cache);
  registerBudgetWriteTools(server, cache);

  return {
    async start() {
      const transport = new StdioServerTransport();
      await server.connect(transport);
      console.error('ZenMoney MCP Server started');
    },
  };
}
