import { z } from 'zod';
import crypto from 'node:crypto';
import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import type { DataCache } from '../utils/cache.js';
import type { Transaction } from '../api/types.js';
import { formatTransaction } from '../utils/format.js';
import { validateDate, validateUUID, todayString } from '../utils/validation.js';

export function registerTransactionTools(server: McpServer, cache: DataCache): void {

  // GET TRANSACTIONS
  server.tool(
    'get_transactions',
    'Get ZenMoney transactions filtered by date range, account, category, or type. Returns up to 100 transactions by default.',
    {
      start_date: z.string().describe('Start date (yyyy-MM-dd). Required.'),
      end_date: z.string().optional().describe('End date (yyyy-MM-dd). Defaults to today.'),
      account_id: z.string().optional().describe('Filter by account UUID'),
      category_id: z.string().optional().describe('Filter by category UUID'),
      type: z.enum(['expense', 'income', 'transfer']).optional().describe('Filter by transaction type'),
      limit: z.number().optional().default(100).describe('Max results (default: 100, max: 500)'),
    },
    async ({ start_date, end_date, account_id, category_id, type, limit }) => {
      validateDate(start_date, 'start_date');
      if (end_date) validateDate(end_date, 'end_date');
      if (account_id) validateUUID(account_id, 'account_id');
      if (category_id) validateUUID(category_id, 'category_id');

      const effectiveLimit = Math.min(limit, 500);
      const effectiveEndDate = end_date ?? todayString();

      await cache.ensureInitialized();

      let transactions = Array.from(cache.transactions.values())
        .filter(t => !t.deleted)
        .filter(t => t.date >= start_date && t.date <= effectiveEndDate);

      if (account_id) {
        transactions = transactions.filter(t =>
          t.incomeAccount === account_id || t.outcomeAccount === account_id
        );
      }

      if (category_id) {
        transactions = transactions.filter(t =>
          t.tag?.includes(category_id)
        );
      }

      // Filter by type
      if (type) {
        transactions = transactions.filter(t => {
          const isTransfer = t.outcomeAccount !== t.incomeAccount && t.outcome > 0 && t.income > 0;
          const isExpense = t.outcome > 0 && !isTransfer;
          const isIncome = t.income > 0 && !isTransfer;

          if (type === 'transfer') return isTransfer;
          if (type === 'expense') return isExpense;
          if (type === 'income') return isIncome;
          return false;
        });
      }

      // Sort by date desc
      transactions.sort((a, b) => b.date.localeCompare(a.date) || b.created - a.created);

      const limited = transactions.slice(0, effectiveLimit);
      const formatted = limited.map(t =>
        formatTransaction(t, cache.accounts, cache.tags, cache.instruments, cache.merchants)
      );

      const result: Record<string, unknown> = { transactions: formatted };
      if (transactions.length > effectiveLimit) {
        result.truncated = true;
        result.total = transactions.length;
        result.showing = effectiveLimit;
      }

      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  // CREATE TRANSACTION
  server.tool(
    'create_transaction',
    'Create a new transaction in ZenMoney. Supports expense, income, and transfer types.',
    {
      type: z.enum(['expense', 'income', 'transfer']).describe('Transaction type'),
      amount: z.number().positive().describe('Transaction amount (positive number)'),
      account_id: z.string().describe('Account UUID. For expense: source account. For income: destination account.'),
      to_account_id: z.string().optional().describe('Destination account UUID (required for transfers)'),
      category_ids: z.array(z.string()).optional().describe('Category UUIDs'),
      date: z.string().optional().describe('Date (yyyy-MM-dd). Defaults to today.'),
      payee: z.string().optional().describe('Payee name'),
      comment: z.string().optional().describe('Comment/note'),
      currency_id: z.number().optional().describe('Currency instrument ID if different from account currency'),
      income_amount: z.number().positive().optional().describe('Income amount for cross-currency transfers (when source and destination have different currencies)'),
    },
    async ({ type, amount, account_id, to_account_id, category_ids, date, payee, comment, currency_id, income_amount }) => {
      validateUUID(account_id, 'account_id');
      if (to_account_id) validateUUID(to_account_id, 'to_account_id');
      if (category_ids) category_ids.forEach((id, i) => validateUUID(id, `category_ids[${i}]`));
      const txDate = date ?? todayString();
      if (date) validateDate(date, 'date');

      if (type === 'transfer' && !to_account_id) {
        throw new Error('to_account_id is required for transfer transactions');
      }

      await cache.ensureInitialized();

      const account = cache.getAccount(account_id);
      if (!account) throw new Error(`Account not found: ${account_id}`);

      const user = Array.from(cache.users.values())[0];
      if (!user) throw new Error('No user found');

      const now = Math.floor(Date.now() / 1000);
      const instrumentId = currency_id ?? account.instrument;

      // Build transaction based on type
      const tx: Transaction = {
        id: crypto.randomUUID(),
        user: user.id,
        changed: now,
        created: now,
        deleted: false,
        hold: null,
        // Default: same account for both sides (expense/income)
        incomeInstrument: instrumentId,
        incomeAccount: account_id,
        income: 0,
        outcomeInstrument: instrumentId,
        outcomeAccount: account_id,
        outcome: 0,
        tag: category_ids ?? null,
        merchant: null,
        payee: payee ?? null,
        originalPayee: null,
        comment: comment ?? null,
        date: txDate,
        mcc: null,
        reminderMarker: null,
        opIncome: null,
        opIncomeInstrument: null,
        opOutcome: null,
        opOutcomeInstrument: null,
        latitude: null,
        longitude: null,
        qrCode: null,
      };

      switch (type) {
        case 'expense':
          tx.outcome = amount;
          tx.outcomeAccount = account_id;
          tx.outcomeInstrument = instrumentId;
          tx.incomeAccount = account_id;
          tx.incomeInstrument = instrumentId;
          tx.income = 0;
          break;
        case 'income':
          tx.income = amount;
          tx.incomeAccount = account_id;
          tx.incomeInstrument = instrumentId;
          tx.outcomeAccount = account_id;
          tx.outcomeInstrument = instrumentId;
          tx.outcome = 0;
          break;
        case 'transfer': {
          const toAccount = cache.getAccount(to_account_id!);
          if (!toAccount) throw new Error(`Destination account not found: ${to_account_id}`);
          tx.outcome = amount;
          tx.outcomeAccount = account_id;
          tx.outcomeInstrument = account.instrument;
          tx.incomeAccount = to_account_id!;
          tx.incomeInstrument = toAccount.instrument;
          if (account.instrument !== toAccount.instrument) {
            if (!income_amount) throw new Error('income_amount is required for cross-currency transfers');
            tx.income = income_amount;
          } else {
            tx.income = amount;
          }
          break;
        }
      }

      await cache.writeDiff({ transaction: [tx] });
      const created = cache.getTransaction(tx.id) ?? tx;
      const formatted = formatTransaction(created, cache.accounts, cache.tags, cache.instruments, cache.merchants);

      return { content: [{ type: 'text', text: JSON.stringify({ created: formatted }, null, 2) }] };
    }
  );

  // UPDATE TRANSACTION
  server.tool(
    'update_transaction',
    'Update an existing transaction. Only pass fields you want to change.',
    {
      id: z.string().describe('Transaction UUID to update'),
      amount: z.number().positive().optional().describe('New amount'),
      category_ids: z.array(z.string()).optional().describe('New category UUIDs'),
      date: z.string().optional().describe('New date (yyyy-MM-dd)'),
      payee: z.string().optional().describe('New payee'),
      comment: z.string().optional().describe('New comment'),
    },
    async ({ id, amount, category_ids, date, payee, comment }) => {
      validateUUID(id, 'id');
      if (date) validateDate(date, 'date');
      if (category_ids) category_ids.forEach((cid, i) => validateUUID(cid, `category_ids[${i}]`));

      await cache.ensureInitialized();

      const existing = cache.getTransaction(id);
      if (!existing) throw new Error(`Transaction not found: ${id}`);

      const updated: Transaction = { ...existing, changed: Math.floor(Date.now() / 1000) };

      if (amount !== undefined) {
        // Determine type and update accordingly
        const isTransfer = existing.outcomeAccount !== existing.incomeAccount && existing.outcome > 0 && existing.income > 0;
        if (isTransfer) {
          updated.outcome = amount;
          updated.income = amount;
        } else if (existing.outcome > 0) {
          updated.outcome = amount;
        } else {
          updated.income = amount;
        }
      }
      if (category_ids !== undefined) updated.tag = category_ids;
      if (date !== undefined) updated.date = date;
      if (payee !== undefined) updated.payee = payee;
      if (comment !== undefined) updated.comment = comment;

      await cache.writeDiff({ transaction: [updated] });
      const result = cache.getTransaction(id) ?? updated;
      const formatted = formatTransaction(result, cache.accounts, cache.tags, cache.instruments, cache.merchants);

      return { content: [{ type: 'text', text: JSON.stringify({ updated: formatted }, null, 2) }] };
    }
  );

  // DELETE TRANSACTION
  server.tool(
    'delete_transaction',
    'Delete a transaction (soft-delete). The transaction will be marked as deleted.',
    {
      id: z.string().describe('Transaction UUID to delete'),
    },
    async ({ id }) => {
      validateUUID(id, 'id');

      await cache.ensureInitialized();

      const existing = cache.getTransaction(id);
      if (!existing) throw new Error(`Transaction not found: ${id}`);

      const deleted: Transaction = {
        ...existing,
        deleted: true,
        changed: Math.floor(Date.now() / 1000),
      };

      await cache.writeDiff({ transaction: [deleted] });

      return {
        content: [{
          type: 'text',
          text: JSON.stringify({ deleted: true, id, date: existing.date, amount: existing.outcome || existing.income }, null, 2),
        }],
      };
    }
  );
}
