import { z } from 'zod';
import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import type { DataCache } from '../utils/cache.js';
import { formatAccount } from '../utils/format.js';
import { validateUUID } from '../utils/validation.js';
import crypto from 'node:crypto';

export function registerAccountTools(server: McpServer, cache: DataCache): void {
  server.tool(
    'get_accounts',
    'Get all ZenMoney accounts with balances. Returns list of accounts with id, title, type, balance, currency.',
    {
      include_archived: z.boolean().optional().default(false).describe('Include archived accounts'),
    },
    async ({ include_archived }) => {
      await cache.ensureInitialized();

      let accounts = Array.from(cache.accounts.values());
      if (!include_archived) {
        accounts = accounts.filter(a => !a.archive);
      }

      const formatted = accounts.map(a => formatAccount(a, cache.instruments));
      return { content: [{ type: 'text', text: JSON.stringify(formatted, null, 2) }] };
    }
  );

  server.tool(
    'create_account',
    'Create a new account in ZenMoney. Returns the created account.',
    {
      title: z.string().describe('Account name'),
      type: z.enum(['cash', 'ccard', 'checking']).describe('Account type'),
      currency_id: z.number().describe('Currency instrument ID (get from get_instruments)'),
      balance: z.number().optional().default(0).describe('Initial balance'),
      credit_limit: z.number().optional().default(0).describe('Credit limit (for ccard type)'),
    },
    async ({ title, type, currency_id, balance, credit_limit }) => {
      await cache.ensureInitialized();

      const user = Array.from(cache.users.values())[0];
      if (!user) throw new Error('No user found');

      if (!cache.instruments.has(currency_id)) {
        throw new Error(`Unknown currency_id: ${currency_id}. Use get_instruments to see available currencies.`);
      }

      const now = Math.floor(Date.now() / 1000);
      const newAccount = {
        id: crypto.randomUUID(),
        user: user.id,
        instrument: currency_id,
        type,
        role: null,
        company: null,
        title,
        syncID: null,
        balance,
        startBalance: balance,
        creditLimit: credit_limit,
        inBalance: true,
        savings: false,
        enableCorrection: false,
        enableSMS: false,
        archive: false,
        private: false,
        capitalization: null,
        percent: null,
        startDate: null,
        endDateOffset: null,
        endDateOffsetInterval: null,
        payoffStep: null,
        payoffInterval: null,
        changed: now,
      };

      await cache.writeDiff({ account: [newAccount] });
      const created = cache.getAccount(newAccount.id);
      return {
        content: [{
          type: 'text',
          text: JSON.stringify(created ? formatAccount(created, cache.instruments) : newAccount, null, 2),
        }],
      };
    }
  );
}
