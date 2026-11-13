# M0 — Audit the Project and Plan `knowledge-site/`

You are Codex in the RSI Kernel Agent project. The owner has moved the RSI knowledge base into a dedicated source folder. The target project must contain `knowledge-site/` with the complete usable knowledge base: documents, images/diagrams, access-permission policy, and sanitized ChatGPT/Codex connection templates.

This is audit and migration planning first. Do not perform destructive moves or copy credentials.

## 1. Establish boundaries
- Record cwd, Git root, branch and `git status`.
- Identify the actual project root and designated knowledge source folder. If multiple candidates exist, ask; do not guess.
- Read applicable `AGENTS.md`, README and project rules.
- Inspect existing `knowledge-site/` without overwriting it.
- Respect write-scope restrictions. Do not write outside the approved scope.

## 2. Inventory all source assets
Recursively inventory Markdown/text, PDF, Office docs, images, screenshots, Mermaid source/rendered diagrams, attachments, relative links, access metadata and connection configs.
For each item record source path, type, size, title, linked assets, references, duplicates/outdated status, and destination proposal.
Do not print secret values. For suspected secrets record only path and category.

## 3. Establish document authority
Classify as CURRENT, SUPPLEMENT, REFERENCE, ARCHIVE, DRAFT or UNKNOWN. Identify conflicting revisions and exact competing claims. Do not silently merge or delete.
Preserve agreed RSI principles:
- multiple strategies A/B/... can each generate multiple evidence-backed Plans and Candidates in one round;
- no forced single winner before experiments;
- Memory reads all status cards, then full histories for every participating strategy;
- Analyzer is a Profiler child branch;
- Analyzer combines code, workload, Profile/Benchmark evidence, Memory and Knowledge to return multiple Plans;
- Benchmark owns correctness, comparable timing, coverage and promotion;
- unknown causal explanation does not automatically block a correct, reliably faster candidate;
- preserve failures, regressions, deferred candidates, costs and decisions.

## 4. Permissions and connections — security critical
Distinguish account roles, repository permissions, filesystem permissions and runtime tool permissions.
Create a proposed `ACCESS_POLICY.md` with least privilege, read/write boundaries, TPU execution permissions and external sharing rules. Do not change ACLs.
Inspect MCP/ChatGPT/Codex config structure but never copy live credentials.
Never copy `~/.codex/auth.json`, OAuth tokens, API keys, cookies, browser profiles, private keys or service-account secrets.
Create only sanitized examples using environment-variable placeholders. Do not override provider authentication in project config.
Document which account-level connectors require interactive user authorization.
Check `.gitignore` for secrets and generated traces.

## 5. M0 deliverables
- knowledge inventory
- document authority map
- duplicate/conflict report
- image/attachment manifest
- access/connection audit (no secrets)
- migration plan and proposed tree
- unresolved questions and exact approvals required

## 6. Stop conditions
Do not move, rename, delete or overwrite source files. Do not install Skills/plugins, change permissions, authorize connectors or run TPU jobs. Return what was actually inspected and what remains unavailable.
