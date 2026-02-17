import { z } from 'zod';
import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import type { DataCache } from '../utils/cache.js';
import { formatReminder } from '../utils/format.js';

export function registerReminderTools(server: McpServer, cache: DataCache): void {
  server.tool(
    'get_reminders',
    'Get scheduled payment reminders. Shows recurring transactions and their markers.',
    {
      include_processed: z.boolean().optional().default(false).describe('Include already processed reminders'),
      active_only: z.boolean().optional().default(true).describe('Only show reminders without endDate or endDate in the future'),
      limit: z.number().optional().default(50).describe('Max reminders to return (default: 50)'),
      markers_limit: z.number().optional().default(5).describe('Max markers per reminder (default: 5)'),
    },
    async ({ include_processed, active_only, limit, markers_limit }) => {
      await cache.ensureInitialized();

      const now = new Date();
      const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;

      let reminders = Array.from(cache.reminders.values());

      // Filter active only (no endDate or endDate >= today)
      if (active_only) {
        reminders = reminders.filter(r => !r.endDate || r.endDate >= todayStr);
      }

      // Sort by startDate desc
      reminders.sort((a, b) => b.startDate.localeCompare(a.startDate));

      const effectiveLimit = Math.min(limit, 200);
      const totalCount = reminders.length;
      reminders = reminders.slice(0, effectiveLimit);

      const result = reminders.map(r => {
          const formatted = formatReminder(r, cache.accounts, cache.tags);

          // Attach markers with limit
          const markers = Array.from(cache.reminderMarkers.values())
            .filter(m => m.reminder === r.id)
            .filter(m => include_processed || m.state === 'planned')
            .sort((a, b) => a.date.localeCompare(b.date))
            .slice(0, markers_limit)
            .map(m => ({
              id: m.id,
              date: m.date,
              state: m.state,
              income: m.income,
              outcome: m.outcome,
            }));

          return { ...formatted, markers: markers.length > 0 ? markers : undefined };
        });

      const output: Record<string, unknown> = { reminders: result };
      if (totalCount > effectiveLimit) {
        output.truncated = true;
        output.total = totalCount;
        output.showing = effectiveLimit;
      }

      return { content: [{ type: 'text', text: JSON.stringify(output, null, 2) }] };
    }
  );
}
