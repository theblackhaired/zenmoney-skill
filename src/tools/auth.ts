import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { ZenMoneyClient } from '../api/client.js';
import { DataCache } from '../utils/cache.js';

export function registerAuthTools(server: McpServer, client: ZenMoneyClient, cache: DataCache): void {
  server.tool(
    'check_auth_status',
    'Check current authentication status and token validity. Returns whether the client is authenticated and can make API calls.',
    {},
    async () => {
      try {
        // Use a lightweight incremental sync instead of full sync
        // If cache is already initialized, this returns minimal diff
        // If not, this initializes the cache (useful side effect)
        await cache.ensureInitialized();

        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  status: 'authenticated',
                  message: 'Token is valid and working',
                  note: 'ZenMoney API is accessible',
                },
                null,
                2
              ),
            },
          ],
        };
      } catch (error) {
        const errorMessage = error instanceof Error ? error.message : String(error);

        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  status: 'error',
                  message: 'Authentication failed',
                  error: errorMessage,
                  solution: errorMessage.includes('401') || errorMessage.includes('expired')
                    ? 'Token expired. Run: npm run auth or get a new token from https://budgera.com/settings/export'
                    : 'Check your credentials or network connection',
                },
                null,
                2
              ),
            },
          ],
        };
      }
    }
  );
}
