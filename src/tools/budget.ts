import { z } from 'zod';
import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import type { DataCache } from '../utils/cache.js';
import type { Budget } from '../api/types.js';
import { validateMonth, validatePositiveNumber } from '../utils/validation.js';

export function registerBudgetWriteTools(server: McpServer, cache: DataCache): void {

  function findCategoryId(cache: DataCache, category: string): string {
    if (category.toUpperCase() === 'ALL') {
      return '00000000-0000-0000-0000-000000000000';
    }
    if (cache.tags.has(category)) {
      return category;
    }
    const foundTag = Array.from(cache.tags.values()).find(
      t => t.title.toLowerCase() === category.toLowerCase()
    );
    if (!foundTag) {
      throw new Error(`Category not found: ${category}`);
    }
    return foundTag.id;
  }

  // CREATE/UPDATE BUDGET
  server.tool(
    'create_budget',
    'Create or update budget limit for a category in a specific month.',
    {
      month: z.string().describe('Month in yyyy-MM format (e.g., 2026-03)'),
      category: z.string().describe('Category name or UUID. Use "ALL" for aggregate budget across all categories.'),
      income: z.number().optional().describe('Income budget limit (optional, default: 0)'),
      outcome: z.number().optional().describe('Outcome budget limit (optional, default: 0)'),
      income_lock: z.boolean().optional().default(false).describe('Lock income budget (prevent auto-changes)'),
      outcome_lock: z.boolean().optional().default(false).describe('Lock outcome budget (prevent auto-changes)'),
    },
    async ({ month, category, income, outcome, income_lock, outcome_lock }) => {
      validateMonth(month, 'month');
      if (income !== undefined) validatePositiveNumber(income, 'income');
      if (outcome !== undefined) validatePositiveNumber(outcome, 'outcome');

      await cache.ensureInitialized();

      // Get user ID from any account
      const firstAccount = Array.from(cache.accounts.values())[0];
      if (!firstAccount) {
        throw new Error('No accounts found. Cannot determine user ID.');
      }
      const userId = firstAccount.user;

      const categoryId = findCategoryId(cache, category);

      const monthDate = `${month}-01`;

      // Create or update budget
      const budget: Budget = {
        user: userId,
        changed: Math.floor(Date.now() / 1000),
        tag: categoryId === '00000000-0000-0000-0000-000000000000' ? null : categoryId,
        date: monthDate,
        income: income ?? 0,
        incomeLock: income_lock ?? false,
        outcome: outcome ?? 0,
        outcomeLock: outcome_lock ?? false,
      };

      await cache.writeDiff({ budget: [budget] });

      // Get category name for response
      const categoryName = categoryId === '00000000-0000-0000-0000-000000000000'
        ? 'ALL (aggregate)'
        : cache.tags.get(categoryId)?.title ?? category;

      return {
        content: [{
          type: 'text',
          text: JSON.stringify({
            success: true,
            budget: {
              month,
              category: categoryName,
              category_id: categoryId,
              income: budget.income,
              outcome: budget.outcome,
              income_lock: budget.incomeLock,
              outcome_lock: budget.outcomeLock,
            }
          }, null, 2)
        }]
      };
    }
  );

  // UPDATE BUDGET
  server.tool(
    'update_budget',
    'Update existing budget for a category. Only provide fields you want to change.',
    {
      month: z.string().describe('Month in yyyy-MM format (e.g., 2026-03)'),
      category: z.string().describe('Category name or UUID. Use "ALL" for aggregate budget.'),
      income: z.number().optional().describe('New income budget limit'),
      outcome: z.number().optional().describe('New outcome budget limit'),
      income_lock: z.boolean().optional().describe('New income lock state'),
      outcome_lock: z.boolean().optional().describe('New outcome lock state'),
    },
    async ({ month, category, income, outcome, income_lock, outcome_lock }) => {
      validateMonth(month, 'month');
      if (income !== undefined) validatePositiveNumber(income, 'income');
      if (outcome !== undefined) validatePositiveNumber(outcome, 'outcome');

      await cache.ensureInitialized();

      const categoryId = findCategoryId(cache, category);

      const monthDate = `${month}-01`;
      const budgetKey = `${categoryId === '00000000-0000-0000-0000-000000000000' ? 'null' : categoryId}:${monthDate}`;

      // Find existing budget
      const existing = cache.budgets.get(budgetKey);
      if (!existing) {
        throw new Error(`Budget not found for category "${category}" in ${month}. Use create_budget to create a new one.`);
      }

      // Update only provided fields
      const updated: Budget = {
        ...existing,
        changed: Math.floor(Date.now() / 1000),
        income: income ?? existing.income,
        incomeLock: income_lock ?? existing.incomeLock,
        outcome: outcome ?? existing.outcome,
        outcomeLock: outcome_lock ?? existing.outcomeLock,
      };

      await cache.writeDiff({ budget: [updated] });

      const categoryName = categoryId === '00000000-0000-0000-0000-000000000000'
        ? 'ALL (aggregate)'
        : cache.tags.get(categoryId)?.title ?? category;

      return {
        content: [{
          type: 'text',
          text: JSON.stringify({
            success: true,
            message: 'Budget updated',
            budget: {
              month,
              category: categoryName,
              income: updated.income,
              outcome: updated.outcome,
              income_lock: updated.incomeLock,
              outcome_lock: updated.outcomeLock,
            }
          }, null, 2)
        }]
      };
    }
  );

  // DELETE BUDGET
  server.tool(
    'delete_budget',
    'Delete budget for a category by setting both income and outcome to 0.',
    {
      month: z.string().describe('Month in yyyy-MM format (e.g., 2026-03)'),
      category: z.string().describe('Category name or UUID. Use "ALL" for aggregate budget.'),
    },
    async ({ month, category }) => {
      validateMonth(month, 'month');

      await cache.ensureInitialized();

      const categoryId = findCategoryId(cache, category);

      const monthDate = `${month}-01`;
      const budgetKey = `${categoryId === '00000000-0000-0000-0000-000000000000' ? 'null' : categoryId}:${monthDate}`;

      const existing = cache.budgets.get(budgetKey);
      if (!existing) {
        throw new Error(`Budget not found for category "${category}" in ${month}.`);
      }

      // Delete by setting to 0
      const deleted: Budget = {
        ...existing,
        changed: Math.floor(Date.now() / 1000),
        income: 0,
        outcome: 0,
      };

      await cache.writeDiff({ budget: [deleted] });

      const categoryName = categoryId === '00000000-0000-0000-0000-000000000000'
        ? 'ALL (aggregate)'
        : cache.tags.get(categoryId)?.title ?? category;

      return {
        content: [{
          type: 'text',
          text: JSON.stringify({
            success: true,
            message: 'Budget deleted',
            category: categoryName,
            month,
          }, null, 2)
        }]
      };
    }
  );
}
