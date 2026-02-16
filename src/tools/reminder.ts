import { z } from 'zod';
import crypto from 'node:crypto';
import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import type { DataCache } from '../utils/cache.js';
import type { Reminder } from '../api/types.js';
import { validateDate, validateUUID, validatePositiveNumber, todayString } from '../utils/validation.js';

export function registerReminderWriteTools(server: McpServer, cache: DataCache): void {

  // CREATE REMINDER
  server.tool(
    'create_reminder',
    'Create a new recurring reminder (планируемая транзакция) in ZenMoney. Supports expense, income, and transfer types with recurring schedules.',
    {
      type: z.enum(['expense', 'income', 'transfer']).describe('Reminder type'),
      amount: z.number().positive().describe('Transaction amount (positive number)'),
      account_id: z.string().describe('Account UUID. For expense: source account. For income: destination account.'),
      to_account_id: z.string().optional().describe('Destination account UUID (required for transfers)'),
      category_ids: z.array(z.string()).optional().describe('Category UUIDs'),
      payee: z.string().optional().describe('Payee name'),
      comment: z.string().optional().describe('Comment/note'),

      // Recurrence settings
      interval: z.enum(['day', 'week', 'month', 'year']).describe('Recurrence interval'),
      step: z.number().positive().optional().default(1).describe('Step multiplier (e.g., 2 for every 2 months). Default: 1'),
      points: z.array(z.number()).optional().describe('Specific points in interval (e.g., [1,15] for 1st and 15th day of month)'),
      start_date: z.string().optional().describe('Start date (yyyy-MM-dd). Defaults to today.'),
      end_date: z.string().optional().describe('End date (yyyy-MM-dd). Optional, null for indefinite.'),
      notify: z.boolean().optional().default(true).describe('Enable notifications. Default: true'),
    },
    async ({ type, amount, account_id, to_account_id, category_ids, payee, comment, interval, step, points, start_date, end_date, notify }) => {
      validateUUID(account_id, 'account_id');
      if (to_account_id) validateUUID(to_account_id, 'to_account_id');
      if (category_ids) category_ids.forEach(id => validateUUID(id, 'category_id'));
      validatePositiveNumber(amount, 'amount');
      if (start_date) validateDate(start_date, 'start_date');
      if (end_date) validateDate(end_date, 'end_date');

      if (type === 'transfer' && !to_account_id) {
        throw new Error('to_account_id is required for transfer type');
      }

      const effectiveStartDate = start_date ?? todayString();

      await cache.ensureInitialized();

      // Get account details for currency
      const account = cache.accounts.get(account_id);
      if (!account) {
        throw new Error(`Account not found: ${account_id}`);
      }

      const toAccount = to_account_id ? cache.accounts.get(to_account_id) : null;
      if (to_account_id && !toAccount) {
        throw new Error(`Destination account not found: ${to_account_id}`);
      }

      // Validate category IDs
      if (category_ids) {
        for (const catId of category_ids) {
          if (!cache.tags.has(catId)) {
            throw new Error(`Category not found: ${catId}`);
          }
        }
      }

      // Create reminder object
      const reminderId = crypto.randomUUID();
      const userId = account.user;

      const reminder: Reminder = {
        id: reminderId,
        user: userId,
        changed: Math.floor(Date.now() / 1000),

        // Transaction spec
        incomeInstrument: type === 'income' ? account.instrument : (toAccount?.instrument ?? account.instrument),
        incomeAccount: type === 'income' ? account_id : (to_account_id ?? account_id),
        income: type === 'expense' ? 0 : amount,
        outcomeInstrument: type === 'income' ? account.instrument : account.instrument,
        outcomeAccount: type === 'income' ? account_id : account_id,
        outcome: type === 'income' ? 0 : amount,

        // Metadata
        tag: category_ids ?? null,
        merchant: null,
        payee: payee ?? null,
        comment: comment ?? null,

        // Recurrence
        interval,
        step: step ?? 1,
        points: points ?? null,
        startDate: effectiveStartDate,
        endDate: end_date ?? null,
        notify: notify ?? true,
      };

      // Write to ZenMoney
      await cache.writeDiff({
        reminder: [reminder],
      });

      return {
        content: [{
          type: 'text',
          text: JSON.stringify({
            success: true,
            reminder: {
              id: reminder.id,
              type,
              amount,
              account: account.title,
              to_account: toAccount?.title,
              recurrence: `Every ${step > 1 ? step + ' ' : ''}${interval}${step > 1 ? 's' : ''}`,
              start_date: effectiveStartDate,
              end_date: end_date ?? 'indefinite',
              points: points ?? 'all',
            }
          }, null, 2)
        }]
      };
    }
  );

  // UPDATE REMINDER
  server.tool(
    'update_reminder',
    'Update an existing reminder. Only provide fields you want to change.',
    {
      id: z.string().describe('Reminder UUID to update'),
      amount: z.number().positive().optional().describe('New amount'),
      category_ids: z.array(z.string()).optional().describe('New category UUIDs'),
      payee: z.string().optional().describe('New payee'),
      comment: z.string().optional().describe('New comment'),
      interval: z.enum(['day', 'week', 'month', 'year']).optional().describe('New interval'),
      step: z.number().positive().optional().describe('New step'),
      points: z.array(z.number()).optional().describe('New points'),
      end_date: z.string().optional().describe('New end date (yyyy-MM-dd)'),
      notify: z.boolean().optional().describe('New notify setting'),
    },
    async ({ id, amount, category_ids, payee, comment, interval, step, points, end_date, notify }) => {
      validateUUID(id, 'id');
      if (amount) validatePositiveNumber(amount, 'amount');
      if (category_ids) category_ids.forEach(cid => validateUUID(cid, 'category_id'));
      if (end_date) validateDate(end_date, 'end_date');

      await cache.ensureInitialized();

      const existing = cache.reminders.get(id);
      if (!existing) {
        throw new Error(`Reminder not found: ${id}`);
      }

      // Validate new category IDs
      if (category_ids) {
        for (const catId of category_ids) {
          if (!cache.tags.has(catId)) {
            throw new Error(`Category not found: ${catId}`);
          }
        }
      }

      // Create updated reminder
      const updated: Reminder = {
        ...existing,
        changed: Math.floor(Date.now() / 1000),
      };

      // Update amount if provided
      if (amount !== undefined) {
        const isIncome = existing.income > 0 && existing.outcome === 0;
        const isExpense = existing.outcome > 0 && existing.income === 0;

        if (isIncome) {
          updated.income = amount;
        } else if (isExpense) {
          updated.outcome = amount;
        } else {
          // Transfer - update both
          updated.income = amount;
          updated.outcome = amount;
        }
      }

      // Update optional fields
      if (category_ids !== undefined) updated.tag = category_ids;
      if (payee !== undefined) updated.payee = payee;
      if (comment !== undefined) updated.comment = comment;
      if (interval !== undefined) updated.interval = interval;
      if (step !== undefined) updated.step = step;
      if (points !== undefined) updated.points = points;
      if (end_date !== undefined) updated.endDate = end_date;
      if (notify !== undefined) updated.notify = notify;

      // Write to ZenMoney
      await cache.writeDiff({
        reminder: [updated],
      });

      return {
        content: [{
          type: 'text',
          text: JSON.stringify({
            success: true,
            message: 'Reminder updated',
            id: updated.id,
          }, null, 2)
        }]
      };
    }
  );

  // DELETE REMINDER
  server.tool(
    'delete_reminder',
    'Delete a reminder (soft-delete).',
    {
      id: z.string().describe('Reminder UUID to delete'),
    },
    async ({ id }) => {
      validateUUID(id, 'id');

      await cache.ensureInitialized();

      const existing = cache.reminders.get(id);
      if (!existing) {
        throw new Error(`Reminder not found: ${id}`);
      }

      // Soft delete: set endDate to past
      const deleted: Reminder = {
        ...existing,
        changed: Math.floor(Date.now() / 1000),
        endDate: '2000-01-01', // Past date to stop reminder
      };

      await cache.writeDiff({
        reminder: [deleted],
      });

      return {
        content: [{
          type: 'text',
          text: JSON.stringify({
            success: true,
            message: 'Reminder deleted',
            id,
          }, null, 2)
        }]
      };
    }
  );
}
