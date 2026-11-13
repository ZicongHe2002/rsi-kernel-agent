# ChatGPT / Codex Connections

This folder contains sanitized connection documentation and examples only.

Document MCP server names, purpose, non-secret endpoint placeholders, required scopes, interactive authorization, environment variable names (never values), health checks and disconnect steps.

Never commit API keys, bearer tokens, OAuth access/refresh tokens, cookies, browser profiles, `~/.codex/auth.json`, private keys or service-account secrets.

Use project `.codex/config.toml` only for safe, shareable trusted-project settings. Keep user authentication and provider configuration in the user's secure local Codex flow. Account-level ChatGPT connectors may require interactive authorization and may not be exportable as repository files.
