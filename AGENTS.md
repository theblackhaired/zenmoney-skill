# Project instructions

- This standalone MCP is a personal finance integration. Never copy, print, log, commit, or include `config.json`, `.cache.json`, `.mcp.json`, tokens, account snapshots, balances, or private profiles in artifacts.
- Preserve the 28 names and financial behavior defined by `scripts/zenmoney/tools.py` and the source contract tests. The MCP adapter calls the Python core directly.
- MCP requests use a fresh full public ZenMoney `/v8/diff/` snapshot per call. Do not add persistent API data caching. Keep write verification by server force-fetch.
- Remote requests must fail closed without valid OAuth identity, audience, subject, and scope. Writes require both write scope and explicit `confirm_write=true`.
- Run the financial and MCP test suites before claiming parity. Never use live write calls as tests.
- Do not commit unless explicitly requested. Review the diff and exclude private state before any Git write.
