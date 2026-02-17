import { z } from 'zod';
import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import type { DataCache } from '../utils/cache.js';
import { validateDate, todayString } from '../utils/validation.js';

export function registerAnalyticsTools(server: McpServer, cache: DataCache): void {
  server.tool(
    'get_analytics',
    'Get spending/income analytics grouped by category, account, or merchant. Aggregates transaction data for a date range.',
    {
      start_date: z.string().describe('Start date (yyyy-MM-dd)'),
      end_date: z.string().optional().describe('End date (yyyy-MM-dd). Defaults to today.'),
      group_by: z.enum(['category', 'account', 'merchant']).optional().default('category').describe('Group results by'),
      type: z.enum(['expense', 'income', 'all']).optional().default('expense').describe('Transaction type to analyze'),
    },
    async ({ start_date, end_date, group_by, type }) => {
      validateDate(start_date, 'start_date');
      if (end_date) validateDate(end_date, 'end_date');

      const effectiveEndDate = end_date ?? todayString();

      await cache.ensureInitialized();

      // Filter transactions by date range
      const transactions = Array.from(cache.transactions.values())
        .filter(t => !t.deleted)
        .filter(t => t.date >= start_date && t.date <= effectiveEndDate);

      // Filter by type
      const filteredTransactions = transactions.filter(t => {
        const isTransfer = t.outcomeAccount !== t.incomeAccount && t.outcome > 0 && t.income > 0;
        const isExpense = t.outcome > 0 && t.income === 0 && !isTransfer;
        const isIncome = t.income > 0 && t.outcome === 0 && !isTransfer;

        if (type === 'expense') return isExpense;
        if (type === 'income') return isIncome;
        return isExpense || isIncome; // type === 'all'
      });

      // Group and aggregate
      const groups = new Map<string, { income: number; outcome: number; count: number; currency: string }>();

      for (const tx of filteredTransactions) {
        let key = 'Uncategorized';
        let currency = '';

        if (group_by === 'category') {
          if (tx.tag && tx.tag.length > 0) {
            const firstTag = cache.tags.get(tx.tag[0]);
            key = firstTag?.title || 'Uncategorized';
          }
          // Resolve currency from outcome or income account
          const accountId = tx.outcome > 0 ? tx.outcomeAccount : tx.incomeAccount;
          const account = cache.accounts.get(accountId);
          const instrument = account ? cache.instruments.get(account.instrument) : null;
          currency = instrument?.shortTitle || 'RUB';
        } else if (group_by === 'account') {
          const accountId = type === 'income' ? tx.incomeAccount : tx.outcomeAccount;
          const account = cache.accounts.get(accountId);
          key = account?.title || 'Unknown Account';
          const instrument = account ? cache.instruments.get(account.instrument) : null;
          currency = instrument?.shortTitle || 'RUB';
        } else if (group_by === 'merchant') {
          if (tx.merchant) {
            const merchant = cache.merchants.get(tx.merchant);
            key = merchant?.title || tx.payee || 'Unknown Merchant';
          } else if (tx.payee) {
            key = tx.payee;
          }
          const accountId = tx.outcome > 0 ? tx.outcomeAccount : tx.incomeAccount;
          const account = cache.accounts.get(accountId);
          const instrument = account ? cache.instruments.get(account.instrument) : null;
          currency = instrument?.shortTitle || 'RUB';
        }

        if (!groups.has(key)) {
          groups.set(key, { income: 0, outcome: 0, count: 0, currency });
        }

        const group = groups.get(key)!;
        group.income += tx.income;
        group.outcome += tx.outcome;
        group.count += 1;
      }

      // Calculate grand total based on type
      let grandTotal = 0;
      for (const group of groups.values()) {
        if (type === 'expense') grandTotal += group.outcome;
        else if (type === 'income') grandTotal += group.income;
        else grandTotal += group.income + group.outcome;
      }

      // Convert to sorted array
      const result = {
        period: { from: start_date, to: effectiveEndDate },
        type,
        groupBy: group_by,
        grandTotal,
        transactionCount: filteredTransactions.length,
        groups: Array.from(groups.entries())
          .map(([name, data]) => ({
            name,
            total: type === 'expense' ? data.outcome : type === 'income' ? data.income : data.income + data.outcome,
            count: data.count,
            currency: data.currency,
            ...(type === 'all' ? { income: data.income, outcome: data.outcome } : {}),
          }))
          .sort((a, b) => b.total - a.total),
      };

      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );
}
