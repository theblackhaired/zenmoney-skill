# Runtime model

The MCP calls the migrated `scripts/zenmoney` Python core directly. Its 28 names and financial validation come from `scripts/zenmoney/tools.py`. The legacy CLI is not shipped.

## Per-call financial state

- `tools._run_tool_fresh` creates an in-memory `Cache` for one serialized call and clears it in `finally`.
- Syntax is checked before network access. Entity validation runs again after the fresh fetch.
- Ordinary data tools call public `POST /v8/diff/` with `serverTimestamp: 0`. `check_auth_status` owns its live sync; `suggest` calls `/v8/suggest/`; `setup_budget_mode` changes local config only.
- No MCP call reads or writes `.cache.json`. A fresh read is deliberate despite extra API traffic. Financial results are not cached between calls.
- After a write, the core force-fetches affected entity types with cursor zero and confirms submitted fields or deletion absence. An unconfirmed change is an error.

## Local config

`ZENMONEY_STATE_DIR` selects the private directory for `config.json`. The MCP adapter supplies a user-data-directory default before importing the core. The token comes from `ZENMONEY_TOKEN` first, then `config.json -> token`. Atomic replace and file locks protect configuration edits. The core has no disk-backed API cache implementation.

## Concurrency and context

The financial handlers use a process-wide cache reference and HTTP client. The MCP adapter serializes calls with an async lock; remote HTTP should run as one process/worker per financial profile. Results have `compact` and `full` modes. Compact responses reduce material sent to the model, but do not change the fetched snapshot, calculations or write verification. Errors are never shortened.

## Remote authentication

The remote ASGI wrapper requires a valid OAuth JWT on every `/mcp` request before MCP dispatch. It validates signature against configured JWKS, issuer, exact audience, expiry, allowed subject and scopes. Scope `finance:read` is required for all MCP access; writes also require `finance:write` and `confirm_write=true`. The server-side ZenMoney API token is separate. An external OAuth authorization server supplies browser login, PKCE, discovery, client registration and refresh-token flow.
