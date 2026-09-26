# Deployed legacy source snapshot (2026-09-26)

These two files preserve the exact source found in the older ZenMoney server deployment. They are reference copies, not the active package or deployment instructions.

- `bootstrap_realm.py` targets the former standalone `budget` realm and historical MCP audience.
- `auth.py` accepts MCP resource paths that end in `/mcp`, including the former nested URL.

The active shared `master` realm is configured by the ProFinansy deployment source. Keep runtime credentials, tokens, account data, and server environment files out of this directory.
