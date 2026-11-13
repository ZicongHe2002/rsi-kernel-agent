# RSI Kernel Agent — Access Policy

Document who may read, modify, execute and export each resource.

| Resource | Read | Write | Execute | External sharing |
|---|---|---|---|---|
| Source code | define | define | N/A | define |
| Knowledge documents | define | define | N/A | define |
| Raw Profile traces | define | define | N/A | define |
| Benchmark results | define | define | N/A | define |
| TPU environment | define | define | define | define |
| ChatGPT/Codex connectors | define | define | define | define |

Principles: least privilege; no secrets in Git; raw code/traces follow company policy; external searches use sanitized queries; TPU execution and account-level permission changes require authorization.

This file documents policy; it does not apply ACLs. Git does not preserve full user/group ACL state.

Never store API keys, OAuth tokens, cookies, private keys, browser profiles or local auth databases in this repository. Use approved secure stores or environment-variable injection.
