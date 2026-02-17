import { authenticate } from './auth.js';
import { AuthCredentials, AuthData, DiffObject } from './types.js';

const BASE_URL = 'https://api.zenmoney.ru';

export interface ClientConfig {
  token?: string;
  credentials?: AuthCredentials;
}

export class ZenMoneyClient {
  private token: string | null = null;
  private credentials: AuthCredentials | null = null;
  private authData: AuthData | null = null;

  constructor(config: ClientConfig) {
    if (config.token) {
      this.token = config.token;
    }
    if (config.credentials) {
      this.credentials = config.credentials;
    }
  }

  async ensureAuth(): Promise<void> {
    if (this.token) return;
    if (!this.credentials) {
      throw new Error('No token or credentials provided');
    }
    this.authData = await authenticate(this.credentials);
    this.token = this.authData.accessToken;
  }

  private async request<T>(endpoint: string, body: unknown): Promise<T> {
    await this.ensureAuth();

    const response = await fetch(`${BASE_URL}${endpoint}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${this.token}`,
      },
      body: JSON.stringify(body),
    });

    if (response.status === 401) {
      if (!this.credentials) {
        throw new Error('Token expired. Re-authenticate: run "npm run auth" or get a new token from https://budgera.com/settings/export');
      }
      // Token expired — re-authenticate with credentials
      this.token = null;
      await this.ensureAuth();
      // Retry once
      const retryResponse = await fetch(`${BASE_URL}${endpoint}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${this.token}`,
        },
        body: JSON.stringify(body),
      });
      if (!retryResponse.ok) {
        throw new Error(`API error: ${retryResponse.status} ${await retryResponse.text()}`);
      }
      return retryResponse.json() as Promise<T>;
    }

    if (!response.ok) {
      throw new Error(`API error: ${response.status} ${await response.text()}`);
    }

    return response.json() as Promise<T>;
  }

  /**
   * Sync data with ZenMoney. Can both read and write.
   * - To read: pass serverTimestamp (0 = full sync)
   * - To write: include entity arrays (transaction, account, etc.)
   */
  async diff(params: DiffObject = {}): Promise<DiffObject> {
    const body: DiffObject = {
      currentClientTimestamp: Math.floor(Date.now() / 1000),
      serverTimestamp: 0,
      ...params,
    };
    return this.request<DiffObject>('/v8/diff/', body);
  }

  /**
   * Get category/merchant suggestions for a transaction.
   */
  async suggest(data: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>('/v8/suggest/', data);
  }
}
