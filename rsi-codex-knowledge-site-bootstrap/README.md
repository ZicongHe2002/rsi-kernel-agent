# RSI Kernel Agent — Codex Knowledge-Site Bootstrap

This package prepares a project-local `knowledge-site/` and audits existing Codex Skills.

## Security
`knowledge-site/` may contain documents, images, diagrams, permission **policies**, and sanitized connection templates. Never copy passwords, API keys, bearer tokens, OAuth access/refresh tokens, cookies, private keys, browser profiles, `auth.json`, or other live credentials into the repository.

Account permissions are not ordinary files. Document required roles and boundaries in `access/ACCESS_POLICY.md`; apply actual ACLs separately through approved controls. Git does not preserve full user/group ACLs.

ChatGPT/Codex connector authorization is generally account/user scoped. Keep only sanitized MCP templates in Git; credentials remain in secure local stores or environment variables. Do not put provider-auth overrides in project config.

## Order
1. Run `prompts/01_M0_AUDIT_AND_KNOWLEDGE_SITE.md`.
2. Review inventory, conflicts, ACL plan and connection template.
3. Approve exact write scope.
4. Run `prompts/02_POPULATE_KNOWLEDGE_SITE.md`.
5. Run `prompts/03_CODEX_SKILL_AUDIT_AND_ROUTING.md`.

## Codex locations
- Repo Skills: `.agents/skills/`
- User Skills: `~/.agents/skills/`
- Admin Skills: `/etc/codex/skills/`
- Project guidance: `AGENTS.md`
- User guidance: `~/.codex/AGENTS.md`
- Project config: `.codex/config.toml` (trusted projects only)
- User config: `~/.codex/config.toml`

Do not assume this ChatGPT conversation can inspect local Codex directories.
